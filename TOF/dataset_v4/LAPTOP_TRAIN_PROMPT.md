# 노트북 학습 프롬프트 — dataset_v4 (2026-11-05 녹화)

아래 블록 전체를 노트북의 AI 에이전트에게 그대로 붙여 넣는다.

---

너는 마주봄 프로젝트의 ToF 자세 분류 모델을 노트북에서 다시 학습·평가한다.
모든 사용자 응답은 한국어로 쓴다. 비밀번호·API 키는 출력하거나 파일에 쓰지 않는다.

## 1. 배경

- 센서: VL53L5CX 8×8 ToF 두 개(tof1 = 발 쪽, tof2 = 머리 쪽). 삼각대에 세워 침대를 길이 방향으로 마주 본다.
- 2026-11-05에 두 센서 높이를 **바닥에서 약 1.5m**로 올렸다(이전 약 1.25m·하향 약 48°). 각도는 다시 재지 않았다.
  → 예전 녹화(dataset_v3)와 시야가 다르므로 **dataset_v3와 섞지 않는다.**
- 높이를 올린 이유: 침대 중앙 가장자리 걸터앉음이 예전에는 2~4칸(체구 작은 참가자는 0칸)만 잡혔다. 이번 녹화에서는 S01~S05 기준 13~21칸이 잡힌다.
- 라벨 6종: `empty, supine, side_left, side_right, sitting, edge_sit` (이 순서).

## 2. 데이터 위치와 가져오기

- Pi: `pi@192.168.0.4` (DHCP라 바뀔 수 있다. 안 되면 사용자에게 물어본다)
- 저장소 루트: `/home/pi/Documents/Github/Majubom`
- 데이터: `TOF/dataset_v4/` (약 44MB, jsonl 111개 + `sessions.csv`)

Pi CPU는 느리고 xgboost·lightgbm·torch 학습 환경이 없다. **학습은 노트북에서 한다.** 데이터만 복사해 온다.

```bash
rsync -av pi@192.168.0.4:/home/pi/Documents/Github/Majubom/TOF/dataset_v4/ ./dataset_v4/
```

Pi의 원본 파일은 수정·삭제·덮어쓰기하지 않는다.

## 3. 데이터 형식과 구성

- `sessions.csv` 열: `session,started_at,label,subject,cond,rep,sec,tof1_frames,tof2_frames,file`
- jsonl 한 줄 = 한 센서의 한 프레임: `t`(Pi 수신 시각, ISO), `sensor`, `resolution`, `distances_mm`(64칸, 실패 -1), `targets`, `label`, `subject`, `cond`, `rep`, `session`
- **중요:** dataset_v4의 `distances_mm`는 운영 서버가 정리한 값이다(`server/tof_server.py clean_distances`).
  - 센서 상태 5와 9를 유효로 본다.
  - 빈 칸은 직전 유효값으로 최대 3프레임 채운다.
  - dataset_v3(펌웨어 원본, 상태 5만 유효)와 입력 분포가 다르다. 대신 운영 입력과는 같다.
- 세션 구성(111개):
  - 빈 침대 21개: `subject=없음` r1~r3, S01~S06 각 r1~r3. 모두 실제로 따로 녹화했다(복사본 아님).
  - 자세 90개: S01~S06 각 15개. 누움 3종 × (가운데·왼쪽가장자리·오른쪽가장자리) + 앉음 × (가운데·머리쪽·발쪽) + 걸터앉음 × (보이는쪽_머리가까이·가운데·발가까이). 각 약 30초.
  - 왼쪽·오른쪽은 누운 사람 본인 기준이다. 위치(`cond`)는 학습 라벨이 아니다.
- 참가자:
  - S06은 키가 작고 체구가 작다. 녹화 때 **검정 옷**을 입었다. 측정 실패 칸이 센서당 평균 약 8~11개에서 23~27개로 늘었다(940nm 적외선 흡수로 보임).
  - S06의 앉음 가운데와 걸터앉음 가운데는 '빈 침대보다 100mm 넘게 가까운 칸'이 0~2칸이다.
  - 사용자 결정: 검정 옷을 별도 조건으로 나누지 않고 S06을 그대로 학습·평가에 포함한다. 다만 결과 해석에는 이 사실을 적는다.
- 사람이 센서에 가까운 자세(발 쪽 앉음·걸터앉음 등)에서는 그 센서의 전송이 약 4Hz로 떨어진다(평소 약 10Hz). 세션마다 최소 약 110프레임이다.

## 4. 학습 코드

`minhyuk` 브랜치의 `camera-pose/src/bed_pose/tof_learning/`를 쓴다.
- `session_data.py`: tof1·tof2를 250ms 안에서 짝짓는다. 앞뒤 1초를 버린다. 3채널 입력을 만든다. 사람 단위 분할을 한다.
- `session_train.py`: 학습 코드.

