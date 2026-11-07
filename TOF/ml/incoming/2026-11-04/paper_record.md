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
| epoch: 최대·묶음별 선택·최종 | {"maximum": 60, "fold_selected": [16, 6], "final": 11, "runs": [{"role": "inner_validation", "completed_epochs": 26, "train_pairs": 1803, "selected_epoch": 16}, {"role": "refit", "completed_epochs": 16, "train_pairs": 2703, "selected_epoch": null}, {"role": "inner_validation", "completed_epochs": 16, "train_pairs": 1857, "selected_epoch": 6}, {"role": "refit", "completed_epochs": 6, "train_pairs": 2712, "selected_epoch": null}, {"role": "refit", "completed_epochs": 11, "train_pairs": 5415, "selected_epoch": null}]} |
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
| 참여자 수 | 2 |
| 입력 세션 수 | 37 |
| 평가 프레임 쌍이 있는 세션 수 | 37 |
| 전체 프레임 쌍 수 | 5415 |
| 검증 방식 | 사람 단위 (Leave-One-Subject-Out) |
| 같은 사람 평가 여부 | False |
| 짝짓기·구간 제외 규칙 | 수신 시각 차이 ≤250ms, 1:1 짝짓기, 세션 앞뒤 1초 제외 |

| 클래스 | 프레임 쌍 수 |
|---|---:|
| empty | 1217 |
| supine | 954 |
| side_left | 772 |
| side_right | 772 |
| sitting | 940 |
| edge_sit | 760 |

## 분류기 비교

정확도·macro-F1·재현율은 0–1 척도이며, 완료된 학습의 외부 시험 묶음 예측만 집계한다.

| 모델 | 입력 | 정확도 | macro-F1 | Pi 5 CPU ms/프레임 쌍 |
|---|---|---:|---:|---:|
| RF 원시 | 거리 128개 | 0.2208679594 | 0.2112954886 |  |
| CNN | 센서별 3채널 8×8 | 0.5554939982 | 0.5163731038 |  |
| CNN 특징 + RF | CNN 특징 | 0.6145891043 | 0.5594051028 |  |
| CNN 특징 + XGBoost | CNN 특징 | 0.5130193906 | 0.4602910866 |  |
| CNN 특징 + LightGBM | CNN 특징 | 0.5423822715 | 0.4898120317 |  |

## 실험 조건

카메라 2대, 침대 같은 긴 면의 양 끝 위쪽에서 촬영, 반대쪽 긴 면은 보이지 않음. 카메라 쪽은 누운 사람의 오른쪽. S01은 2026-11-03, S02는 2026-11-04에 촬영. 참여자 차이와 촬영일 차이가 함께 포함된다. S02는 앞뒤 1초 제외 후 최소 150쌍 확보 방식이다.

| 입력 처리 | 값 |
|---|---|
| ROI 마스킹 적용 | False |
| 빈 침대 평가 분리 | 빈 침대의 실제 촬영 묶음으로 학습·시험을 분리하고 각 기록을 한 번만 사용 |

## 사람별 외부 시험 결과

| 시험 참여자 | 모델 | 시험 쌍 수 | 정확도 | macro-F1 |
|---|---|---:|---:|---:|
| S01 | RF 원시 | 2712 | 0.1618731563 | 0.1590327758 |
| S01 | CNN | 2712 | 0.4867256637 | 0.2869970239 |
| S01 | CNN 특징 + RF | 2712 | 0.5103244838 | 0.3106632765 |
| S01 | CNN 특징 + XGBoost | 2712 | 0.5158554572 | 0.3282288993 |
| S01 | CNN 특징 + LightGBM | 2712 | 0.5213864307 | 0.3394565934 |
| S02 | RF 원시 | 2703 | 0.2800591935 | 0.198350769 |
| S02 | CNN | 2703 | 0.624491306 | 0.6234872367 |
| S02 | CNN 특징 + RF | 2703 | 0.7192008879 | 0.7168923244 |
| S02 | CNN 특징 + XGBoost | 2703 | 0.5101738809 | 0.4948008265 |
| S02 | CNN 특징 + LightGBM | 2703 | 0.5634480207 | 0.5329311353 |

### 시험 참여자 S01 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 0 | 0.01785714286 | 0.003105590062 | 0 | 0.5653061224 | 0.4935064935 |
| CNN | 1 | 0 | 0.003105590062 | 0 | 0.8 | 0.5194805195 |
| CNN 특징 + RF | 1 | 0 | 0.003105590062 | 0 | 0.8 | 0.7272727273 |
| CNN 특징 + XGBoost | 1 | 0 | 0.01863354037 | 0.01557632399 | 0.8163265306 | 0.7175324675 |
| CNN 특징 + LightGBM | 1 | 0.02976190476 | 0.01242236025 | 0.02492211838 | 0.8163265306 | 0.7142857143 |

