#!/usr/bin/env python3

import argparse
import datetime as dt
import statistics
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Missing dependency: pyserial", file=sys.stderr)
    print("Install it with: python3 -m pip install pyserial", file=sys.stderr)
    sys.exit(1)


MOVEMENTS = [
    ("center_free", "Leave the cap centered and do not press Z."),
    ("move_left", "Move the object left. Do NOT press Z."),
    ("move_right", "Move the object right. Do NOT press Z."),
    ("move_up", "Move the object up. Do NOT press Z."),
    ("move_down", "Move the object down. Do NOT press Z."),
    ("center_pressed_z", "Return to center, now keep Z pressed."),
    ("rotate_left", "With Z pressed, rotate left."),
    ("rotate_right", "With Z pressed, rotate right."),
    ("rotate_up", "With Z pressed, rotate up."),
    ("rotate_down", "With Z pressed, rotate down."),
]


def detect_port(explicit_port: str | None) -> str:
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
        raise RuntimeError("Could not auto-detect the RP2040 port. Use --port explicitly.")

    return best.device


def parse_xyz(line: str) -> tuple[float, float, float] | None:
    parts = [item.strip() for item in line.split(",")]
    if len(parts) < 3:
        return None

    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def summarize(values: list[float]) -> str:
    return (
        f"start={values[0]:.4f}, end={values[-1]:.4f}, delta={(values[-1] - values[0]):.4f}, "
        f"min={min(values):.4f}, max={max(values):.4f}, mean={statistics.fmean(values):.4f}, "
        f"span={(max(values) - min(values)):.4f}"
    )


def countdown(seconds: int) -> None:
    for remaining in range(seconds, 0, -1):
        print(f"  Starting in {remaining}...", flush=True)
        time.sleep(1.0)


def capture_window(
    ser: serial.Serial,
    duration_s: float,
    absolute_start: float,
) -> tuple[list[tuple[float, str]], list[tuple[float, float, float, float]]]:
    raw_lines: list[tuple[float, str]] = []
    samples: list[tuple[float, float, float, float]] = []

    end_time = time.time() + duration_s
    while time.time() < end_time:
        raw = ser.readline()
        if not raw:
            continue

        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        elapsed = time.time() - absolute_start
        raw_lines.append((elapsed, line))

        xyz = parse_xyz(line)
        if xyz is None:
            continue

        x, y, z = xyz
        samples.append((elapsed, x, y, z))

    return raw_lines, samples


def build_report(
    label: str,
    instruction: str,
    raw_lines: list[tuple[float, str]],
    samples: list[tuple[float, float, float, float]],
) -> str:
    lines: list[str] = []
    lines.append(f"[{label}]")
    lines.append(f"instruction={instruction}")

    if samples:
        start_s = samples[0][0]
        end_s = samples[-1][0]
        lines.append(
            f"start_s={start_s:.3f}, end_s={end_s:.3f}, duration_s={(end_s - start_s):.3f}, samples={len(samples)}"
        )

        xs = [row[1] for row in samples]
        ys = [row[2] for row in samples]
        zs = [row[3] for row in samples]
        lines.append(f"x: {summarize(xs)}")
        lines.append(f"y: {summarize(ys)}")
        lines.append(f"z: {summarize(zs)}")
    else:
        lines.append("No numeric samples captured.")

    lines.append("raw_samples:")
    if raw_lines:
        for timestamp, text in raw_lines:
            lines.append(f"  {timestamp:.3f}s {text}")
    else:
        lines.append("  <none>")

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guided capture wizard for DIY SpaceMouse calibration/debug."
    )
    parser.add_argument(
        "--port",
        help="Serial port to open (default: auto-detect RP2040 USB serial).",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate (default: 115200).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=6.0,
        help="Recording duration per movement in seconds (default: 6).",
    )
    parser.add_argument(
        "--countdown",
        type=int,
        default=3,
        help="Countdown before each capture starts (default: 3).",
    )
    parser.add_argument(
        "--output",
        help="Output TXT path. Default: captures/spacemouse_capture_<timestamp>.txt",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        port = detect_port(args.port)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path("captures") / f"spacemouse_capture_{stamp}.txt"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening {port} at {args.baud} baud...", flush=True)

    try:
        with serial.Serial(port, args.baud, timeout=0.2) as ser:
            time.sleep(2.0)
            ser.reset_input_buffer()
            absolute_start = time.time()

            reports: list[str] = []

            print("")
            print("Guided capture started.")
            print(f"Each movement will be recorded for {args.duration:.1f}s.")
            print("Press Enter when ready for each step. Use Ctrl+C to abort.")
            print("")

            for index, (label, instruction) in enumerate(MOVEMENTS, start=1):
                print(f"[{index}/{len(MOVEMENTS)}] {label}")
                print(f"  {instruction}")
                input("  Press Enter when ready...")

                if args.countdown > 0:
                    countdown(args.countdown)

                print(f"  Recording {label}...", flush=True)
                raw_lines, samples = capture_window(ser, args.duration, absolute_start)
                report = build_report(label, instruction, raw_lines, samples)
                reports.append(report)

                if samples:
                    print(
                        f"  Captured {len(samples)} numeric samples "
                        f"({samples[0][0]:.3f}s -> {samples[-1][0]:.3f}s).",
                        flush=True,
                    )
                else:
                    print("  Warning: no numeric samples captured for this step.", flush=True)

                print("")

            header = [
                "DIY SpaceMouse guided capture",
                f"port={port}",
                f"baud={args.baud}",
                f"duration_s={args.duration}",
                "",
                "Recorded movement blocks",
                "",
            ]

            output_path.write_text("\n".join(header + reports), encoding="utf-8")
            print(f"Capture complete. Saved to {output_path}")

    except KeyboardInterrupt:
        print("\nCapture aborted.", file=sys.stderr)
        return 1
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
