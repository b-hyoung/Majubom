#include <Wire.h>
#include <vl53l5cx_class.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ─── 사용자 설정 ─────────────────────────────────────
const char* WIFI_SSID  = "Jvision_Lab";
const char* WIFI_PASS  = "1234567890";
const char* SERVER_URL = "http://192.168.1.57:5001/tof";
// ─────────────────────────────────────────────────────

#define SDA_PIN      8
#define SCL_PIN      9
#define LPN1_PIN     4
#define LPN2_PIN     5
#define SENSOR1_ADDR 0x52
#define SENSOR2_ADDR 0x54
#define ZONE_COUNT   16    // 4x4 (8x8은 이 배선의 I2C 버스 한계로 wedge되어 4x4 운용)
#define GRID_SIDE    4     // 4x4 한 변
#define INTERVAL_MS  500

// initSlow를 위한 서브클래스 (protected p_dev 접근)
class VL53L5CX_Ex : public VL53L5CX {
public:
  VL53L5CX_Ex(TwoWire *i2c, int lpn_pin) : VL53L5CX(i2c, lpn_pin) {}

  // LPN 토글 + 충분한 대기 후 초기화 (addr != 0x52 면 주소 변경)
  int initSlow(uint8_t addr) {
    vl53l5cx_off();
    delay(200);
    vl53l5cx_on();
    delay(1000);
    // Reinit I2C after sensor power-on (recovers from any stuck bus state)
    p_dev->platform.dev_i2c->begin(SDA_PIN, SCL_PIN);
    p_dev->platform.dev_i2c->setClock(400000);
    delay(50);

    // Full I2C scan to find sensor at any address
    TwoWire *wi = p_dev->platform.dev_i2c;
    uint8_t curAddr = 0;
    Serial.print("  scan:");
    for (uint8_t a = 0x08; a <= 0x77; a++) {
      wi->beginTransmission(a);
      if (wi->endTransmission() == 0) {
        Serial.printf(" 0x%02X(8b=0x%02X)", a, a << 1);
        if (curAddr == 0) curAddr = (a << 1); // convert 7-bit → 8-bit
      }
    }
    Serial.println();
    if (curAddr == 0) { Serial.println("  ERR: sensor not on bus"); return -1; }
    Serial.printf("  using 8bit=0x%02X\n", curAddr);

    // Align p_dev address so WrByte talks to the right device
    p_dev->platform.address = curAddr;

    // Only send set_i2c_address if not already at target
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

VL53L5CX_Ex sensor1(&Wire, LPN1_PIN);
VL53L5CX_Ex sensor2(&Wire, LPN2_PIN);
unsigned long lastSendMs = 0;

// ─────────────────────────────────────────────────────

void wifiBegin() {
  if (strlen(WIFI_PASS) > 0) WiFi.begin(WIFI_SSID, WIFI_PASS);
  else                         WiFi.begin(WIFI_SSID);
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

void printGrid(const char* name, VL53L5CX_ResultsData &r) {
  Serial.printf("\n=== %s (4x4 mm) ===\n", name);
  for (int row = 0; row < GRID_SIDE; row++) {
    for (int col = 0; col < GRID_SIDE; col++) {
      int z = row * GRID_SIDE + col;
      int d = -1;
      if (r.nb_target_detected[z] > 0) {
        uint8_t st = r.target_status[VL53L5CX_NB_TARGET_PER_ZONE * z];
        if (st == 5) d = r.distance_mm[VL53L5CX_NB_TARGET_PER_ZONE * z];
      }
      if (d < 0) Serial.printf(" %5s", "----");
      else        Serial.printf(" %5d", d);
    }
    Serial.println();
  }
}

void postSensor(const char* sensorName, VL53L5CX_ResultsData &r) {
  JsonDocument doc;   // v7 동적 문서 (8x8=64존 수용)
  doc["sensor"]     = sensorName;
  doc["resolution"] = "4x4";

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
  int code = http.POST(body);
  Serial.printf("[%s] POST → HTTP %d\n", sensorName, code);
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== VL53L5CX Dual Sensor Boot ===");

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  // begin() → 각 LPN 핀을 OUTPUT + LOW (두 센서 모두 꺼짐)
  sensor1.begin();
  sensor2.begin();
  delay(100);

  // ── 초기화 순서 ───────────────────────────────────────
  // 두 센서 모두 기본 주소 0x52 사용.
  // 버스 충돌 방지: 센서2 먼저 0x54로 변경 → 센서1은 0x52 유지

  // [Step 1] 센서2: LPN2 HIGH → 0x52 부팅 → 0x54 변경 → 펌웨어 로드
  Serial.println("[tof2] 초기화 중... (수 초 소요)");
  if (sensor2.initSlow(SENSOR2_ADDR) != 0) {
    Serial.println("ERR: tof2 initSlow() 실패");
    while (true) delay(1000);
  }
  Serial.printf("[tof2] 0x%02X OK\n", SENSOR2_ADDR);

  // [Step 2] 센서1: LPN1 HIGH → 0x52 부팅 → 주소 유지 → 펌웨어 로드
  //          (버스: tof1@0x52, tof2@0x54 - 충돌 없음)
  Serial.println("[tof1] 초기화 중... (수 초 소요)");
  if (sensor1.initSlow(SENSOR1_ADDR) != 0) {
    Serial.println("ERR: tof1 initSlow() 실패");
    while (true) delay(1000);
  }
  Serial.printf("[tof1] 0x%02X OK\n", SENSOR1_ADDR);

  // [Step 3] 해상도·주기 설정 및 Ranging 시작
  sensor1.vl53l5cx_set_resolution(VL53L5CX_RESOLUTION_4X4);
  sensor1.vl53l5cx_set_ranging_frequency_hz(2);
  sensor1.vl53l5cx_start_ranging();
  Serial.println("[tof1] ranging 시작");

  sensor2.vl53l5cx_set_resolution(VL53L5CX_RESOLUTION_4X4);
  sensor2.vl53l5cx_set_ranging_frequency_hz(2);
  sensor2.vl53l5cx_start_ranging();
  Serial.println("[tof2] ranging 시작");

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

  VL53L5CX_ResultsData r1, r2;
  uint8_t ready1 = 0, ready2 = 0;
  bool got1 = false, got2 = false;

  sensor1.vl53l5cx_check_data_ready(&ready1);
  if (ready1) got1 = (sensor1.vl53l5cx_get_ranging_data(&r1) == 0);

  sensor2.vl53l5cx_check_data_ready(&ready2);
  if (ready2) got2 = (sensor2.vl53l5cx_get_ranging_data(&r2) == 0);

  if (!got1 && !got2) return;

  if (WiFi.status() != WL_CONNECTED) {
    ensureWiFi();
    return;
  }

  if (got1) {
    printGrid("tof1", r1);
    postSensor("tof1", r1);
  }
  if (got2) {
    printGrid("tof2", r2);
    postSensor("tof2", r2);
  }

  lastSendMs = millis();
}
