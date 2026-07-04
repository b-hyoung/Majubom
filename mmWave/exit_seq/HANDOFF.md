# 마주봄 · mmWave 이탈 예측 — 작업 핸드오프

> 이 파일만 읽으면 이어서 작업 가능. 담당=박형석(CSI/mmWave). 작성 2026-07-04 밤.
> **목표**: mmWave로 **침대 이탈을 "미리 예측"**(감지 아님). ToF가 자동 라벨을 주고, mmWave 점군 시계열로 학습.

---

## 0. 한 줄 요약 (지금 어디까지 왔나)
- ✅ **파이프라인 완성**: mmWave 점군 30프레임 시퀀스 + ToF 라벨 자동 수집(`co_log.py`) → `seq_dataset.jsonl`
- ✅ **569 시퀀스 수집**(1명·1세션, 첫 검증용)
- ✅ **핵심 가설 검증**: "전이 이벤트 라벨 + 시간순(LSTM)"이 "정적 자세 라벨 + 뭉갬(RF)"보다 이탈 예측을 잘함 (F1 42 vs 13)
- ⚠️ **성능은 시작 단계**(F1 42, 이탈 36%만 잡음) → **데이터 대량 필요**(여러 사람·수십 이벤트)
- ⚠️ **USB 불안정**이 반복 블로커

---

## 1. 하드웨어 · 네트워크 (그대로 재현)
| 대상 | 값 |
|---|---|
| mmWave | IWR6843AOP, 이 맥에 **USB 직결**. CLI=`/dev/cu.usbserial-011D1AD60`, DATA=`/dev/cu.usbserial-011D1AD61`, baud 921600 |
| cfg | `mmWave/AOP_v_C_legs_safe.cfg` (sensorPosition 2m·틸트, 점군 TLV 1020 출력) |
| ToF | **파이 `192.168.6.10:5001`** (조우진, main 코드) · `GET /tof/latest` → `posture`, `GET /tof/presence` → `in_bed` |

**⚠️ USB 불안정**: DATA가 자주 멈추거나 `Device not configured`로 드롭됨.
- 복구 = **cfg 재전송**(`send_mmw.send_config(CLI, cfg)` → sensorStart). 간헐적이라 1~2회 재시도로 붙음.
- 수집 전 반드시 `python3 -c "from send_mmw import send_config; send_config('/dev/cu.usbserial-011D1AD60','AOP_v_C_legs_safe.cfg')"` 로 스트리밍 확인.
- **케이블 교체·맥 직결이 근본 해결**(아직 안 함).

---

## 2. ToF 라벨의 진실 (중요 — 헷갈리기 쉬움)
- ToF `posture` = **정지 자세**: `empty / supine(누움) / sitting(앉음) / side_left / side_right(돌아눕기=뒤척임)`
- **`posture`엔 "이탈(exit)"이 없음!** 이탈 = **`in_bed` 가 true→false 되는 것**(`/tof/presence`).
- 즉 **이탈 이벤트는 posture가 아니라 in_bed로 잡아야 함.** (오늘 co_log는 posture만 로깅 → 다음에 in_bed 추가 필요)
- ToF 자세모델 = 파이의 `predict_posture`(server/, `TOF/dataset/`로 학습). 우리는 이걸 HTTP로 읽어 **자동 라벨**로 씀 (사람 태깅 0).

---

## 3. 만든 스크립트 (mmWave/exit_seq/)
| 파일 | 역할 |
|---|---|
| **`co_log.py`** | mmWave 점군 30프레임 시퀀스(FPS 64점) + ToF `posture` 라벨 동시 수집 → `seq_dataset.jsonl` |
| `record_seq.py` | 시퀀스 수집기(FPS·frame_fixed 함수 제공, co_log가 import) |
| (부모 `mmWave/`) `posture_probe.py` | 점군(TLV 1020) 파싱 + 특징. `extract_pointcloud` 제공 |
| (부모) `send_mmw.py` | TLV 파싱(1020 점군, **1010 track = velX/Y/Z 있음**), `send_config`, `iter_frames` |

