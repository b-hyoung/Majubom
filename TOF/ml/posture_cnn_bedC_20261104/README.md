# S01+S02 ToF 학습 모델 · 2026-11-04

비마스킹 실험의 최종 모델 묶음이다. 기본 추론 모델은 CNN 특징 + Random Forest다. 두 사람 전체로 최종 재학습했으며, metrics.json의 수치는 사람 단위 외부 시험 예측이다. 최종 통합 모델의 학습 자료 정확도를 시험 수치로 사용하지 않았다.

## 모델과 평가

- `cnn_cpu.pt`는 CPU TorchScript다. `forward`는 6개 클래스 logits, `features`는 64차원 벡터를 반환한다.
- `cnn_rf.joblib`, `cnn_xgboost.joblib`, `cnn_lightgbm.joblib`은 CNN 특징을 입력받는다. XGBoost 원본 JSON도 포함했다.
- `raw_rf.joblib`은 원시 거리 128개를 입력받는 비교 모델이다.
- `metrics.json`에 S02 학습 → S01 시험과 S01 학습 → S02 시험의 정확도, macro-F1, 재현율, 혼동 행렬 및 합산 결과가 있다.
- `paper_record.md`는 실제 설정과 결과를 담고 있다. Pi CPU 추론 시간은 미측정이다.
- `training_sessions.csv`의 37개 세션에서 5,415쌍을 사용했다. S01 2,712쌍, S02 2,703쌍이다. S01의 재촬영 1개를 포함한다.
- `session_metadata.json`은 빈 침대의 촬영 묶음 귀속이다. 같은 세션을 학습·시험에 중복 사용하지 않도록 분리했다.
- `split_audit.json`에 원본 세션 해시와 묶음별 표본 ID가 있다. `run_manifest.json`에 seed와 라이브러리 버전이 있다.

## 입력 및 전처리

센서 순서는 tof1=발치, tof2=머리맡이다. 각 센서의 distances_mm은 원래 행 우선 64칸 순서를 유지한다. CNN 입력 모양은 [B, 2, 3, 8, 8]이다. 센서별 세 채널 순서는 다음과 같다.

1. 거리 / 4000. 유한하고 0보다 크며 4000mm 이하인 칸만 유효하다. 무효 칸은 0이다.
2. (빈 침대 기준 거리 − 현재 거리) / 4000. 차이는 −4000~4000mm로 제한한다. 기준과 현재 값이 모두 유효한 칸에서만 계산하고 나머지는 0이다.
3. 현재 거리의 유효 칸 마스크. 유효하면 1, 나머지는 0이다.

거리의 −1, 0, null, 비유한 값은 무효다. 원시 거리 RF에서는 무효를 −1로 보존한다. RF는 tof1 64개 뒤에 tof2 64개를 이어 붙인다. 원시 RF의 4000mm 초과 양수는 기존 학습과 같이 원래 값을 유지한다. ROI 공간 마스킹과 좌우 반전 증강은 적용하지 않았다.

기준 파일은 함께 복사한 `training_baseline.json`이다. 학습 때 복사했던 Pi `server/tof_baseline.json`과 동일한 내용이며 SHA-256은 preprocessing.json에 기록했다. 전처리 JSON에도 실제 기준 배열을 포함했다.

클래스 인덱스 순서는 `empty, supine, side_left, side_right, sitting, edge_sit`이다. 카메라 판정은 ToF 값 교정이나 학습 라벨에 사용하지 않았다.

## 시간 짝짓기

학습은 센서 수신 시각을 정렬하고 tof1 시각에 가장 가까운 tof2를 250ms 이내에서 한 번씩만 짝지었다. 한 프레임을 여러 쌍에 재사용하지 않았다. 세션 전체의 첫 수신 시각+1초부터 마지막 수신 시각−1초까지를 유지하고 두 시각이 모두 그 구간 안인 쌍만 사용했다.

추론 예제는 한 쌍의 최신 프레임을 입력받아 250ms 시차를 검사한다. URL 입력은 두 센서 모두 500ms 이내의 수신인지 추가 검사한다. 연속 추론을 운영 서버에 연결할 때는 호출자가 센서별 received_at을 추적해 같은 프레임의 재사용을 막고 1:1 짝짓기 큐를 유지해야 한다.

## 단일 프레임 쌍 CPU 실행

```bash
cd /home/pi/Documents/Github/Majubom/TOF/ml/incoming/2026-11-04
python3 infer.py --model cnn_rf --latest-url http://127.0.0.1:5001/tof/latest
```

`--model`은 `raw_rf`, `cnn`, `cnn_rf`, `cnn_xgboost`, `cnn_lightgbm` 중 선택한다. 파일 입력은 `--input frame_pair.json`을 사용한다. JSON 최상위에 tof1과 tof2가 있어야 하고, 각 객체는 distances_mm 64개와 ISO received_at을 가져야 한다. 날짜에 시간대가 없으면 한국 표준시로 해석한다.

이 예제는 매 실행 시 모델을 읽는 단일 쌍 확인용 코드다. 연속 서비스에서는 모델을 한 번 로드해 재사용해야 한다. 점수는 모델 출력값이며 현장 정확도로 해석하지 않는다.

## 실행 의존성

학습 환경은 Python 3.12, torch 2.6.0+cu124, numpy 2.5.4, scikit-learn 1.9.1, xgboost 3.4.1, lightgbm 4.7.0, joblib 1.6.0이다. Pi에서는 ARM64 CPU용 PyTorch와 호환되는 Python·라이브러리가 필요하다. joblib 모델은 학습에 사용한 라이브러리 버전을 맞춰야 한다. CUDA는 추론 코드에서 사용하지 않는다.

노트북 CPU에서 다섯 모델의 내보내기 및 추론 결과를 대조했다. Pi에서의 실행 호환성과 처리 시간은 별도 확인이 필요하다. 운영 서버 적용은 담당자가 모델·전처리·클래스 순서를 함께 확인한 후 진행한다.
