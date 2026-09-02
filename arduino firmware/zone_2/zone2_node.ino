/* =====================================================================
 *  Intelligent Multi-Zone Smart Agriculture IoT System  --  FIWARE
 *  ---------------------------------------------------------------
 *  Zone 2 node firmware   |   ESP32-32U DevKit (38-pin)
 *
 *  Data path:  ESP32 --MQTT/UltraLight2.0--> IoT Agent --> Orion --> Mongo
 *
 *  PIN MAP
 *    GPIO4   DHT22            air temperature + humidity
 *    GPIO21  I2C SDA          DS3231 RTC
 *    GPIO22  I2C SCL          DS3231 RTC
 *    GPIO15  Valve relay      -> wire to IN2 on the shared 4-ch board
 *    GPIO25  YF-B2 flow       via 10k/3k3 divider, interrupt input
 *    GPIO32  Pump relay       IN4, SHARED open-drain line
 *    GPIO16  RS485 RXD        SN3002 soil sensor (Serial2)
 *    GPIO17  RS485 TXD        SN3002 soil sensor (Serial2)
 *
 *  WARNING    - GPIO15 is a strapping pin (MTDO). If the relay board holds it
 *              LOW at power-up the bootloader goes silent and uploads fail
 *              with "No serial data received". Unplug that wire to flash.
 *
 *  IRRIGATION - pulse-and-soak with hysteresis: dose, soak, re-measure.
 *              Starts below moistMin, ends at or above moistMax. Both are
 *              set remotely with the setmin / setmax commands and persist
 *              in NVS across reboots.
 *
 *  DRY-TANK   - no reservoir probe on this node. The interlock service pushes
 *              a `valve close` through Orion when zone 1 sees the tank run
 *              low, which stands this controller down for MANUAL_OVERRIDE_MS.
 *
 *  Libraries: PubSubClient (Nick O'Leary), RTClib (Adafruit),
 *             DHT sensor library (Adafruit) + Adafruit Unified Sensor
 * ===================================================================== */

// =====================================================================
//  1. CONFIGURATION  -- edit this block per zone
// =====================================================================
#define ZONE_NUMBER          2
#define DEVICE_ID            "zone002"          // matches IoT Agent provisioning
#define FIWARE_API_KEY       "4jggokgpepnvsb2uv4s40d59ov"

static const char* WIFI_SSID = "kokinetwork-2G";
static const char* WIFI_PASS = "0887588455";

static const char*    MQTT_HOST = "192.168.0.164";   // Ubuntu server / Mosquitto
static const uint16_t MQTT_PORT = 1883;
static const char*    MQTT_USER = "";                // leave "" if anonymous
static const char*    MQTT_PASS = "";

// ---- timing -----------------------------------------------------------
#define PUBLISH_INTERVAL_MS      120000UL // idle: every 120 s
#define PUBLISH_INTERVAL_WATER_MS 30000UL // while irrigating: every 30 s
#define SENSOR_POLL_MS             5000UL // local sensor refresh (control loop)
#define MAX_WATERING_MS      600000UL    // hard cap: 10 min, then force close
#define DRY_RUN_GRACE_MS      15000UL    // wait this long before flow check
#define WDT_TIMEOUT_S             30     // watchdog

// ---- relay polarity ---------------------------------------------------
// Active-LOW opto board + load on NC terminal => GPIO HIGH energises the load.
// If you re-wire to the NO terminal, just swap these two lines.
#define VALVE_ON              LOW
#define VALVE_OFF             HIGH

// Pump: open-drain wired-OR. LOW = assert, HIGH = release (high-Z).
#define PUMP_ASSERT           LOW
#define PUMP_RELEASE          HIGH

// ---- flow sensor calibration -----------------------------------------
// YF-B2 datasheet: F(Hz) = 6.6 * Q(L/min)  ->  396 pulses per litre.
#define FLOW_PULSES_PER_LITRE 396.0f
#define MIN_EXPECTED_FLOW     0.20f      // L/min, below this = dry run

