# ToF 높이 1.5m 학습 결과 · 2026-11-05

실제 참여자 6명, 111세션, 수신 시각 짝짓기 후 24,232쌍이다. 시드 42·0·1·2·3 각각 사람 단위 6겹 교차검증으로 평가했다. 아래 ±는 시드 간 모집단 표준편차(ddof=0)이며 단위는 정확도·재현율의 % 또는 macro-F1의 0–1 척도다.

## 모델별 주 결과

| 모델 | 정확도 % | 정확도 최소~최대 % | macro-F1 | macro-F1 최소~최대 | 4분류 정확도 % |
|---|---:|---:|---:|---:|---:|
| raw_rf | 88.47 ± 0.47 | 87.86~89.27 | 0.8904 ± 0.0043 | 0.8853~0.8982 | 99.49 ± 0.03 |
| raw_xgboost | 86.39 ± 0.00 | 86.39~86.39 | 0.8730 ± 0.0000 | 0.8730~0.8730 | 97.59 ± 0.00 |
| raw_lightgbm | 82.56 ± 0.00 | 82.56~82.56 | 0.8322 ± 0.0000 | 0.8322~0.8322 | 96.18 ± 0.00 |
| cnn | 78.30 ± 1.27 | 76.14~79.49 | 0.7726 ± 0.0120 | 0.7576~0.7849 | 93.16 ± 0.85 |
| cnn_rf | 77.52 ± 1.21 | 75.69~79.07 | 0.7614 ± 0.0089 | 0.7510~0.7738 | 90.35 ± 1.81 |
| cnn_xgboost | 78.63 ± 1.22 | 76.49~80.09 | 0.7715 ± 0.0082 | 0.7586~0.7827 | 92.65 ± 2.01 |
| cnn_lightgbm | 78.53 ± 1.35 | 76.13~80.31 | 0.7703 ± 0.0120 | 0.7495~0.7869 | 93.47 ± 0.63 |

4분류는 같은 외부 시험 예측에서 누움 세 종류를 합쳐 계산했다.

## 자세별 재현율 %

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| raw_rf | 100.00 ± 0.00 | 95.50 ± 0.40 | 60.74 ± 2.46 | 83.31 ± 3.54 | 100.00 ± 0.00 | 94.99 ± 0.30 |
| raw_xgboost | 97.99 ± 0.00 | 85.32 ± 0.00 | 71.30 ± 0.00 | 77.44 ± 0.00 | 98.28 ± 0.00 | 91.73 ± 0.00 |
| raw_lightgbm | 96.75 ± 0.00 | 82.54 ± 0.00 | 58.86 ± 0.00 | 77.77 ± 0.00 | 97.36 ± 0.00 | 84.93 ± 0.00 |
| cnn | 99.94 ± 0.04 | 92.74 ± 3.79 | 57.38 ± 2.39 | 55.48 ± 7.30 | 76.11 ± 3.33 | 81.47 ± 5.32 |
| cnn_rf | 99.97 ± 0.04 | 94.92 ± 3.06 | 55.30 ± 3.56 | 48.15 ± 6.36 | 79.27 ± 3.22 | 82.33 ± 5.14 |
| cnn_xgboost | 99.93 ± 0.06 | 96.25 ± 0.45 | 54.81 ± 4.59 | 54.76 ± 8.17 | 78.23 ± 1.95 | 81.32 ± 5.38 |
| cnn_lightgbm | 99.92 ± 0.05 | 96.11 ± 0.54 | 54.87 ± 3.71 | 54.07 ± 7.06 | 78.85 ± 1.61 | 80.92 ± 6.81 |

## 원시 RF의 녹화 위치별 정답 재현율

| 자세 | 위치 기록 | 쌍 수 | 재현율 % |
|---|---|---:|---:|
| edge_sit | 보이는쪽_가운데 | 944 | 99.47 ± 0.80 |
| edge_sit | 보이는쪽_머리가까이 | 652 | 84.51 ± 1.08 |
| edge_sit | 보이는쪽_발가까이 | 726 | 98.57 ± 0.74 |
| empty | 빈침대 | 5535 | 100.00 ± 0.00 |
| side_left | 가운데 | 1474 | 48.79 ± 5.31 |
| side_left | 오른쪽가장자리 | 1421 | 45.00 ± 4.14 |
| side_left | 왼쪽가장자리 | 1561 | 86.34 ± 0.89 |
| side_right | 가운데 | 1385 | 66.25 ± 5.75 |
| side_right | 오른쪽가장자리 | 1346 | 91.23 ± 3.61 |
| side_right | 왼쪽가장자리 | 1529 | 91.79 ± 3.61 |
| sitting | 가운데 | 986 | 100.00 ± 0.00 |
| sitting | 머리쪽 | 1110 | 100.00 ± 0.00 |
| sitting | 발쪽 | 747 | 100.00 ± 0.00 |
| supine | 가운데 | 1657 | 96.63 ± 0.29 |
| supine | 오른쪽가장자리 | 1551 | 96.79 ± 1.15 |
| supine | 왼쪽가장자리 | 1608 | 93.10 ± 0.64 |

## 시험 참여자별 정확도 %

| 모델 | S01 | S02 | S03 | S04 | S05 | S06 |
|---|---:|---:|---:|---:|---:|---:|
| raw_rf | 96.99 ± 1.25 | 96.36 ± 0.72 | 87.42 ± 0.92 | 93.40 ± 1.78 | 76.76 ± 0.58 | 81.17 ± 1.78 |
| raw_xgboost | 92.46 ± 0.00 | 98.52 ± 0.00 | 79.15 ± 0.00 | 96.99 ± 0.00 | 77.50 ± 0.00 | 74.78 ± 0.00 |
| raw_lightgbm | 89.38 ± 0.00 | 95.27 ± 0.00 | 84.34 ± 0.00 | 93.11 ± 0.00 | 77.67 ± 0.00 | 58.69 ± 0.00 |
| cnn | 83.69 ± 3.91 | 90.62 ± 3.60 | 83.66 ± 2.82 | 88.44 ± 5.35 | 77.51 ± 2.48 | 49.63 ± 5.75 |
| cnn_rf | 84.84 ± 1.66 | 90.58 ± 3.70 | 82.17 ± 2.23 | 89.03 ± 4.32 | 76.35 ± 2.50 | 46.25 ± 3.96 |
| cnn_xgboost | 85.78 ± 2.29 | 91.25 ± 2.41 | 80.48 ± 3.23 | 88.89 ± 3.71 | 76.52 ± 2.62 | 52.17 ± 6.34 |
| cnn_lightgbm | 86.22 ± 2.15 | 90.68 ± 2.81 | 80.57 ± 3.15 | 89.18 ± 4.03 | 75.30 ± 2.27 | 52.56 ± 5.99 |