#### S01 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 0 | 0 | 0 | 0 | 0 | 767 |
| supine | 495 | 9 | 0 | 0 | 0 | 0 |
| side_left | 321 | 0 | 1 | 0 | 0 | 0 |
| side_right | 278 | 43 | 0 | 0 | 0 | 0 |
| sitting | 211 | 0 | 0 | 0 | 277 | 2 |
| edge_sit | 94 | 0 | 0 | 0 | 62 | 152 |

#### S01 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 767 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 0 | 0 | 0 | 372 | 132 |
| side_left | 39 | 0 | 1 | 0 | 136 | 146 |
| side_right | 4 | 0 | 0 | 0 | 206 | 111 |
| sitting | 0 | 0 | 0 | 0 | 392 | 98 |
| edge_sit | 147 | 0 | 0 | 0 | 1 | 160 |

#### S01 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 767 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 0 | 0 | 0 | 389 | 115 |
| side_left | 0 | 0 | 1 | 0 | 111 | 210 |
| side_right | 0 | 0 | 0 | 0 | 154 | 167 |
| sitting | 0 | 0 | 0 | 0 | 392 | 98 |
| edge_sit | 84 | 0 | 0 | 0 | 0 | 224 |

#### S01 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 767 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 0 | 0 | 0 | 372 | 132 |
| side_left | 1 | 6 | 6 | 0 | 207 | 102 |
| side_right | 3 | 0 | 0 | 5 | 286 | 27 |
| sitting | 0 | 0 | 0 | 0 | 400 | 90 |
| edge_sit | 87 | 0 | 0 | 0 | 0 | 221 |

#### S01 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 767 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 15 | 0 | 0 | 374 | 115 |
| side_left | 2 | 1 | 4 | 0 | 208 | 107 |
| side_right | 7 | 0 | 0 | 8 | 281 | 25 |
| sitting | 0 | 0 | 0 | 0 | 400 | 90 |
| edge_sit | 87 | 0 | 0 | 0 | 1 | 220 |

### 시험 참여자 S02 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 0 | 0.01555555556 | 1 | 0 | 0.6666666667 | 0 |
| CNN | 0.7 | 0.74 | 0.3577777778 | 0.8736141907 | 0.6666666667 | 0.4092920354 |
| CNN 특징 + RF | 1 | 0.8155555556 | 0.2555555556 | 0.8647450111 | 0.6644444444 | 0.7146017699 |
| CNN 특징 + XGBoost | 1 | 0.3044444444 | 0.3333333333 | 0.7228381375 | 0.1555555556 | 0.5442477876 |
| CNN 특징 + LightGBM | 1 | 0.6911111111 | 0.1733333333 | 0.7760532151 | 0.1511111111 | 0.5884955752 |

#### S02 · RF 원시 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 0 | 0 | 450 | 0 | 0 | 0 |
| supine | 0 | 7 | 443 | 0 | 0 | 0 |
| side_left | 0 | 0 | 450 | 0 | 0 | 0 |
| side_right | 0 | 0 | 436 | 0 | 15 | 0 |
| sitting | 0 | 0 | 150 | 0 | 300 | 0 |
| edge_sit | 431 | 0 | 20 | 1 | 0 | 0 |

#### S02 · CNN 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 315 | 0 | 0 | 0 | 0 | 135 |
| supine | 0 | 333 | 0 | 97 | 20 | 0 |
| side_left | 0 | 3 | 161 | 267 | 6 | 13 |
| side_right | 0 | 6 | 42 | 394 | 9 | 0 |
| sitting | 0 | 0 | 143 | 7 | 300 | 0 |
| edge_sit | 40 | 0 | 6 | 2 | 219 | 185 |

#### S02 · CNN 특징 + RF 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 367 | 0 | 79 | 4 | 0 |
| side_left | 27 | 0 | 115 | 308 | 0 | 0 |
| side_right | 0 | 3 | 58 | 390 | 0 | 0 |
| sitting | 0 | 1 | 107 | 43 | 299 | 0 |
| edge_sit | 109 | 0 | 1 | 4 | 15 | 323 |

#### S02 · CNN 특징 + XGBoost 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 137 | 0 | 305 | 8 | 0 |
| side_left | 1 | 0 | 150 | 291 | 8 | 0 |
| side_right | 0 | 0 | 121 | 326 | 4 | 0 |
| sitting | 0 | 227 | 132 | 18 | 70 | 3 |
| edge_sit | 113 | 0 | 0 | 31 | 62 | 246 |