// ---- Modbus / SN3002 --------------------------------------------------
#define SOIL_SLAVE_ADDR       0x01
#define SOIL_BAUD             4800       // confirmed from datasheet
#define SOIL_FUNC             0x03       // read holding registers
#define SOIL_REG_BASE         0x0000     // moisture, temp, EC, pH
#define SOIL_REG_COUNT        4
#define SOIL_TIMEOUT_MS       300
// NPK registers (0x04..0x06) are non-functional on this unit -> not read.

// Set true only if you also provision an "al" attribute in the IoT Agent.
#define SEND_ALERT_ATTR       false

// =====================================================================
//  1b. AUTOMATIC IRRIGATION
// ---------------------------------------------------------------------
//  Pulse-and-soak. Soil moisture responds to watering over MINUTES, so
//  watering until the probe reads "wet" massively overwaters. Instead:
//  deliver a fixed dose, wait for it to reach the probe, re-measure.
// =====================================================================
#define AUTO_IRRIGATION       true

// These are DEFAULTS ONLY. The live values live in NVS and are set remotely
// with the setmin / setmax FIWARE commands; they survive reboots. A fresh
// board (or one after nvs erase) falls back to these.
#define SOIL_MOISTURE_START   30.0f     // % - below this, begin a cycle
#define SOIL_MOISTURE_TARGET  45.0f     // % - at or above this, cycle ends
#define IRRIGATION_DOSE_L     2.0f      // litres per dose
#define IRRIGATION_DOSE_MAX_MS  180000UL // 3 min cap if flow sensor is off
#define SOAK_PERIOD_MS        1800000UL // 30 min for water to reach the probe
#define DAILY_VOLUME_CAP_L    10.0f     // hard stop per calendar day

// This node has NO reservoir probe -- only zone 1 does. Dry-tank protection
// arrives as a `valve close` from the interlock service via Orion, which
// stands the controller down for MANUAL_OVERRIDE_MS. The interlock re-sends
// every 5 min while the tank is low. Local dry-run flow detection remains
// the last-resort protection if that network path fails.

// Only water inside this LOCAL hour window (early morning = least evaporation).
// The RTC holds UTC; Sofia is +3 in summer, +2 in winter.
#define LOCAL_UTC_OFFSET_H    3
#define IRRIGATION_HOUR_START 5
#define IRRIGATION_HOUR_END   9

// After the safety supervisor intervenes (dry run / hard cap), stay off this
// long rather than immediately retrying into the same fault.
#define SAFETY_LOCKOUT_MS     3600000UL // 1 h

// A manual valve command from FIWARE suspends the automatic logic this long.
#define MANUAL_OVERRIDE_MS    1800000UL // 30 min

// =====================================================================
//  2. INCLUDES
// =====================================================================
#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <RTClib.h>
#include <DHT.h>
#include <esp_task_wdt.h>
#include <Preferences.h>

// =====================================================================
//  3. PIN DEFINITIONS
// =====================================================================
#define PIN_DHT        4
#define PIN_I2C_SDA    21
#define PIN_I2C_SCL    22
#define PIN_VALVE      15
#define PIN_FLOW       25
#define PIN_PUMP       32
#define PIN_RS485_RX   16
#define PIN_RS485_TX   17
#define HAS_DHT         1
#define DHTTYPE        DHT22             // zone 2 uses DHT22 (zone 3 has DHT11)

// =====================================================================
//  4. GLOBALS
// =====================================================================
DHT           dht(PIN_DHT, DHTTYPE);
RTC_DS3231    rtc;
WiFiClient    wifiClient;
PubSubClient  mqtt(wifiClient);

String topicAttrs, topicCmd, topicCmdExe;

// --- sensor cache ---
struct Readings {
  float airTemp   = NAN;
  float airHum    = NAN;
  float soilMoist = NAN;
  float soilTemp  = NAN;
  float soilEC    = NAN;
  float soilPH    = NAN;
  float flowRate  = 0.0f;     // L/min
  float volumeTot = 0.0f;     // L, cumulative since boot
} rd;

// --- actuator state ---
bool          valveOpen        = false;
bool          pumpRequested    = false;
unsigned long wateringStartMs  = 0;
const char*   alertMsg         = "ok";

