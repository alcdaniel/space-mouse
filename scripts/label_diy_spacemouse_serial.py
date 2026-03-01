#!/usr/bin/env python3

import argparse
import queue
import statistics
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Missing dependency: pyserial", file=sys.stderr)
    print("Install it with: python3 -m pip install pyserial", file=sys.stderr)
    sys.exit(1)


def detect_port(explicit_port: Optional[str]) -> str:
    if explicit_port:
        return explicit_port

    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("No serial ports found.")

    def score(port) -> tuple[int, str]:
        device = port.device or ""
        description = (port.description or "").lower()
        vid = port.vid

        if vid == 0x239A:
            return (0, device)
        if "usbmodem" in device:
            return (1, device)
        if "usb serial" in description or "usb" in description:
            return (2, device)
        if "ttyacm" in device or "ttyusb" in device:
            return (3, device)
        return (9, device)

    ports.sort(key=score)
    best = ports[0]

    if score(best)[0] >= 9:
        raise RuntimeError("Could not auto-detect the RP2040 port. Use --port.")

    return best.device


def parse_sample(line: str) -> Optional[tuple[float, float, float]]:
    values = []
    for token in line.split(","):
        item = token.strip()
        if not item:
            continue
        try:
            values.append(float(item))
        except ValueError:
            continue
        if len(values) == 3:
            return values[0], values[1], values[2]
    return None


class SerialReader(threading.Thread):
    def __init__(
        self,
        port: str,
        baud: int,
        output_queue: queue.Queue,
        command_queue: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.output_queue = output_queue
        self.command_queue = command_queue
        self.stop_event = stop_event

    def run(self) -> None:
        try:
            with serial.Serial(self.port, self.baud, timeout=0.2) as ser:
                time.sleep(2.0)
                ser.reset_input_buffer()
                start = time.time()
                self.output_queue.put(("status", f"Connected to {self.port}"))

                while not self.stop_event.is_set():
                    try:
                        while True:
                            command = self.command_queue.get_nowait()
                            ser.write(command.encode("utf-8"))
                            ser.flush()
                    except queue.Empty:
                        pass

                    raw = ser.readline()
                    if not raw:
                        continue

                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    elapsed = time.time() - start
                    self.output_queue.put(("line", line))

                    sample = parse_sample(line)
                    if sample is not None:
                        x, y, z = sample
                        self.output_queue.put(("sample", elapsed, x, y, z))
        except Exception as exc:
            self.output_queue.put(("error", str(exc)))


class PlotCanvas(tk.Canvas):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, highlightthickness=0, **kwargs)
        self.configure(background="#101418")

    def redraw(
        self,
        samples: list[tuple[float, float, float, float]],
        mark_start_time: Optional[float],
        mark_end_time: Optional[float],
    ) -> None:
        self.delete("all")

        width = max(self.winfo_width(), 10)
        height = max(self.winfo_height(), 10)

        self.create_rectangle(0, 0, width, height, fill="#101418", outline="")
        self.create_line(0, height / 2, width, height / 2, fill="#2f3942")
        self.create_text(
            8,
            8,
            anchor="nw",
            text="X red | Y green | Z blue",
            fill="#c8d1d9",
            font=("Menlo", 11),
        )

        if len(samples) < 2:
            return

        visible = samples[-300:]
        max_abs = 0.1
        for _, x, y, z in visible:
            max_abs = max(max_abs, abs(x), abs(y), abs(z))

        t0 = visible[0][0]
        t1 = visible[-1][0]
        t_span = max(t1 - t0, 0.001)

        def to_xy(point_time: float, value: float) -> tuple[float, float]:
            x_pos = ((point_time - t0) / t_span) * (width - 20) + 10
            y_pos = (height / 2) - (value / max_abs) * ((height - 40) / 2)
            return x_pos, y_pos

        def draw_axis(index: int, color: str) -> None:
            coords = []
            for sample in visible:
                px, py = to_xy(sample[0], sample[index])
                coords.extend((px, py))
            if len(coords) >= 4:
                self.create_line(*coords, fill=color, width=2, smooth=False)

        draw_axis(1, "#ff5f56")
        draw_axis(2, "#27c93f")
        draw_axis(3, "#57a0ff")

        self.create_text(
            width - 8,
            8,
            anchor="ne",
            text=f"scale +/-{max_abs:.3f}",
            fill="#9aa6b2",
            font=("Menlo", 11),
        )

        if mark_start_time is not None:
            start_x, _ = to_xy(max(mark_start_time, t0), 0.0)
            self.create_line(start_x, 0, start_x, height, fill="#ffd166", dash=(6, 4))

        if mark_end_time is not None:
            end_x, _ = to_xy(min(mark_end_time, t1), 0.0)
            self.create_line(end_x, 0, end_x, height, fill="#ef476f", dash=(6, 4))


