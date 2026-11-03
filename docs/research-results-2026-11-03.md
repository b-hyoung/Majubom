# 오늘 연구 결과! · 2026년 11월 3일

Bed C의 ToF 자세 분류 실험을 정리했습니다. 이번 결과는 참여자 한 명의 같은 날 녹화를 회차별로 나눈 평가입니다.

## 2.3.x Deep feature extraction and machine-learning classification

Bed C was a hospital bed monitored by two tripod-mounted 8 × 8 time-of-flight (ToF) sensors, one at the foot and one at the head. The operator instructed six classes: empty, supine, left lateral, right lateral, sitting, and edge-sitting. The protocol comprised three 30-s repetitions per class, varying position for occupied-bed recordings. The analysis included 19 sessions because one supine repetition was recorded twice. Reception timestamps were used to pair sensor frames one-to-one within 250 ms. The first and last second of each session were excluded. Two cameras viewed the bed from above at opposite ends of the same long side. The other long side was outside their view. Camera predictions were used for a separate agreement analysis against the instructed labels.

Each sensor supplied three 8 × 8 channels: distance normalized over 0–4000 mm, the empty-bed reference minus the current distance (scaled by 4000 mm), and a binary validity mask. Invalid distances were set to zero in the normalized distance channel. The difference channel was set to zero where either measurement was invalid. This channel measures displacement along sensor sightlines relative to the empty bed. Its relationship to height above the bed depends on sensor geometry.

The two sensors viewed the bed from opposite ends, so matching grid indices did not denote matching physical positions. Separate 2D CNN branches processed the two grids. Each branch had three 3 × 3 convolutional layers with channels 3 → 16 → 32 → 32, stride 1, and padding 1. Their outputs were reduced to 32 features each and concatenated into a 64-dimensional vector before classification. Shared convolutional weights apply the same local filters across the grid. In contrast, each raw-distance feature identifies a fixed grid cell. Horizontal reflection with left/right label exchange was considered for training augmentation. The run reported here used no augmentation.

All five models used the same held-out repetition folds. For each fold, the CNN was trained from scratch using only the training partition. Epoch selection used an internal validation split, followed by refitting on the outer training partition before feature extraction. The comparison included a Random Forest (RF) with 128 raw distances, a standalone CNN classifier, and CNN features classified by RF, XGBoost, or LightGBM. The hybrid design allows a fixed encoder to supply features for retraining a tree classifier on a CPU when new labeled data become available.

CNN training ran on an NVIDIA GeForce RTX 4060 Laptop GPU using CUDA PyTorch 2.6.0+cu124. It used AdamW (learning rate 0.001, weight decay 0.001), a batch size of 128, and up to 60 epochs. Early stopping monitored internal validation macro-F1 with a patience of 10 epochs. The selected epoch counts were 3, 2, and 16 for the three folds, and the final refit used 3 epochs. Each tree ensemble used 200 estimators. Both RF variants used balanced class weights. XGBoost used histogram-based fitting with maximum depth 4. LightGBM used a maximum of 15 leaves and a minimum of 5 samples per leaf. The three hybrid classifiers used inverse-frequency sample weights. The run used NumPy 2.5.4, scikit-learn 1.9.1, XGBoost 3.4.1, LightGBM 4.7.0, and joblib 1.6.0. The CNN and tree models were exported for CPU inference, with Raspberry Pi 5 as the target.

## 3.1 Classifier comparison

The evaluation comprised 2,712 frame pairs from 19 sessions. The class counts were 767 empty, 504 supine, 322 left lateral, 321 right lateral, 490 sitting, and 308 edge-sitting. Predictions from the three held-out repetition folds were pooled for evaluation.

**Table 1. Classifier comparison**

| Model | Input | Accuracy | Macro-F1 | Inference on Pi CPU |
|---|---|---:|---:|---:|
| Raw-distance RF | 128 raw distances | 54.65% | 0.4882 | |
| 2D CNN | Two 3-channel ToF grids | 47.97% | 0.2923 | |
| CNN + RF | 64 CNN features | 44.10% | 0.2839 | |
| CNN + XGBoost | 64 CNN features | 37.09% | 0.2247 | |
| CNN + LightGBM | 64 CNN features | 40.56% | 0.2564 | |

The raw-distance RF had the highest accuracy and macro-F1. None of the CNN-based variants improved either metric in this comparison. The raw-distance RF correctly classified 4 of 321 right-lateral pairs and classified 203 of them as sitting. Camera predictions agreed with instructed labels in 47.93% of 2,700 time-aligned comparisons, with unclassified predictions counted as disagreements.