// --- flow interrupt ---
volatile uint32_t flowPulses = 0;
portMUX_TYPE flowMux = portMUX_INITIALIZER_UNLOCKED;
unsigned long lastFlowCalcMs = 0;

// --- remotely settable configuration (persisted in NVS) ---
Preferences prefs;
struct RuntimeConfig {
  float moistMin;      // start a cycle below this  (setmin)
  float moistMax;      // cycle ends at or above    (setmax)
  bool  autoEnabled;   // master switch             (setauto)
} cfg;
bool cycleActive = false;   // true between first dose and reaching moistMax

// --- irrigation state machine ---
enum IrrState { IRR_IDLE = 0, IRR_WATERING = 1, IRR_SOAKING = 2,
                IRR_LOCKOUT = 3, IRR_MANUAL = 4 };
IrrState      irrState        = IRR_IDLE;
unsigned long irrStateSinceMs = 0;
float         doseStartVolume = 0.0f;
float         dailyVolume     = 0.0f;
int           dailyVolumeDay  = -1;
unsigned long manualUntilMs   = 0;
unsigned long lockoutUntilMs  = 0;

// --- scheduling ---
unsigned long lastPublishMs = 0;
unsigned long lastPollMs    = 0;
unsigned long lastMqttTryMs = 0;
bool          rtcOk         = false;

void IRAM_ATTR flowISR() {
  portENTER_CRITICAL_ISR(&flowMux);
  flowPulses++;
  portEXIT_CRITICAL_ISR(&flowMux);
}

// =====================================================================
//  5. ACTUATOR CONTROL
// =====================================================================
void applyPump() {
  // Open-drain: only pull the shared line down when this zone wants water.
  digitalWrite(PIN_PUMP, pumpRequested ? PUMP_ASSERT : PUMP_RELEASE);
}

void setValve(bool open, const char* reason) {
  if (open == valveOpen) return;
  valveOpen = open;
  digitalWrite(PIN_VALVE, open ? VALVE_ON : VALVE_OFF);

  if (open) {
    wateringStartMs = millis();
    pumpRequested   = true;
    // Discard anything counted while shut so the dry-run grace window
    // measures only real post-open flow.
    portENTER_CRITICAL(&flowMux);
    flowPulses = 0;
    portEXIT_CRITICAL(&flowMux);
    lastFlowCalcMs = millis();
  } else {
    pumpRequested   = false;
  }
  applyPump();

  Serial.printf("[VALVE] %s (%s)\n", open ? "OPEN" : "CLOSED", reason);
}

// Runs every loop: enforces the safety limits regardless of what FIWARE says.
void safetySupervisor() {
  if (!valveOpen) return;
  unsigned long elapsed = millis() - wateringStartMs;

  if (elapsed > MAX_WATERING_MS) {
    alertMsg = "timeout";
    setValve(false, "max watering time reached");
    return;
  }
  if (elapsed > DRY_RUN_GRACE_MS && rd.flowRate < MIN_EXPECTED_FLOW) {
    alertMsg = "dryrun";
    setValve(false, "no flow detected - pump dry or valve stuck shut");
  }
}

// =====================================================================
//  5b. AUTOMATIC IRRIGATION CONTROLLER
// =====================================================================
void loadConfig() {
  prefs.begin("irrig", false);
  cfg.moistMin    = prefs.getFloat("min",  SOIL_MOISTURE_START);
  cfg.moistMax    = prefs.getFloat("max",  SOIL_MOISTURE_TARGET);
  cfg.autoEnabled = prefs.getBool ("auto", AUTO_IRRIGATION);
  prefs.end();
}

void saveConfig() {
  prefs.begin("irrig", false);
  prefs.putFloat("min",  cfg.moistMin);
  prefs.putFloat("max",  cfg.moistMax);
  prefs.putBool ("auto", cfg.autoEnabled);
  prefs.end();
  Serial.printf("[CFG ] saved: min %.1f  max %.1f  auto %d\n",
                cfg.moistMin, cfg.moistMax, cfg.autoEnabled);
}

