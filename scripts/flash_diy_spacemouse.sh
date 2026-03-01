#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_SKETCH_SUBDIR="diy-spacemouse"
SKETCH_SUBDIR="$DEFAULT_SKETCH_SUBDIR"
FQBN="rp2040:rp2040:adafruit_qtpy:usbstack=tinyusb"
CORE_ID="rp2040:rp2040"
BOOT_VOLUME="/Volumes/RPI-RP2"
REQUIRED_LIBS=()
EXPECT_CORE_TINYUSB=0
USER_TINYUSB_DIR="$HOME/Documents/Arduino/libraries/Adafruit_TinyUSB_Library"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--sketch <folder-inside-Code>]

Default sketch:
  Code/${DEFAULT_SKETCH_SUBDIR}

Examples:
  $(basename "$0")
  $(basename "$0") --sketch diy-spacemouse
  $(basename "$0") --sketch diy-spacemouse-calibration
  $(basename "$0") --sketch diy-spacemouse-like-diagnostic
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sketch)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --sketch" >&2
        usage
        exit 1
      fi
      SKETCH_SUBDIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$SKETCH_SUBDIR" = /* || "$SKETCH_SUBDIR" == *".."* ]]; then
  echo "Sketch path must be a folder inside Code/." >&2
  exit 1
fi

SKETCH_DIR="${REPO_ROOT}/Code/${SKETCH_SUBDIR}"
BUILD_TAG="${SKETCH_SUBDIR//\//-}"
BUILD_TAG="${BUILD_TAG// /-}"
BUILD_DIR="/tmp/${BUILD_TAG}-build"

case "$SKETCH_SUBDIR" in
  diy-spacemouse)
    REQUIRED_LIBS=(
      "Adafruit TinyUSB Library"
      "OneButton"
      "SimpleKalmanFilter"
      "XENSIV 3D Magnetic Sensor TLx493D"
    )
    ;;
  diy-spacemouse-like)
    EXPECT_CORE_TINYUSB=1
    REQUIRED_LIBS=(
      "SimpleKalmanFilter"
      "XENSIV 3D Magnetic Sensor TLx493D"
    )
    ;;
  diy-spacemouse-calibration)
    REQUIRED_LIBS=(
      "XENSIV 3D Magnetic Sensor TLx493D"
    )
    ;;
  diy-spacemouse-like-diagnostic)
    EXPECT_CORE_TINYUSB=1
    REQUIRED_LIBS=()
    ;;
  *)
    REQUIRED_LIBS=(
      "SimpleKalmanFilter"
      "XENSIV 3D Magnetic Sensor TLx493D"
    )
    ;;
esac

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

has_lib() {
  local lib_name="$1"
  arduino-cli lib list | grep -Fq "$lib_name"
}

install_lib_if_missing() {
  local lib_name="$1"
  if has_lib "$lib_name"; then
    echo "Library already installed: $lib_name"
  else
    echo "Installing library: $lib_name"
    arduino-cli lib install "$lib_name"
  fi
}

has_core() {
  arduino-cli core list | awk '{print $1}' | grep -Fxq "$CORE_ID"
}

detect_serial_port() {
  arduino-cli board list | awk '
    /RP2040|rp2040/ && $1 ~ /^\/dev\// { print $1; exit }
    /\/dev\/cu\.usbmodem/ { candidate=$1 }
    END {
      if (candidate != "") print candidate
    }
  '
}

wait_for_bootsel_volume() {
  local timeout_seconds="${1:-60}"
  local waited=0

  while (( waited < timeout_seconds )); do
    if [[ -d "$BOOT_VOLUME" ]]; then
      return 0
    fi
    sleep 1
    ((waited+=1))
  done

  return 1
}

need_cmd arduino-cli
need_cmd cp

if [[ ! -d "$SKETCH_DIR" ]]; then
  echo "Sketch directory not found: $SKETCH_DIR" >&2
  exit 1
fi

echo "Using sketch: $SKETCH_DIR"
if (( EXPECT_CORE_TINYUSB )) && [[ -d "$USER_TINYUSB_DIR" ]]; then
  echo "Note: this sketch is intended to use the RP2040 core's bundled TinyUSB stack."
  echo "If HID enumerates but no reports arrive, temporarily rename:"
  echo "  $USER_TINYUSB_DIR"
fi
echo
echo "Checking Arduino core..."
if has_core; then
  echo "Core already installed: $CORE_ID"
else
  echo "Installing core: $CORE_ID"
  arduino-cli core install "$CORE_ID"
fi

echo
echo "Checking required libraries..."
for lib in "${REQUIRED_LIBS[@]}"; do
  install_lib_if_missing "$lib"
done

echo
SERIAL_PORT="$(detect_serial_port || true)"
if [[ -n "$SERIAL_PORT" ]]; then
  echo "Detected RP2040 serial port: $SERIAL_PORT"
else
  echo "No RP2040 serial port detected right now."
fi

echo
echo "Compiling sketch..."
arduino-cli compile -b "$FQBN" --build-path "$BUILD_DIR" --build-property "usb_stack=tinyusb" "$SKETCH_DIR"

UF2_PATH="$(find "$BUILD_DIR" -maxdepth 1 -name '*.uf2' | head -n 1)"
if [[ -z "$UF2_PATH" ]]; then
  echo "UF2 build artifact not found in $BUILD_DIR" >&2
  exit 1
fi

echo
echo "Built UF2: $UF2_PATH"

if [[ ! -d "$BOOT_VOLUME" ]]; then
  echo
  echo "Put the board into BOOTSEL mode now:"
  echo "1. Hold BOOT"
  echo "2. Press RESET"
  echo "3. Release BOOT"
  echo "The RP2040 should mount as RPI-RP2."
  read -r -p "Press Enter when you have done that and the board is ready..."
fi

echo
echo "Waiting for $BOOT_VOLUME..."
if ! wait_for_bootsel_volume 60; then
  echo "Timed out waiting for $BOOT_VOLUME." >&2
  exit 1
fi

echo "Copying UF2 to the board..."
cp -X "$UF2_PATH" "$BOOT_VOLUME/"

echo
echo "Flash complete. The board should reboot automatically."
