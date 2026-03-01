# My DIY Spacemouse for Fusion 360

This is my personal repository for building, calibrating, and maintaining my own version of the DIY SpaceMouse for Fusion 360.

It is based on the original DIY SpaceMouse project shared by the creator of the linked build video and Instructables guide. This repository keeps that attribution visible and preserves the original `CC BY-NC-SA 4.0` licensing terms for the upstream work and for my changes to it.

Watch the build video ↓

[<img src="/Images/Spacemouse_Thumbnail@2x.png">](https://youtu.be/iHBgNGnTiK4)

This device is made for Fusion360 but can be adapted to other CAD applications. Current features: Orbit, Pan, Home view and Fit to view.

Build instructions → [Instructables](https://www.instructables.com/DIY-Space-Mouse-for-Fusion-360-Using-Magnets)

## Attribution

- Original project concept, build guide, and media: the creator of the linked YouTube video and Instructables tutorial above
- This repository: my personal adaptation, firmware workflow, calibration tooling, and project maintenance
- When reusing or remixing this repository, keep attribution to the original creator and this repository

## Current Firmware Workflow

This repo now includes:

- a main firmware sketch for the DIY SpaceMouse
- a separate calibration sketch for reading the magnetometer
- shell scripts to compile and flash both sketches
- a guided Python capture tool to record the real magnet ranges

The current flash scripts target an `Adafruit QT Py RP2040` board and use the RP2040 UF2 bootloader (`RPI-RP2` volume).

## Requirements

- macOS (the flash scripts copy the UF2 to `/Volumes/RPI-RP2`)
- `arduino-cli`
- `python3`
- `git`

Install `pyserial` for the guided calibration capture:

```bash
python3 -m pip install pyserial
```

The flash scripts install these indexed Arduino dependencies automatically when missing:

- `Adafruit TinyUSB Library`
- `OneButton`
- `SimpleKalmanFilter`
- `XENSIV 3D Magnetic Sensor TLx493D`

The main sketch also depends on `TinyUSB_Mouse_and_Keyboard`, which is not always available from the Arduino Library Manager index. If compilation fails with:

```text
TinyUSB_Mouse_and_Keyboard.h: No such file or directory
```

install it manually:

```bash
git clone https://github.com/cyborg5/TinyUSB_Mouse_and_Keyboard.git "$HOME/Documents/Arduino/libraries/TinyUSB_Mouse_and_Keyboard"
```

## Flash The Main Firmware

The main firmware sketch is:

```text
Code/diy-spacemouse/diy-spacemouse.ino
```

To compile and flash it:

```bash
bash scripts/flash_diy_spacemouse.sh
```

What the script does:

1. Checks that `arduino-cli` exists.
2. Installs the `rp2040:rp2040` core if needed.
3. Installs the indexed Arduino libraries listed above if needed.
4. Compiles the sketch for:

```text
rp2040:rp2040:adafruit_qtpy:usbstack=tinyusb
```

5. Waits for the board to appear in BOOTSEL mode as `RPI-RP2`.
6. Copies the generated `.uf2` file to the board.

If the board is not already in BOOTSEL mode, the script will prompt you to do this:

1. Hold `BOOT`
2. Press `RESET`
3. Release `BOOT`
4. Wait for the `RPI-RP2` drive to appear

After the copy finishes, the board should reboot automatically.

## Fusion Bridge For `diy-spacemouse-like`

If you flash `Code/diy-spacemouse-like`, the board exposes a generic HID multi-axis device. Fusion on macOS may not react to that device directly, so this repository includes a local bridge that translates its HID reports into mouse and keyboard events that Fusion already understands.

The bridge script is:

```text
scripts/fusion_hid_bridge.py
```

Typical workflow:

1. Keep `~/Documents/Arduino/libraries/Adafruit_TinyUSB_Library` renamed or disabled so `diy-spacemouse-like` uses the RP2040 core's bundled TinyUSB stack.
2. Flash the HID firmware:

```bash
bash scripts/flash_diy_spacemouse.sh --sketch diy-spacemouse-like
```

3. Run the bridge in the same Python environment where `import hid` works:

```bash
python3 scripts/fusion_hid_bridge.py
```

Notes:

- On macOS, Terminal (or the Python app you use) must have Accessibility permission, otherwise Fusion will not react to the synthetic input events.
- `RID 1` is mapped to pan (`Shift` + middle-mouse drag).
- `RID 2` is mapped to orbit (middle-mouse drag).
- `RID 3` button bit 1 triggers `Cmd` + `Shift` + `H` and button bit 2 triggers a double middle click.
- If the direction feels wrong, rerun the bridge with `--invert-x` and/or `--invert-y`.

## Flash The Calibration Firmware

The calibration sketch is:

```text
Code/diy-spacemouse-calibration/diy-spacemouse-calibration.ino
```

To compile and flash it:

```bash
bash scripts/flash_diy_spacemouse_calibration.sh
```

This script uses the same BOOTSEL flow as the main flasher, but it uploads the calibration firmware instead of the HID firmware.

## Calibrate The Magnet

The purpose of calibration is to measure:

- the true center position when the cap is untouched
- the center shift when Z is pressed
- the usable min/max range for each motion direction

### Step 1: Flash The Calibration Sketch

Flash the calibration firmware first:

```bash
bash scripts/flash_diy_spacemouse_calibration.sh
```

That sketch streams raw magnetometer values as CSV over USB serial at `115200`.

### Step 2: Record A Guided Capture

Run the guided capture tool:

```bash
python3 scripts/capture_diy_spacemouse_guided.py
```

By default it auto-detects the RP2040 serial port, records each step for 6 seconds, and saves a file to:

```text
captures/spacemouse_capture_<timestamp>.txt
```

You can also pass explicit settings:

```bash
python3 scripts/capture_diy_spacemouse_guided.py --port /dev/cu.usbmodem2101 --duration 6 --countdown 3
```

### Step 3: Perform The Movements Exactly As Requested

The capture script will guide you through these movements in order:

1. `center_free`
2. `move_left`
3. `move_right`
4. `move_up`
5. `move_down`
6. `center_pressed_z`
7. `rotate_left`
8. `rotate_right`
9. `rotate_up`
10. `rotate_down`

What each one means:

- `center_free`: leave the cap centered, do not touch Z, and do not move the magnet
- `move_left/right/up/down`: move the cap in those directions without pressing Z
- `center_pressed_z`: return the cap to the center, then press Z and hold it still
- `rotate_left/right/up/down`: keep Z pressed and move in the requested direction

Important for good calibration:

- Keep the cap physically still during `center_free` and `center_pressed_z`
- Use your full comfortable travel for each direction
- Do not rush the movement
- Do not release Z during the `rotate_*` captures

### Step 4: Use The Capture Values

The generated `.txt` file contains:

- `min`
- `max`
- `mean`
- `span`

for `x`, `y`, and `z` in each captured movement.

Those values are used to tune:

- the center deadband
- the Z press threshold
- the left/right and up/down scaling
- the rotation scaling with Z pressed

The current firmware was tuned from these captures. If your hardware geometry changes, capture again and update the constants in:

```text
Code/diy-spacemouse/diy-spacemouse.ino
```

### Step 5: Flash The Main Firmware Again

After updating the main sketch constants, flash the HID firmware again:

```bash
bash scripts/flash_diy_spacemouse.sh
```

## Typical End-To-End Setup

For a fresh setup, the usual sequence is:

1. Flash the calibration sketch.
2. Run the guided capture script.
3. Review the generated file in `captures/`.
4. Update `Code/diy-spacemouse/diy-spacemouse.ino` if needed.
5. Flash the main firmware.
6. Test the SpaceMouse in Fusion 360.

## Troubleshooting

If the board is detected but the flash script waits forever:

- make sure the board is mounted as `RPI-RP2`
- if not, repeat the `BOOT -> RESET -> release BOOT` sequence

If compilation fails because a library is missing:

- run the same flash script again after installing the missing library
- for `TinyUSB_Mouse_and_Keyboard`, use the manual `git clone` command above

If the calibration script records no data:

- confirm the calibration firmware is flashed, not the main HID firmware
- confirm the board enumerates as a serial device
- confirm `pyserial` is installed

If the main firmware moves by itself at center:

- capture a fresh `center_free`
- increase the deadband in `Code/diy-spacemouse/diy-spacemouse.ino`
- then reflash the main firmware

## License

This repository is a personal adaptation of an existing project, so the correct license remains `Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)`.

That means:

- you must give appropriate credit to the original creator
- you must keep attribution to this adaptation when sharing modified versions
- you may not use the work commercially
- shared adaptations must stay under the same license

[![CC BY-NC-SA 4.0][cc-by-nc-sa-shield]][cc-by-nc-sa]

[![CC BY-NC-SA 4.0][cc-by-nc-sa-image]][cc-by-nc-sa]

[cc-by-nc-sa]: http://creativecommons.org/licenses/by-nc-sa/4.0/
[cc-by-nc-sa-image]: https://licensebuttons.net/l/by-nc-sa/4.0/88x31.png
[cc-by-nc-sa-shield]: https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey.svg
