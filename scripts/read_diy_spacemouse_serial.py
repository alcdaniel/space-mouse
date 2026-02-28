#!/usr/bin/env python3

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Missing dependency: pyserial", file=sys.stderr)
    print("Install it with: python3 -m pip install pyserial", file=sys.stderr)
    sys.exit(1)


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
        raise RuntimeError(
            "Could not auto-detect the RP2040 port. Use --port explicitly."
        )

    return best.device


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read the DIY spacemouse calibration stream from the board."
    )
    parser.add_argument(
        "--port",
        help="Serial port to open (default: auto-detect RP2040-style USB port).",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate (default: 115200).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Stop after reading this many lines (default: unlimited).",
    )
    parser.add_argument(
        "--send",
        help="Optional command to send after opening, e.g. c, z, +, -, d, f, h.",
    )
    parser.add_argument(
        "--timestamp",
        action="store_true",
        help="Prefix each line with elapsed seconds.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        port = detect_port(args.port)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Opening {port} at {args.baud} baud...", file=sys.stderr)

    try:
        with serial.Serial(port, args.baud, timeout=1) as ser:
            time.sleep(2.0)
            ser.reset_input_buffer()

            if args.send:
                ser.write(args.send.encode("utf-8"))
                ser.flush()
                time.sleep(0.2)

            start = time.time()
            lines_read = 0

            while True:
                raw = ser.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if args.timestamp:
                    elapsed = time.time() - start
                    print(f"{elapsed:8.3f}s {line}")
                else:
                    print(line)

                lines_read += 1
                if args.count > 0 and lines_read >= args.count:
                    break

    except KeyboardInterrupt:
        return 0
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
