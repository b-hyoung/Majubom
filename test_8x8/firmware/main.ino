// ============================================================
//  ⚠ 실험용 사본 — TOF/src/main.ino 를 건드리지 않기 위해 복사한 파일
//  목적: VL53L5CX 8x8(64존) 모드에서 I2C 버스가 wedge 되는지 확인
//  프로덕션(TOF/src/main.ino)과의 차이점:
//    - VL53L5CX_RESOLUTION_4X4 → VL53L5CX_RESOLUTION_8X8
//    - ZONE_COUNT 16 → 64, GRID_SIDE 4 → 8
//    - resolution 필드 "4x4" → "8x8"
//    - SERVER_URL → 테스트 전용 서버(포트 5011, DB 없음)로 변경
//  테스트가 끝나면 이 파일은 지우거나 그대로 test_8x8/ 안에 남겨둬도 됨
//  (원본 TOF/src/main.ino 는 전혀 수정하지 않았음)
// ============================================================
#include <Wire.h>
#include <vl53l5cx_class.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ─── 사용자 설정 (테스트할 보드 쪽만 맞춰서 수정) ─────────
const char* SENSOR_NAME = "tof2";   // 테스트 중인 보드가 어느 쪽인지만 표시용
const char* WIFI_SSID   = "2411 ServerRoom";
const char* WIFI_PASS   = "D@lstn!0722";
// ↓ 테스트 서버(이 저장소를 연 PC, Wi-Fi IP — "2411 ServerRoom"망, Pi와 같은 대역).
//   PC가 바뀌거나 재연결되면 ipconfig로 재확인 필요.
const char* SERVER_URL  = "http://192.168.6.20:5011/tof";
// ─────────────────────────────────────────────────────────

#define SDA_PIN      8
#define SCL_PIN      9
#define LPN_PIN      4
#define SENSOR_ADDR  0x52   // 버스 독점 → 기본 주소 그대로 사용 (주소 변경 불필요)
#define ZONE_COUNT   64     // 8x8 테스트용 (프로덕션은 16=4x4)
#define GRID_SIDE    8      // 8x8 테스트용 (프로덕션은 4)
#define INTERVAL_MS  100    // 전송 주기 100ms (10Hz) — 프로덕션과 동일

// initSlow를 위한 서브클래스 (protected p_dev 접근)
class VL53L5CX_Ex : public VL53L5CX {
public:
  VL53L5CX_Ex(TwoWire *i2c, int lpn_pin) : VL53L5CX(i2c, lpn_pin) {}

  // LPN 토글 + 충분한 대기 후 초기화 (버스 stuck 상태 복구)
  int initSlow(uint8_t addr) {
    vl53l5cx_off();
    delay(200);
    vl53l5cx_on();
    delay(1000);
    p_dev->platform.dev_i2c->begin(SDA_PIN, SCL_PIN);
    p_dev->platform.dev_i2c->setClock(400000);
    delay(50);

    // I2C 스캔으로 현재 주소 자동 감지
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

    // 단일 센서라 보통 0x52 그대로 → 주소 변경 불필요. 혹시 다르면 맞춤.
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

// ─────────────────────────────────────────────────────────

void wifiBegin() {
  if (strlen(WIFI_PASS) > 0) WiFi.begin(WIFI_SSID, WIFI_PASS);
  else                       WiFi.begin(WIFI_SSID);
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
  Serial.printf("\n=== %s (8x8 TEST, mm) ===\n", SENSOR_NAME);
  for (int row = 0; row < GRID_SIDE; row++) {
    for (int col = 0; col < GRID_SIDE; col++) {
      int z = row * GRID_SIDE + col;
      int d = -1;
      if (r.nb_target_detected[z] > 0) {
        uint8_t st = r.target_status[VL53L5CX_NB_TARGET_PER_ZONE * z];
        if (st == 5) d = r.distance_mm[VL53L5CX_NB_TARGET_PER_ZONE * z];
      }
      if (d < 0) Serial.printf(" %5s", "----");
      else       Serial.printf(" %5d", d);
    }
    Serial.println();
  }
}

void postSensor(VL53L5CX_ResultsData &r) {
  JsonDocument doc;
  doc["sensor"]     = SENSOR_NAME;
  doc["resolution"] = "8x8";   // ← 테스트: 8x8로 표시

  JsonArray dist = doc["distances_mm"].to<JsonArray>();
  JsonArray tgts = doc["targets"].to<JsonArray>();

  for (int z = 0; z < ZONE_COUNT; z++) {
    int d = -1;
    int t = (int)r.nb_target_detected[z];
    if (t > 0) {
      uint8_t st = r.target_status[VL53L5CX_NB_TARGET_PER_ZONE * z];
      if (st == 5) d = (int)r.distance_mm[VL53L5CX_NB_TARGET_PER_ZONE * z];  // 센서단 필터
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
  Serial.printf("[%s] POST(8x8 test) → HTTP %d\n", SENSOR_NAME, code);
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n=== VL53L5CX 8x8 TEST Boot (%s) ===\n", SENSOR_NAME);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  sensor.begin();   // LPN 핀 OUTPUT + LOW (센서 꺼짐)
  delay(100);

  Serial.printf("[%s] 초기화 중... (수 초 소요)\n", SENSOR_NAME);
  if (sensor.initSlow(SENSOR_ADDR) != 0) {
    Serial.printf("ERR: %s initSlow() 실패\n", SENSOR_NAME);
    while (true) delay(1000);
  }
  Serial.printf("[%s] 0x%02X OK\n", SENSOR_NAME, SENSOR_ADDR);

  sensor.vl53l5cx_set_resolution(VL53L5CX_RESOLUTION_8X8);   // ← 테스트 핵심: 8x8
  sensor.vl53l5cx_set_ranging_frequency_hz(15);   // 8x8 최대치 그대로 유지
  sensor.vl53l5cx_start_ranging();
  Serial.printf("[%s] ranging 시작 (8x8 TEST)\n", SENSOR_NAME);
  Serial.println("  → 여기서 시리얼 로그가 멈추거나 반복 실패하면 버스 wedge 재발입니다.");

  Serial.printf("[WiFi] 연결 중: %s\n", WIFI_SSID);
  wifiBegin();
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] IP: %s\n", WiFi.localIP().toString().c_str());
  Serial.println("=== 측정 시작 (8x8 TEST) ===\n");
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

  // 시리얼 그리드 출력은 1초에 한 번만 (전송 병목 방지)
  static unsigned long lastPrintMs = 0;
  if (millis() - lastPrintMs > 1000) { printGrid(r); lastPrintMs = millis(); }
  postSensor(r);
  lastSendMs = millis();
}
