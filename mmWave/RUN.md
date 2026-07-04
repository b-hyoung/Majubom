# mmWave 실행 가이드 (Raspberry Pi)

IWR6843(AOP) → `send_mmw.py`(TLV 파싱·보행지표·환자 특정) → `mmw_server.py`(baseline·z-score·알람).
코드는 순수 Python이라 **OS 무관**. Pi/Windows 차이는 **시리얼 포트 이름과 셋업뿐**(코드 수정 없음).

관련 문서: 예외/한계·튜닝 → [RISK_NOTES.md](RISK_NOTES.md)

---

## 1. 최초 1회 셋업

```bash
pip3 install pyserial numpy flask flask-cors requests
sudo usermod -a -G dialout $USER    # 시리얼 접근 권한 (재로그인 필요)
```

## 2. 포트 확인 (CP2105 → /dev/ttyUSB*)

IWR6843 AOP의 USB 브릿지는 **Silicon Labs CP2105(듀얼 UART)** → `cp210x` 드라이버로 **`/dev/ttyUSB0`, `/dev/ttyUSB1`** 두 개 생성.

```bash
dmesg | grep -i cp210x       # 어느 ttyUSB가 잡혔는지
ls -l /dev/ttyUSB*
```

- **Enhanced 인터페이스 = CLI(설정) 포트** — 보통 `ttyUSB0`
- **Standard 인터페이스 = DATA(스트림) 포트** — 보통 `ttyUSB1`
- 순서가 확실치 않으면 아래 실행 후 **cfg가 `ok`로 뜨고 프레임이 들어오면 맞는 것**. 안 되면 두 포트를 바꿔서 재시도.

> Windows 대응: `ttyUSB0/1` ↔ `COM5(Enhanced)/COM6(Standard)`

## 3. 실행

**서버** (터미널 1, 포트 5002):
```bash
python3 mmw_server.py
# 또는 전체 센서 서버 한 번에: python3 run_all.py
```

**송신부** (터미널 2, 센서가 USB로 붙은 Pi에서):
```bash
python3 send_mmw.py \
  --cli /dev/ttyUSB0 --data /dev/ttyUSB1 \
  --cfg AOP_bed_2m7_d15.cfg \
  --target room_01
```
- `--cfg` : 최초 부팅/설정 변경 시에만. 센서에 이미 cfg가 올라가 있으면 **생략**(생략하면 CLI 포트도 안 엶).
- `--target` : 침대(환자) 식별자. 테스트는 `--target test_bed` 로 실환자와 분리.

## 4. 센서 설정 (.cfg)

- [AOP_bed_2m7_d15.cfg](AOP_bed_2m7_d15.cfg) — 높이 2.7~3m, 아래 15° 틸트, 침대/방 커버.
  - `sensorPosition <높이> <방위틸트> <하향틸트>` — 실제 장착에 맞게 수정. (소수점 미적용 시 정수로)
  - `staticBoundaryBox / boundaryBox / presenceBoundaryBox` — `/mmw/viz`로 포인트 보고 방 크기에 맞춤.
- [AOP_diag_wideopen.cfg](AOP_diag_wideopen.cfg) — 진단용(좌표변환 없이 존 개방). "프레임은 오는데 target 0"일 때 원인 격리.

## 5. 침대존 캡처 (측정 도구)

새 환경에서 침대존 좌표를 실측:
```bash
python3 send_mmw.py --cli /dev/ttyUSB0 --data /dev/ttyUSB1 \
  --capture-zone 30 --capture-label bed
```
30초간 target x/y/z를 모아 **권장 존(p5~p95)** 을 출력 → `send_mmw.py`의 `BED_X/BED_Y`에 반영.

## 6. 모니터링

브라우저(`http://<Pi-IP>:5002`):
| 경로 | 내용 |
|---|---|
| `/` | 실시간 모니터(2초 새로고침) |
| `/mmw/viz` | 포인트 클라우드 시각화(존 좌표 읽기용) |
| `/mmw/latest?target=<id>` | 최신 판정 + baseline + z-score(JSON) |
| `/mmw/log?n=25` | 최근 결과 로그(JSON) |
| `POST /mmw/reset?target=<id>` | 해당 대상 baseline 초기화 |

송신부 콘솔 한 줄 예:
```
raw={...} | tid=8 aff=0.98 cand=1 lock=True walk=True str=0.83 n=2
```
`lock`=환자 특정 / `walk`=보행 반영 / `str`=경로 직진성 / `cand`=침대 후보 수.

## 7. 주요 튜너블 (send_mmw.py 상단)

| 상수 | 현재값 | 의미 |
|---|---|---|
| `BED_X, BED_Y` | (-0.9,0.4),(1.0,3.6) | 침대 footprint(매트리스 코어) — 환경마다 재캡처 |
| `WINDOW_SEC` | 6.0 | 보행 지표 창(좁은 방 6s, 넓으면 10s도 가능) |
| `AFFINITY_MIN` | 0.25 | 환자 특정 최소 소속도 |
| `AFFINITY_HORIZON` | 60.0 | 소속도 이력 길이(초) |
| `MERGE_DIST` | 0.7 | 근접 track 병합(split ghost 대응) |
| `STRAIGHT_MIN` | 0.5 | 경로 직진성 최소(회전 윈도우 배제) |
| `WALK_SPEED_MIN` / `WALK_DISP_MIN` | 0.20 / 0.30 | 보행 판정 |

서버측(`mmw_logic.py`): `HEIGHT_DROP_FALL`(낙상 절대임계), `TH_CAUTION/WARNING/CRITICAL`(total_abs 단계), `WARMUP_DAYS`(정식 알람 유예 — z-score 표시는 무관).

## 8. baseline 쌓기 (테스트)

- baseline은 `lock=True` + `walk=True`(속도·순이동·직진성 통과)인 윈도우마다 1개 누적.
- **환자 혼자거나, 침대 소속도로 특정된 상태에서 직선 보행**해야 쌓임.
- `n≥5`부터 z-score 계산. 안정적 baseline은 `n≈15~20`.
- 깨끗한 실험은 baseline 확정 후 `/mmw/reset`으로 초기화하고 다시 쌓기 권장.

---

## Windows(테스트) 차이 요약

- 포트: `--cli COM5 --data COM6` (Enhanced/Standard)
- 한글 콘솔 깨짐 방지: `PYTHONIOENCODING=utf-8` (Linux는 기본 UTF-8이라 불필요)
- 나머지 동일.
