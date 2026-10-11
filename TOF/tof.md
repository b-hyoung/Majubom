# VL53L5CX ToF 센서 운용 가이드

## 현재 연결(2026-10-06)

ESP32-S3 한 보드당 VL53L5CX 하나, 총 두 보드(tof1/tof2), 8×8 거리 64개.
센서는 둘 다 바닥에서 1.20m, 수평에서 아래로 tof1(발) 50°·tof2(머리) 30° 설치했다(2026-10-08; 이전 20°는 매트리스 커버 부족).
ESP32는 공유기 majubom의 2.4GHz Wi-Fi, Pi는 동일 LAN의 유선 연결이다.
Pi 현재 IP 192.168.0.4, 수신 주소 `http://192.168.0.4:5001/tof`.

기존 간호사 화면 :5001/dashboard와 추가 로우데이터 화면 :5012를 함께 사용한다.
서버가 기존 저장/분석을 수행하고 로우데이터 화면으로 최신 프레임만 비동기 복사한다.
자세한 실행/저장 구분은 [SENSOR_DIAGNOSTICS.md](../docs/SENSOR_DIAGNOSTICS.md).

## 배선(두 보드 동일)

| VL53L5CX | ESP32-S3 |
|---|---|
| VIN/GND | 3V3/GND |
| SDA/SCL | GPIO 8 / GPIO 9 |
| LPN | GPIO 4 |
| INT | 미연결 |

## 펌웨어 설정과 업로드

`src/secrets.example.h`를 `src/secrets.h`로 복사해 실제 2.4GHz 비밀번호를 입력한다.
MAJUBOM_SENSOR_NAME은 보드별 tof1/tof2, MAJUBOM_WIFI_SSID는 majubom,
MAJUBOM_SERVER_URL은 현재 Pi IP의 :5001/tof로 설정한다. secrets.h는 Git 제외다.
Pi 주소는 DHCP로 바뀔 수 있으므로 공유기에서 예약하거나 업로드 전 다시 확인한다.

```bash
# 프로젝트 루트에서
cd TOF/src
../../.venv/bin/pio run
../../.venv/bin/pio run -t upload --upload-port <ESP32-USB-포트>
../../.venv/bin/pio device monitor --port <ESP32-USB-포트> --baud 115200
```

현재 펌웨어 컴파일은 검증했으며 비밀번호 입력과 실제 업로드는 아직 수행하지 않았다.
최초 Wi-Fi 연결은 10초 후 재시도한다. raw_distances_mm와 target_status를 함께 보내고,
distances_mm의 유효 상태(status 5) 필터는 유지한다.
상하/좌우 방향은 로우데이터 화면에서 기준 물체를 움직여 확인한 뒤 결정한다.
설치 각도만으로 센서 PCB의 롤 방향을 알 수 없어 자동 반전하지 않는다.

## 정상 동작 확인

시리얼에 `Single Sensor Boot`, 센서 초기화 성공, Wi-Fi IP, `POST → HTTP 200`이 표시되고
서버 `/tof/latest`에서 해당 ID 수신 시각이 갱신되어야 한다.
둘 다 ID가 같으면 다른 센서 프레임을 덮어쓰므로 두 보드를 구분해 업로드한다.
Wi-Fi가 연결돼도 서버 주소/포트/LAN 격리가 잘못되면 POST가 실패한다.
Pi가 유선인 것은 문제없고, ESP32-S3 연결 대상 Wi-Fi는 2.4GHz여야 한다.

과거 듀얼센서 버스/4×4 문제 해결 기록은 TROUBLESHOOTING.md에 있으며 현재 배선 가이드와 구분한다.
