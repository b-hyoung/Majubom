# 노트북 학습 프롬프트 — dataset_v4 + ROI 마스킹 (2026-11-06)

아래 블록 전체를 노트북의 AI 에이전트에게 그대로 붙여 넣는다.

---

너는 마주봄 프로젝트의 ToF 자세 분류 모델을 노트북에서 **ROI 마스킹을 적용해** 다시 학습·평가한다.
모든 사용자 응답은 한국어로 쓴다. 비밀번호·API 키는 출력하거나 파일에 쓰지 않는다.

## 1. 배경

- 센서: VL53L5CX 8×8 ToF 두 개(tof1 = 발 쪽, tof2 = 머리 쪽)
  - 바닥에서 약 1.5m 높이, 각도 미측정(2026-11-05)
  - 라벨 6종: `empty, supine, side_left, side_right, sitting, edge_sit` (이 순서)
- 어제(11-05) 노트북에서 dataset_v4를 ROI 없이 학습했다.
  - 결과: `minhyuk` 브랜치 커밋 c68c8c7 `docs/research-results-2026-11-05.md`
  - 묶음: `TOF/ml/incoming/2026-11-05/v4-150cm-6p-5seeds-6lE7WB/`
  - raw_rf 88.47±0.47%, cnn 78.30±1.27%
- 오늘(11-06) Pi에서 같은 분할·같은 하이퍼파라미터로 ROI 마스크 7가지를 비교했다.
  - 보고서: `docs/논문메모_ROI_20261106.md`
  - **기존 `TOF/ml/roi.py`의 `mask_offbed`(프레임마다 칸 방향과 실제 거리로 침대 폭 밖 칸을 -1로)가 7종 모델 모두를 올렸다**(평균 +1.33%p, 시드 3개).
  - 사용자 결정: ROI 마스킹을 넣는다.
  - Pi는 느려서 시드 2·3을 못 돌렸다. 이번 작업으로 노트북에서 **시드 5개 전체**를 다시 확인하고, 운영용 최종 모델을 만든다.

## 2. 코드와 데이터 가져오기

데이터·기준·ROI 모듈·Pi 실험 결과는 `main`에 있다. 학습 코드는 `minhyuk` 브랜치(`camera-pose/src/bed_pose/tof_learning/`)에 있다.

```bash
git fetch origin
git switch minhyuk && git pull --ff-only origin minhyuk
# main에서 필요한 파일만 작업 트리로 가져온다(main 전체를 병합하지 않는다)
git checkout origin/main -- \
  TOF/dataset_v4 \
  TOF/ml/roi.py \
  TOF/ml/incoming/2026-11-05 \
  TOF/ml/roi_experiment_20261106 \
  docs/논문메모_ROI_20261106.md
```

- 데이터: `TOF/dataset_v4/`(jsonl 111개 + `sessions.csv`, 약 44MB)
  - 형식과 구성은 `TOF/dataset_v4/LAPTOP_TRAIN_PROMPT.md` 3절을 그대로 따른다.
  - 정리된 값이다(상태 5·9 유효, 빈 칸은 3프레임까지 채움). dataset_v3와 섞지 않는다.
- 빈 침대 기준: **어제와 같은 파일**을 쓴다. 새로 만들지 않는다. 비교가 공정해야 한다.
  - 경로: `TOF/ml/incoming/2026-11-05/v4-150cm-6p-5seeds-6lE7WB/training_baseline.json`
  - sha256이 `5239cf`로 시작하는지 확인한다.
- ROI 모듈: `TOF/ml/roi.py`. 이 파일은 **수정하지 않는다.**
  - 예전 1.2m 설치 기하 값이 들어 있지만, Pi 실험은 이 값 그대로 이득을 확인했다.
  - 기하를 바꾸면 다시 비교해야 한다.

## 3. 학습 방법

- `train-session --roi-module TOF/ml/roi.py`를 쓴다. `session_roi.apply_roi`가 이미 있다.
  - 원시 거리와 빈 침대 기준에 같은 `mask_offbed`를 적용한다.
  - 3채널(거리·기준 차이·유효 칸)을 다시 계산한다.
  - 각 모델 폴더에 `roi.py`와 `roi_audit.json`을 남긴다.
- 모델 7종을 모두 돌린다:
  - `raw_rf`, `raw_xgboost`, `raw_lightgbm`
  - `cnn`, `cnn_rf`, `cnn_xgboost`, `cnn_lightgbm`
  - 원시 XGB·LGBM이 커밋된 `session_train.py`에 없으면 어제 노트북에서 쓴 코드와 같게 추가한다.
  - 하이퍼파라미터는 어제와 같다:
    - XGB `n_estimators=200, max_depth=4, tree_method='hist'`
    - LGBM `n_estimators=200, num_leaves=15, min_child_samples=5`
    - 역빈도 `sample_weight`
