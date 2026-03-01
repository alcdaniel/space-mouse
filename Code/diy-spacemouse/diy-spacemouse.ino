#include <TinyUSB_Mouse_and_Keyboard.h>
#include <OneButton.h>
#include <TLx493D_inc.hpp>
#include <SimpleKalmanFilter.h>

using namespace ifx::tlx493d;

TLx493D_A1B6 mag(Wire1, TLx493D_IIC_ADDR_A0_e);
SimpleKalmanFilter xFilter(1, 1, 0.2), yFilter(1, 1, 0.2), zFilter(1, 1, 0.2);

OneButton button1(27, true);
OneButton button2(24, true);

float xOffset = 0.0f, yOffset = 0.0f, zOffset = 0.0f;
float xCurrent = 0.0f, yCurrent = 0.0f, zCurrent = 0.0f;

const int calSamples = 300;
const int panMaxSpeed = 28;
const int orbitMaxSpeed = 48;
const float movementScale = 0.10f;
const unsigned long centerReleaseMs = 500;
const float minAxisMotion = 0.18f;

// center_free shows about +/-0.26 of noise around the mean on X/Y.
// Keep the release band above that and the activation band a bit higher.
const float xyDeadband = 0.40f;
const float xyReleaseBand = 0.30f;

// Lower the Z activation point so it triggers around half the previous travel.
const float zEnterThreshold = 0.60f;
const float zExitThreshold = 0.30f;

// Ranges relative to the centered position, extracted from the capture file.
const float panLeftRange = 1.93f;
const float panRightRange = 2.00f;
const float panUpRange = 2.10f;
const float panDownRange = 1.53f;

const float orbitLeftRange = 1.83f;
const float orbitRightRange = 2.10f;
const float orbitUpRange = 3.18f;
const float orbitDownRange = 1.43f;

bool shiftHeld = false;
bool middleHeld = false;
bool zModeActive = false;
float zModeXOffset = 0.0f;
float zModeYOffset = 0.0f;
unsigned long lastMotionAtMs = 0;
unsigned long nearCenterSinceMs = 0;
float moveCarryX = 0.0f;
float moveCarryY = 0.0f;

void goHome();
void fitToScreen();

float clampUnit(float value)
{
  if (value > 1.0f)
  {
    return 1.0f;
  }

  if (value < -1.0f)
  {
    return -1.0f;
  }

  return value;
}

float normalizeAxis(float value, float negativeRange, float positiveRange, float deadband)
{
  if (value > deadband)
  {
    float denominator = positiveRange - deadband;
    if (denominator <= 0.0f)
    {
      return 0.0f;
    }
    return clampUnit((value - deadband) / denominator);
  }

  if (value < -deadband)
  {
    float denominator = negativeRange - deadband;
    if (denominator <= 0.0f)
    {
      return 0.0f;
    }
    return clampUnit((value + deadband) / denominator);
  }

  return 0.0f;
}

int scaledMouseDelta(float normalizedValue, int maxSpeed, float &carry)
{
  float scaledValue = normalizedValue * maxSpeed * movementScale + carry;
  int wholeStep = (int)scaledValue;
  carry = scaledValue - (float)wholeStep;
  return wholeStep;
}

bool isNearCenter(float xValue, float yValue, float band)
{
  return (xValue < band && xValue > -band && yValue < band && yValue > -band);
}

float gateAxisMotion(float normalizedValue)
{
  if (normalizedValue > -minAxisMotion && normalizedValue < minAxisMotion)
  {
    return 0.0f;
  }

  return normalizedValue;
}

void setShiftHeld(bool active)
{
  if (shiftHeld == active)
  {
    return;
  }

  if (active)
  {
    Keyboard.press(KEY_LEFT_SHIFT);
  }
  else
  {
    Keyboard.release(KEY_LEFT_SHIFT);
  }

  shiftHeld = active;
}

void setMiddleHeld(bool active)
{
  if (middleHeld == active)
  {
    return;
  }

  if (active)
  {
    Mouse.press(MOUSE_MIDDLE);
  }
  else
  {
    Mouse.release(MOUSE_MIDDLE);
  }

  middleHeld = active;
}

void releaseNavigationState()
{
  if (middleHeld)
  {
    Mouse.release(MOUSE_MIDDLE);
    middleHeld = false;
  }

  if (shiftHeld)
  {
    Keyboard.releaseAll();
    shiftHeld = false;
  }

  moveCarryX = 0.0f;
  moveCarryY = 0.0f;
}

