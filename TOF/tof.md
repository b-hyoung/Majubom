# VL53L5CX ToF 센서 운용 가이드

## 전체 연결 흐름 (한눈에)

```
[ESP32-S3 + ToF 센서 2개]  ──POST /tof (WiFi)──▶  [라즈베리파이5: Flask 서버]  ──▶  SQLite 저장
                                                          │
                                          [PC/노트북 브라우저] ── GET 폴링 ──┘  대시보드로 실시간 표시
```

- **서버**는 라즈베리파이5에서 돌아감 (포트: ToF=5001, CSI=5003, mmWave=5002)
- ESP32와 PC(대시보드)는 **모두 Pi와 같은 WiFi**에 있어야 함
- 현재 네트워크: WiFi **`2411 ServerRoom`**, Pi IP **`192.168.6.10`**
  > ⚠️ Pi IP는 네트워크가 바뀌거나 재부팅하면 달라짐. Pi에서 `hostname -I`로 확인.

### A. 라즈베리파이(서버) — 최초 1회 + 매번
```bash
cd ~/Documents/GitHub/Majubom
pip install -r requirements.txt      # 최초 1회 (flask, flask-cors)
cd server
python3 run_all.py                   # ToF+CSI+mmWave 한 번에 실행 (Ctrl+C로 종료)
```
- 실행되면 raw 데이터가 `server/majubom.db`의 `tof_readings` 테이블에 자동 저장됨

### B. PC/노트북 — 대시보드 보기
Pi와 같은 WiFi(`2411 ServerRoom`)에 접속한 뒤 브라우저에서:
```
http://192.168.6.10:5003/dashboard
```
> IP가 바뀌었으면 Pi의 `hostname -I` 값으로 접속. 화면 상단 호스트 입력칸은 비워두면 접속 주소를 자동으로 사용.

### C. ESP32(ToF 센서) — 아래 "펌웨어 업로드" 참고
`src/main.ino`의 WiFi/서버 IP를 현재 네트워크에 맞춰 수정 후 업로드.

---

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
`src/main.ino` 상단에서 수정 (현재 네트워크 기준 설정값):
```cpp
const char* WIFI_SSID  = "2411 ServerRoom";
const char* WIFI_PASS  = "D@lstn!0722";
const char* SERVER_URL = "http://192.168.6.10:5001/tof";
```
- `WIFI_SSID`/`WIFI_PASS`: **Pi가 붙어 있는 것과 같은 WiFi** (ESP32-S3는 2.4GHz만 지원)
- `SERVER_URL` IP: **서버(라즈베리파이) IP**로 지정 → Pi에서 `hostname -I`로 확인
  > 네트워크가 바뀌면 Pi IP도 바뀌므로 이 세 줄을 그때그때 맞춰야 함

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
[WiFi] IP: 192.168.6.xx
=== 측정 시작 ===
```

측정이 시작되면 각 프레임마다 서버 전송 결과가 찍힘:
```
[tof1] POST → HTTP 200      # 200이면 서버가 정상 수신 (대시보드에 값 뜸)
[tof2] POST → HTTP 200
```

### 값이 대시보드에 안 뜰 때 체크
| 시리얼 로그 | 원인 | 해결 |
|-------------|------|------|
| WiFi 연결 실패 | SSID/비번 오타, 5GHz AP | `WIFI_SSID`/`WIFI_PASS` 확인, 2.4GHz인지 확인 |
| `POST → HTTP -1` | 서버 IP 틀림 / 서버 미실행 | Pi에서 `hostname -I`로 IP 재확인 후 `SERVER_URL` 수정, `run_all.py` 실행 여부 확인 |
| `POST → HTTP 200`인데 화면 X | PC가 다른 WiFi | PC를 Pi와 같은 WiFi에 접속, 대시보드 주소의 IP 확인 |

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