#### S02 · CNN 특징 + LightGBM 혼동 행렬

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 450 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 311 | 0 | 134 | 5 | 0 |
| side_left | 1 | 0 | 78 | 368 | 3 | 0 |
| side_right | 0 | 0 | 101 | 350 | 0 | 0 |
| sitting | 1 | 96 | 104 | 45 | 68 | 136 |
| edge_sit | 108 | 0 | 5 | 11 | 62 | 266 |

### 클래스별 재현율

| 모델 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| RF 원시 | 0 | 0.01677148847 | 0.5841968912 | 0 | 0.6138297872 | 0.2 |
| CNN | 0.8890714873 | 0.3490566038 | 0.2098445596 | 0.5103626943 | 0.7361702128 | 0.4539473684 |
| CNN 특징 + RF | 1 | 0.3846960168 | 0.1502590674 | 0.5051813472 | 0.735106383 | 0.7197368421 |
| CNN 특징 + XGBoost | 1 | 0.14360587 | 0.2020725389 | 0.4287564767 | 0.5 | 0.6144736842 |
| CNN 특징 + LightGBM | 1 | 0.3417190776 | 0.1062176166 | 0.4637305699 | 0.4978723404 | 0.6394736842 |

### 혼동 행렬

행은 지시 라벨, 열은 예측 라벨이며 순서는 위 클래스 표와 같다.

#### RF 원시

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 0 | 0 | 450 | 0 | 0 | 767 |
| supine | 495 | 16 | 443 | 0 | 0 | 0 |
| side_left | 321 | 0 | 451 | 0 | 0 | 0 |
| side_right | 278 | 43 | 436 | 0 | 15 | 0 |
| sitting | 211 | 0 | 150 | 0 | 577 | 2 |
| edge_sit | 525 | 0 | 20 | 1 | 62 | 152 |

#### CNN

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1082 | 0 | 0 | 0 | 0 | 135 |
| supine | 0 | 333 | 0 | 97 | 392 | 132 |
| side_left | 39 | 3 | 162 | 267 | 142 | 159 |
| side_right | 4 | 6 | 42 | 394 | 215 | 111 |
| sitting | 0 | 0 | 143 | 7 | 692 | 98 |
| edge_sit | 187 | 0 | 6 | 2 | 220 | 345 |

#### CNN 특징 + RF

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1217 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 367 | 0 | 79 | 393 | 115 |
| side_left | 27 | 0 | 116 | 308 | 111 | 210 |
| side_right | 0 | 3 | 58 | 390 | 154 | 167 |
| sitting | 0 | 1 | 107 | 43 | 691 | 98 |
| edge_sit | 193 | 0 | 1 | 4 | 15 | 547 |

#### CNN 특징 + XGBoost

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1217 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 137 | 0 | 305 | 380 | 132 |
| side_left | 2 | 6 | 156 | 291 | 215 | 102 |
| side_right | 3 | 0 | 121 | 331 | 290 | 27 |
| sitting | 0 | 227 | 132 | 18 | 470 | 93 |
| edge_sit | 200 | 0 | 0 | 31 | 62 | 467 |

#### CNN 특징 + LightGBM

| 지시 라벨 / 예측 | empty | supine | side_left | side_right | sitting | edge_sit |
|---|---:|---:|---:|---:|---:|---:|
| empty | 1217 | 0 | 0 | 0 | 0 | 0 |
| supine | 0 | 326 | 0 | 134 | 379 | 115 |
| side_left | 3 | 1 | 82 | 368 | 211 | 107 |
| side_right | 7 | 0 | 101 | 358 | 281 | 25 |
| sitting | 1 | 96 | 104 | 45 | 468 | 226 |
| edge_sit | 195 | 0 | 5 | 11 | 63 | 486 |

## 카메라 라벨과 말로 붙인 라벨의 일치율

| 항목 | 값 |
|---|---|
| 시간 정렬된 비교의 일치율 | 0.5627432808 |
| 시간 정렬된 비교 수 | 5395 |
| 비교 중 분류 가능한 비율 | 0.5992585728 |
| 카메라 비교가 없는 ToF 쌍 수 | 20 |
| 혼동 행렬 | [[0, 0, 3, 0, 0, 0, 1210], [0, 668, 27, 61, 0, 0, 196], [0, 0, 605, 0, 0, 0, 165], [0, 0, 0, 770, 0, 0, 0], [0, 0, 0, 0, 462, 3, 465], [0, 0, 0, 0, 103, 531, 126]] |
| 혼동 행렬 열 순서 | ["empty", "supine", "side_left", "side_right", "sitting", "edge_sit", "unclassified"] |

미분류는 일치율 계산에서 오답으로 처리한다. 카메라 비교가 없으면 일치율을 0으로 대체하지 않는다.

## Pi 5 CPU 추론 측정 조건

| 항목 | 값 |
|---|---|
| 장치·스레드·워밍업·측정 횟수·전처리 포함 여부 |  |

