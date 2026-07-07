# ToF 8×8 실험 폴더 (프로덕션과 완전 분리)

> `TOF/`, `server/`는 전혀 건드리지 않았음. 여기 안에서만 테스트.

## 목적
1. `main.ino`를 8×8(64존)로 바꾼 보드 **1개**가 I2C 버스 wedge 없이 도는지 확인
2. WiFi POST로 64존짜리 payload가 서버까지 문제없이 오는지 확인

## 구성
```
test_8x8/
  firmware/
    platformio.ini   — TOF/src/platformio.ini 복사(lib_extra_dirs 경로만 확인 필요)
    src/main.ino      — TOF/src/main.ino 복사 + 8x8/64존/테스트서버 URL만 변경
  server/
    test_tof_server.py — DB 없는 메모리 전용 Flask 서버, 포트 5011
```

## 실행 순서

### 1) 테스트 서버 — 이미 실행 중
이 PC(Wi-Fi IP `192.168.6.20`, "2411 ServerRoom"망 — Pi와 같은 대역)에서 이미 떠 있음:
```bash
cd test_8x8/server
python test_tof_server.py
```
→ 같은 WiFi에 있는 아무 기기에서나 `http://192.168.6.20:5011/dashboard` 접속하면 실시간 그리드 확인 가능
→ PC가 재부팅되거나 Wi-Fi가 바뀌면 `ipconfig`로 IP 재확인 후 아래 `SERVER_URL`도 같이 갱신 필요

### 2) 테스트 보드 펌웨어 업로드
- `firmware/platformio.ini`의 `lib_extra_dirs`를 **실제 빌드할 PC의 Arduino libraries 경로**로 수정
  (VL53L5CX 아두이노 라이브러리가 있는 폴더 — 지금 값은 원작성자 PC 기준이라 그대로 쓰면 빌드 실패)
- `firmware/src/main.ino`의 `SERVER_URL`은 이미 `http://192.168.6.20:5011/tof`로 맞춰둠(위 서버 기준)
- 테스트 보드가 "2411 ServerRoom" WiFi에 붙어있어야 이 PC와 통신 가능
- 테스트할 보드 **1개만** 이 펌웨어로 업로드 (나머지 1개는 프로덕션 펌웨어 그대로 유지)

### 3) 확인할 것
- **시리얼 모니터**: `ranging 시작 (8x8 TEST)` 이후 로그가 계속 찍히는지 (멈추면 버스 wedge)
- **대시보드**: 8×8(64칸) 그리드가 끊김 없이 갱신되는지

## 주의
- 이 서버는 SQLite를 안 씀(메모리만) — 재시작하면 데이터 날아감. 테스트 전용이라 문제없음.
- 프로덕션 서버(`server/tof_server.py`, 포트 5001)와 `server/majubom.db`는 전혀 건드리지 않음.
- 테스트 끝나면 이 폴더(`test_8x8/`)는 지워도 프로덕션에 영향 없음.
