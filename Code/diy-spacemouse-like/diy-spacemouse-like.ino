#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <TLx493D_inc.hpp>
#include <SimpleKalmanFilter.h>

using namespace ifx::tlx493d;

// -------------------- HID descriptor (Multi-axis Controller) --------------------
static const uint8_t hid_report_descriptor[] = {
  0x05, 0x01,        // Usage Page (Generic Desktop)
  0x09, 0x08,        // Usage (Multi-axis Controller)
  0xA1, 0x01,        // Collection (Application)

  // Report ID 1: Translation (X, Y, Z) 3x int16
  0x85, 0x01,        //   Report ID (1)
  0x16, 0x00, 0x80,  //   Logical Min  (-32768)
  0x26, 0xFF, 0x7F,  //   Logical Max  (32767)
  0x75, 0x10,        //   Report Size (16)
  0x95, 0x03,        //   Report Count (3)
  0x09, 0x30,        //   Usage (X)
  0x09, 0x31,        //   Usage (Y)
  0x09, 0x32,        //   Usage (Z)
  0x81, 0x02,        //   Input (Data,Var,Abs)

  // Report ID 2: Rotation (Rx, Ry, Rz) 3x int16
  0x85, 0x02,        //   Report ID (2)
  0x16, 0x00, 0x80,  //   Logical Min  (-32768)
  0x26, 0xFF, 0x7F,  //   Logical Max  (32767)
  0x75, 0x10,        //   Report Size (16)
  0x95, 0x03,        //   Report Count (3)
  0x09, 0x33,        //   Usage (Rx)
  0x09, 0x34,        //   Usage (Ry)
  0x09, 0x35,        //   Usage (Rz)
  0x81, 0x02,        //   Input (Data,Var,Abs)

  // Report ID 3: Buttons (8 bits)
  0x85, 0x03,        //   Report ID (3)
  0x05, 0x09,        //   Usage Page (Button)
  0x19, 0x01,        //   Usage Min (1)
  0x29, 0x08,        //   Usage Max (8)
  0x15, 0x00,        //   Logical Min (0)
  0x25, 0x01,        //   Logical Max (1)
  0x75, 0x01,        //   Report Size (1)
  0x95, 0x08,        //   Report Count (8)
  0x81, 0x02,        //   Input (Data,Var,Abs)

  0xC0               // End Collection
};

Adafruit_USBD_HID usb_hid;

// -------------------- Your original logic --------------------
TLx493D_A1B6 mag(Wire1, TLx493D_IIC_ADDR_A0_e);
SimpleKalmanFilter xFilter(1, 1, 0.2), yFilter(1, 1, 0.2), zFilter(1, 1, 0.2);

const uint8_t button1Pin = 27;
const uint8_t button2Pin = 24;

float xOffset = 0.0f, yOffset = 0.0f, zOffset = 0.0f;
float xCurrent = 0.0f, yCurrent = 0.0f, zCurrent = 0.0f;

const int calSamples = 300;
const int panMaxSpeed = 28;
const int orbitMaxSpeed = 48;
const float movementScale = 0.10f;
const unsigned long centerReleaseMs = 500;
const float minAxisMotion = 0.18f;

const float xyDeadband = 0.40f;
const float xyReleaseBand = 0.30f;

const float zEnterThreshold = 0.60f;
const float zExitThreshold = 0.30f;

const float panLeftRange = 1.93f;
const float panRightRange = 2.00f;
const float panUpRange = 2.10f;
const float panDownRange = 1.53f;

const float orbitLeftRange = 1.83f;
const float orbitRightRange = 2.10f;
const float orbitUpRange = 3.18f;
const float orbitDownRange = 1.43f;

bool zModeActive = false;
float zModeXOffset = 0.0f;
float zModeYOffset = 0.0f;
unsigned long lastMotionAtMs = 0;
unsigned long nearCenterSinceMs = 0;
float moveCarryX = 0.0f;
float moveCarryY = 0.0f;

// HID scaling: convert "mouse steps" to int16.
// Start with 900 and tune: higher = more aggressive.
const int16_t mouseStepToHid = 900;

// Send rate
const uint32_t reportPeriodMs = 10; // 100 Hz
uint32_t lastReportMs = 0;
uint8_t reportPhase = 0;

// Buttons bitfield (ID 3)
volatile uint8_t buttonBits = 0;