노트북 CPU 측정이나 TorchScript 내보내기 검사는 Pi 실측 추론 시간으로 쓰지 않는다.

## 기존 S01 최종 모델을 그대로 S02에 적용한 평가

| 모델 | 정확도 | macro-F1 |
|---|---:|---:|
| RF 원시 | 0.28005919 | 0.19835077 |
| CNN | 0.65223825 | 0.63317213 |
| CNN 특징 + RF | 0.64002960 | 0.63217683 |
| CNN 특징 + XGBoost | 0.52275250 | 0.51319782 |
| CNN 특징 + LightGBM | 0.47872734 | 0.48078360 |

## 카메라 라벨과 지시 라벨의 구간별 일치율

| 구간 | 비교 수 | 일치율 | 분류 가능 비율 |
|---|---:|---:|---:|
| 전체 | 5395 | 0.56274328 | 0.59925857 |
| S01 | 2700 | 0.47925926 | 0.52851852 |
| S01/empty | 765 | 0.00000000 | 0.00000000 |
| 자세/empty | 1213 | 0.00000000 | 0.00247321 |
| S01/supine | 503 | 0.58051690 | 0.64015905 |
| 자세/supine | 952 | 0.70168067 | 0.79411765 |
| 사람 있는 구간 | 4182 | 0.72596844 | 0.77235772 |
| S01/사람 있는 구간 | 1935 | 0.66873385 | 0.73746770 |
| 위치/가운데 | 1091 | 0.80109991 | 0.80201650 |
| 위치/카메라 반대쪽 가장자리 | 859 | 0.75436554 | 0.75436554 |
| 위치/카메라 쪽 가장자리 | 1566 | 0.79374202 | 0.91507024 |
| S01/side_left | 322 | 0.96583851 | 0.96583851 |
| 자세/side_left | 770 | 0.78571429 | 0.78571429 |
| 방향/카메라에 등을 보임 | 770 | 0.78571429 | 0.78571429 |
| S01/side_right | 321 | 1.00000000 | 1.00000000 |
| 자세/side_right | 770 | 1.00000000 | 1.00000000 |
| 방향/카메라를 향함 | 770 | 1.00000000 | 1.00000000 |
| S01/sitting | 481 | 0.34303534 | 0.34303534 |
| 자세/sitting | 930 | 0.49677419 | 0.50000000 |
| S01/edge_sit | 308 | 0.66558442 | 1.00000000 |
| 자세/edge_sit | 760 | 0.69868421 | 0.83421053 |
| S02 | 2695 | 0.64638219 | 0.67012987 |
| S02/empty | 448 | 0.00000000 | 0.00669643 |
| S02/supine | 449 | 0.83741648 | 0.96659243 |
| S02/사람 있는 구간 | 2247 | 0.77525590 | 0.80240320 |
| S02/side_left | 448 | 0.65625000 | 0.65625000 |
| S02/side_right | 449 | 1.00000000 | 1.00000000 |
| S02/sitting | 449 | 0.66146993 | 0.66815145 |
| S02/edge_sit | 452 | 0.72123894 | 0.72123894 |

개별 구간 혼동 행렬은 camera-stratified.json에 기록했다. 카메라 일치율은 수신 시각과 heartbeat 오프셋에 기반한다. 걸터앉기 라벨을 정답 후보로 사용할지는 해당 구간 일치율과 사람 검토를 함께 확인한다.

## ROI 확인

기존 S01 및 이번 입력 전처리는 roi_mask=false. Pi /tof/latest는 수신 거리 전체를 전달. Pi 운영 predict_posture.py의 별도 ROI와 구분. 비마스킹 모델 5종을 학습함.

ROI 적용 모델을 추가로 학습한 실험은 수행하지 않았다. 따라서 ROI 제거의 성능 차이는 빈칸으로 둔다.

## 결과 해석 범위

방향별 외부 시험 지표는 각각 따로 보고한다. 37세션에는 S01 재촬영 1개가 포함된다. S02의 이전 30초 녹화와 S03용 빈 침대는 이번 자료에서 제외했다. 최종 통합 모델은 모든 자료로 학습했으며, 같은 자료의 학습 정확도는 시험 성능으로 보고하지 않았다.

## 카메라와 ToF 학습의 구분

ToF 학습 입력은 두 센서 거리와 빈 침대 기준 차이·유효 칸 마스크다. 정답은 운영자가 말로 지정한 세션 라벨이다. 카메라 판정은 별도 일치율 평가에만 사용했다. 카메라 판정으로 ToF 값이나 라벨을 교정한 횟수는 0이다. 학습에 사용한 원본 JSONL 37개를 복사 원본과 SHA-256으로 대조해 동일함을 확인했다.
