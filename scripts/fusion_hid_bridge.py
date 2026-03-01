#!/usr/bin/env python3

import argparse
import struct
import sys
import time
from dataclasses import dataclass

try:
    import hid
except ImportError as exc:
    print(f"Missing dependency: hid ({exc})", file=sys.stderr)
    print("Install it in the Python environment you use to run the bridge.", file=sys.stderr)
    sys.exit(1)

try:
    from pynput.keyboard import Controller as KeyboardController
    from pynput.keyboard import Key
    from pynput.mouse import Button
    from pynput.mouse import Controller as MouseController
except ImportError as exc:
    print(f"Missing dependency: pynput ({exc})", file=sys.stderr)
    sys.exit(1)


@dataclass
class BridgeConfig:
    vendor_id: int | None
    product_id: int | None
    translate_divisor: float
    rotate_divisor: float
    translate_gain: float
    rotate_gain: float
    idle_release_s: float
    poll_interval_s: float
    invert_x: bool
    invert_y: bool
    verbose: bool


def parse_args() -> BridgeConfig:
    parser = argparse.ArgumentParser(
        description="Read diy-spacemouse-like HID reports and translate them into macOS input events for Fusion."
    )
    parser.add_argument("--vendor-id", type=lambda value: int(value, 0), help="Optional HID vendor id filter.")
    parser.add_argument("--product-id", type=lambda value: int(value, 0), help="Optional HID product id filter.")
    parser.add_argument(
        "--translate-divisor",
        type=float,
        default=900.0,
        help="Divisor used to convert RID 1 int16 values into cursor delta steps.",
    )
    parser.add_argument(
        "--rotate-divisor",
        type=float,
        default=900.0,
        help="Divisor used to convert RID 2 int16 values into cursor delta steps.",
    )
    parser.add_argument(
        "--translate-gain",
        type=float,
        default=1.0,
        help="Extra gain applied to RID 1 deltas after the divisor.",
    )
    parser.add_argument(
        "--rotate-gain",
        type=float,
        default=1.0,
        help="Extra gain applied to RID 2 deltas after the divisor.",
    )
    parser.add_argument(
        "--idle-release-ms",
        type=float,
        default=120.0,
        help="Release held drag state after this much inactivity.",
    )
    parser.add_argument(
        "--poll-ms",
        type=float,
        default=5.0,
        help="Sleep between read attempts.",
    )
    parser.add_argument("--invert-x", action="store_true", help="Invert the X axis.")
    parser.add_argument("--invert-y", action="store_true", help="Invert the Y axis.")
    parser.add_argument("--verbose", action="store_true", help="Print decoded reports and bridge state.")
    args = parser.parse_args()

    if (args.vendor_id is None) != (args.product_id is None):
        parser.error("--vendor-id and --product-id must be used together.")

    return BridgeConfig(
        vendor_id=args.vendor_id,
        product_id=args.product_id,
        translate_divisor=args.translate_divisor,
        rotate_divisor=args.rotate_divisor,
        translate_gain=args.translate_gain,
        rotate_gain=args.rotate_gain,
        idle_release_s=args.idle_release_ms / 1000.0,
        poll_interval_s=args.poll_ms / 1000.0,
        invert_x=args.invert_x,
        invert_y=args.invert_y,
        verbose=args.verbose,
    )


def find_spacemouse(config: BridgeConfig) -> dict:
    for device in hid.enumerate():
        if device.get("usage_page") != 0x01 or device.get("usage") != 0x08:
            continue
        if config.vendor_id is not None:
            if device.get("vendor_id") != config.vendor_id or device.get("product_id") != config.product_id:
                continue
        return device
    raise RuntimeError("No HID Multi-axis Controller device found.")


def unpack_vector(report: list[int]) -> tuple[int, int, int]:
    if len(report) < 7:
        raise ValueError(f"Expected 7-byte vector report, got {len(report)} bytes.")
    payload = bytes(report[1:7])
    return struct.unpack("<hhh", payload)


