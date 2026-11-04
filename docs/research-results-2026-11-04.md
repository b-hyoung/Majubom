# 오늘 연구 결과! · 2026-11-04

Bed C의 새 S01 촬영분과 S02·S05 실측 자료로 2D CNN 및 트리 분류기를 다시 학습했다. S03syn·S04syn은 최종 모델의 별도 합성 시험에만 사용했다. 카메라 판정은 학습 정답이나 ToF 값 교정에 사용하지 않았다.

## 이번 학습과 평가

- 실제 참여자: S01·S02·S05, 3명. 54세션·8,106프레임 쌍. 이전 S01 대신 재촬영 18세션·2,701쌍을 선택했고 원본 파일은 보존했다.
- S02는 18세션·2,703쌍, S05는 18세션·2,702쌍이다. 각 회차는 구간 앞뒤 1초를 제외한 유효 프레임 쌍 150개 이상이다.
- 학습: 실제 자료만 사용. 세 사람 단위 교차검증의 각 묶음에서 CNN을 새로 학습했다. 내부 검증으로 epoch를 선택하고 바깥 시험 사람은 학습·epoch 선택에서 제외했다.
- 별도 시험: 기존 합성 v1과 몸 후보만 변형한 v2 모두 평가했다. 최신 수정본 v2를 주 결과로 표시한다. 실제 새 참여자 S03·S04 촬영 결과가 아니다.
- 이번 입력은 ROI 마스킹을 적용하지 않았다. 가로 반전 증강도 적용하지 않았다. 센서별 반전 대응 검증이 확인되지 않아 증강을 완료했다고 기록하지 않는다.
- 학습 GPU: NVIDIA GeForce RTX 4060 Laptop GPU. GPU 학습 후 내보낸 TorchScript CNN과 트리를 노트북 CPU에서 실제 불러와 합성 시험을 수행했다. Pi 추론 시간은 측정하지 않았다.

## S03syn·S04syn 별도 시험: 수정본 v2

합성 자세 4,198쌍만 평가한 표다. 빈 침대는 없으므로 macro-F1은 존재하는 다섯 자세 기준이다. 클래스 순서는 아래 전체 기록을 따른다.

| 모델 | S03syn 정확도 | S04syn 정확도 | 합성 합산 정확도 | 합성 합산 macro-F1 |
|---|---:|---:|---:|---:|
| RF 원시 | 62.42% | 100.00% | 82.59% | 0.8340 |
| 2D CNN | 46.68% | 97.38% | 73.89% | 0.7424 |
| CNN 특징 + RF | 62.98% | 99.87% | 82.78% | 0.8358 |
| CNN 특징 + XGBoost | 55.89% | 99.96% | 79.54% | 0.8047 |
| CNN 특징 + LightGBM | 52.96% | 99.87% | 78.13% | 0.7931 |

### 공통 빈 침대를 포함한 여섯 클래스 비교

S05의 실제 빈 침대 3세션·450쌍을 한 번만 더해 총 4,648쌍으로 평가했다. 이 빈 침대는 학습에도 사용된 자료이므로 독립 시험이 아니다.

| 모델 | v1 정확도 | v1 macro-F1 | v2 정확도 | v2 macro-F1 |
|---|---:|---:|---:|---:|
| RF 원시 | 62.69% | 0.5999 | 84.27% | 0.8426 |
| 2D CNN | 62.28% | 0.6434 | 76.42% | 0.7733 |
| CNN 특징 + RF | 66.89% | 0.6822 | 84.44% | 0.8521 |
| CNN 특징 + XGBoost | 62.99% | 0.6449 | 81.52% | 0.8290 |
| CNN 특징 + LightGBM | 62.26% | 0.6373 | 80.25% | 0.8180 |

### 실제 사람 단위 검증

위 합성 시험과 별개로 실제 세 명을 한 명씩 제외하여 검증한 결과다. 새로운 사람에 대한 근거로는 이 결과를 우선한다.

| 모델 | 정확도 | macro-F1 |
|---|---:|---:|
| RF 원시 | 61.15% | 0.5906 |
| 2D CNN | 38.86% | 0.3517 |
| CNN 특징 + RF | 39.79% | 0.3598 |
| CNN 특징 + XGBoost | 41.38% | 0.3934 |
| CNN 특징 + LightGBM | 42.89% | 0.4017 |