void calibrateCenter()
{
  for (int i = 0; i < calSamples; i++)
  {
    double xRaw = 0.0;
    double yRaw = 0.0;
    double zRaw = 0.0;

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

void setup()
{
  button1.attachClick(goHome);
  button1.attachLongPressStop(goHome);

  button2.attachClick(fitToScreen);
  button2.attachLongPressStop(fitToScreen);

  Mouse.begin();
  Keyboard.begin();

  mag.begin();
  calibrateCenter();
}

void loop()
{
  button1.tick();
  button2.tick();

  double xRaw = 0.0;
  double yRaw = 0.0;
  double zRaw = 0.0;

  delay(10);
  mag.getMagneticField(&xRaw, &yRaw, &zRaw);

  xCurrent = xFilter.updateEstimate((float)xRaw - xOffset);
  yCurrent = yFilter.updateEstimate((float)yRaw - yOffset);
  zCurrent = zFilter.updateEstimate((float)zRaw - zOffset);

  bool wasZModeActive = zModeActive;

  if (!zModeActive && zCurrent > zEnterThreshold)
  {
    zModeActive = true;
    zModeXOffset = xCurrent;
    zModeYOffset = yCurrent;
  }
  else if (zModeActive && zCurrent < zExitThreshold)
  {
    zModeActive = false;
  }

  bool modeChanged = (wasZModeActive != zModeActive);
  if (modeChanged)
  {
    releaseNavigationState();
    lastMotionAtMs = 0;
    nearCenterSinceMs = 0;
  }

  unsigned long now = millis();

  if (!zModeActive)
  {
    // Free mode: PAN = Shift + MMB + proportional XY
    float panInputX = modeChanged ? 0.0f : xCurrent;
    float panInputY = modeChanged ? 0.0f : yCurrent;
    float panX = normalizeAxis(panInputX, panLeftRange, panRightRange, xyDeadband);
    float panY = normalizeAxis(panInputY, panDownRange, panUpRange, xyDeadband);
    panX = gateAxisMotion(panX);
    panY = gateAxisMotion(panY);
    if (panX > 0.0f)
    {
      panX = clampUnit(panX * 1.5f);
    }
    if (panY > 0.0f)
    {
      panY = clampUnit(panY * 2.0f);
    }
    bool panNearCenter = isNearCenter(panInputX, panInputY, xyReleaseBand);

    if (panNearCenter)
    {
      if (nearCenterSinceMs == 0)
      {
        nearCenterSinceMs = now;
      }

      if (now - nearCenterSinceMs >= centerReleaseMs)
      {
        releaseNavigationState();
        lastMotionAtMs = 0;
      }
    }
    else if (panX != 0.0f || panY != 0.0f)
    {
      int mouseX = scaledMouseDelta(panX, panMaxSpeed, moveCarryX);
      int mouseY = -scaledMouseDelta(panY, panMaxSpeed, moveCarryY);

      nearCenterSinceMs = 0;
      setShiftHeld(true);
      setMiddleHeld(true);
      Mouse.move(mouseX, mouseY, 0);
      lastMotionAtMs = now;
    }
    else
    {
      nearCenterSinceMs = 0;
    }

  }
  else
  {
    // Z pressed: only rotate if there is XY motion. Pressing Z alone does nothing.
    float orbitInputX = modeChanged ? 0.0f : (xCurrent - zModeXOffset);
    float orbitInputY = modeChanged ? 0.0f : (yCurrent - zModeYOffset);
    float orbitX = normalizeAxis(orbitInputX, orbitLeftRange, orbitRightRange, xyDeadband);
    float orbitY = normalizeAxis(orbitInputY, orbitDownRange, orbitUpRange, xyDeadband);
    orbitX = gateAxisMotion(orbitX);
    orbitY = gateAxisMotion(orbitY);
    if (orbitX > 0.0f)
    {
      orbitX = clampUnit(orbitX * 2.0f);
    }
    if (orbitY > 0.0f)
    {
      orbitY = clampUnit(orbitY * 2.5f);
    }
    bool orbitNearCenter = isNearCenter(orbitInputX, orbitInputY, xyReleaseBand);

    if (orbitNearCenter)
    {
      if (nearCenterSinceMs == 0)
      {
        nearCenterSinceMs = now;
      }

      if (now - nearCenterSinceMs >= centerReleaseMs)
      {
        releaseNavigationState();
        lastMotionAtMs = 0;
      }
    }
    else if (orbitX != 0.0f || orbitY != 0.0f)
    {
      int mouseX = scaledMouseDelta(orbitX, orbitMaxSpeed, moveCarryX);
      int mouseY = -scaledMouseDelta(orbitY, orbitMaxSpeed, moveCarryY);

      nearCenterSinceMs = 0;
      setShiftHeld(false);
      setMiddleHeld(true);
      Mouse.move(mouseX, mouseY, 0);
      lastMotionAtMs = now;
    }
    else
    {
      nearCenterSinceMs = 0;
    }

  }
}

void goHome()
{
  Keyboard.press(KEY_LEFT_GUI);
  Keyboard.press(KEY_LEFT_SHIFT);
  Keyboard.write('h');

  delay(10);
  Keyboard.releaseAll();
  releaseNavigationState();
  zModeActive = false;
  lastMotionAtMs = 0;
}

void fitToScreen()
{
  Mouse.press(MOUSE_MIDDLE);
  Mouse.release(MOUSE_MIDDLE);
  Mouse.press(MOUSE_MIDDLE);
  Mouse.release(MOUSE_MIDDLE);

  releaseNavigationState();
  zModeActive = false;
  lastMotionAtMs = 0;
}
