# Flask 서버 실행 가이드

## 환경 정보
- Python: `C:\Users\woojin\AppData\Local\Programs\Python\Python311\python.exe`
- 포트: **5001**
- 서버 파일: `server/tof_server.py`

---

## 최초 1회 — 패키지 설치

```powershell
C:\Users\woojin\AppData\Local\Programs\Python\Python311\python.exe -m pip install -r requirements.txt
```

---

## 서버 실행

```powershell
C:\Users\woojin\AppData\Local\Programs\Python\Python311\python.exe server\tof_server.py
```

실행 시 출력:
```
VL53L5CX ToF 서버 시작 → http://0.0.0.0:5001
 * Running on http://127.0.0.1:5001
 * Running on http://192.168.0.48:5001
```

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/tof` | ESP32에서 센서 데이터 수신 |
| `GET`  | `/` | 실시간 대시보드 (1초 자동 새로고침) |
| `GET`  | `/tof/latest` | 두 센서 최신 데이터 JSON |
| `GET`  | `/tof/log` | 최근 20건 수신 로그 JSON |

---

## 대시보드 접속

브라우저에서:
```
http://localhost:5001
```
또는 같은 네트워크의 다른 기기에서:
```
http://192.168.0.48:5001
```
> IP는 실행 시 터미널에 출력되는 주소 확인

---

## 수신 데이터 형식 (ESP32 → 서버)

```json
{
  "sensor": "tof1",
  "resolution": "4x4",
  "distances_mm": [234, 456, 123, -1, ...],
  "targets": [1, 1, 1, 0, ...]
}
```
- `distances_mm`: 16개 값, 감지 실패 존은 `-1`
- `sensor`: `"tof1"` 또는 `"tof2"`
