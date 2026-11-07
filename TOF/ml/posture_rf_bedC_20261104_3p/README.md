# 운영 자세 모델 — Bed C 세 사람 실측 RF (2026-11-04 저녁)

노트북에서 학습해 보낸 묶음 `TOF/ml/incoming/2026-11-04/bedc-s01retake-s02-s05-ratios-ZNXqhr/rf-real/`의 사본(체크섬 일치 확인).
`predict_posture.load_model()`이 이 폴더를 가장 먼저 읽는다(실패하면 `posture_cnn_bedC_20261104/` → `posture_model.joblib`).

- 학습: S01 재촬영·S02·S05 실측 8,106쌍(빈 침대 9세션 포함, `real_training_sessions.csv`). 합성 데이터 없음.
- 입력: 원시 거리 128개(tof1 발치 64 + tof2 머리맡 64, 실패 -1), ROI 마스킹 없음. 빈 침대 기준은 쓰지 않는다.
- 출력: 클래스 번호 0~5 = `preprocessing.json`의 labels(empty, supine, side_left, side_right, sitting, edge_sit).
- 사람 단위 교차검증(노트북 보고, `metrics.json`): 정확도 61.15%, macro-F1 0.5906 — S01 42.3%, S02 70.1%, S05 71.1%.
  같은 비교에서 CNN 특징 + XGBoost(합성 50%)는 46.84%(`comparison.md`). Pi에는 xgboost가 설치돼 있지 않다.
- Pi CPU 한 번 약 30ms(RF 200그루, 단일 스레드).
