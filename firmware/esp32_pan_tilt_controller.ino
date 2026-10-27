/* Phase 2 ESP32 pan/tilt safety controller.

   Libraries: ESP32Servo (Arduino Library Manager)
   Serial input: CMD,<sequence>,<pan_deg>,<tilt_deg>,<lock 0|1>,<alert 0|1>\n
   Hardware safety responsibilities:
   - clamps every requested angle;
   - immediately detaches servos if the active-low E-stop opens;
   - stops accepting motion after a 500 ms command timeout;
   - never turns on the lock LED after a timeout or E-stop.
*/
#include <ESP32Servo.h>

constexpr int PAN_SERVO_PIN = 18;
constexpr int TILT_SERVO_PIN = 19;
constexpr int LOCK_LED_PIN = 25;
constexpr int ALERT_LED_PIN = 26;
constexpr int ESTOP_PIN = 27;  // Wire normally closed to GND; INPUT_PULLUP.
constexpr float PAN_MIN = -90.0f, PAN_MAX = 90.0f;
constexpr float TILT_MIN = -30.0f, TILT_MAX = 60.0f;
constexpr unsigned long WATCHDOG_MS = 500;

Servo panServo, tiltServo;
float panDeg = 0.0f, tiltDeg = 0.0f;
unsigned long lastValidCommandMs = 0;
String inputLine;

float clampf(float value, float low, float high) { return max(low, min(high, value)); }
int panToServo(float degrees) { return round(clampf(degrees, PAN_MIN, PAN_MAX) + 90.0f); }
int tiltToServo(float degrees) { return round((clampf(degrees, TILT_MIN, TILT_MAX) - TILT_MIN) * 180.0f / (TILT_MAX - TILT_MIN)); }

bool estopActive() { return digitalRead(ESTOP_PIN) == HIGH; }

void attachServosIfSafe() {
  if (!estopActive() && !panServo.attached()) {
    panServo.attach(PAN_SERVO_PIN, 500, 2500);
    tiltServo.attach(TILT_SERVO_PIN, 500, 2500);
    panServo.write(panToServo(panDeg));
    tiltServo.write(tiltToServo(tiltDeg));
  }
}

void enterSafeStop(const char* reason) {
  digitalWrite(LOCK_LED_PIN, LOW);
  digitalWrite(ALERT_LED_PIN, LOW);
  if (panServo.attached()) panServo.detach();
  if (tiltServo.attached()) tiltServo.detach();
  Serial.print("SAFE_STOP,");
  Serial.println(reason);
}

void applyCommand(String line) {
  unsigned long stopSequence;
  if (sscanf(line.c_str(), "STOP,%lu", &stopSequence) == 1 && line.startsWith("STOP,")) {
    lastValidCommandMs = 0;
    enterSafeStop("PC_STOP");
    return;
  }
  int lockOnly, alertOnly;
  if (sscanf(line.c_str(), "LED,%d,%d", &lockOnly, &alertOnly) == 2 && line.startsWith("LED,") &&
      (lockOnly == 0 || lockOnly == 1) && (alertOnly == 0 || alertOnly == 1)) {
    // A detached mount may still display an alert. Lock is never allowed when stopped.
    digitalWrite(LOCK_LED_PIN, LOW);
    digitalWrite(ALERT_LED_PIN, alertOnly ? HIGH : LOW);
    return;
  }
  unsigned long sequence;
  float requestedPan, requestedTilt;
  int lockLed, alertLed;
  if (sscanf(line.c_str(), "CMD,%lu,%f,%f,%d,%d", &sequence, &requestedPan, &requestedTilt, &lockLed, &alertLed) != 5 ||
      (lockLed != 0 && lockLed != 1) || (alertLed != 0 && alertLed != 1)) {
    Serial.println("ERROR,BAD_COMMAND");
    return;
  }
  if (estopActive()) {
    enterSafeStop("ESTOP");
    return;
  }
  panDeg = clampf(requestedPan, PAN_MIN, PAN_MAX);
  tiltDeg = clampf(requestedTilt, TILT_MIN, TILT_MAX);
  attachServosIfSafe();
  panServo.write(panToServo(panDeg));
  tiltServo.write(tiltToServo(tiltDeg));
  digitalWrite(LOCK_LED_PIN, lockLed ? HIGH : LOW);
  digitalWrite(ALERT_LED_PIN, alertLed ? HIGH : LOW);
  lastValidCommandMs = millis();
  Serial.printf("STATUS,%lu,%.2f,%.2f,0,0\n", sequence, panDeg, tiltDeg);
}

void setup() {
  Serial.begin(115200);
  pinMode(LOCK_LED_PIN, OUTPUT);
  pinMode(ALERT_LED_PIN, OUTPUT);
  pinMode(ESTOP_PIN, INPUT_PULLUP);
  enterSafeStop("STARTUP");
}

void loop() {
  if (estopActive()) {
    enterSafeStop("ESTOP");
    delay(20);
    return;
  }
  if (lastValidCommandMs && millis() - lastValidCommandMs > WATCHDOG_MS) {
    enterSafeStop("WATCHDOG");
    lastValidCommandMs = 0;
  }
  while (Serial.available()) {
    const char value = (char)Serial.read();
    if (value == '\n') {
      applyCommand(inputLine);
      inputLine = "";
    } else if (value != '\r' && inputLine.length() < 100) {
      inputLine += value;
    }
  }
}