- 하이퍼파라미터·분할·조기 종료 규칙은 **어제 ROI 없는 실행과 완전히 같게** 둔다. 달라지는 것은 ROI 하나뿐이어야 한다.
  - 분할: 사람 단위 6겹(S01~S06). `없음` 빈 침대는 회차별로, 참가자 이름의 빈 침대는 그 참가자 시험 묶음에 넣는다.
  - 시험 데이터를 모델 선택·조기 종료에 쓰지 않는다. 합성 데이터는 쓰지 않는다.
- 시드 **42, 0, 1, 2, 3** 다섯 번 돌린다.
- 같은 조건의 ROI 없는 결과는 어제 실행을 다시 쓴다.
  - 확인용으로 ROI 없이 시드 42 한 번을 다시 돌린다. raw_rf가 어제 값과 맞는지 본다.
  - 다르면 원인을 먼저 찾는다.

## 4. 보고할 것 (논문용)

ROI 없음(어제) 대 ROI(오늘)를 나란히 보고한다.

1. 모델별 정확도·macro-F1: 평균 ± 표준편차, 최솟값~최댓값, 시드 5개
2. 같은 시드끼리의 차이(%p)와 이긴 시드 수(/5)
3. 4분류 정확도(빈 침대·누움 3종 묶음·앉음·걸터앉음). 같은 예측을 묶어서 다시 계산만 한다.
4. 자세별 재현율 6개, 참가자별 정확도(S01~S06), 혼동 행렬(참가자 합산)
5. ROI로 제외된 칸 수(`roi_audit.json`의 클래스별 통계)
6. Pi 3시드 결과(`TOF/ml/roi_experiment_20261106/summary.json`, `docs/논문메모_ROI_20261106.md`)와 시드 42·0·1이 맞는지 확인
7. 참고: S06(검정 옷) 제외 5인 표
8. 결론: ROI를 넣었을 때 좋아지는지/나빠지는지 한 줄

## 5. 결과 전달

- 노트북 출력은 새 폴더에 저장한다. 기존 결과 폴더를 덮어쓰지 않는다.
- Pi로 보낼 묶음: `pi@192.168.0.4:/home/pi/Documents/Github/Majubom/TOF/ml/incoming/2026-11-06/<묶음이름>/`
  - Pi 주소는 DHCP로 바뀔 수 있다. 안 되면 사용자에게 물어본다.
- 묶음 내용:
  - 최종 원시 RF: 전체 6인 + 빈 침대 전부에 ROI를 적용해 다시 학습한 것
    - 시드 42, `raw_rf.joblib`
    - scikit-learn 버전을 Pi(1.9.1)와 맞춘다
  - `roi.py`: 학습에 쓴 사본. sha256을 기록한다.
  - `preprocessing.json`:
    - `labels` 순서
    - 입력 정의: 원시 128 = tof1 64 + tof2 64, 실패 -1
    - **`roi: "TOF/ml/roi.py mask_offbed, 프레임마다 실제 거리로 적용, 제외 칸 -1"`**
    - 추론 전에 같은 ROI를 적용해야 한다는 문장
  - `training_baseline.json`(어제와 같은 파일)
  - `metrics.json`(위 4절 전부)
  - `roi_audit.json`, `real_training_sessions.csv`
  - `run_manifest.json`(시드·패키지 버전·입력 sha256·roi.py sha256)
  - `README.md`(한국어 요약), `SHA256SUMS`
  - 검증용 입력/확률: 어제 묶음의 `verification_input.npy` 같은 것. ROI 적용 전 원시 입력과 그때의 출력 확률을 넣는다. Pi에서 마스크 구현이 같은지 확인하는 데 쓴다.
- 논문용 결과 md는 `minhyuk` 브랜치에 어제처럼 `docs/research-results-2026-11-06.md`로 커밋·푸시해도 된다.
  - 데이터(`TOF/dataset_v4` 등)를 `minhyuk`에 다시 커밋하지는 않는다. 2절에서 가져온 파일은 커밋 전에 스테이징에서 뺀다.
- **운영 모델 교체는 하지 않는다.**
  - Pi의 `TOF/ml/predict_posture.py`(로드 순서와 입력 ROI 적용), 서버 재시작은 Pi에서 사용자가 결과를 보고 결정한다.
- 끝나면 사용자에게 한국어로 알린다:
  - 위 표, ROI가 좋아졌는지
  - S06 결과
  - 남은 한계: 각도 미측정, roi.py는 예전 기하, 6인, 검정 옷 1명, 정리된 입력