// -------------------- Helpers (same as yours) --------------------
float clampUnit(float value) {
  if (value > 1.0f) return 1.0f;
  if (value < -1.0f) return -1.0f;
  return value;
}

float normalizeAxis(float value, float negativeRange, float positiveRange, float deadband) {
  if (value > deadband) {
    float denominator = positiveRange - deadband;
    if (denominator <= 0.0f) return 0.0f;
    return clampUnit((value - deadband) / denominator);
  }

  if (value < -deadband) {
    float denominator = negativeRange - deadband;
    if (denominator <= 0.0f) return 0.0f;
    return clampUnit((value + deadband) / denominator);
  }
  return 0.0f;
}

int scaledMouseDelta(float normalizedValue, int maxSpeed, float &carry) {
  float scaledValue = normalizedValue * maxSpeed * movementScale + carry;
  int wholeStep = (int)scaledValue;
  carry = scaledValue - (float)wholeStep;
  return wholeStep;
}

bool isNearCenter(float xValue, float yValue, float band) {
  return (xValue < band && xValue > -band && yValue < band && yValue > -band);
}

float gateAxisMotion(float normalizedValue) {
  if (normalizedValue > -minAxisMotion && normalizedValue < minAxisMotion) {
    return 0.0f;
  }
  return normalizedValue;
}

static inline int16_t clampI16(int32_t v) {
  if (v > 32767) return 32767;
  if (v < -32768) return -32768;
  return (int16_t)v;
}

static inline int16_t mouseStepsToI16(int mouseSteps) {
  // map discrete mouse step to continuous i16
  return clampI16((int32_t)mouseSteps * (int32_t)mouseStepToHid);
}

void releaseNavigationState() {
  moveCarryX = 0.0f;
  moveCarryY = 0.0f;
}

void calibrateCenter() {
  for (int i = 0; i < calSamples; i++) {
    double xRaw = 0.0, yRaw = 0.0, zRaw = 0.0;
    delay(10);
    mag.getMagneticField(&xRaw, &yRaw, &zRaw);
    xOffset += (float)xRaw;
    yOffset += (float)yRaw;
    zOffset += (float)zRaw;
  }
  xOffset /= calSamples;
  yOffset /= calSamples;
  zOffset /= calSamples;
}

void updateButtonBits() {
  uint8_t bits = 0;

  if (digitalRead(button1Pin) == LOW) {
    bits |= 0x01;
  }

  if (digitalRead(button2Pin) == LOW) {
    bits |= 0x02;
  }

  buttonBits = bits;
}

// -------------------- Setup / Loop --------------------
void setup() {
  if (!TinyUSBDevice.isInitialized()) {
    TinyUSBDevice.begin(0);
  }

  pinMode(button1Pin, INPUT_PULLUP);
  pinMode(button2Pin, INPUT_PULLUP);

  usb_hid.setReportDescriptor(hid_report_descriptor, sizeof(hid_report_descriptor));
  usb_hid.setPollInterval(1);
  usb_hid.begin();

  if (TinyUSBDevice.mounted()) {
    TinyUSBDevice.detach();
    delay(10);
    TinyUSBDevice.attach();
  }

  while (!TinyUSBDevice.mounted()) {
    TinyUSBDevice.task();
    delay(1);
  }

  mag.begin();
  calibrateCenter();
}