const char* irrStateName(IrrState s) {
  switch (s) {
    case IRR_IDLE:     return "IDLE";
    case IRR_WATERING: return "WATERING";
    case IRR_SOAKING:  return "SOAKING";
    case IRR_LOCKOUT:  return "LOCKOUT";
    case IRR_MANUAL:   return "MANUAL";
  }
  return "?";
}

void setIrrState(IrrState s) {
  if (s == irrState) return;
  irrState        = s;
  irrStateSinceMs = millis();
  Serial.printf("[IRR ] -> %s\n", irrStateName(s));
}

int localHour() {
  if (!rtcOk) return -1;
  return (rtc.now().hour() + LOCAL_UTC_OFFSET_H) % 24;
}

bool hourAllowed(int h) {
  if (h < 0) return true;                      // no RTC: don't block on time
  if (IRRIGATION_HOUR_START <= IRRIGATION_HOUR_END)
    return (h >= IRRIGATION_HOUR_START && h < IRRIGATION_HOUR_END);
  return (h >= IRRIGATION_HOUR_START || h < IRRIGATION_HOUR_END);  // wraps midnight
}

void rollDailyVolume() {
  if (!rtcOk) return;
  int d = rtc.now().day();
  if (d != dailyVolumeDay) {
    dailyVolumeDay = d;
    dailyVolume    = 0.0f;
    Serial.println("[IRR ] daily volume counter reset");
  }
}

bool shouldStartWatering() {
  if (!cfg.autoEnabled)                          return false;
  if (isnan(rd.soilMoist))                       return false;  // no reading: never guess

  // Hysteresis: once a cycle starts, keep dosing up to moistMax rather than
  // stopping the moment we cross back over moistMin -- otherwise the system
  // chatters around a single threshold.
  float gate = cycleActive ? cfg.moistMax : cfg.moistMin;
  if (rd.soilMoist >= gate) { cycleActive = false; return false; }
  if (dailyVolume  >= DAILY_VOLUME_CAP_L)        return false;
  if (!hourAllowed(localHour()))                 return false;
  return true;
}

void irrigationController() {
  if (!cfg.autoEnabled) return;
  rollDailyVolume();
  unsigned long now = millis();

  // A manual FIWARE command outranks the automatic logic for a while.
  if ((long)(manualUntilMs - now) > 0) { setIrrState(IRR_MANUAL); return; }

  switch (irrState) {

    case IRR_MANUAL:
      if (valveOpen) setValve(false, "manual override expired");
      setIrrState(IRR_IDLE);
      break;

    case IRR_WATERING: {
      if (!valveOpen) {                      // safety supervisor closed it on us
        lockoutUntilMs = now + SAFETY_LOCKOUT_MS;
        setIrrState(IRR_LOCKOUT);
        break;
      }
      float delivered = rd.volumeTot - doseStartVolume;
      bool  doseDone  = delivered >= IRRIGATION_DOSE_L;
      bool  timeUp    = (now - irrStateSinceMs) >= IRRIGATION_DOSE_MAX_MS;
      if (doseDone || timeUp) {
        dailyVolume += delivered;
        setValve(false, doseDone ? "dose delivered" : "dose time cap");
        Serial.printf("[IRR ] delivered %.3f L, daily total %.3f L\n",
                      delivered, dailyVolume);
        setIrrState(IRR_SOAKING);
      }
      break;
    }

    case IRR_SOAKING:
      if (now - irrStateSinceMs >= SOAK_PERIOD_MS) setIrrState(IRR_IDLE);
      break;

    case IRR_LOCKOUT:
      if ((long)(now - lockoutUntilMs) >= 0) setIrrState(IRR_IDLE);
      break;

    case IRR_IDLE:
    default:
      if (shouldStartWatering()) {
        doseStartVolume = rd.volumeTot;
        cycleActive     = true;
        alertMsg        = "ok";
        setValve(true, "auto: soil below threshold");
        setIrrState(IRR_WATERING);
      }
      break;
  }
}

