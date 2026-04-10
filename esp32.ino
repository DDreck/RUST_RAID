#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>

// ===== Wi-Fi =====
const char* WIFI_SSID = "Dreck2";
const char* WIFI_PASS = "dreck123";

// ===== Pi callback =====
#define PI_HOST "10.0.0.229"
#define PI_PORT 8080

// ===== Pins =====
constexpr int PIN_LED_EXT = 17;  // external LED on TX2
constexpr int PIN_LED_ONB = 2;   // onboard LED (kept OFF)
constexpr int PIN_BUZZER  = 16;  // RX2 (active 3.3V buzzer, sink drive)
constexpr int PIN_BUTTON  = 4;   // D4 (to GND, uses pull-up)

// ===== Alarm timing =====
constexpr unsigned long ALARM_DURATION_MS = 2UL * 1000UL; // 2 seconds
constexpr unsigned long LED_BLINK_MS      = 250;
constexpr unsigned long BEEP_ON_MS        = 200;
constexpr unsigned long BEEP_OFF_MS       = 300;

WebServer server(80);

// ===== State flags =====
volatile bool alarmActive   = false;
volatile bool buttonPressed = false;
bool lockoutMode            = false;

unsigned long alarmStart    = 0;
unsigned long lastLedToggle = 0;
unsigned long lastBeepEdge  = 0;

bool ledState  = false;
bool beepState = false;

// ===== Button interrupt =====
void IRAM_ATTR onButton() {
  buttonPressed = true;
}

// ===== Buzzer control =====
void buzzerOn()  { digitalWrite(PIN_BUZZER, LOW);  } // LOW = ON
void buzzerOff() { digitalWrite(PIN_BUZZER, HIGH); } // HIGH = OFF

// ===== LED control (external only) =====
void setLedState(bool on) {
  digitalWrite(PIN_LED_EXT, on ? HIGH : LOW);
}

// ===== Stop alarm + enter lockout =====
void stopAndLockout() {
  alarmActive = false;
  lockoutMode = true;
  buzzerOff();
  setLedState(false);
  digitalWrite(PIN_LED_ONB, LOW); // ensure onboard LED off
}

// ===== Start alarm (auto-clears lockout for fresh cycle) =====
void startAlarm() {
  if (lockoutMode) {
    Serial.println("[LOCKED] Auto-clearing lockout for fresh alarm.");
    lockoutMode = false;
  }

  alarmStart    = millis();
  lastLedToggle = millis();
  lastBeepEdge  = millis();
  ledState      = false;
  beepState     = false;
  alarmActive   = true;
  buttonPressed = false;
  buzzerOff();
  setLedState(false);

  Serial.println("[ALARM] Started.");
}

// ===== HTTP Handlers =====
void handle_start() {
  startAlarm();
  server.send(200, "text/plain", "ALARM STARTED");
}

void handle_stop() {
  stopAndLockout();
  server.send(200, "text/plain", "ALARM STOPPED & LOCKED");
}

void handle_status() {
  String s = "{";
  s += "\"alarm\":"  + String(alarmActive  ? "true" : "false");
  s += ",\"locked\":" + String(lockoutMode ? "true" : "false");
  s += "}";
  server.send(200, "application/json", s);
}

// ===== Notify Pi that button was pressed =====
void callPiTrigger() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP] WiFi not connected, skipping Pi trigger.");
    return;
  }
  HTTPClient http;
  String url = "http://" PI_HOST ":" + String(PI_PORT) + "/trigger";
  http.begin(url);
  int code = http.GET();
  Serial.printf("[HTTP] Pi trigger -> %d\n", code);
  http.end();
}

// ===== Setup =====
void setup() {
  Serial.begin(115200);
  Serial.println("\nBooting ESP32 alarm...");

  pinMode(PIN_LED_EXT, OUTPUT);
  pinMode(PIN_LED_ONB, OUTPUT);
  setLedState(false);
  digitalWrite(PIN_LED_ONB, LOW); // onboard LED off by default

  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, HIGH); // off

  pinMode(PIN_BUTTON, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_BUTTON), onButton, FALLING);

  // ===== Wi-Fi =====
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\n✅ Connected to WiFi!");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());

  // ===== mDNS =====
  if (MDNS.begin("esp32-alarm")) {
    Serial.println("mDNS responder started: http://esp32-alarm.local");
  }

  // ===== HTTP server =====
  server.on("/alarm/start", HTTP_GET, handle_start);
  server.on("/alarm/stop",  HTTP_GET, handle_stop);
  server.on("/status",      HTTP_GET, handle_status);
  server.begin();

  Serial.println("HTTP server started.");
}

// ===== Main loop =====
void loop() {
  server.handleClient();

  // Button press → stop alarm if active, then notify Pi
  if (buttonPressed) {
    buttonPressed = false;
    if (alarmActive) {
      Serial.println("[BUTTON] Alarm stopped by button press.");
      stopAndLockout();
    }
    Serial.println("[BUTTON] Notifying Pi...");
    callPiTrigger();
  }

  // Active alarm logic
  if (alarmActive) {
    // End after 10 minutes
    if (millis() - alarmStart >= ALARM_DURATION_MS) {
      Serial.println("[ALARM] Time expired. Locking out.");
      stopAndLockout();
    } else {
      // Blink external LED only
      if (millis() - lastLedToggle >= LED_BLINK_MS) {
        lastLedToggle = millis();
        ledState = !ledState;
        setLedState(ledState);
      }
      // Beep pattern
      unsigned long elapsed = millis() - lastBeepEdge;
      if (!beepState && elapsed >= BEEP_OFF_MS) {
        beepState = true;
        lastBeepEdge = millis();
        buzzerOn();
      } else if (beepState && elapsed >= BEEP_ON_MS) {
        beepState = false;
        lastBeepEdge = millis();
        buzzerOff();
      }
    }
  }
}
