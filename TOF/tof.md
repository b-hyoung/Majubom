# VL53L5CX ToF 센서 운용 가이드

## 하드웨어 배선

### 센서 1 (tof1)
| VL53L5CX 핀 | ESP32-S3 핀 |
|-------------|-------------|
| VIN         | 3V3         |
| GND         | GND         |
| SDA         | GPIO 8      |
| SCL         | GPIO 9      |
| LPN         | GPIO 4      |
| INT         | 미연결      |

### 센서 2 (tof2)
| VL53L5CX 핀 | ESP32-S3 핀 |
|-------------|-------------|
| VIN         | 3V3         |
| GND         | GND         |
| SDA         | GPIO 8 (공유) |
| SCL         | GPIO 9 (공유) |
| LPN         | GPIO 5      |
| INT         | 미연결      |

---

## 펌웨어 업로드

### 1. WiFi / 서버 IP 설정
`src/main.ino` 상단에서 수정:
```cpp
const char* WIFI_SSID  = "Jvision_Lab";
const char* WIFI_PASS  = "1234567890";
const char* SERVER_URL = "http://192.168.0.48:5001/tof";
```
- `SERVER_URL` IP는 Flask 서버를 실행하는 PC의 IP로 변경
- IP 확인: `ipconfig` → Wi-Fi IPv4 주소

### 2. 업로드
```powershell
~\.platformio\penv\Scripts\pio.exe run -t upload
```
- 업로드 실패 시: ESP32-S3에서 **BOOT 버튼 누른 채로 RESET 버튼** 눌렀다 떼기 → BOOT 떼기

---

## 부팅 및 정상 동작 확인

시리얼 모니터 실행:
```powershell
~\.platformio\penv\Scripts\pio.exe device monitor --port COM4 --baud 115200
```

정상 부팅 시 출력:
```
=== VL53L5CX Dual Sensor Boot ===
[tof2] 초기화 중... (수 초 소요)
  scan: 0x2A(8b=0x54)
  is_alive -> s=0 alive=1
  vl53l5cx_init -> 0
[tof2] 0x54 OK
[tof1] 초기화 중... (수 초 소요)
  scan: 0x29(8b=0x52) 0x2A(8b=0x54)
  is_alive -> s=0 alive=1
  vl53l5cx_init -> 0
[tof1] 0x52 OK
[tof1] ranging 시작
[tof2] ranging 시작
[WiFi] IP: 192.168.0.xx
=== 측정 시작 ===
```

---

## 주의사항

### I2C 주소 관련
- VL53L5CX 기본 주소: **0x52 (8bit)** — USB 재연결(전원 재공급)시에만 초기화됨
- LPN 핀 토글로는 주소 리셋 안 됨
- 펌웨어가 부팅 시 I2C 버스를 스캔해서 현재 주소를 자동 감지하므로 전원을 끊지 않아도 됨

### 업로드 전 시리얼 모니터 종료
- 시리얼 모니터가 열려 있으면 업로드 실패 → 모니터 먼저 종료 후 업로드

### ESP32-S3는 2.4GHz WiFi 전용
- 5GHz AP에는 연결 불가