## S06 자세별 재현율 %

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| raw_rf | 100.00 ± 0.00 | 74.18 ± 2.31 | 55.80 ± 7.83 | 66.51 ± 12.62 | 100.00 ± 0.00 | 99.14 ± 1.31 |
| raw_xgboost | 86.77 ± 0.00 | 48.99 ± 0.00 | 76.58 ± 0.00 | 53.14 ± 0.00 | 100.00 ± 0.00 | 95.86 ± 0.00 |
| raw_lightgbm | 78.55 ± 0.00 | 40.29 ± 0.00 | 23.29 ± 0.00 | 63.26 ± 0.00 | 98.53 ± 0.00 | 56.21 ± 0.00 |
| cnn | 100.00 ± 0.00 | 85.44 ± 13.61 | 0.00 ± 0.00 | 41.55 ± 24.54 | 16.97 ± 14.71 | 38.45 ± 17.86 |
| cnn_rf | 100.00 ± 0.00 | 82.00 ± 18.36 | 0.00 ± 0.00 | 8.31 ± 9.35 | 37.56 ± 16.09 | 42.00 ± 12.99 |
| cnn_xgboost | 100.00 ± 0.00 | 91.13 ± 4.73 | 0.00 ± 0.00 | 31.79 ± 23.63 | 38.34 ± 13.85 | 40.79 ± 18.11 |
| cnn_lightgbm | 100.00 ± 0.00 | 90.85 ± 5.89 | 0.00 ± 0.00 | 34.25 ± 20.35 | 37.26 ± 15.05 | 41.97 ± 19.62 |

## S06 제외 5인 재학습 비교

S06의 빈 침대와 자세를 모두 제외한 뒤 각 시드마다 5겹 교차검증을 새로 수행했다. 전체 6인 모델에서 S06 시험 점수만 제외한 집계와 구분한다.

| 모델 | 5인 정확도 % | 5인 macro-F1 | 5인 4분류 정확도 % |
|---|---:|---:|---:|
| raw_rf | 86.41 ± 0.52 | 0.8684 ± 0.0059 | 99.20 ± 0.15 |
| raw_xgboost | 85.03 ± 0.00 | 0.8539 ± 0.0000 | 97.83 ± 0.00 |
| raw_lightgbm | 85.31 ± 0.00 | 0.8580 ± 0.0000 | 98.17 ± 0.00 |
| cnn | 84.17 ± 1.30 | 0.8430 ± 0.0126 | 98.69 ± 0.57 |
| cnn_rf | 83.99 ± 0.78 | 0.8406 ± 0.0084 | 98.69 ± 0.52 |
| cnn_xgboost | 83.83 ± 0.83 | 0.8361 ± 0.0102 | 98.09 ± 0.76 |
| cnn_lightgbm | 83.07 ± 1.09 | 0.8280 ± 0.0128 | 97.84 ± 0.47 |

## Pi 5 CPU 원시 RF 추론 실측

Raspberry Pi 5 Model B Rev 1.1에서 단일 스레드로 워밍업 10회 후 120회 측정했다. 프레임 쌍당 평균 11.92ms, 중앙값 11.61ms, 95백분위 14.88ms다. 거리 배열을 분류하는 predict_proba 시간이며 수신·네트워크·서버 전체 지연은 제외했다. 여섯 클래스 검증 입력에 대한 노트북과 Pi 확률 최대 차이는 0다.

## 데이터·설정과 한계

- 센서는 발치 tof1, 머리맡 tof2이며 약 1.5m 높이다. 이번 하향 각도는 미측정이다.
- 입력은 운영 서버가 상태 5·9를 허용하고 최대 3프레임 이전 값으로 보완한 거리다. 정리 전 센서 원본 성능은 따로 측정하지 않았다.
- S06은 체구가 작고 검정 옷을 입었다는 녹화 설명이 있다. 체형과 옷 조건이 함께 달라 옷 색상만의 효과는 분리할 수 없다.
- 빈 침대 기준은 안내된 subject=없음 3세션 전체의 칸별 유효 중앙값이다. 유효 비율 60% 미만은 -1이다. 이 3세션은 요청된 교차검증의 빈 침대 시험 표본에도 포함되므로 기준 작성과 평가가 완전히 독립적이지 않다.
- 시험 사람을 CNN 학습·조기 종료에서 제외했다. 학습 쪽에서만 내부 검증으로 epoch를 정한 뒤 바깥 학습 전체를 새로 학습했다. 실제 분할 ID와 입력 해시는 실행 폴더에 저장했다.
- 원시 입력은 128거리다. CNN은 센서별 거리/4000·기준 차이/4000·유효 마스크의 3채널 8×8을 독립 가지로 처리한다. 센서별 합성곱 3층 16·32·32채널과 32차원 투영, 결합 특징 64차원을 사용했다.
- CNN은 AdamW(lr=0.001, weight_decay=0.001), batch 128, 최대 60 epoch, 내부 검증 macro-F1 개선 없음 10 epoch 후 종료한다. ROI·합성·좌우 반전 증강은 적용하지 않았다.
- RF 200그루 balanced, XGBoost 200그루 depth 4 hist, LightGBM 200그루 leaves 15 min_child_samples 5다. 원시 XGB/LGBM은 CNN 뒤 트리와 같은 설정 및 역빈도 표본 가중치를 썼다.
- 이전 설치의 원시 RF 57.88±1.63%, XGB 53.98%, LGBM 43.62%는 Pi 안내 문서의 참고값이다. 설치·참여자 수·전처리가 달라 높이 변화의 단독 효과로 비교할 수 없다.

## 클래스별 프레임 쌍

| 라벨 | 쌍 수 |
|---|---:|
| empty | 5,535 |
| supine | 4,816 |
| side_left | 4,456 |
| side_right | 4,260 |
| sitting | 2,843 |
| edge_sit | 2,322 |

## 평균 실패 칸 수 / 센서 프레임

| 참여자 | tof1 | tof2 |
|---|---:|---:|
| S01 | 14.24 | 12.79 |
| S02 | 7.97 | 11.41 |
| S03 | 9.28 | 11.49 |
| S04 | 9.07 | 11.24 |
| S05 | 8.24 | 10.54 |
| S06 | 20.29 | 21.84 |
| 없음 | 7.65 | 11.04 |

S06의 빈 침대 세션을 제외한 실제 자세 구간에서는 tof1 22.96칸, tof2 24.26칸이 평균적으로 무효였다.

세션별 짝 수 범위는 95~297쌍이다. 학습 기준에서 유효한 칸은 tof1 56칸, tof2 53칸이다.

## 합산 혼동 행렬

행=정답, 열=예측. 순서는 empty, supine, side_left, side_right, sitting, edge_sit. 각 행렬은 시드 5개의 예측 건수를 더한 값이므로 같은 시험 프레임이 5회 들어간다. 시드별 행렬은 metrics.json에 있다.

