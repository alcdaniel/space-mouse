#include <Arduino.h>
#include <Adafruit_TinyUSB.h>

static const uint8_t hid_report_descriptor[] = {
  0x05, 0x01, 0x09, 0x08, 0xA1, 0x01,
  0x85, 0x01, 0x16, 0x00, 0x80, 0x26, 0xFF, 0x7F,
  0x75, 0x10, 0x95, 0x03, 0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x81, 0x02,
  0x85, 0x02, 0x16, 0x00, 0x80, 0x26, 0xFF, 0x7F,
  0x75, 0x10, 0x95, 0x03, 0x09, 0x33, 0x09, 0x34, 0x09, 0x35, 0x81, 0x02,
  0x85, 0x03, 0x05, 0x09, 0x19, 0x01, 0x29, 0x08, 0x15, 0x00, 0x25, 0x01,
  0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
  0xC0
};

Adafruit_USBD_HID usb_hid;

struct __attribute__((packed)) TranslationReport {
  int16_t x;
  int16_t y;
  int16_t z;
};

struct __attribute__((packed)) RotationReport {
  int16_t rx;
  int16_t ry;
  int16_t rz;
};

uint32_t lastReportMs = 0;
uint32_t lastPrintMs = 0;
int16_t heartbeat = 0;
uint8_t reportPhase = 0;

void setup() {
  if (!TinyUSBDevice.isInitialized()) {
    TinyUSBDevice.begin(0);
  }

  Serial.begin(115200);

  uint32_t serialStartMs = millis();
  while (!Serial && millis() - serialStartMs < 2000) {
    delay(10);
  }

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

  Serial.println("USB mounted");
}

void loop() {
  TinyUSBDevice.task();

  uint32_t now = millis();

  if (now - lastPrintMs >= 500) {
    lastPrintMs = now;
    Serial.print("HID ready = ");
    Serial.println(usb_hid.ready() ? "YES" : "NO");
  }

  if (now - lastReportMs < 10) {
    return;
  }
  lastReportMs = now;

  if (!usb_hid.ready()) {
    return;
  }

  TranslationReport translation = { heartbeat, 0, 0 };
  RotationReport rotation = { 0, 0, 0 };
  uint8_t buttons = 0;

  if (reportPhase == 0) {
    heartbeat = (int16_t)(heartbeat + 300);
    translation.x = heartbeat;
    usb_hid.sendReport(1, &translation, sizeof(translation));
  } else if (reportPhase == 1) {
    usb_hid.sendReport(2, &rotation, sizeof(rotation));
  } else {
    usb_hid.sendReport(3, &buttons, sizeof(buttons));
  }

  reportPhase = (uint8_t)((reportPhase + 1) % 3);
}