// =====================================================================
//  6. SENSOR READING
// =====================================================================
void readDHT() {
#if HAS_DHT
  float h = dht.readHumidity();
  float t = dht.readTemperature();
  if (!isnan(h)) rd.airHum  = h;
  if (!isnan(t)) rd.airTemp = t;
#endif
}

// Returns 0..100 % surface wetness. Raw ADC falls as more of the plate is
// bridged by water, so the mapping is inverted.
void updateFlow() {
  unsigned long now = millis();
  unsigned long dt  = now - lastFlowCalcMs;
  if (dt < 1000) return;

  uint32_t pulses;
  portENTER_CRITICAL(&flowMux);
  pulses = flowPulses;
  flowPulses = 0;
  portEXIT_CRITICAL(&flowMux);

  float litres = pulses / FLOW_PULSES_PER_LITRE;

  // A shut valve means no flow, by definition. Without this gate a floating
  // GPIO25 picks up 50 Hz mains hum, which the pulse counter happily reads as
  // ~7.6 L/min -- that fabricates volume AND defeats the dry-run check below.
  if (valveOpen) {
    rd.volumeTot += litres;
    rd.flowRate   = litres * (60000.0f / (float)dt);  // L/min
  } else {
    rd.flowRate   = 0.0f;
  }
  lastFlowCalcMs = now;
}

// ---- Modbus RTU helpers ----------------------------------------------
uint16_t modbusCRC(const uint8_t* buf, uint8_t len) {
  uint16_t crc = 0xFFFF;
  for (uint8_t i = 0; i < len; i++) {
    crc ^= buf[i];
    for (uint8_t b = 0; b < 8; b++) {
      if (crc & 0x0001) { crc >>= 1; crc ^= 0xA001; }
      else              { crc >>= 1; }
    }
  }
  return crc;
}

bool readSoilSensor() {
  uint8_t req[8] = {
    SOIL_SLAVE_ADDR, SOIL_FUNC,
    (uint8_t)(SOIL_REG_BASE  >> 8), (uint8_t)(SOIL_REG_BASE  & 0xFF),
    (uint8_t)(SOIL_REG_COUNT >> 8), (uint8_t)(SOIL_REG_COUNT & 0xFF),
    0, 0
  };
  uint16_t crc = modbusCRC(req, 6);
  req[6] = crc & 0xFF;          // CRC low byte first
  req[7] = crc >> 8;

  while (Serial2.available()) Serial2.read();   // flush stale bytes
  Serial2.write(req, 8);
  Serial2.flush();

  const uint8_t expected = 5 + (SOIL_REG_COUNT * 2);   // addr+func+len+data+crc
  uint8_t resp[32];
  uint8_t idx = 0;
  unsigned long t0 = millis();

  while (idx < expected && (millis() - t0) < SOIL_TIMEOUT_MS) {
    if (Serial2.available()) resp[idx++] = Serial2.read();
  }
  if (idx < expected) { Serial.println("[SOIL] timeout"); return false; }

  uint16_t rxCrc = modbusCRC(resp, expected - 2);
  if ((resp[expected - 2] != (rxCrc & 0xFF)) || (resp[expected - 1] != (rxCrc >> 8))) {
    Serial.println("[SOIL] CRC error");
    return false;
  }
  if (resp[1] != SOIL_FUNC) { Serial.println("[SOIL] exception response"); return false; }

  uint16_t reg[SOIL_REG_COUNT];
  for (uint8_t i = 0; i < SOIL_REG_COUNT; i++)
    reg[i] = ((uint16_t)resp[3 + i * 2] << 8) | resp[4 + i * 2];

  rd.soilMoist = reg[0] / 10.0f;               // %RH  (0.1 % resolution)
  rd.soilTemp  = (int16_t)reg[1] / 10.0f;      // degC (signed, 0.1 resolution)
  rd.soilEC    = (float)reg[2];                // uS/cm
  rd.soilPH    = reg[3] / 10.0f;               // pH   (0.1 resolution)
  return true;
}

// =====================================================================
//  7. TIME
// =====================================================================
String isoTimestamp() {
  if (!rtcOk) return String("");
  DateTime now = rtc.now();
  char buf[24];
  snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02dZ",
           now.year(), now.month(), now.day(),
           now.hour(), now.minute(), now.second());
  return String(buf);
}