### raw_rf

```json
[[27675, 0, 0, 0, 0, 0], [0, 22997, 631, 452, 0, 0], [0, 2389, 13532, 6338, 1, 20], [0, 406, 3135, 17745, 12, 2], [0, 0, 0, 0, 14215, 0], [559, 0, 0, 9, 14, 11028]]
```

### raw_xgboost

```json
[[27120, 0, 290, 265, 0, 0], [160, 20545, 795, 2580, 0, 0], [535, 1705, 15885, 4095, 15, 45], [305, 25, 4375, 16495, 20, 80], [0, 0, 0, 0, 13970, 245], [800, 0, 95, 0, 65, 10650]]
```

### raw_lightgbm

```json
[[26775, 0, 900, 0, 0, 0], [140, 19875, 865, 3160, 25, 15], [225, 2815, 13115, 5290, 30, 805], [345, 105, 4265, 16565, 10, 10], [0, 0, 5, 0, 13840, 370], [445, 0, 1235, 0, 70, 9860]]
```

### cnn

```json
[[27659, 0, 0, 1, 0, 15], [45, 22332, 1092, 580, 0, 31], [894, 3718, 12784, 4404, 11, 469], [725, 2809, 5404, 11817, 19, 526], [10, 446, 0, 744, 10819, 2196], [1181, 430, 28, 171, 341, 9459]]
```

### cnn_rf

```json
[[27667, 0, 0, 0, 0, 8], [602, 22857, 427, 151, 7, 36], [2284, 3107, 12321, 3750, 201, 617], [1947, 2431, 5676, 10256, 54, 936], [453, 0, 0, 12, 11268, 2482], [1748, 4, 0, 0, 300, 9558]]
```

### cnn_xgboost

```json
[[27655, 0, 0, 0, 7, 13], [24, 23178, 416, 448, 0, 14], [869, 3634, 12212, 4642, 406, 517], [828, 2581, 5263, 11663, 38, 927], [486, 137, 0, 58, 11120, 2414], [1299, 410, 1, 97, 362, 9441]]
```

### cnn_lightgbm

```json
[[27653, 0, 0, 1, 10, 11], [16, 23143, 431, 485, 3, 2], [439, 3949, 12226, 4885, 372, 409], [328, 3066, 5294, 11516, 137, 959], [487, 200, 0, 78, 11209, 2241], [1128, 520, 28, 192, 347, 9395]]
```

## 시드 42의 상세 실행 기록

# 논문용 실측 기록

빈칸은 미측정 또는 미확인 항목이다. 이 파일의 준비만으로 녹화·학습·Pi 배포 완료를 의미하지 않는다.

| 기록 항목 | 값 |
|---|---|
| 학습 완료 | True |
| 평가 자료 출처 | dataset_v4 운영자 지시 라벨; 외부 시험 묶음 예측 집계 |

## 실측 설정