**데이터 포맷** (`seq_dataset.jsonl` 한 줄):
```json
{"ts":..., "tof_posture":"sitting", "tof_conf":0.9, "frames":30, "npts":64,
 "seq": [ [[x,y,z,doppler]×64] ×30 ] }
```

---

## 4. 오늘의 실험 결과 (정직하게)
### (a) 정적 자세 분류 — 높이가 답, 속도 무의미
- 이탈전조(sitting) vs 뒤척임(side): **높이/위치만 80.3% = 높이+속도 80.3%** (속도 추가해도 안 오름)
- 중요 특징 = z_mean·z_spread·x/y_spread (높이·위치). **doppler 속도는 top5 밖.**
- ⚠️ 이 수치는 **윈도우 겹침(stride 10)으로 CV 누수 → 낙관적**.

### (b) 왜 안 됐나 (진단, 데이터로 확인됨)
1. **뭉갬(aggregate)** 이라 시간순 안 씀
2. **정적 자세 라벨**이라 시간 신호 없음 (정지 자세는 높이가 이김)

### (c) 제대로 한 테스트 — 시간순+전이라벨 (누수 없음)
- 재라벨: `exit_imminent`(앞 ~3초 내 in_bed→empty) vs `stay`. 이탈 이벤트 17개, 임박 55 / stay 208.
- **시간 분할**(앞 70% train) → 누수 제거. 베이스라인(다수예측) 64.6%.

| 모델 | acc | 이탈 recall | F1 |
|---|---|---|---|
| RF (뭉갬) | 67% | **7%** | **13** |
| **LSTM (시간순)** | 65% | **36%** | **42** |

→ **시간순(LSTM)이 뭉갬(RF)보다 이탈 3~5배 잘 잡음.** "단적+정적자세라서 안 됐다" 진단이 데이터로 맞음.
→ **단 절대 성능 약함**(이탈 36%만, F1 42) — 1명·17이벤트·proxy 라벨이라 그럼.

---

## 5. 다음 할 일 (우선순위)
### ⭐ 1. `co_log.py` 업그레이드 (품질) — 코딩
- **`in_bed` 로깅 추가**: 매 폴링에 `GET /tof/presence` 의 `in_bed`도 저장 → **이탈 이벤트(T) 정식 라벨**
- **track velZ/velX/Y 추가**: `send_mmw`의 target list(TLV 1010)에서 track 속도를 뽑아 로깅 (지금은 점군 doppler만 — 노이즈. track 속도가 깨끗)
- → 그럼 "이탈 T 기준 T-N초 = 이탈임박" 정식 라벨 + 깨끗한 속도

### ⭐⭐ 2. 데이터 대량 수집 (제일 중요) — 노가다
- **이탈 이벤트 17개 → 100개+** (이탈 에피소드 많이 반복)
- **1명 → 3~5명+** (일반화 최대 열쇠, 1명은 과적합)
- **뒤척임(negative)도 균형 있게**
- 수집법: 사람이 침대에서 **① 눕→앉→이탈(리셋: 다시 눕기) ② 뒤척이다 도로 눕기** 를 각 에피소드 사이 2~3초 멈춤으로 반복. **ToF가 자동 라벨** → 사람 태깅 X.

### 3. 재테스트 (검증)
- 새 데이터로 다시 **시간순 LSTM** (per-frame 특징 시퀀스 → LSTM). **시간 분할·F1/recall로 평가**(accuracy는 함정, 불균형).
- 비교 baseline = **ToF-단독 예측**. mmWave가 그보다 **오탐(nuisance) 줄이나** 가 핵심 지표.
- 목표: F1 42 → 70+.

---