void syncRtcFromNtp() {
  if (WiFi.status() != WL_CONNECTED || !rtcOk) return;
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");   // store UTC in the RTC
  struct tm tmNow;
  if (getLocalTime(&tmNow, 5000)) {
    rtc.adjust(DateTime(tmNow.tm_year + 1900, tmNow.tm_mon + 1, tmNow.tm_mday,
                        tmNow.tm_hour, tmNow.tm_min, tmNow.tm_sec));
    Serial.println("[RTC] synced from NTP (UTC)");
  }
}

// =====================================================================
//  8. FIWARE / MQTT  (UltraLight 2.0)
// =====================================================================
// NOTE: 'decimals' must be unsigned int, not uint8_t. With uint8_t the call
// String(float, uint8_t) is ambiguous between String(float, unsigned int)
// and String(unsigned char, unsigned char) and will not compile.
void appendPair(String& p, const char* key, float val, unsigned int decimals) {
  if (isnan(val)) return;                 // skip failed reads, don't send garbage
  // String(float, n) right-justifies in a field of (n + 2), so 0 decimals
  // yields " 0" with a leading space -- which arrives at Orion as a STRING
  // instead of a Number. Trim it.
  String s = String(val, decimals);
  s.trim();
  if (p.length()) p += "|";
  p += key; p += "|"; p += s;
}
void appendPair(String& p, const char* key, const String& val) {
  if (!val.length()) return;
  if (p.length()) p += "|";
  p += key; p += "|"; p += val;
}

void publishMeasurements() {
  String payload;
  appendPair(payload, "t",  rd.airTemp,   1);   // air temperature   degC
  appendPair(payload, "h",  rd.airHum,    1);   // air humidity      %
  appendPair(payload, "sm", rd.soilMoist, 1);   // soil moisture     %
  appendPair(payload, "st", rd.soilTemp,  1);   // soil temperature  degC
  appendPair(payload, "ec", rd.soilEC,    0);   // soil EC           uS/cm
  appendPair(payload, "ph", rd.soilPH,    1);   // soil pH
  appendPair(payload, "fr", rd.flowRate,  2);   // flow rate         L/min
  appendPair(payload, "vt", rd.volumeTot, 3);   // total volume      L
  appendPair(payload, "is", (float)irrState, 0); // irrigation state 0..4
  appendPair(payload, "mn", cfg.moistMin,    1);  // live threshold   %
  appendPair(payload, "mx", cfg.moistMax,    1);  // live target      %
  appendPair(payload, "vs", String(valveOpen ? 1 : 0));
  appendPair(payload, "ps", String(pumpRequested ? 1 : 0));
  appendPair(payload, "ts", isoTimestamp());
#if SEND_ALERT_ATTR
  appendPair(payload, "al", String(alertMsg));
#endif

  if (mqtt.publish(topicAttrs.c_str(), payload.c_str())) {
    Serial.println("[MQTT] -> " + payload);
  } else {
    Serial.println("[MQTT] publish FAILED");
  }
}

void sendCommandAck(const String& cmdName, const String& result) {
  String ack = String(DEVICE_ID) + "@" + cmdName + "|" + result;
  mqtt.publish(topicCmdExe.c_str(), ack.c_str());
  Serial.println("[MQTT] ack -> " + ack);
}