class FusionBridge:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.drag_mode: str | None = None
        self.last_motion_at = 0.0
        self.last_button_bits = 0
        self.mouse = MouseController()
        self.keyboard = KeyboardController()

    def log(self, message: str) -> None:
        if self.config.verbose:
            print(message, flush=True)

    def ensure_drag_mode(self, mode: str) -> None:
        if self.drag_mode == mode:
            return

        self.release_drag()

        if mode == "pan":
            self.keyboard.press(Key.shift)

        self.mouse.press(Button.middle)
        self.drag_mode = mode
        self.log(f"drag_mode={mode}")

    def release_drag(self) -> None:
        if self.drag_mode is None:
            return

        self.mouse.release(Button.middle)

        if self.drag_mode == "pan":
            self.keyboard.release(Key.shift)

        self.log(f"drag_mode={self.drag_mode}->none")
        self.drag_mode = None

    def drag_by(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return

        self.mouse.move(dx, dy)
        self.last_motion_at = time.monotonic()

    def scale_delta(self, raw_x: int, raw_y: int, divisor: float, gain: float) -> tuple[int, int]:
        dx = int(round((raw_x / divisor) * gain))
        dy = int(round((raw_y / divisor) * gain))

        if self.config.invert_x:
            dx = -dx
        if self.config.invert_y:
            dy = -dy

        return dx, dy

    def handle_translation(self, report: list[int]) -> None:
        x, y, z = unpack_vector(report)
        dx, dy = self.scale_delta(x, y, self.config.translate_divisor, self.config.translate_gain)
        self.log(f"RID1 x={x} y={y} z={z} -> dx={dx} dy={dy}")
        if dx or dy:
            self.ensure_drag_mode("pan")
            self.drag_by(dx, dy)

    def handle_rotation(self, report: list[int]) -> None:
        rx, ry, rz = unpack_vector(report)
        dx, dy = self.scale_delta(rx, ry, self.config.rotate_divisor, self.config.rotate_gain)
        self.log(f"RID2 rx={rx} ry={ry} rz={rz} -> dx={dx} dy={dy}")
        if dx or dy:
            self.ensure_drag_mode("orbit")
            self.drag_by(dx, dy)

    def send_home_shortcut(self) -> None:
        self.log("button1 -> Cmd+Shift+H")
        self.keyboard.press(Key.cmd)
        self.keyboard.press(Key.shift)
        self.keyboard.press("h")
        self.keyboard.release("h")
        self.keyboard.release(Key.shift)
        self.keyboard.release(Key.cmd)

    def send_fit_to_view(self) -> None:
        self.log("button2 -> double middle click")
        self.release_drag()
        for _ in range(2):
            self.mouse.click(Button.middle, 1)
            time.sleep(0.01)

    def handle_buttons(self, report: list[int]) -> None:
        if len(report) < 2:
            raise ValueError(f"Expected 2-byte button report, got {len(report)} bytes.")

        button_bits = report[1]
        pressed = button_bits & ~self.last_button_bits
        self.last_button_bits = button_bits
        self.log(f"RID3 buttons={button_bits:08b}")

        if pressed & 0x01:
            self.send_home_shortcut()
        if pressed & 0x02:
            self.send_fit_to_view()

    def maybe_release_idle(self) -> None:
        if self.drag_mode is None:
            return

        if time.monotonic() - self.last_motion_at >= self.config.idle_release_s:
            self.release_drag()


def main() -> int:
    config = parse_args()

    try:
        device_info = find_spacemouse(config)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Using HID device: {device_info}", flush=True)
    print("Grant Accessibility permission to Terminal/Python if Fusion does not react.", flush=True)
    hid_device = hid.device()

    try:
        hid_device.open_path(device_info["path"])
        hid_device.set_nonblocking(True)
        bridge = FusionBridge(config)

        while True:
            report = hid_device.read(64)
            if report:
                report_id = report[0]

                if report_id == 1:
                    bridge.handle_translation(report)
                elif report_id == 2:
                    bridge.handle_rotation(report)
                elif report_id == 3:
                    bridge.handle_buttons(report)
                elif config.verbose:
                    print(f"Ignoring report id {report_id}: {report}", flush=True)

            bridge.maybe_release_idle()
            time.sleep(config.poll_interval_s)

    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"Failed to open HID device ({exc}).", file=sys.stderr)
        print("Close other readers of the device and try again.", file=sys.stderr)
        return 1
    finally:
        if "bridge" in locals():
            bridge.release_drag()
        try:
            hid_device.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