| 항목 | 값 |
|---|---|
| 센서별 conv 층 수·채널 | {"tof1_foot": [{"in_channels": 3, "out_channels": 16, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}, {"in_channels": 16, "out_channels": 32, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}, {"in_channels": 32, "out_channels": 32, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}], "tof2_head": [{"in_channels": 3, "out_channels": 16, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}, {"in_channels": 16, "out_channels": 32, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}, {"in_channels": 32, "out_channels": 32, "kernel_size": [3, 3], "stride": [1, 1], "padding": [1, 1]}]} |
| 이어 붙인 특징 차원 | 64 |
| epoch: 최대·묶음별 선택·최종 | {"maximum": 60, "fold_selected": [18, 26, 19, 18, 19, 31], "final": 19, "runs": [{"role": "inner_validation", "completed_epochs": 28, "train_pairs": 16378, "selected_epoch": 18}, {"role": "refit", "completed_epochs": 18, "train_pairs": 20372, "selected_epoch": null}, {"role": "inner_validation", "completed_epochs": 36, "train_pairs": 16400, "selected_epoch": 26}, {"role": "refit", "completed_epochs": 26, "train_pairs": 19982, "selected_epoch": null}, {"role": "inner_validation", "completed_epochs": 29, "train_pairs": 16972, "selected_epoch": 19}, {"role": "refit", "completed_epochs": 19, "train_pairs": 20554, "selected_epoch": null}, {"role": "inner_validation", "completed_epochs": 28, "train_pairs": 16835, "selected_epoch": 18}, {"role": "refit", "completed_epochs": 18, "train_pairs": 20417, "selected_epoch": null}, {"role": "inner_validation", "completed_epochs": 29, "train_pairs": 16494, "selected_epoch": 19}, {"role": "refit", "completed_epochs": 19, "train_pairs": 20076, "selected_epoch": null}, {"role": "inner_validation", "completed_epochs": 41, "train_pairs": 16177, "selected_epoch": 31}, {"role": "refit", "completed_epochs": 31, "train_pairs": 19759, "selected_epoch": null}, {"role": "refit", "completed_epochs": 19, "train_pairs": 24232, "selected_epoch": null}]} |
| optimizer·학습률·기타 설정 | {"name": "AdamW", "defaults": {"lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-08, "weight_decay": 0.001, "amsgrad": false, "foreach": null, "maximize": false, "capturable": false, "differentiable": false, "fused": null}, "loss": "class_weighted_cross_entropy", "gradient_clip_norm": 1.0} |
| batch | 128 |
| early stopping 기준 | {"metric": "inner_validation_macro_f1", "direction": "maximize", "strict_improvement": true, "patience": 10, "final_refit": "fixed_selected_epochs_without_early_stopping"} |
| 실제 적용 증강 | 미적용 |
| RF·XGBoost·LightGBM 전체 설정 | {"raw_rf": {"bootstrap": true, "ccp_alpha": 0.0, "class_weight": "balanced", "criterion": "gini", "max_depth": null, "max_features": "sqrt", "max_leaf_nodes": null, "max_samples": null, "min_impurity_decrease": 0.0, "min_samples_leaf": 1, "min_samples_split": 2, "min_weight_fraction_leaf": 0.0, "monotonic_cst": null, "n_estimators": 200, "n_jobs": 8, "oob_score": false, "random_state": 42, "verbose": 0, "warm_start": false}, "raw_xgboost": {"objective": "multi:softprob", "base_score": null, "booster": null, "callbacks": null, "colsample_bylevel": null, "colsample_bynode": null, "colsample_bytree": null, "device": "cpu", "early_stopping_rounds": null, "enable_categorical": true, "eval_metric": null, "feature_types": null, "feature_weights": null, "gamma": null, "grow_policy": null, "importance_type": null, "interaction_constraints": null, "learning_rate": null, "max_bin": null, "max_cat_threshold": null, "max_cat_to_onehot": null, "max_delta_step": null, "max_depth": 4, "max_leaves": null, "min_child_weight": null, "missing": "nan", "monotone_constraints": null, "multi_strategy": null, "n_estimators": 200, "n_jobs": 8, "num_parallel_tree": null, "random_state": 42, "reg_alpha": null, "reg_lambda": null, "sampling_method": null, "scale_pos_weight": null, "subsample": null, "tree_method": "hist", "validate_parameters": null, "verbosity": null, "num_class": 6}, "raw_lightgbm": {"boosting_type": "gbdt", "class_weight": null, "colsample_bytree": 1.0, "importance_type": "split", "learning_rate": 0.1, "max_depth": -1, "min_child_samples": 5, "min_child_weight": 0.001, "min_split_gain": 0.0, "n_estimators": 200, "n_jobs": 8, "num_leaves": 15, "objective": null, "random_state": 42, "reg_alpha": 0.0, "reg_lambda": 0.0, "subsample": 1.0, "subsample_for_bin": 200000, "subsample_freq": 0, "verbosity": -1, "device_type": "cpu"}, "cnn_rf": {"bootstrap": true, "ccp_alpha": 0.0, "class_weight": "balanced", "criterion": "gini", "max_depth": null, "max_features": "sqrt", "max_leaf_nodes": null, "max_samples": null, "min_impurity_decrease": 0.0, "min_samples_leaf": 1, "min_samples_split": 2, "min_weight_fraction_leaf": 0.0, "monotonic_cst": null, "n_estimators": 200, "n_jobs": 8, "oob_score": false, "random_state": 42, "verbose": 0, "warm_start": false}, "cnn_xgboost": {"objective": "multi:softprob", "base_score": null, "booster": null, "callbacks": null, "colsample_bylevel": null, "colsample_bynode": null, "colsample_bytree": null, "device": "cpu", "early_stopping_rounds": null, "enable_categorical": true, "eval_metric": null, "feature_types": null, "feature_weights": null, "gamma": null, "grow_policy": null, "importance_type": null, "interaction_constraints": null, "learning_rate": null, "max_bin": null, "max_cat_threshold": null, "max_cat_to_onehot": null, "max_delta_step": null, "max_depth": 4, "max_leaves": null, "min_child_weight": null, "missing": "nan", "monotone_constraints": null, "multi_strategy": null, "n_estimators": 200, "n_jobs": 8, "num_parallel_tree": null, "random_state": 42, "reg_alpha": null, "reg_lambda": null, "sampling_method": null, "scale_pos_weight": null, "subsample": null, "tree_method": "hist", "validate_parameters": null, "verbosity": null, "num_class": 6}, "cnn_lightgbm": {"boosting_type": "gbdt", "class_weight": null, "colsample_bytree": 1.0, "importance_type": "split", "learning_rate": 0.1, "max_depth": -1, "min_child_samples": 5, "min_child_weight": 0.001, "min_split_gain": 0.0, "n_estimators": 200, "n_jobs": 8, "num_leaves": 15, "objective": null, "random_state": 42, "reg_alpha": 0.0, "reg_lambda": 0.0, "subsample": 1.0, "subsample_for_bin": 200000, "subsample_freq": 0, "verbosity": -1, "device_type": "cpu"}} |
| 트리 학습 가중치 | {"raw_rf": "balanced class_weight", "cnn_rf": "balanced class_weight × inverse-frequency sample_weight", "raw_xgboost": "inverse-frequency sample_weight", "raw_lightgbm": "inverse-frequency sample_weight", "cnn_xgboost": "inverse-frequency sample_weight", "cnn_lightgbm": "inverse-frequency sample_weight"} |
| 사용 라이브러리 버전 | {"torch": "2.6.0+cu124", "numpy": "2.5.4", "scikit-learn": "1.9.1", "xgboost": "3.4.1", "lightgbm": "4.7.0", "joblib": "1.6.0"} |
| 학습에 사용한 GPU 이름 | NVIDIA GeForce RTX 4060 Laptop GPU |
| 학습 장치 | cuda |
| 난수 seed | 42 |

## 데이터

| 항목 | 값 |
|---|---|
| 참여자 수 | 6 |
| 입력 세션 수 | 111 |
| 평가 프레임 쌍이 있는 세션 수 | 111 |
| 전체 프레임 쌍 수 | 24232 |
| 검증 방식 | 사람 단위 (Leave-One-Subject-Out) |
| 같은 사람 평가 여부 | False |
| 짝짓기·구간 제외 규칙 | 수신 시각 차이 ≤250ms, 1:1 짝짓기, 세션 앞뒤 1초 제외 |

| 클래스 | 프레임 쌍 수 |
|---|---:|
| empty | 5535 |
| supine | 4816 |
| side_left | 4456 |
| side_right | 4260 |
| sitting | 2843 |
| edge_sit | 2322 |

## 분류기 비교

정확도·macro-F1·재현율은 0–1 척도이며, 완료된 학습의 외부 시험 묶음 예측만 집계한다.

| 모델 | 입력 | 정확도 | macro-F1 | Pi 5 CPU ms/프레임 쌍 |
|---|---|---:|---:|---:|
| RF 원시 | 거리 128개 | 0.8857296137 | 0.8900676823 | 11.6140125 |
| XGBoost 원시 | 거리 128개 | 0.8638577088 | 0.8729938935 |  |
| LightGBM 원시 | 거리 128개 | 0.8256025091 | 0.8321553498 |  |
| CNN | 센서별 3채널 8×8 | 0.7948993067 | 0.7839784357 |  |
| CNN 특징 + RF | CNN 특징 | 0.7906899967 | 0.7737546728 |  |
| CNN 특징 + XGBoost | CNN 특징 | 0.8009243975 | 0.7826596502 |  |
| CNN 특징 + LightGBM | CNN 특징 | 0.8030703202 | 0.7869005793 |  |

## 실험 조건

ToF 두 센서만 사용, 카메라 자료 없음

| 라벨·입력 출처 | 값 |
|---|---|
| 카메라 판정의 학습 사용 | False |
| 카메라 판정으로 ToF 값 교정 | False |
| 정답 | 운영자가 말로 지정한 세션 라벨 |

| 입력 처리 | 값 |
|---|---|
| ROI 마스킹 적용 | False |
| ROI 설정·제외 칸 집계 |  |
| 빈 침대 평가 분리 | 회차 기준 분배 |

## 사람별 외부 시험 결과

| 시험 참여자 | 모델 | 시험 쌍 수 | 정확도 | macro-F1 |
|---|---|---:|---:|---:|
| S01 | RF 원시 | 3860 | 0.9797927461 | 0.9809568139 |
| S01 | XGBoost 원시 | 3860 | 0.924611399 | 0.9274558449 |
| S01 | LightGBM 원시 | 3860 | 0.8937823834 | 0.8996626782 |
| S01 | CNN | 3860 | 0.8940414508 | 0.8887749139 |
| S01 | CNN 특징 + RF | 3860 | 0.871761658 | 0.8624113825 |
| S01 | CNN 특징 + XGBoost | 3860 | 0.8712435233 | 0.8617535834 |
| S01 | CNN 특징 + LightGBM | 3860 | 0.8715025907 | 0.8578925048 |
| S02 | RF 원시 | 4250 | 0.956 | 0.9587404274 |
| S02 | XGBoost 원시 | 4250 | 0.9851764706 | 0.986345898 |
| S02 | LightGBM 원시 | 4250 | 0.9527058824 | 0.9555250315 |
| S02 | CNN | 4250 | 0.8929411765 | 0.8811637376 |
| S02 | CNN 특징 + RF | 4250 | 0.928 | 0.9144525055 |
| S02 | CNN 특징 + XGBoost | 4250 | 0.9388235294 | 0.9282978167 |
| S02 | CNN 특징 + LightGBM | 4250 | 0.9256470588 | 0.9125537984 |
| S03 | RF 원시 | 3678 | 0.8594344753 | 0.8266331866 |
| S03 | XGBoost 원시 | 3678 | 0.7914627515 | 0.7477537956 |
| S03 | LightGBM 원시 | 3678 | 0.8433931485 | 0.8082491693 |
| S03 | CNN | 3678 | 0.8493746601 | 0.8199016263 |
| S03 | CNN 특징 + RF | 3678 | 0.8363240892 | 0.7990595612 |
| S03 | CNN 특징 + XGBoost | 3678 | 0.7974442632 | 0.738375222 |
| S03 | CNN 특징 + LightGBM | 3678 | 0.8297988037 | 0.8034832394 |
| S04 | RF 원시 | 3815 | 0.9336828309 | 0.9409835691 |
| S04 | XGBoost 원시 | 3815 | 0.9698558322 | 0.972594745 |
| S04 | LightGBM 원시 | 3815 | 0.931061599 | 0.9419907534 |
| S04 | CNN | 3815 | 0.8138925295 | 0.8329237887 |
| S04 | CNN 특징 + RF | 3815 | 0.8851900393 | 0.8975004712 |
| S04 | CNN 특징 + XGBoost | 3815 | 0.8553079948 | 0.8550198096 |
| S04 | CNN 특징 + LightGBM | 3815 | 0.8560943644 | 0.8514452323 |
| S05 | RF 원시 | 4156 | 0.7714148219 | 0.7810105134 |
| S05 | XGBoost 원시 | 4156 | 0.7750240616 | 0.7974089313 |
| S05 | LightGBM 원시 | 4156 | 0.7767083734 | 0.8101124553 |
| S05 | CNN | 4156 | 0.7480750722 | 0.7444167562 |
| S05 | CNN 특징 + RF | 4156 | 0.7225697786 | 0.7180152961 |
| S05 | CNN 특징 + XGBoost | 4156 | 0.7365255053 | 0.7290276976 |
| S05 | CNN 특징 + LightGBM | 4156 | 0.7360442733 | 0.7296946048 |
| S06 | RF 원시 | 4473 | 0.8247261346 | 0.8379786308 |
| S06 | XGBoost 원시 | 4473 | 0.7478202549 | 0.7752682304 |
| S06 | LightGBM 원시 | 4473 | 0.5868544601 | 0.6147932611 |
| S06 | CNN | 4473 | 0.5987033311 | 0.5439440838 |
| S06 | CNN 특징 + RF | 4473 | 0.5354348312 | 0.4618576908 |
| S06 | CNN 특징 + XGBoost | 4473 | 0.6255309636 | 0.5500095911 |
| S06 | CNN 특징 + LightGBM | 4473 | 0.6226246367 | 0.5437860508 |

### 시험 참여자 S01 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 1 | 1 | 0.886627907 | 1 | 1 |
| XGBoost 원시 | 1 | 0.9953917051 | 0.9456681351 | 0.6351744186 | 1 | 1 |
| LightGBM 원시 | 1 | 0.8940092166 | 0.8751835536 | 0.6279069767 | 1 | 1 |
| CNN | 0.9962581852 | 1 | 0.9500734214 | 0.460755814 | 1 | 1 |
| CNN 특징 + RF | 1 | 1 | 0.9295154185 | 0.3502906977 | 1 | 1 |
| CNN 특징 + XGBoost | 0.9943872778 | 1 | 0.9251101322 | 0.3604651163 | 1 | 1 |
| CNN 특징 + LightGBM | 0.9990645463 | 1 | 0.9647577093 | 0.3154069767 | 1 | 1 |

#### S01 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1069 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 651 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 681 | 0 | 0 | 0 |
| side_right | 0 | 0 | 78 | 610 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 449 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 322 |

#### S01 · XGBoost 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1069 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 648 | 3 | 0 | 0 | 0 |
| side_left | 0 | 3 | 644 | 33 | 1 | 0 |
| side_right | 0 | 0 | 251 | 437 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 449 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 322 |

#### S01 · LightGBM 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1069 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 582 | 69 | 0 | 0 | 0 |
| side_left | 0 | 81 | 596 | 4 | 0 | 0 |
| side_right | 0 | 3 | 253 | 432 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 449 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 322 |

#### S01 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1065 | 0 | 0 | 0 | 0 | 4 |
| supine | 0 | 651 | 0 | 0 | 0 | 0 |
| side_left | 0 | 10 | 647 | 24 | 0 | 0 |
| side_right | 0 | 213 | 158 | 317 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 449 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 322 |

#### S01 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1069 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 651 | 0 | 0 | 0 | 0 |
| side_left | 0 | 31 | 633 | 17 | 0 | 0 |
| side_right | 0 | 310 | 137 | 241 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 449 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 322 |

#### S01 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1063 | 0 | 0 | 0 | 1 | 5 |
| supine | 0 | 651 | 0 | 0 | 0 | 0 |
| side_left | 0 | 37 | 630 | 14 | 0 | 0 |
| side_right | 0 | 271 | 169 | 248 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 449 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 322 |

#### S01 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1068 | 0 | 0 | 0 | 1 | 0 |
| supine | 0 | 651 | 0 | 0 | 0 | 0 |
| side_left | 0 | 10 | 657 | 14 | 0 | 0 |
| side_right | 0 | 266 | 205 | 217 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 449 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 322 |

### 시험 참여자 S02 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 1 | 0.7509986684 | 1 | 1 | 1 |
| XGBoost 원시 | 1 | 1 | 0.9174434088 | 0.9987405542 | 1 | 1 |
| LightGBM 원시 | 1 | 1 | 0.7323568575 | 1 | 1 | 1 |
| CNN | 1 | 0.9733009709 | 0.9520639148 | 0.6397984887 | 0.773006135 | 1 |
| CNN 특징 + RF | 1 | 0.9902912621 | 0.948069241 | 0.8098236776 | 0.7791411043 | 1 |
| CNN 특징 + XGBoost | 1 | 0.9769417476 | 0.9374167776 | 0.8664987406 | 0.8200408998 | 1 |
| CNN 특징 + LightGBM | 1 | 0.9745145631 | 0.9187749667 | 0.8463476071 | 0.7873210634 | 0.9772079772 |

#### S02 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1041 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 824 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 564 | 187 | 0 | 0 |
| side_right | 0 | 0 | 0 | 794 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 489 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 351 |

#### S02 · XGBoost 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1041 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 824 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 689 | 62 | 0 | 0 |
| side_right | 0 | 0 | 1 | 793 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 489 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 351 |

#### S02 · LightGBM 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1041 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 824 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 550 | 201 | 0 | 0 |
| side_right | 0 | 0 | 0 | 794 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 489 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 351 |

#### S02 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1041 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 802 | 22 | 0 | 0 | 0 |
| side_left | 0 | 36 | 715 | 0 | 0 | 0 |
| side_right | 0 | 16 | 270 | 508 | 0 | 0 |
| sitting | 0 | 0 | 0 | 4 | 378 | 107 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 351 |

#### S02 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1041 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 816 | 5 | 3 | 0 | 0 |
| side_left | 0 | 39 | 712 | 0 | 0 | 0 |
| side_right | 0 | 19 | 132 | 643 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 381 | 108 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 351 |

#### S02 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1041 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 805 | 7 | 12 | 0 | 0 |
| side_left | 0 | 47 | 704 | 0 | 0 | 0 |
| side_right | 4 | 9 | 93 | 688 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 401 | 88 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 351 |

#### S02 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1041 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 803 | 7 | 14 | 0 | 0 |
| side_left | 0 | 61 | 690 | 0 | 0 | 0 |
| side_right | 5 | 11 | 106 | 672 | 0 | 0 |
| sitting | 6 | 0 | 0 | 0 | 385 | 98 |
| edge_sit | 0 | 0 | 0 | 0 | 8 | 343 |

### 시험 참여자 S03 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 1 | 0.392092257 | 0.9613152805 | 1 | 0.6085626911 |
| XGBoost 원시 | 1 | 1 | 0.3574958814 | 0.665377176 | 0.891509434 | 0.5168195719 |
| LightGBM 원시 | 1 | 1 | 0.3887973641 | 0.8955512573 | 0.8443396226 | 0.7400611621 |
| CNN | 1 | 1 | 0.6342668863 | 0.5512572534 | 0.7641509434 | 1 |
| CNN 특징 + RF | 1 | 1 | 0.3047775947 | 0.8433268859 | 0.766509434 | 1 |
| CNN 특징 + XGBoost | 1 | 1 | 0.2092257002 | 0.8413926499 | 0.5825471698 | 0.9816513761 |
| CNN 특징 + LightGBM | 1 | 1 | 0.2701812191 | 0.7137330754 | 0.9268867925 | 0.9877675841 |

#### S03 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1010 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 793 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 238 | 362 | 0 | 7 |
| side_right | 0 | 0 | 20 | 497 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 424 | 0 |
| edge_sit | 128 | 0 | 0 | 0 | 0 | 199 |

#### S03 · XGBoost 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1010 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 793 | 0 | 0 | 0 | 0 |
| side_left | 107 | 0 | 217 | 282 | 0 | 1 |
| side_right | 3 | 0 | 170 | 344 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 378 | 46 |
| edge_sit | 158 | 0 | 0 | 0 | 0 | 169 |

#### S03 · LightGBM 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1010 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 793 | 0 | 0 | 0 | 0 |
| side_left | 45 | 0 | 236 | 321 | 0 | 5 |
| side_right | 1 | 0 | 53 | 463 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 358 | 66 |
| edge_sit | 85 | 0 | 0 | 0 | 0 | 242 |

#### S03 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1010 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 793 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 385 | 222 | 0 | 0 |
| side_right | 0 | 0 | 232 | 285 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 324 | 100 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 327 |

#### S03 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1010 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 793 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 185 | 422 | 0 | 0 |
| side_right | 0 | 0 | 81 | 436 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 325 | 99 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 327 |

#### S03 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1010 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 793 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 127 | 480 | 0 | 0 |
| side_right | 0 | 0 | 82 | 435 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 247 | 177 |
| edge_sit | 6 | 0 | 0 | 0 | 0 | 321 |

#### S03 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1010 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 793 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 164 | 443 | 0 | 0 |
| side_right | 0 | 0 | 148 | 369 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 393 | 31 |
| edge_sit | 4 | 0 | 0 | 0 | 0 | 323 |

### 시험 참여자 S04 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 1 | 0.7096370463 | 0.9708141321 | 1 | 0.994100295 |
| XGBoost 원시 | 1 | 1 | 0.8936170213 | 0.9692780338 | 1 | 0.9705014749 |
| LightGBM 원시 | 1 | 0.9953216374 | 0.7108886108 | 0.9723502304 | 1 | 0.9705014749 |
| CNN | 1 | 0.5216374269 | 0.8197747184 | 0.8509984639 | 1 | 0.8230088496 |
| CNN 특징 + RF | 1 | 0.8526315789 | 0.7722152691 | 0.8202764977 | 1 | 0.9616519174 |
| CNN 특징 + XGBoost | 1 | 0.865497076 | 0.7446808511 | 0.7649769585 | 1 | 0.7640117994 |
| CNN 특징 + LightGBM | 0.9987562189 | 0.849122807 | 0.7672090113 | 0.7634408602 | 1 | 0.7669616519 |

#### S04 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 804 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 855 | 0 | 0 | 0 | 0 |
| side_left | 0 | 0 | 567 | 232 | 0 | 0 |
| side_right | 0 | 0 | 17 | 632 | 2 | 0 |
| sitting | 0 | 0 | 0 | 0 | 367 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 2 | 337 |

#### S04 · XGBoost 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 804 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 855 | 0 | 0 | 0 | 0 |
| side_left | 0 | 75 | 714 | 10 | 0 | 0 |
| side_right | 0 | 0 | 17 | 631 | 2 | 1 |
| sitting | 0 | 0 | 0 | 0 | 367 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 10 | 329 |

#### S04 · LightGBM 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 804 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 851 | 0 | 4 | 0 | 0 |
| side_left | 0 | 231 | 568 | 0 | 0 | 0 |
| side_right | 0 | 0 | 16 | 633 | 2 | 0 |
| sitting | 0 | 0 | 0 | 0 | 367 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 10 | 329 |

#### S04 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 804 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 446 | 408 | 1 | 0 | 0 |
| side_left | 0 | 0 | 655 | 144 | 0 | 0 |
| side_right | 0 | 0 | 91 | 554 | 6 | 0 |
| sitting | 0 | 0 | 0 | 0 | 367 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 60 | 279 |

#### S04 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 804 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 729 | 126 | 0 | 0 | 0 |
| side_left | 0 | 0 | 617 | 153 | 29 | 0 |
| side_right | 0 | 0 | 112 | 534 | 5 | 0 |
| sitting | 0 | 0 | 0 | 0 | 367 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 13 | 326 |

#### S04 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 804 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 740 | 115 | 0 | 0 | 0 |
| side_left | 0 | 0 | 595 | 189 | 15 | 0 |
| side_right | 0 | 0 | 137 | 498 | 16 | 0 |
| sitting | 0 | 0 | 0 | 0 | 367 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 80 | 259 |

#### S04 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 803 | 0 | 0 | 0 | 0 | 1 |
| supine | 0 | 726 | 129 | 0 | 0 | 0 |
| side_left | 0 | 0 | 613 | 139 | 47 | 0 |
| side_right | 0 | 0 | 104 | 497 | 50 | 0 |
| sitting | 0 | 0 | 0 | 0 | 367 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 79 | 260 |

### 시험 참여자 S05 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 1 | 0.1751207729 | 0.6658322904 | 1 | 1 |
| XGBoost 원시 | 1 | 0.6768149883 | 0.3719806763 | 0.829787234 | 0.994 | 1 |
| LightGBM 원시 | 1 | 0.68735363 | 0.5905797101 | 0.5982478098 | 1 | 0.9975186104 |
| CNN | 1 | 1 | 0.05072463768 | 0.6733416771 | 1 | 1 |
| CNN 특징 + RF | 1 | 1 | 0.003623188406 | 0.5894868586 | 1 | 1 |
| CNN 특징 + XGBoost | 1 | 1 | 0.01690821256 | 0.6508135169 | 1 | 0.9950372208 |
| CNN 특징 + LightGBM | 1 | 1 | 0.02173913043 | 0.6408010013 | 1 | 1 |

#### S05 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 772 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 854 | 0 | 0 | 0 | 0 |
| side_left | 0 | 455 | 145 | 228 | 0 | 0 |
| side_right | 0 | 163 | 104 | 532 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 500 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 403 |

#### S05 · XGBoost 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 772 | 0 | 0 | 0 | 0 | 0 |
| supine | 32 | 578 | 0 | 244 | 0 | 0 |
| side_left | 0 | 247 | 308 | 263 | 2 | 8 |
| side_right | 58 | 4 | 59 | 663 | 0 | 15 |
| sitting | 0 | 0 | 0 | 0 | 497 | 3 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 403 |

#### S05 · LightGBM 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 772 | 0 | 0 | 0 | 0 | 0 |
| supine | 28 | 587 | 1 | 236 | 0 | 2 |
| side_left | 0 | 65 | 489 | 260 | 6 | 8 |
| side_right | 68 | 0 | 251 | 478 | 0 | 2 |
| sitting | 0 | 0 | 0 | 0 | 500 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 1 | 402 |

#### S05 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 772 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 854 | 0 | 0 | 0 | 0 |
| side_left | 0 | 541 | 42 | 245 | 0 | 0 |
| side_right | 0 | 207 | 54 | 538 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 500 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 403 |

#### S05 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 772 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 854 | 0 | 0 | 0 | 0 |
| side_left | 0 | 557 | 3 | 268 | 0 | 0 |
| side_right | 0 | 213 | 115 | 471 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 500 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 403 |

#### S05 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 772 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 854 | 0 | 0 | 0 | 0 |
| side_left | 0 | 531 | 14 | 283 | 0 | 0 |
| side_right | 2 | 200 | 77 | 520 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 500 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 2 | 401 |

#### S05 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 772 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 854 | 0 | 0 | 0 | 0 |
| side_left | 0 | 533 | 18 | 277 | 0 | 0 |
| side_right | 1 | 218 | 68 | 512 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 500 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 403 |

### 시험 참여자 S06 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 0.7425506555 | 0.4405063291 | 0.8446362515 | 1 | 1 |
| XGBoost 원시 | 0.8676996424 | 0.4898688915 | 0.7658227848 | 0.5314426634 | 1 | 0.9586206897 |
| LightGBM 원시 | 0.7854588796 | 0.4028605483 | 0.2329113924 | 0.6325524044 | 0.9853420195 | 0.5620689655 |
| CNN | 1 | 0.8152562574 | 0 | 0.6522811344 | 0.3973941368 | 0.6586206897 |
| CNN 특징 + RF | 1 | 0.9749702026 | 0 | 0.2046855734 | 0.4006514658 | 0.5620689655 |
| CNN 특징 + XGBoost | 1 | 0.9201430274 | 0 | 0.7127003699 | 0.3045602606 | 0.7275862069 |
| CNN 특징 + LightGBM | 1 | 0.9332538737 | 0 | 0.6868064118 | 0.2833876221 | 0.7448275862 |

#### S06 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 839 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 623 | 133 | 83 | 0 | 0 |
| side_left | 0 | 0 | 348 | 441 | 1 | 0 |
| side_right | 0 | 0 | 126 | 685 | 0 | 0 |
| sitting | 0 | 0 | 0 | 0 | 614 | 0 |
| edge_sit | 0 | 0 | 0 | 0 | 0 | 580 |

#### S06 · XGBoost 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 728 | 0 | 58 | 53 | 0 | 0 |
| supine | 0 | 411 | 156 | 272 | 0 | 0 |
| side_left | 0 | 16 | 605 | 169 | 0 | 0 |
| side_right | 0 | 1 | 377 | 431 | 2 | 0 |
| sitting | 0 | 0 | 0 | 0 | 614 | 0 |
| edge_sit | 2 | 0 | 19 | 0 | 3 | 556 |

#### S06 · LightGBM 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 659 | 0 | 180 | 0 | 0 | 0 |
| supine | 0 | 338 | 103 | 392 | 5 | 1 |
| side_left | 0 | 186 | 184 | 272 | 0 | 148 |
| side_right | 0 | 18 | 280 | 513 | 0 | 0 |
| sitting | 0 | 0 | 1 | 0 | 605 | 8 |
| edge_sit | 4 | 0 | 247 | 0 | 3 | 326 |

#### S06 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 839 | 0 | 0 | 0 | 0 | 0 |
| supine | 3 | 684 | 0 | 125 | 0 | 27 |
| side_left | 148 | 3 | 0 | 403 | 0 | 236 |
| side_right | 2 | 5 | 0 | 529 | 0 | 275 |
| sitting | 0 | 0 | 0 | 7 | 244 | 363 |
| edge_sit | 198 | 0 | 0 | 0 | 0 | 382 |

#### S06 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 839 | 0 | 0 | 0 | 0 | 0 |
| supine | 1 | 818 | 0 | 20 | 0 | 0 |
| side_left | 373 | 92 | 0 | 15 | 0 | 310 |
| side_right | 72 | 222 | 0 | 166 | 0 | 351 |
| sitting | 5 | 0 | 0 | 7 | 246 | 356 |
| edge_sit | 254 | 0 | 0 | 0 | 0 | 326 |

#### S06 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 839 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 772 | 0 | 67 | 0 | 0 |
| side_left | 29 | 64 | 0 | 498 | 153 | 46 |
| side_right | 0 | 14 | 0 | 578 | 0 | 219 |
| sitting | 0 | 0 | 0 | 0 | 187 | 427 |
| edge_sit | 134 | 1 | 0 | 23 | 0 | 422 |

#### S06 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 839 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 783 | 0 | 56 | 0 | 0 |
| side_left | 48 | 92 | 0 | 454 | 144 | 52 |
| side_right | 0 | 22 | 0 | 557 | 0 | 232 |
| sitting | 4 | 0 | 0 | 0 | 174 | 436 |
| edge_sit | 136 | 0 | 0 | 9 | 3 | 432 |

### 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 1 | 0.9551495017 | 0.5706912029 | 0.8802816901 | 1 | 0.9440137812 |
| XGBoost 원시 | 0.9799457995 | 0.8531976744 | 0.7129712747 | 0.7744131455 | 0.9827646852 | 0.9173126615 |
| LightGBM 원시 | 0.9674796748 | 0.8253737542 | 0.5886445242 | 0.7776995305 | 0.9736194161 | 0.8492678725 |
| CNN | 0.9992773261 | 0.8783222591 | 0.5484739677 | 0.6410798122 | 0.7956384101 | 0.8888888889 |
| CNN 특징 + RF | 1 | 0.9678156146 | 0.4824955117 | 0.584741784 | 0.7977488568 | 0.8850129199 |
| CNN 특징 + XGBoost | 0.9989159892 | 0.9582641196 | 0.4645421903 | 0.6964788732 | 0.756595146 | 0.8940568475 |
| CNN 특징 + LightGBM | 0.9996386631 | 0.9572259136 | 0.4807001795 | 0.6629107981 | 0.7977488568 | 0.8970714901 |

### 혼동 행렬

행은 지시 라벨, 열은 예측 라벨이며 순서는 위 클래스 표와 같다.

#### RF 원시

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 5535 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 4600 | 133 | 83 | 0 | 0 |
| side_left | 0 | 455 | 2543 | 1450 | 1 | 7 |
| side_right | 0 | 163 | 345 | 3750 | 2 | 0 |
| sitting | 0 | 0 | 0 | 0 | 2843 | 0 |
| edge_sit | 128 | 0 | 0 | 0 | 2 | 2192 |

#### XGBoost 원시

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 5424 | 0 | 58 | 53 | 0 | 0 |
| supine | 32 | 4109 | 159 | 516 | 0 | 0 |
| side_left | 107 | 341 | 3177 | 819 | 3 | 9 |
| side_right | 61 | 5 | 875 | 3299 | 4 | 16 |
| sitting | 0 | 0 | 0 | 0 | 2794 | 49 |
| edge_sit | 160 | 0 | 19 | 0 | 13 | 2130 |

#### LightGBM 원시

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 5355 | 0 | 180 | 0 | 0 | 0 |
| supine | 28 | 3975 | 173 | 632 | 5 | 3 |
| side_left | 45 | 563 | 2623 | 1058 | 6 | 161 |
| side_right | 69 | 21 | 853 | 3313 | 2 | 2 |
| sitting | 0 | 0 | 1 | 0 | 2768 | 74 |
| edge_sit | 89 | 0 | 247 | 0 | 14 | 1972 |

#### CNN

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 5531 | 0 | 0 | 0 | 0 | 4 |
| supine | 3 | 4230 | 430 | 126 | 0 | 27 |
| side_left | 148 | 590 | 2444 | 1038 | 0 | 236 |
| side_right | 2 | 441 | 805 | 2731 | 6 | 275 |
| sitting | 0 | 0 | 0 | 11 | 2262 | 570 |
| edge_sit | 198 | 0 | 0 | 0 | 60 | 2064 |

#### CNN 특징 + RF

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 5535 | 0 | 0 | 0 | 0 | 0 |
| supine | 1 | 4661 | 131 | 23 | 0 | 0 |
| side_left | 373 | 719 | 2150 | 875 | 29 | 310 |
| side_right | 72 | 764 | 577 | 2491 | 5 | 351 |
| sitting | 5 | 0 | 0 | 7 | 2268 | 563 |
| edge_sit | 254 | 0 | 0 | 0 | 13 | 2055 |

#### CNN 특징 + XGBoost

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 5529 | 0 | 0 | 0 | 1 | 5 |
| supine | 0 | 4615 | 122 | 79 | 0 | 0 |
| side_left | 29 | 679 | 2070 | 1464 | 168 | 46 |
| side_right | 6 | 494 | 558 | 2967 | 16 | 219 |
| sitting | 0 | 0 | 0 | 0 | 2151 | 692 |
| edge_sit | 140 | 1 | 0 | 23 | 82 | 2076 |

#### CNN 특징 + LightGBM

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 5533 | 0 | 0 | 0 | 1 | 1 |
| supine | 0 | 4610 | 136 | 70 | 0 | 0 |
| side_left | 48 | 696 | 2142 | 1327 | 191 | 52 |
| side_right | 6 | 517 | 631 | 2824 | 50 | 232 |
| sitting | 10 | 0 | 0 | 0 | 2268 | 565 |
| edge_sit | 140 | 0 | 0 | 9 | 90 | 2083 |

## 카메라 라벨과 말로 붙인 라벨의 일치율

| 항목 | 값 |
|---|---|
| 시간 정렬된 비교의 일치율 |  |
| 시간 정렬된 비교 수 |  |
| 비교 중 분류 가능한 비율 |  |
| 카메라 비교가 없는 ToF 쌍 수 |  |
| 혼동 행렬 |  |
| 혼동 행렬 열 순서 |  |

미분류는 일치율 계산에서 오답으로 처리한다. 카메라 비교가 없으면 일치율을 0으로 대체하지 않는다.

## Pi 5 CPU 추론 측정 조건

| 항목 | 값 |
|---|---|
| 장치·스레드·워밍업·측정 횟수·전처리 포함 여부 |  |

노트북 CPU 측정이나 TorchScript 내보내기 검사는 Pi 실측 추론 시간으로 쓰지 않는다.

