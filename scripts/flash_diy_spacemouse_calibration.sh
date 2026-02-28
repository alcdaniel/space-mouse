#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SKETCH_DIR="${REPO_ROOT}/Code/diy-spacemouse-calibration"
BUILD_DIR="/tmp/diy-spacemouse-calibration-build"
FQBN="rp2040:rp2040:adafruit_qtpy:usbstack=tinyusb"
CORE_ID="rp2040:rp2040"
BOOT_VOLUME="/Volumes/RPI-RP2"

REQUIRED_LIBS=(
  "XENSIV 3D Magnetic Sensor TLx493D"
)

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
echo "Compiling calibration sketch..."
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
echo "Calibration flash complete. The board should reboot automatically."