## 해석과 한계

이번 실제 사람 단위 검증에서는 RF 원시가 CNN 계열보다 높았다. 딥러닝이나 하이브리드가 개선됐다고 결론 내릴 수 없다. S03syn은 이전 S01 촬영에서, S04syn은 S02에서 파생됐다. S04syn의 원본 세션은 학습에 포함되고, S03syn은 새 S01 촬영분과 참여자가 같다. 따라서 합성 시험은 변형에 대한 재현 평가이며 새로운 체형에 대한 독립 정확도가 아니다.

수정 합성 자료의 센서 프레임 중 24.54%는 원본과 완전히 동일하다. 빈 침대 재사용과 배경·결측 보존도 높은 합성 점수에 영향을 줄 수 있다. 실제 참여자가 세 명이고 회차별 위치·신호 분포가 다르므로 원인 확정에는 자료가 부족하다. 다음 실험은 추가 실제 참여자 및 다른 날 촬영을 분리하여 검증하고, 센서 방향·baseline 시점·라벨 구간과 사람별 혼동을 확인해야 한다.

## 재현 정보와 산출물

- 최종 모델: `camera-pose/outputs/2026-11-04/S01retake-S02-S05-real/final/` (`cnn_cpu.ts`, `raw_rf.joblib`, `cnn_rf.joblib`, `cnn_xgboost.json`, `cnn_lightgbm.joblib`, `preprocessing.json`).
- 실제 검증: 같은 실행 폴더의 `metrics.json`, `split_audit.json`, `comparison.csv`, `run_manifest.json`.
- 합성 검증: 같은 실행 폴더의 `synthetic_test_metrics.json`에 v1/v2·그룹별 정확도·macro-F1·재현율·혼동 행렬.
- 선택한 54세션 목록: `camera-pose/outputs/2026-11-04/dataset-S01retake-S02-S05/sessions.csv`. 빈 침대 귀속은 같은 폴더 `session_metadata.json`.
- 비교 조건을 유지하기 위해 기존 로컬 사본 `pi-s01-s02/tof_baseline.json`을 고정했다. Pi의 현재 baseline을 덮어쓰지 않았다. baseline 시점과 새 촬영의 차이는 후속 점검 항목이다.
- baseline SHA-256: `23f2e8acddadd3111c1b87ad211af8d8237ea6ff98d4b69de1fa6e8af6cacead`.
- 입력 순서: 발치 tof1, 머리맡 tof2. 센서마다 거리/4000, (빈 침대−현재 거리)/4000, 유효 마스크의 3채널 8×8이다. 0 초과 4000mm 이하만 유효하며 `-1`·비유한 값은 거리·마스크 0, 기준 또는 현재가 무효인 차이 채널도 0이다.
- 두 센서는 수신 시각 차이 250ms 이내에서 중복 없이 1:1로 짝지으며 세션 앞뒤 1초를 제외한다. 클래스 순서: `empty, supine, side_left, side_right, sitting, edge_sit`.
- 원시 데이터·모델은 로컬 보관하며 GitHub에는 이 결과 문서만 올린다. Pi 운영 모델·baseline·DB는 변경하지 않았다.

## 합성 시험 상세: v2, 빈 침대 포함

혼동 행렬은 행=정답, 열=예측이다. 순서: `empty, supine, side_left, side_right, sitting, edge_sit`. 정확도·재현율은 0–1 척도다.

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1.0000 | 1.0000 | 0.7863 | 0.8536 | 0.5181 | 1.0000 |
| 2D CNN | 1.0000 | 0.8574 | 0.8653 | 0.6684 | 0.4777 | 0.8566 |
| CNN 특징 + RF | 1.0000 | 0.8774 | 0.8407 | 0.7409 | 0.7702 | 0.9118 |
| CNN 특징 + XGBoost | 1.0000 | 0.8658 | 0.8653 | 0.6969 | 0.6394 | 0.9289 |
| CNN 특징 + LightGBM | 1.0000 | 0.8669 | 0.8653 | 0.6995 | 0.5883 | 0.9105 |

### RF 원시: 합성 시험 혼동 행렬