class App:
    def __init__(self, root: tk.Tk, port: Optional[str], baud: int) -> None:
        self.root = root
        self.root.title("DIY Spacemouse Label Tool")
        self.root.geometry("1200x820")

        self.output_queue: queue.Queue = queue.Queue()
        self.command_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.reader: Optional[SerialReader] = None

        self.samples: list[tuple[float, float, float, float]] = []
        self.mark_start_index: Optional[int] = None
        self.mark_start_time: Optional[float] = None
        self.last_redraw = 0.0

        self.port_var = tk.StringVar(value=port or "")
        self.baud_var = tk.StringVar(value=str(baud))
        self.label_var = tk.StringVar(value="rotate_left_right")
        self.status_var = tk.StringVar(value="Disconnected")
        self.live_var = tk.StringVar(value="x=0.000 y=0.000 z=0.000")

        self.build_ui()
        self.connect_if_possible()
        self.root.after(50, self.pump_events)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self) -> None:
        self.root.configure(background="#0c1014")

        controls = ttk.Frame(self.root, padding=12)
        controls.pack(fill="x")

        ttk.Label(controls, text="Port").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.port_var, width=22).grid(row=0, column=1, padx=(6, 14))

        ttk.Label(controls, text="Baud").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.baud_var, width=10).grid(row=0, column=3, padx=(6, 14))

        ttk.Button(controls, text="Connect", command=self.connect).grid(row=0, column=4, padx=4)
        ttk.Button(controls, text="Reconnect", command=self.reconnect).grid(row=0, column=5, padx=4)

        ttk.Label(controls, textvariable=self.status_var).grid(row=0, column=6, padx=(12, 0), sticky="w")

        ttk.Label(controls, text="Label").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.label_var, width=32).grid(row=1, column=1, columnspan=2, padx=(6, 14), pady=(10, 0), sticky="we")
        ttk.Button(controls, text="Start Label", command=self.start_label).grid(row=1, column=3, padx=4, pady=(10, 0))
        ttk.Button(controls, text="End Label", command=self.end_label).grid(row=1, column=4, padx=4, pady=(10, 0))
        ttk.Button(controls, text="Copy Report", command=self.copy_report).grid(row=1, column=5, padx=4, pady=(10, 0))
        ttk.Button(controls, text="Clear Report", command=self.clear_report).grid(row=1, column=6, padx=4, pady=(10, 0))

        quick = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        quick.pack(fill="x")

        ttk.Label(quick, text="Board commands").pack(side="left")
        ttk.Button(quick, text="Calibrate (c)", command=lambda: self.send_command("c")).pack(side="left", padx=4)
        ttk.Button(quick, text="Zero Now (z)", command=lambda: self.send_command("z")).pack(side="left", padx=4)
        ttk.Button(quick, text="Gain +", command=lambda: self.send_command("+")).pack(side="left", padx=4)
        ttk.Button(quick, text="Gain -", command=lambda: self.send_command("-")).pack(side="left", padx=4)
        ttk.Button(quick, text="Deadband +", command=lambda: self.send_command("d")).pack(side="left", padx=4)
        ttk.Button(quick, text="Deadband -", command=lambda: self.send_command("f")).pack(side="left", padx=4)
        ttk.Label(quick, textvariable=self.live_var).pack(side="right")

        self.plot = PlotCanvas(self.root, height=380)
        self.plot.pack(fill="both", expand=True, padx=12)

        panes = ttk.Frame(self.root, padding=12)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(0, weight=1)

        left_frame = ttk.Frame(panes)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Label(left_frame, text="Live Serial").pack(anchor="w")
        self.live_text = tk.Text(
            left_frame,
            height=14,
            wrap="none",
            background="#11161c",
            foreground="#d7dee5",
            insertbackground="#d7dee5",
            font=("Menlo", 11),
        )
        self.live_text.pack(fill="both", expand=True)

        right_frame = ttk.Frame(panes)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Label(right_frame, text="Label Report").pack(anchor="w")
        self.report_text = tk.Text(
            right_frame,
            height=14,
            wrap="word",
            background="#11161c",
            foreground="#d7dee5",
            insertbackground="#d7dee5",
            font=("Menlo", 11),
        )
        self.report_text.pack(fill="both", expand=True)
        self.report_text.insert(
            "end",
            "Recorded movement blocks.\n\n",
        )

    def connect_if_possible(self) -> None:
        if self.port_var.get().strip():
            self.connect()
            return

        try:
            self.port_var.set(detect_port(None))
            self.connect()
        except RuntimeError as exc:
            self.status_var.set(str(exc))

    def connect(self) -> None:
        if self.reader and self.reader.is_alive():
            self.status_var.set("Already connected")
            return

        try:
            port = detect_port(self.port_var.get().strip() or None)
            baud = int(self.baud_var.get().strip())
        except (RuntimeError, ValueError) as exc:
            self.status_var.set(str(exc))
            return

        self.stop_event = threading.Event()
        self.reader = SerialReader(
            port=port,
            baud=baud,
            output_queue=self.output_queue,
            command_queue=self.command_queue,
            stop_event=self.stop_event,
        )
        self.reader.start()
        self.port_var.set(port)
        self.status_var.set(f"Opening {port}...")

    def reconnect(self) -> None:
        self.disconnect()
        self.samples.clear()
        self.mark_start_index = None
        self.mark_start_time = None
        self.plot.redraw(self.samples, None, None)
        self.connect()

    def disconnect(self) -> None:
        if self.reader and self.reader.is_alive():
            self.stop_event.set()
            self.reader.join(timeout=1.0)
        self.reader = None

    def send_command(self, command: str) -> None:
        self.command_queue.put(command)
        self.status_var.set(f"Sent command: {command}")

    def append_live_line(self, line: str) -> None:
        self.live_text.insert("end", line + "\n")
        if int(self.live_text.index("end-1c").split(".")[0]) > 300:
            self.live_text.delete("1.0", "50.0")
        self.live_text.see("end")

    def start_label(self) -> None:
        if not self.samples:
            self.status_var.set("No samples yet.")
            return

        self.mark_start_index = len(self.samples) - 1
        self.mark_start_time = self.samples[self.mark_start_index][0]
        self.status_var.set(f"Label started: {self.label_var.get().strip() or 'unlabeled'}")

    def end_label(self) -> None:
        if self.mark_start_index is None or self.mark_start_index >= len(self.samples):
            self.status_var.set("Start a label first.")
            return

        end_index = len(self.samples) - 1
        if end_index <= self.mark_start_index:
            self.status_var.set("Not enough samples in labeled segment.")
            return

        segment = self.samples[self.mark_start_index : end_index + 1]
        label = self.label_var.get().strip() or "unlabeled"
        report = self.build_report(label, segment)
        self.report_text.insert("end", report)
        self.report_text.see("end")

        self.status_var.set(f"Saved label: {label}")
        self.mark_start_index = None
        self.mark_start_time = None

    def build_report(
        self,
        label: str,
        segment: list[tuple[float, float, float, float]],
    ) -> str:
        start_t = segment[0][0]
        end_t = segment[-1][0]

        xs = [item[1] for item in segment]
        ys = [item[2] for item in segment]
        zs = [item[3] for item in segment]

        def stat_line(name: str, values: list[float]) -> str:
            start_v = values[0]
            end_v = values[-1]
            delta_v = end_v - start_v
            return (
                f"{name}: start={start_v:.4f}, end={end_v:.4f}, "
                f"delta={delta_v:.4f}, min={min(values):.4f}, max={max(values):.4f}, "
                f"mean={statistics.fmean(values):.4f}, span={(max(values) - min(values)):.4f}"
            )

        lines = [
            f"[{label}]",
            f"start_s={start_t:.3f}, end_s={end_t:.3f}, duration_s={(end_t - start_t):.3f}, samples={len(segment)}",
            stat_line("x", xs),
            stat_line("y", ys),
            stat_line("z", zs),
            "",
        ]
        return "\n".join(lines)

    def copy_report(self) -> None:
        content = self.report_text.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_var.set("Report copied to clipboard")

    def clear_report(self) -> None:
        self.report_text.delete("1.0", "end")
        self.report_text.insert(
            "end",
            "Recorded movement blocks.\n\n",
        )
        self.status_var.set("Report cleared")

    def pump_events(self) -> None:
        mark_end_time = None
        if self.samples:
            mark_end_time = self.samples[-1][0]

        while True:
            try:
                event = self.output_queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "status":
                self.status_var.set(event[1])
            elif kind == "line":
                self.append_live_line(event[1])
            elif kind == "sample":
                _, elapsed, x, y, z = event
                self.samples.append((elapsed, x, y, z))
                if len(self.samples) > 3000:
                    self.samples = self.samples[-3000:]
                    if self.mark_start_index is not None:
                        self.mark_start_index = max(0, self.mark_start_index - 1)
                self.live_var.set(f"x={x:.4f}  y={y:.4f}  z={z:.4f}")
                mark_end_time = elapsed
            elif kind == "error":
                self.status_var.set(f"Serial error: {event[1]}")
                self.disconnect()

        now = time.time()
        if now - self.last_redraw > 0.08:
            self.last_redraw = now
            self.plot.redraw(self.samples, self.mark_start_time, mark_end_time)

        self.root.after(50, self.pump_events)

    def on_close(self) -> None:
        self.disconnect()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live graph and labeling tool for the DIY spacemouse calibration stream."
    )
    parser.add_argument("--port", help="Serial port to open. Default: auto-detect.")
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate. Default: 115200.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = tk.Tk()
    App(root, args.port, args.baud)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