필요한 수정:
1. **빈 침대 기준(training_baseline.json)**
   - dataset_v4의 `subject=없음` 빈 침대 3세션에서 센서별·칸별 중앙값으로 만든다.
   - 유효 비율 60% 미만인 칸은 -1이다.
   - 형식: `{"tof1": [64], "tof2": [64]}`
   - 이전 기준(dataset_v3용)은 쓰지 않는다.
2. **원시 거리 모델 두 개 추가**
   - 지금 `session_train.py`는 원시 거리 128개를 RF(`raw_rf`)로만 학습한다. XGBoost·LightGBM은 CNN 특징으로만 돌린다.
   - `raw_xgboost`, `raw_lightgbm`을 추가한다. 하이퍼파라미터는 CNN 쪽과 같게 한다(XGB `n_estimators=200, max_depth=4, tree_method='hist'`, LGBM `n_estimators=200, num_leaves=15, min_child_samples=5`). 역빈도 `sample_weight`를 쓴다.
3. **시드 반복**
   - 같은 입력에서도 RF는 시드에 따라 처음 보는 사람 정확도가 크게 흔들렸다. dataset_v3 3인에서 S01 31~46%, 전체 55.6~60.6%였다.
   - 시드 **42, 0, 1, 2, 3** 다섯 번 돌린다. 평균 ± 표준편차와 최솟값~최댓값을 보고한다. 한 번 잘 나온 값만 쓰지 않는다.

## 5. 평가 (논문용)

- 분할: **사람 단위 교차검증(leave-one-subject-out)**, 6겹(S01~S06).
  - `subject=없음` 빈 침대는 기존 `outer_folds` 규칙대로 회차별로 나눈다.
  - 각 참가자 이름의 빈 침대는 그 참가자의 시험 묶음에 들어간다.
- 시험 데이터를 모델 선택·조기 종료에 쓰지 않는다. 합성 데이터는 이번에 쓰지 않는다.
- 비교 모델:
  - `raw_rf`, `raw_xgboost`, `raw_lightgbm`
  - `cnn`, `cnn_rf`, `cnn_xgboost`, `cnn_lightgbm`
- 보고할 것:
  1. 모델별 정확도·macro-F1 (평균 ± 표준편차, 시드 5개)
  2. 모델별 자세 재현율 6개
  3. 참가자별 정확도(S01~S06)
  4. 혼동 행렬(참가자 합산)
  5. 누움 3종을 하나로 묶은 4분류(빈 침대·누움·앉음·걸터앉음) 정확도. 같은 예측을 묶어서 다시 계산만 한다. 다시 학습하지 않는다.
  6. 참고 비교: S06을 뺀 5인 결과를 별도 표로 둔다. 검정 옷 영향 해석용이며 주 결과는 6인이다.
  7. Pi CPU 추론 시간은 노트북에서 재지 않는다. Pi에서 따로 잰다.
- 비교 기준(dataset_v3, 1.25m 설치, 3인, Pi에서 같은 방식으로 재현):
  - 원시 RF 57.88 ± 1.63%, 원시 XGB 53.98%, 원시 LGBM 43.62%
  - CNN 특징 계열 39~43%
  - 설치·인원·전처리가 모두 달라 직접 비교가 아니라 참고값이다.

## 6. 결과 전달

- 노트북 출력은 새 폴더에 저장한다. 기존 결과 폴더를 덮어쓰지 않는다.
- Pi로 보낼 묶음: `pi@192.168.0.4:/home/pi/Documents/Github/Majubom/TOF/ml/incoming/2026-11-05/<묶음이름>/`
  - 최종 원시 RF: 전체 6인 + 빈 침대 전부로 다시 학습한 것. 시드 42, `raw_rf.joblib`. scikit-learn 버전을 Pi(1.9.1)와 맞춘다.
  - `preprocessing.json`: `labels` 순서와 입력 정의(원시 128 = tof1 64 + tof2 64, 실패 -1, ROI 없음)
  - `training_baseline.json`, `metrics.json`(위 평가 전부), `real_training_sessions.csv`, `run_manifest.json`(시드·패키지 버전·입력 sha256), `README.md`(한국어 요약), `SHA256SUMS`
- **운영 모델 교체는 하지 않는다.** Pi의 `TOF/ml/predict_posture.py` 로드 순서 변경과 서버 재시작은 사용자가 결과를 보고 결정한다.
- 끝나면 사용자에게 한국어로 알린다:
  - 위 표들, 이전 대비 무엇이 좋아지고 나빠졌는지
  - S06의 결과
  - 남은 한계: 각도 미측정, 6인, 검정 옷 1명, 정리된 입력