```json
[
  [
    450,
    0,
    0,
    0,
    0,
    0
  ],
  [
    0,
    954,
    0,
    0,
    0,
    0
  ],
  [
    0,
    110,
    607,
    55,
    0,
    0
  ],
  [
    0,
    113,
    0,
    659,
    0,
    0
  ],
  [
    116,
    97,
    0,
    0,
    487,
    240
  ],
  [
    0,
    0,
    0,
    0,
    0,
    760
  ]
]
```

### 2D CNN: 합성 시험 혼동 행렬

```json
[
  [
    450,
    0,
    0,
    0,
    0,
    0
  ],
  [
    0,
    818,
    12,
    0,
    0,
    124
  ],
  [
    0,
    0,
    668,
    49,
    0,
    55
  ],
  [
    0,
    6,
    46,
    516,
    2,
    202
  ],
  [
    0,
    382,
    45,
    60,
    449,
    4
  ],
  [
    70,
    0,
    20,
    12,
    7,
    651
  ]
]
```

### CNN 특징 + RF: 합성 시험 혼동 행렬

```json
[
  [
    450,
    0,
    0,
    0,
    0,
    0
  ],
  [
    0,
    837,
    0,
    0,
    3,
    114
  ],
  [
    0,
    1,
    649,
    76,
    0,
    46
  ],
  [
    0,
    11,
    4,
    572,
    3,
    182
  ],
  [
    0,
    16,
    12,
    87,
    724,
    101
  ],
  [
    64,
    0,
    0,
    2,
    1,
    693
  ]
]
```

### CNN 특징 + XGBoost: 합성 시험 혼동 행렬

```json
[
  [
    450,
    0,
    0,
    0,
    0,
    0
  ],
  [
    0,
    826,
    0,
    0,
    2,
    126
  ],
  [
    0,
    2,
    668,
    34,
    0,
    68
  ],
  [
    0,
    5,
    1,
    538,
    4,
    224
  ],
  [
    0,
    48,
    33,
    65,
    601,
    193
  ],
  [
    47,
    0,
    1,
    0,
    6,
    706
  ]
]
```

### CNN 특징 + LightGBM: 합성 시험 혼동 행렬

```json
[
  [
    450,
    0,
    0,
    0,
    0,
    0
  ],
  [
    0,
    827,
    0,
    0,
    3,
    124
  ],
  [
    0,
    2,
    668,
    35,
    0,
    67
  ],
  [
    0,
    6,
    9,
    540,
    4,
    213
  ],
  [
    0,
    13,
    19,
    79,
    553,
    276
  ],
  [
    55,
    0,
    0,
    3,
    10,
    692
  ]
]
```

## 실제 교차검증·학습 설정 원문

아래는 이번 실행에서 자동 생성한 논문 기록이다. 미측정 값은 빈칸이다.
# 논문용 실측 기록

빈칸은 미측정 또는 미확인 항목이다. 이 파일의 준비만으로 녹화·학습·Pi 배포 완료를 의미하지 않는다.

| 기록 항목 | 값 |
|---|---|
| 학습 완료 | True |
| 평가 자료 출처 | Pi dataset_v3 운영자 지시 라벨; 외부 시험 묶음 예측 집계 |

## 실측 설정