// Incoming payload format:  <deviceId>@<command>|<value>
void mqttCallback(char* topic, byte* payload, unsigned int len) {
  String msg;
  msg.reserve(len);
  for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  Serial.println("[MQTT] <- " + msg);

  int at  = msg.indexOf('@');
  int bar = msg.indexOf('|');
  if (at < 0) return;

  String cmdName = (bar > at) ? msg.substring(at + 1, bar) : msg.substring(at + 1);
  String value   = (bar > at) ? msg.substring(bar + 1)     : "";
  cmdName.trim(); value.trim(); value.toLowerCase();

  if (cmdName == "valve") {
    if (value == "open" || value == "on" || value == "1") {
      alertMsg      = "ok";
      manualUntilMs = millis() + MANUAL_OVERRIDE_MS;   // auto stands down
      setValve(true, "FIWARE command");
      sendCommandAck(cmdName, "open");
    } else if (value == "close" || value == "off" || value == "0") {
      manualUntilMs = millis() + MANUAL_OVERRIDE_MS;
      setValve(false, "FIWARE command");
      sendCommandAck(cmdName, "closed");
    } else {
      sendCommandAck(cmdName, "ERROR bad value");
    }
  }
  else if (cmdName == "setmin") {
    float v = value.toFloat();
    // Orion forbids = ; < > " ' ( ) in attribute values, so acks use spaces.
    if (v <= 0.0f || v >= 100.0f)       sendCommandAck(cmdName, "ERROR out of range");
    else if (v >= cfg.moistMax)         sendCommandAck(cmdName, "ERROR min above max");
    else {
      cfg.moistMin = v; saveConfig();
      sendCommandAck(cmdName, "OK min " + String(v, 1) + " max " + String(cfg.moistMax, 1));
    }
  }
  else if (cmdName == "setmax") {
    float v = value.toFloat();
    if (v <= 0.0f || v > 100.0f)        sendCommandAck(cmdName, "ERROR out of range");
    else if (v <= cfg.moistMin)         sendCommandAck(cmdName, "ERROR max below min");
    else {
      cfg.moistMax = v; saveConfig();
      sendCommandAck(cmdName, "OK min " + String(cfg.moistMin, 1) + " max " + String(v, 1));
    }
  }
  else if (cmdName == "setauto") {
    bool on = (value == "1" || value == "on" || value == "true");
    cfg.autoEnabled = on;
    saveConfig();
    if (!on && valveOpen) setValve(false, "auto disabled remotely");
    sendCommandAck(cmdName, on ? "OK auto on" : "OK auto off");
  }
  else {
    sendCommandAck(cmdName, "ERROR unknown command");
  }
}

bool mqttConnect() {
  String clientId = String("esp32-") + DEVICE_ID;
  bool ok = (strlen(MQTT_USER) > 0)
          ? mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)
          : mqtt.connect(clientId.c_str());

  if (ok) {
    mqtt.subscribe(topicCmd.c_str());
    Serial.println("[MQTT] connected, subscribed to " + topicCmd);
  } else {
    Serial.printf("[MQTT] connect failed, rc=%d\n", mqtt.state());
  }
  return ok;
}

void ensureConnectivity() {
  if (WiFi.status() != WL_CONNECTED) {
    // Safety: if the link is gone we cannot be told to stop -> close the valve.
    if (valveOpen) { alertMsg = "offline"; setValve(false, "WiFi lost"); }
    WiFi.reconnect();
    return;
  }
  if (!mqtt.connected() && millis() - lastMqttTryMs > 5000) {
    lastMqttTryMs = millis();
    mqttConnect();
  }
}