## 6. 실행 명령 (그대로)
```bash
cd MajuBom/mmWave/exit_seq

# (수집 전) mmWave 스트리밍 확인/복구
python3 -c "import sys; sys.path.insert(0,'..'); from send_mmw import send_config; send_config('/dev/cu.usbserial-011D1AD60','../AOP_v_C_legs_safe.cfg')"

# ToF 라벨 살아있나
curl -s http://192.168.6.10:5001/tof/latest | python3 -m json.tool | head

# 수집 (백그라운드로, 하나만!)
nohup python3 co_log.py > /tmp/colog.out 2>&1 &
#  ⚠️ 이전 co_log 프로세스 남아있으면 포트 충돌("multiple access") → ps로 확인 후 kill

# 분석 (예: scratch의 ts_test.py 참고 — 시간분할 LSTM vs RF)
```

---

## 7. 핵심 원칙 (계속 지킬 것)
- **역할 분담**: ToF=위치·이탈이벤트·**자동 라벨** / mmWave=속도·동역학·**전이 예측** / CSI=생체 개인화 가중
- **평가 = F1/recall + ToF-단독 대비 오탐 감소** (accuracy는 불균형이라 함정)
- **우리 데이터가 증명** — 논문/상용 통계는 "방향 근거"지 우리 근거 아님
- **오픈소스 = 도구**(RadHAR 읽기·MiliPoint PyTorch·PNHM 우리칩), 데이터·융합·결과가 우리 것. 출처/라이선스 표기.
- **검증 안 된 건 표시** — 예: mPCT-LSTM은 원문 못 봄(스니펫), IWR1443이라 우리 칩 아님.

---

## 8. 참고 (검증상태 포함)
**오픈소스**
- RadHAR (BSD-3) github.com/nesl/RadHAR — TI 점군 HAR, 복셀+CNN-BiLSTM. **입력형식이 우리랑 같음**(읽기용, 70GB·Keras라 채택X)
- MiliPoint (MIT) github.com/yizzfz/MiliPoint — PyTorch 점군 데이터/모델(채택·참고용)
- pymmw (MIT) github.com/m6c7l/pymmw — TI IWR TLV 파싱 참고

**논문 (우리 칩/전이)**
- PNHM (Sensors 2023, PMC10708869) — **IWR6843ISK(우리 계열)**, 30프레임 PointNet++, 서→앉 87%. ✅검증
- Sensor-Stack Limits (arXiv 2606.23534) — **IWR6843AOP(우리 칩)** in-bed 한계: 4자세 0.674, 좌/우·prone 어려움, "희소 CFAR 점군이 벽" ✅검증
- mPCT-LSTM (2025) — 희소점 경량 PCT+LSTM. ⚠️**원문 못 봄(403)**, IWR1443이라 **우리 칩 아님**. 방법만 참고

**상용/근거**
- Vayyar: **누움→앉음→가장자리→이탈 단계** + 도플러(속도) 사용 → 우리 방향 검증
- VSTAlert: 이탈 30~65초 전 예측 (스니펫)
- 이탈 예측 오탐 현실: 좋은 시스템도 nuisance ~31% → **오탐 줄이기가 승부처**
- 전조 신호 = **높이 상승 + 가장자리 이동 + 앉기** (공통)

---

## 9. 요약 — 내일 바로 시작할 것
1. `co_log.py`에 **in_bed + track velZ** 추가 (품질)
2. **여러 사람 × 이탈/뒤척임 반복** 대량 수집 (양) ← 제일 중요
3. **시간순 LSTM 재학습 → F1/recall** 로 평가, ToF-단독 대비 오탐 비교
4. USB 안정화(케이블) 병행

> 핵심 교훈: **"이탈 예측은 시간순 + 전이 이벤트 라벨"로 가야 한다**(정적 자세·뭉갬은 안 됨). 이미 데이터로 확인됨(LSTM F1 42 > RF 13). 이제 **데이터를 늘려 성능을 올리는 단계.**