void loop() {
  TinyUSBDevice.task();
  updateButtonBits();

  // Read sensor (same cadence as your original)
  double xRaw = 0.0, yRaw = 0.0, zRaw = 0.0;
  delay(10);
  mag.getMagneticField(&xRaw, &yRaw, &zRaw);

  xCurrent = xFilter.updateEstimate((float)xRaw - xOffset);
  yCurrent = yFilter.updateEstimate((float)yRaw - yOffset);
  zCurrent = zFilter.updateEstimate((float)zRaw - zOffset);

  bool wasZModeActive = zModeActive;

  if (!zModeActive && zCurrent > zEnterThreshold) {
    zModeActive = true;
    zModeXOffset = xCurrent;
    zModeYOffset = yCurrent;
  } else if (zModeActive && zCurrent < zExitThreshold) {
    zModeActive = false;
  }

  bool modeChanged = (wasZModeActive != zModeActive);
  if (modeChanged) {
    releaseNavigationState();
    lastMotionAtMs = 0;
    nearCenterSinceMs = 0;
  }

  unsigned long now = millis();

  // Rate limit HID
  if (now - lastReportMs < reportPeriodMs) return;
  lastReportMs = now;

  if (!usb_hid.ready()) return;

  // Default: zero vectors
  int16_t Tx = 0, Ty = 0, Tz = 0;
  int16_t Rx = 0, Ry = 0, Rz = 0;

  if (!zModeActive) {
    // --- Free mode: PAN pipeline identical to yours ---
    float panInputX = modeChanged ? 0.0f : xCurrent;
    float panInputY = modeChanged ? 0.0f : yCurrent;

    float panX = normalizeAxis(panInputX, panLeftRange, panRightRange, xyDeadband);
    float panY = normalizeAxis(panInputY, panDownRange, panUpRange, xyDeadband);

    panX = gateAxisMotion(panX);
    panY = gateAxisMotion(panY);

    if (panX > 0.0f) panX = clampUnit(panX * 1.25f);
    if (panY > 0.0f) panY = clampUnit(panY * 2.0f);
    panY *= 0.5f;

    bool panNearCenter = isNearCenter(panInputX, panInputY, xyReleaseBand);

    if (panNearCenter) {
      if (nearCenterSinceMs == 0) nearCenterSinceMs = now;

      if (now - nearCenterSinceMs >= centerReleaseMs) {
        releaseNavigationState();
        lastMotionAtMs = 0;
        // keep zero output
      }
    } else if (panX != 0.0f || panY != 0.0f) {
      int mouseX = scaledMouseDelta(panX, panMaxSpeed, moveCarryX);
      int mouseY = -scaledMouseDelta(panY, panMaxSpeed, moveCarryY);

      nearCenterSinceMs = 0;
      lastMotionAtMs = now;

      // HID translation vector derived from same mouse steps
      Tx = mouseStepsToI16(mouseX);
      Ty = mouseStepsToI16(mouseY);
      Tz = 0;
    } else {
      nearCenterSinceMs = 0;
    }

  } else {
    // --- Z pressed: ORBIT pipeline identical to yours ---
    float orbitInputX = modeChanged ? 0.0f : (xCurrent - zModeXOffset);
    float orbitInputY = modeChanged ? 0.0f : (yCurrent - zModeYOffset);

    float orbitX = normalizeAxis(orbitInputX, orbitLeftRange, orbitRightRange, xyDeadband);
    float orbitY = normalizeAxis(orbitInputY, orbitDownRange, orbitUpRange, xyDeadband);

    orbitX = gateAxisMotion(orbitX);
    orbitY = gateAxisMotion(orbitY);

    if (orbitX > 0.0f) orbitX = clampUnit(orbitX * 2.0f);
    if (orbitY > 0.0f) orbitY = clampUnit(orbitY * 2.5f);

    bool orbitNearCenter = isNearCenter(orbitInputX, orbitInputY, xyReleaseBand);

    if (orbitNearCenter) {
      if (nearCenterSinceMs == 0) nearCenterSinceMs = now;

      if (now - nearCenterSinceMs >= centerReleaseMs) {
        releaseNavigationState();
        lastMotionAtMs = 0;
        // keep zero output
      }
    } else if (orbitX != 0.0f || orbitY != 0.0f) {
      int mouseX = scaledMouseDelta(orbitX, orbitMaxSpeed, moveCarryX);
      int mouseY = -scaledMouseDelta(orbitY, orbitMaxSpeed, moveCarryY);

      nearCenterSinceMs = 0;
      lastMotionAtMs = now;

      // HID rotation vector derived from same mouse steps
      Rx = mouseStepsToI16(mouseX);
      Ry = mouseStepsToI16(mouseY);
      Rz = 0;
    } else {
      nearCenterSinceMs = 0;
    }
  }

  struct __attribute__((packed)) { int16_t x,y,z; } repT = { Tx, Ty, Tz };
  struct __attribute__((packed)) { int16_t rx,ry,rz; } repR = { Rx, Ry, Rz };
  uint8_t buttons = buttonBits;

  if (reportPhase == 0) {
    usb_hid.sendReport(1, &repT, sizeof(repT));
  } else if (reportPhase == 1) {
    usb_hid.sendReport(2, &repR, sizeof(repR));
  } else {
    usb_hid.sendReport(3, &buttons, sizeof(buttons));
  }

  reportPhase = (uint8_t)((reportPhase + 1) % 3);
}
