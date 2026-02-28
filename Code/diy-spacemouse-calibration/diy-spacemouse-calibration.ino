#include <TLx493D_inc.hpp>

using namespace ifx::tlx493d;

TLx493D_A1B6 mag(Wire1, TLx493D_IIC_ADDR_A0_e);

void setup() {
  Serial.begin(115200);
  delay(500);

  // Init magnetometer
  mag.begin();

  Serial.println("# TLV493D streaming: x,y,z");
}

void loop() {
  double x = 0.0;
  double y = 0.0;
  double z = 0.0;

  delay(10);
  mag.getMagneticField(&x, &y, &z);

  // Imprime en CSV: x,y,z
  Serial.print(x, 6);
  Serial.print(",");
  Serial.print(y, 6);
  Serial.print(",");
  Serial.print(z, 6);
  Serial.println();
}