| 항목 | 값 |
|---|---|
| 센서별 conv 층 수·채널 | {"tof1_foot": [{"in_channels": 3, "out_channels": 16, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}, {"in_channels": 16, "out_channels": 32, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}, {"in_channels": 32, "out_channels": 32, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}], "tof2_head": [{"in_channels": 3, "out_channels": 16, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}, {"in_channels": 16, "out_channels": 32, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}, {"in_channels": 32, "out_channels": 32, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}]} |
| 이어 붙인 특징 차원 | 64 |
| epoch: 최대·묶음별 선택·최종 | {"maximum": 60, "fold_selected": [33, 5, 3], "final": 5, "runs": [{"role": "inner_validation", "completed_epochs": 43, "train_pairs": 3603, "selected_epoch": 33}, {"role": "refit", "completed_epochs": 33, "train_pairs": 5405, "selected_epoch": null}, {"role": "inner_validation", "completed_epochs": 15, "train_pairs": 3600, "selected_epoch": 5}, {"role": "refit", "completed_epochs": 5, "train_pairs": 5403, "selected_epoch": null}, {"role": "inner_validation", "completed_epochs": 13, "train_pairs": 3603, "selected_epoch": 3}, {"role": "refit", "completed_epochs": 3, "train_pairs": 5404, "selected_epoch": null}, {"role": "refit", "completed_epochs": 5, "train_pairs": 8106, "selected_epoch": null}]} |
| optimizer·학습률·기타 설정 | {"name": "AdamW", "defaults": {"lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-08, "weight_decay": 0.001, "amsgrad": false, "foreach": null, "maximize": false, "capturable": false, "differentiable": false, "fused": null}, "loss": "class_weighted_cross_entropy", "gradient_clip_norm": 1.0} |
| batch | 128 |
| early stopping 기준 | {"metric": "inner_validation_macro_f1", "direction": "maximize", "strict_improvement": true, "patience": 10, "final_refit": "fixed_selected_epochs_without_early_stopping"} |
| 실제 적용 증강 | 미적용 |
| RF·XGBoost·LightGBM 전체 설정 | {"raw_rf": {"bootstrap": true, "ccp_alpha": 0.0, "class_weight": "balanced", "criterion": "gini", "max_depth": null, "max_features": "sqrt", "max_leaf_nodes": null, "max_samples": null, "min_impurity_decrease": 0.0, "min_samples_leaf": 1, "min_samples_split": 2, "min_weight_fraction_leaf": 0.0, "monotonic_cst": null, "n_estimators": 200, "n_jobs": 8, "oob_score": false, "random_state": 42, "verbose": 0, "warm_start": false}, "cnn_rf": {"bootstrap": true, "ccp_alpha": 0.0, "class_weight": "balanced", "criterion": "gini", "max_depth": null, "max_features": "sqrt", "max_leaf_nodes": null, "max_samples": null, "min_impurity_decrease": 0.0, "min_samples_leaf": 1, "min_samples_split": 2, "min_weight_fraction_leaf": 0.0, "monotonic_cst": null, "n_estimators": 200, "n_jobs": 8, "oob_score": false, "random_state": 42, "verbose": 0, "warm_start": false}, "cnn_xgboost": {"objective": "multi:softprob", "base_score": null, "booster": null, "callbacks": null, "colsample_bylevel": null, "colsample_bynode": null, "colsample_bytree": null, "device": "cpu", "early_stopping_rounds": null, "enable_categorical": true, "eval_metric": null, "feature_types": null, "feature_weights": null, "gamma": null, "grow_policy": null, "importance_type": null, "interaction_constraints": null, "learning_rate": null, "max_bin": null, "max_cat_threshold": null, "max_cat_to_onehot": null, "max_delta_step": null, "max_depth": 4, "max_leaves": null, "min_child_weight": null, "missing": "nan", "monotone_constraints": null, "multi_strategy": null, "n_estimators": 200, "n_jobs": 8, "num_parallel_tree": null, "random_state": 42, "reg_alpha": null, "reg_lambda": null, "sampling_method": null, "scale_pos_weight": null, "subsample": null, "tree_method": "hist", "validate_parameters": null, "verbosity": null, "num_class": 6}, "cnn_lightgbm": {"boosting_type": "gbdt", "class_weight": null, "colsample_bytree": 1.0, "importance_type": "split", "learning_rate": 0.1, "max_depth": -1, "min_child_samples": 5, "min_child_weight": 0.001, "min_split_gain": 0.0, "n_estimators": 200, "n_jobs": 8, "num_leaves": 15, "objective": null, "random_state": 42, "reg_alpha": 0.0, "reg_lambda": 0.0, "subsample": 1.0, "subsample_for_bin": 200000, "subsample_freq": 0, "verbosity": -1, "device_type": "cpu"}} |
| 트리 학습 가중치 | {"raw_rf": "balanced class_weight", "cnn_rf": "balanced class_weight × inverse-frequency sample_weight", "cnn_xgboost": "inverse-frequency sample_weight", "cnn_lightgbm": "inverse-frequency sample_weight"} |
| 사용 라이브러리 버전 | {"torch": "2.6.0+cu124", "numpy": "2.5.4", "scikit-learn": "1.9.1", "xgboost": "3.4.1", "lightgbm": "4.7.0", "joblib": "1.6.0"} |
| 학습에 사용한 GPU 이름 | NVIDIA GeForce RTX 4060 Laptop GPU |
| 학습 장치 | cuda |
| 난수 seed | 42 |

## 데이터

| 항목 | 값 |
|---|---|
| 참여자 수 | 3 |
| 입력 세션 수 | 54 |
| 평가 프레임 쌍이 있는 세션 수 | 54 |
| 전체 프레임 쌍 수 | 8106 |
| 검증 방식 | 사람 단위 (Leave-One-Subject-Out) |
| 같은 사람 평가 여부 | False |
| 짝짓기·구간 제외 규칙 | 수신 시각 차이 ≤250ms, 1:1 짝짓기, 세션 앞뒤 1초 제외 |

| 클래스 | 프레임 쌍 수 |
|---|---:|
| empty | 1350 |
| supine | 1350 |
| side_left | 1351 |
| side_right | 1352 |
| sitting | 1350 |
| edge_sit | 1353 |

## 분류기 비교

정확도·macro-F1·재현율은 0–1 척도이며, 완료된 학습의 외부 시험 묶음 예측만 집계한다.

| 모델 | 입력 | 정확도 | macro-F1 | Pi 5 CPU ms/프레임 쌍 |
|---|---|---:|---:|---:|
| RF 원시 | 거리 128개 | 0.6115223291 | 0.5905739228 |  |
| CNN | 센서별 3채널 8×8 | 0.3886010363 | 0.3517369931 |  |
| CNN 특징 + RF | CNN 특징 | 0.3978534419 | 0.3598384044 |  |
| CNN 특징 + XGBoost | CNN 특징 | 0.4137675796 | 0.393380344 |  |
| CNN 특징 + LightGBM | CNN 특징 | 0.4289415248 | 0.4016979789 |  |

## 실험 조건

카메라 2대, 침대 같은 긴 면의 양 끝 위쪽에서 촬영, 반대쪽 긴 면은 보이지 않음

| 라벨·입력 출처 | 값 |
|---|---|
| 카메라 판정의 학습 사용 | False |
| 카메라 판정으로 ToF 값 교정 | False |
| 정답 | 운영자가 말로 지정한 세션 라벨 |

| 입력 처리 | 값 |
|---|---|
| ROI 마스킹 적용 | False |
| ROI 설정·제외 칸 집계 |  |
| 빈 침대 평가 분리 | session_metadata.json의 촬영 묶음 참여자에 귀속 |

## 사람별 외부 시험 결과

| 시험 참여자 | 모델 | 시험 쌍 수 | 정확도 | macro-F1 |
|---|---|---:|---:|---:|
| S01 | RF 원시 | 2701 | 0.4231766013 | 0.3498925482 |
| S01 | CNN | 2701 | 0.3509811181 | 0.2513485403 |
| S01 | CNN 특징 + RF | 2701 | 0.359866716 | 0.251631837 |
| S01 | CNN 특징 + XGBoost | 2701 | 0.3746760459 | 0.2799467984 |
| S01 | CNN 특징 + LightGBM | 2701 | 0.3880044428 | 0.2983363528 |
| S02 | RF 원시 | 2703 | 0.7007029227 | 0.6482997027 |
| S02 | CNN | 2703 | 0.3473917869 | 0.288481477 |
| S02 | CNN 특징 + RF | 2703 | 0.4084350721 | 0.3550822148 |
| S02 | CNN 특징 + XGBoost | 2703 | 0.4028856826 | 0.365348915 |
| S02 | CNN 특징 + LightGBM | 2703 | 0.4043655198 | 0.3599260553 |
| S05 | RF 원시 | 2702 | 0.710584752 | 0.7086507176 |
| S05 | CNN | 2702 | 0.4674315322 | 0.4732230469 |
| S05 | CNN 특징 + RF | 2702 | 0.4252405625 | 0.4020367502 |
| S05 | CNN 특징 + XGBoost | 2702 | 0.4637305699 | 0.4616070597 |
| S05 | CNN 특징 + LightGBM | 2702 | 0.4944485566 | 0.4919588995 |

### 시험 참여자 S01 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 0 | 0.6244444444 | 0 | 0.6111111111 | 0.3037694013 |
| CNN | 1 | 0 | 0 | 0.09555555556 | 0.3355555556 | 0.6740576497 |
| CNN 특징 + RF | 1 | 0 | 0.002222222222 | 0.05777777778 | 0.3333333333 | 0.7649667406 |
| CNN 특징 + XGBoost | 0.9977777778 | 0 | 0.06444444444 | 0.03111111111 | 0.42 | 0.733924612 |
| CNN 특징 + LightGBM | 1 | 0 | 0.1488888889 | 0.04666666667 | 0.3711111111 | 0.7605321508 |

#### S01 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 0 | 165 | 135 | 0 | 150 |
| side_left | 20 | 0 | 281 | 64 | 0 | 85 |
| side_right | 287 | 0 | 163 | 0 | 0 | 0 |
| sitting | 25 | 0 | 150 | 0 | 275 | 0 |
| edge_sit | 244 | 0 | 0 | 0 | 70 | 137 |

#### S01 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 234 | 0 | 6 | 1 | 163 | 46 |
| side_left | 101 | 0 | 0 | 0 | 239 | 110 |
| side_right | 242 | 0 | 0 | 43 | 148 | 17 |
| sitting | 149 | 0 | 0 | 0 | 151 | 150 |
| edge_sit | 147 | 0 | 0 | 0 | 0 | 304 |

#### S01 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 116 | 0 | 10 | 0 | 186 | 138 |
| side_left | 7 | 0 | 1 | 0 | 321 | 121 |
| side_right | 240 | 0 | 7 | 26 | 165 | 12 |
| sitting | 146 | 0 | 0 | 0 | 150 | 154 |
| edge_sit | 100 | 0 | 0 | 0 | 6 | 345 |

#### S01 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 449 | 0 | 0 | 0 | 0 | 1 |
| supine | 34 | 0 | 64 | 10 | 221 | 121 |
| side_left | 2 | 0 | 29 | 3 | 281 | 135 |
| side_right | 144 | 0 | 51 | 14 | 226 | 15 |
| sitting | 111 | 0 | 1 | 0 | 189 | 149 |
| edge_sit | 87 | 0 | 0 | 0 | 33 | 331 |

#### S01 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 57 | 0 | 57 | 12 | 189 | 135 |
| side_left | 24 | 0 | 67 | 3 | 212 | 144 |
| side_right | 135 | 0 | 44 | 21 | 228 | 22 |
| sitting | 139 | 0 | 0 | 0 | 167 | 144 |
| edge_sit | 91 | 0 | 0 | 0 | 17 | 343 |

### 시험 참여자 S02 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 0 | 0.5622222222 | 0.7849223947 | 0.9977777778 | 0.8584070796 |
| CNN | 0.74 | 1 | 0.01333333333 | 0 | 0 | 0.3318584071 |
| CNN 특징 + RF | 0.7155555556 | 0.98 | 0.02888888889 | 0.2106430155 | 0 | 0.5154867257 |
| CNN 특징 + XGBoost | 0.6266666667 | 0.9266666667 | 0.3822222222 | 0.0487804878 | 0.01111111111 | 0.4225663717 |
| CNN 특징 + LightGBM | 0.5911111111 | 0.9777777778 | 0.3622222222 | 0.0266075388 | 0.006666666667 | 0.4623893805 |

#### S02 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 0 | 57 | 391 | 2 | 0 |
| side_left | 0 | 0 | 253 | 81 | 116 | 0 |
| side_right | 0 | 0 | 17 | 354 | 47 | 33 |
| sitting | 0 | 0 | 1 | 0 | 449 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 64 | 388 |

#### S02 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 333 | 47 | 0 | 0 | 70 | 0 |
| supine | 0 | 450 | 0 | 0 | 0 | 0 |
| side_left | 0 | 375 | 6 | 3 | 0 | 66 |
| side_right | 0 | 306 | 71 | 0 | 0 | 74 |
| sitting | 0 | 300 | 82 | 68 | 0 | 0 |
| edge_sit | 0 | 30 | 125 | 3 | 144 | 150 |

#### S02 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 322 | 107 | 0 | 3 | 13 | 5 |
| supine | 0 | 441 | 9 | 0 | 0 | 0 |
| side_left | 0 | 279 | 13 | 129 | 0 | 29 |
| side_right | 0 | 224 | 23 | 95 | 8 | 101 |
| sitting | 0 | 153 | 44 | 106 | 0 | 147 |
| edge_sit | 0 | 26 | 3 | 3 | 187 | 233 |

#### S02 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 282 | 150 | 1 | 0 | 3 | 14 |
| supine | 0 | 417 | 26 | 0 | 1 | 6 |
| side_left | 0 | 202 | 172 | 28 | 2 | 46 |
| side_right | 0 | 156 | 94 | 22 | 5 | 174 |
| sitting | 0 | 300 | 27 | 118 | 5 | 0 |
| edge_sit | 9 | 37 | 40 | 2 | 173 | 191 |

#### S02 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 266 | 166 | 1 | 2 | 1 | 14 |
| supine | 0 | 440 | 9 | 0 | 1 | 0 |
| side_left | 0 | 244 | 163 | 12 | 0 | 31 |
| side_right | 0 | 159 | 106 | 12 | 0 | 174 |
| sitting | 0 | 300 | 40 | 107 | 3 | 0 |
| edge_sit | 4 | 47 | 30 | 8 | 154 | 209 |

### 시험 참여자 S05 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 0.6911111111 | 0.578713969 | 0.3525498891 | 0.6666666667 | 0.9755555556 |
| CNN | 0.8822222222 | 0.54 | 0.0022172949 | 0.3924611973 | 0.3844444444 | 0.6044444444 |
| CNN 특징 + RF | 0.9511111111 | 0.02666666667 | 0.06873614191 | 0.2106430155 | 0.3333333333 | 0.9622222222 |
| CNN 특징 + XGBoost | 0.92 | 0.1555555556 | 0.2128603104 | 0.2261640798 | 0.3333333333 | 0.9355555556 |
| CNN 특징 + LightGBM | 0.92 | 0.3311111111 | 0.2505543237 | 0.1685144124 | 0.3333333333 | 0.9644444444 |

#### S05 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 311 | 139 | 0 | 0 | 0 |
| side_left | 0 | 67 | 261 | 118 | 5 | 0 |
| side_right | 0 | 48 | 244 | 159 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 300 | 150 |
| edge_sit | 0 | 0 | 0 | 0 | 11 | 439 |

#### S05 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 397 | 0 | 0 | 1 | 0 | 52 |
| supine | 0 | 243 | 0 | 0 | 199 | 8 |
| side_left | 0 | 416 | 1 | 34 | 0 | 0 |
| side_right | 0 | 135 | 137 | 177 | 0 | 2 |
| sitting | 0 | 0 | 144 | 6 | 173 | 127 |
| edge_sit | 0 | 1 | 117 | 32 | 28 | 272 |

#### S05 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 428 | 2 | 0 | 0 | 16 | 4 |
| supine | 0 | 12 | 0 | 203 | 234 | 1 |
| side_left | 0 | 123 | 31 | 162 | 135 | 0 |
| side_right | 0 | 70 | 234 | 95 | 52 | 0 |
| sitting | 0 | 0 | 148 | 2 | 150 | 150 |
| edge_sit | 1 | 2 | 12 | 1 | 1 | 433 |

#### S05 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 414 | 7 | 0 | 0 | 24 | 5 |
| supine | 0 | 70 | 42 | 296 | 42 | 0 |
| side_left | 0 | 136 | 96 | 167 | 52 | 0 |
| side_right | 0 | 33 | 303 | 102 | 13 | 0 |
| sitting | 0 | 3 | 148 | 3 | 150 | 146 |
| edge_sit | 1 | 12 | 14 | 0 | 2 | 421 |

#### S05 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 414 | 7 | 0 | 0 | 23 | 6 |
| supine | 0 | 149 | 68 | 171 | 62 | 0 |
| side_left | 0 | 153 | 113 | 155 | 21 | 9 |
| side_right | 0 | 33 | 316 | 76 | 26 | 0 |
| sitting | 0 | 0 | 149 | 3 | 150 | 148 |
| edge_sit | 1 | 1 | 11 | 0 | 3 | 434 |

### 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 0.2303703704 | 0.5884529978 | 0.3794378698 | 0.7585185185 | 0.7124907613 |
| CNN | 0.8740740741 | 0.5133333333 | 0.00518134715 | 0.1627218935 | 0.24 | 0.5365853659 |
| CNN 특징 + RF | 0.8888888889 | 0.3355555556 | 0.03330866025 | 0.1597633136 | 0.2222222222 | 0.7472283814 |
| CNN 특징 + XGBoost | 0.8481481481 | 0.3607407407 | 0.2198371577 | 0.1020710059 | 0.2548148148 | 0.696969697 |
| CNN 특징 + LightGBM | 0.837037037 | 0.4362962963 | 0.2538860104 | 0.08062130178 | 0.237037037 | 0.7287509239 |

### 혼동 행렬

행은 지시 라벨, 열은 예측 라벨이며 순서는 위 클래스 표와 같다.

#### RF 원시

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1350 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 311 | 361 | 526 | 2 | 150 |
| side_left | 20 | 67 | 795 | 263 | 121 | 85 |
| side_right | 287 | 48 | 424 | 513 | 47 | 33 |
| sitting | 25 | 0 | 151 | 0 | 1024 | 150 |
| edge_sit | 244 | 0 | 0 | 0 | 145 | 964 |

#### CNN

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1180 | 47 | 0 | 1 | 70 | 52 |
| supine | 234 | 693 | 6 | 1 | 362 | 54 |
| side_left | 101 | 791 | 7 | 37 | 239 | 176 |
| side_right | 242 | 441 | 208 | 220 | 148 | 93 |
| sitting | 149 | 300 | 226 | 74 | 324 | 277 |
| edge_sit | 147 | 31 | 242 | 35 | 172 | 726 |

#### CNN 특징 + RF

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1200 | 109 | 0 | 3 | 29 | 9 |
| supine | 116 | 453 | 19 | 203 | 420 | 139 |
| side_left | 7 | 402 | 45 | 291 | 456 | 150 |
| side_right | 240 | 294 | 264 | 216 | 225 | 113 |
| sitting | 146 | 153 | 192 | 108 | 300 | 451 |
| edge_sit | 101 | 28 | 15 | 4 | 194 | 1011 |

#### CNN 특징 + XGBoost

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1145 | 157 | 1 | 0 | 27 | 20 |
| supine | 34 | 487 | 132 | 306 | 264 | 127 |
| side_left | 2 | 338 | 297 | 198 | 335 | 181 |
| side_right | 144 | 189 | 448 | 138 | 244 | 189 |
| sitting | 111 | 303 | 176 | 121 | 344 | 295 |
| edge_sit | 97 | 49 | 54 | 2 | 208 | 943 |

#### CNN 특징 + LightGBM

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1130 | 173 | 1 | 2 | 24 | 20 |
| supine | 57 | 589 | 134 | 183 | 252 | 135 |
| side_left | 24 | 397 | 343 | 170 | 233 | 184 |
| side_right | 135 | 192 | 466 | 109 | 254 | 196 |
| sitting | 139 | 300 | 189 | 110 | 320 | 292 |
| edge_sit | 96 | 48 | 41 | 8 | 174 | 986 |

## 카메라 라벨과 말로 붙인 라벨의 일치율

| 항목 | 값 |
|---|---|
| 시간 정렬된 비교의 일치율 | 0.6057036578 |
| 시간 정렬된 비교 수 | 8065 |
| 비교 중 분류 가능한 비율 | 0.6947303162 |
| 카메라 비교가 없는 ToF 쌍 수 | 41 |
| 혼동 행렬 | [[0, 0, 3, 0, 0, 0, 1325], [0, 1123, 82, 31, 0, 0, 112], [0, 131, 1026, 1, 8, 0, 179], [0, 0, 0, 1348, 0, 0, 0], [0, 0, 0, 0, 808, 107, 428], [0, 0, 0, 0, 355, 580, 418]] |
| 혼동 행렬 열 순서 | ["empty", "supine", "side_left", "side_right", "sitting", "edge_sit", "unclassified"] |

미분류는 일치율 계산에서 오답으로 처리한다. 카메라 비교가 없으면 일치율을 0으로 대체하지 않는다.

## Pi 5 CPU 추론 측정 조건

| 항목 | 값 |
|---|---|
| 장치·스레드·워밍업·측정 횟수·전처리 포함 여부 |  |

노트북 CPU 측정이나 TorchScript 내보내기 검사는 Pi 실측 추론 시간으로 쓰지 않는다.