// =====================================================================
//  9. SETUP
// =====================================================================
void setup() {
  // --- actuators first, fail-safe state before anything else ---
  pinMode(PIN_VALVE, OUTPUT);
  digitalWrite(PIN_VALVE, VALVE_OFF);
  pinMode(PIN_PUMP, OUTPUT_OPEN_DRAIN);
  digitalWrite(PIN_PUMP, PUMP_RELEASE);

  Serial.begin(115200);
  delay(200);
  Serial.printf("\n=== Zone %d node booting (%s) ===\n", ZONE_NUMBER, DEVICE_ID);

  loadConfig();   // NVS overrides the compiled defaults

  // --- watchdog ---
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t wdtCfg = { .timeout_ms = WDT_TIMEOUT_S * 1000,
                                   .idle_core_mask = 0,
                                   .trigger_panic = true };
  esp_task_wdt_init(&wdtCfg);
#else
  esp_task_wdt_init(WDT_TIMEOUT_S, true);
#endif
  esp_task_wdt_add(NULL);

  // --- sensors ---
  dht.begin();

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  rtcOk = rtc.begin();
  if (!rtcOk) Serial.println("[RTC] DS3231 not found!");
  else if (rtc.lostPower()) Serial.println("[RTC] lost power, needs NTP sync");


  Serial2.begin(SOIL_BAUD, SERIAL_8N1, PIN_RS485_RX, PIN_RS485_TX);

  // Level-shifted by the external 10k/3k3 divider. The internal pulldown
  // (~45k) is only a stopgap against mains pickup if the divider is ever
  // disconnected -- it does not replace the external network.
  pinMode(PIN_FLOW, INPUT_PULLDOWN);
  attachInterrupt(digitalPinToInterrupt(PIN_FLOW), flowISR, RISING);
  lastFlowCalcMs = millis();

  // --- MQTT topics (UltraLight 2.0) ---
  topicAttrs  = String("/") + FIWARE_API_KEY + "/" + DEVICE_ID + "/attrs";
  topicCmd    = String("/") + FIWARE_API_KEY + "/" + DEVICE_ID + "/cmd";
  topicCmdExe = String("/") + FIWARE_API_KEY + "/" + DEVICE_ID + "/cmdexe";

  // --- network ---
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[WiFi] connecting");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(400); Serial.print("."); esp_task_wdt_reset();
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WiFi] IP: "); Serial.println(WiFi.localIP());
    Serial.printf("[WiFi] RSSI: %d dBm\n", WiFi.RSSI());
    syncRtcFromNtp();
  }

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(60);        // PubSubClient default is 15 s -- too tight for a
                                // 30 s publish interval; Mosquitto drops at 1.5x
  mqtt.setSocketTimeout(15);
  mqttConnect();

  // --- boot self-test: say plainly which sensors answered ---
#if HAS_DHT
  readDHT();
  if (isnan(rd.airTemp) || isnan(rd.airHum))
    Serial.println("[DHT ] no response - check 4k7-10k pullup on GPIO4, 3V3 supply,"
                   " and that Adafruit's 'DHT sensor library' is the one selected");
  else
    Serial.printf("[DHT ] ok  %.1f C  %.1f %%\n", rd.airTemp, rd.airHum);
#else
  Serial.println("[DHT ] disabled (HAS_DHT 0)");
#endif

  Serial.printf("[IRR ] auto=%s  start<%.1f%%  target>=%.1f%%  dose=%.1fL"
                "  soak=%lumin  window=%02d-%02d local  cap=%.1fL/day\n",
                cfg.autoEnabled ? "on" : "off", cfg.moistMin, cfg.moistMax,
                IRRIGATION_DOSE_L, SOAK_PERIOD_MS / 60000UL,
                IRRIGATION_HOUR_START, IRRIGATION_HOUR_END, DAILY_VOLUME_CAP_L);
  Serial.println("[CFG ] set remotely with setmin / setmax / setauto commands");
  Serial.println("[IRR ] no local tank probe: protection arrives as a valve"
                 " close from the interlock service");

  if (readSoilSensor())
    Serial.printf("[SOIL] ok  moist %.1f %%  temp %.1f C  EC %.0f uS/cm  pH %.1f\n",
                  rd.soilMoist, rd.soilTemp, rd.soilEC, rd.soilPH);

  Serial.println("=== ready ===");
}

// =====================================================================
//  10. MAIN LOOP
// =====================================================================
void loop() {
  esp_task_wdt_reset();

  ensureConnectivity();
  mqtt.loop();

  updateFlow();
  safetySupervisor();       // safety first: it can override the controller
  irrigationController();

  unsigned long now = millis();

  if (now - lastPollMs >= SENSOR_POLL_MS) {
    lastPollMs = now;
    readDHT();
      readSoilSensor();
  }

  // Sample faster during an irrigation event -- that is the interesting part
  // of the series, and 120 s would smear a 3 min dose into two points.
  unsigned long interval = valveOpen ? PUBLISH_INTERVAL_WATER_MS
                                     : PUBLISH_INTERVAL_MS;
  if (now - lastPublishMs >= interval) {
    lastPublishMs = now;
    if (mqtt.connected()) publishMeasurements();
  }
}