**Table 2. Class recall (%)**

| Model | Empty | Supine | Left lateral | Right lateral | Sitting | Edge-sitting |
|---|---:|---:|---:|---:|---:|---:|
| Raw-distance RF | 100.00 | 40.87 | 36.34 | 1.25 | 17.55 | 98.05 |
| 2D CNN | 100.00 | 4.37 | 1.86 | 32.40 | 80.00 | 3.25 |
| CNN + RF | 98.70 | 33.73 | 0.00 | 3.74 | 46.94 | 8.77 |
| CNN + XGBoost | 99.35 | 31.35 | 2.48 | 11.21 | 3.27 | 8.44 |
| CNN + LightGBM | 99.09 | 40.67 | 0.62 | 10.59 | 14.69 | 8.77 |

### Confusion matrices

Rows are instructed labels and columns are predictions. Values are frame-pair counts.

**Raw-distance RF**

| Instructed / predicted | Empty | Supine | Left lateral | Right lateral | Sitting | Edge-sitting |
|---|---:|---:|---:|---:|---:|---:|
| Empty | 767 | 0 | 0 | 0 | 0 | 0 |
| Supine | 0 | 206 | 155 | 79 | 64 | 0 |
| Left lateral | 1 | 110 | 117 | 45 | 49 | 0 |
| Right lateral | 0 | 113 | 1 | 4 | 203 | 0 |
| Sitting | 0 | 280 | 0 | 124 | 86 | 0 |
| Edge-sitting | 0 | 0 | 0 | 6 | 0 | 302 |

**2D CNN**

| Instructed / predicted | Empty | Supine | Left lateral | Right lateral | Sitting | Edge-sitting |
|---|---:|---:|---:|---:|---:|---:|
| Empty | 767 | 0 | 0 | 0 | 0 | 0 |
| Supine | 0 | 22 | 0 | 65 | 417 | 0 |
| Left lateral | 130 | 0 | 6 | 76 | 110 | 0 |
| Right lateral | 103 | 0 | 9 | 104 | 105 | 0 |
| Sitting | 1 | 0 | 0 | 97 | 392 | 0 |
| Edge-sitting | 260 | 0 | 2 | 35 | 1 | 10 |

**CNN + RF**

| Instructed / predicted | Empty | Supine | Left lateral | Right lateral | Sitting | Edge-sitting |
|---|---:|---:|---:|---:|---:|---:|
| Empty | 757 | 0 | 0 | 0 | 0 | 10 |
| Supine | 0 | 170 | 0 | 6 | 328 | 0 |
| Left lateral | 60 | 109 | 0 | 97 | 0 | 56 |
| Right lateral | 98 | 108 | 14 | 12 | 1 | 88 |
| Sitting | 0 | 162 | 0 | 0 | 230 | 98 |
| Edge-sitting | 214 | 0 | 38 | 28 | 1 | 27 |

**CNN + XGBoost**

| Instructed / predicted | Empty | Supine | Left lateral | Right lateral | Sitting | Edge-sitting |
|---|---:|---:|---:|---:|---:|---:|
| Empty | 762 | 0 | 0 | 0 | 0 | 5 |
| Supine | 28 | 158 | 15 | 100 | 163 | 40 |
| Left lateral | 7 | 94 | 8 | 120 | 3 | 90 |
| Right lateral | 79 | 93 | 39 | 36 | 1 | 73 |
| Sitting | 16 | 272 | 0 | 43 | 16 | 143 |
| Edge-sitting | 282 | 0 | 0 | 0 | 0 | 26 |

**CNN + LightGBM**

| Instructed / predicted | Empty | Supine | Left lateral | Right lateral | Sitting | Edge-sitting |
|---|---:|---:|---:|---:|---:|---:|
| Empty | 760 | 0 | 0 | 0 | 0 | 7 |
| Supine | 28 | 205 | 12 | 103 | 143 | 13 |
| Left lateral | 31 | 97 | 2 | 111 | 50 | 31 |
| Right lateral | 80 | 111 | 32 | 34 | 15 | 49 |
| Sitting | 18 | 216 | 0 | 34 | 72 | 150 |
| Edge-sitting | 281 | 0 | 0 | 0 | 0 | 27 |

### Limitations

The data came from one participant on one day, with 30-s sessions and position changes across repetitions. The evaluation measures performance on held-out repetitions under this recording protocol. Performance on new participants and Pi 5 inference latency remain unmeasured.
