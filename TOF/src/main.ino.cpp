# 1 "C:\\Users\\minhy\\AppData\\Local\\Temp\\tmp_eisa9xr"
#include <Arduino.h>
# 1 "D:/GitHub/Majubom/TOF/src/main.ino"







#include <Wire.h>
#include <vl53l5cx_class.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>


const char* SENSOR_NAME = "tof2";
const char* WIFI_SSID = "2411 ServerRoom";
const char* WIFI_PASS = "D@lstn!0722";
const char* SERVER_URL = "http://192.168.6.10:5001/tof";


#define SDA_PIN 8
#define SCL_PIN 9
#define LPN_PIN 4
#define SENSOR_ADDR 0x52
#define ZONE_COUNT 64
#define GRID_SIDE 8
#define INTERVAL_MS 10


class VL53L5CX_Ex : public VL53L5CX {
public:
  VL53L5CX_Ex(TwoWire *i2c, int lpn_pin) : VL53L5CX(i2c, lpn_pin) {}


  int initSlow(uint8_t addr) {
    vl53l5cx_off();
    delay(200);
    vl53l5cx_on();
    delay(1000);
    p_dev->platform.dev_i2c->begin(SDA_PIN, SCL_PIN);
    p_dev->platform.dev_i2c->setClock(400000);
    delay(50);


    TwoWire *wi = p_dev->platform.dev_i2c;
    uint8_t curAddr = 0;
    Serial.print("  scan:");
    for (uint8_t a = 0x08; a <= 0x77; a++) {
      wi->beginTransmission(a);
      if (wi->endTransmission() == 0) {
        Serial.printf(" 0x%02X(8b=0x%02X)", a, a << 1);
        if (curAddr == 0) curAddr = (a << 1);
      }
    }
    Serial.println();
    if (curAddr == 0) { Serial.println("  ERR: sensor not on bus"); return -1; }
    Serial.printf("  using 8bit=0x%02X\n", curAddr);

    p_dev->platform.address = curAddr;


    if (curAddr != addr) {
      uint8_t s = vl53l5cx_set_i2c_address(addr);
      Serial.printf("  set_addr(0x%02X) -> %d\n", addr, s);
      if (s != 0) return -1;
      delay(20);
    }

    uint8_t isAlive = 0;
    uint8_t s2 = vl53l5cx_is_alive(&isAlive);
    Serial.printf("  is_alive -> s=%d alive=%d\n", s2, isAlive);
    if (s2 != 0 || !isAlive) return -2;
    uint8_t s3 = vl53l5cx_init();
    Serial.printf("  vl53l5cx_init -> %d\n", s3);
    return (int)s3;
  }
};

VL53L5CX_Ex sensor(&Wire, LPN_PIN);
unsigned long lastSendMs = 0;
void wifiBegin();
void ensureWiFi();
void printGrid(VL53L5CX_ResultsData &r);
void postSensor(VL53L5CX_ResultsData &r);
void setup();
void loop();
#line 84 "D:/GitHub/Majubom/TOF/src/main.ino"
void wifiBegin() {
  if (strlen(WIFI_PASS) > 0) WiFi.begin(WIFI_SSID, WIFI_PASS);
  else WiFi.begin(WIFI_SSID);
}

void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.println("[WiFi] 재연결 중...");
  WiFi.disconnect();
  wifiBegin();
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t > 10000) {
      Serial.println("[WiFi] 재연결 실패 - 재시도 예정");
      return;
    }
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] 재연결: %s\n", WiFi.localIP().toString().c_str());
}

void printGrid(VL53L5CX_ResultsData &r) {
  Serial.printf("\n=== %s (8x8 mm) ===\n", SENSOR_NAME);
  for (int row = 0; row < GRID_SIDE; row++) {
    for (int col = 0; col < GRID_SIDE; col++) {
      int z = row * GRID_SIDE + col;
      int d = -1;
      if (r.nb_target_detected[z] > 0) {
        uint8_t st = r.target_status[VL53L5CX_NB_TARGET_PER_ZONE * z];
        if (st == 5) d = r.distance_mm[VL53L5CX_NB_TARGET_PER_ZONE * z];
      }
      if (d < 0) Serial.printf(" %5s", "----");
      else Serial.printf(" %5d", d);
    }
    Serial.println();
  }
}

void postSensor(VL53L5CX_ResultsData &r) {
  JsonDocument doc;
  doc["sensor"] = SENSOR_NAME;
  doc["resolution"] = "8x8";

  JsonArray dist = doc["distances_mm"].to<JsonArray>();
  JsonArray tgts = doc["targets"].to<JsonArray>();

  for (int z = 0; z < ZONE_COUNT; z++) {
    int d = -1;
    int t = (int)r.nb_target_detected[z];
    if (t > 0) {
      uint8_t st = r.target_status[VL53L5CX_NB_TARGET_PER_ZONE * z];
      if (st == 5) d = (int)r.distance_mm[VL53L5CX_NB_TARGET_PER_ZONE * z];
    }
    dist.add(d);
    tgts.add(t);
  }

  String body;
  serializeJson(doc, body);

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(200);
  int code = http.POST(body);
  Serial.printf("[%s] POST → HTTP %d\n", SENSOR_NAME, code);
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n=== VL53L5CX Single Sensor Boot (%s) ===\n", SENSOR_NAME);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  sensor.begin();
  delay(100);

  Serial.printf("[%s] 초기화 중... (수 초 소요)\n", SENSOR_NAME);
  if (sensor.initSlow(SENSOR_ADDR) != 0) {
    Serial.printf("ERR: %s initSlow() 실패\n", SENSOR_NAME);
    while (true) delay(1000);
  }
  Serial.printf("[%s] 0x%02X OK\n", SENSOR_NAME, SENSOR_ADDR);

  sensor.vl53l5cx_set_resolution(VL53L5CX_RESOLUTION_8X8);
  sensor.vl53l5cx_set_ranging_frequency_hz(15);
  sensor.vl53l5cx_start_ranging();
  Serial.printf("[%s] ranging 시작\n", SENSOR_NAME);

  Serial.printf("[WiFi] 연결 중: %s\n", WIFI_SSID);
  wifiBegin();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] IP: %s\n", WiFi.localIP().toString().c_str());
  Serial.println("=== 측정 시작 ===\n");
}

void loop() {
  ensureWiFi();

  if (millis() - lastSendMs < INTERVAL_MS) {
    delay(5);
    return;
  }

  VL53L5CX_ResultsData r;
  uint8_t ready = 0;
  sensor.vl53l5cx_check_data_ready(&ready);
  if (!ready) return;
  if (sensor.vl53l5cx_get_ranging_data(&r) != 0) return;

  if (WiFi.status() != WL_CONNECTED) {
    ensureWiFi();
    return;
  }


  static unsigned long lastPrintMs = 0;
  if (millis() - lastPrintMs > 1000) { printGrid(r); lastPrintMs = millis(); }
  postSensor(r);
  lastSendMs = millis();
}