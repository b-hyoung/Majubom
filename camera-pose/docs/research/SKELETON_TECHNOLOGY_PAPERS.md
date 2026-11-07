# 스켈레톤 인식 기술

[문서 목차](../README.md) · [분야 목차](README.md) · [현재 상태](../PROJECT_STATUS.md)


[논문 안내](LITERATURE_REVIEW.md) · [침대 자세 논문](BED_POSTURE_PAPERS.md) · [기술 후보 비교](TECHNOLOGY_CANDIDATES.md) · [프로젝트 홈](../../README.md)

**기준일: 2026-10-26 · 13편 · 관련 본문 확인 13편**

관절 추출·프레임워크 7편과, 추출한 관절의 시계열·행동 분석 및 리뷰 6편입니다. 관절을 찾는 모델과 관절로 자세·동작을 분류하는 모델은 역할이 다릅니다.

> **확인 범위:** 원문 또는 공개 저자판의 방법·평가 조건·결과를 대조했습니다. 확인한 판본·절·표는 각 항목에 표시합니다. 문헌 검토는 코드 실행·성능 재현이나 침대 환경의 정확도 보증을 뜻하지 않습니다.

## 빠른 안내

- **MediaPipe 이해:** [P29](#p29) 처리 프레임워크 → [P05](#p05) BlazePose 2D → [P26](#p26) GHUM 3D.
- **관절 추출 비교:** [P06](#p06) RTMPose, [P27](#p27) OpenPose 원형, [P37](#p37) OpenPose 확장, [P28](#p28) HRNet.
- **시간·행동 분석:** [P10](#p10) TCN → [P07](#p07)·[P08](#p08) 그래프 → [P09](#p09) 히트맵 시계열. [P18](#p18)은 전체 연구 지도, [P24](#p24)는 잡음·결측 연구입니다.

## 논문 한눈에 보기

| 논문 | 기술·역할 | 구분 |
|---|---|---|
| [P05 · BlazePose](#p05) | 33개 2D 랜드마크 | 관절 추출 |
| [P06 · RTMPose](#p06) | CSPNeXt·SimCC·GAU | 관절 추출 |
| [P26 · BlazePose GHUM Holistic](#p26) | 몸·손 3D 추정·GHUM Lifter | 관절 추출·3D 표현 |
| [P27 · OpenPose / PAF](#p27) | 관절 히트맵·신체 연결 | 관절 추출 |
| [P37 · OpenPose 확장(2021)](#p37) | PAF 중심 반복 정교화·발 관절 통합 | 관절 추출·후속 확장 |
| [P28 · HRNet](#p28) | 병렬 해상도·반복 융합 | 관절 추출 |
| [P29 · MediaPipe 프레임워크](#p29) | Packet·Stream·Calculator·Graph | 실행 프레임워크 |
| [P07 · ST-GCN](#p07) | 관절의 공간·시간 그래프 | 후속 행동 분류 |
| [P08 · 2s-AGCN](#p08) | 관절·뼈 두 흐름·적응형 그래프 | 후속 행동 분류 |
| [P09 · PoseC3D](#p09) | 관절 히트맵 시계열·3D CNN | 후속 행동 분류 |
| [P10 · TCN 비교 연구](#p10) | 인과적·팽창 1D 합성곱 | 일반 시계열 모델링 |
| [P18 · 스켈레톤 낙상 탐지 리뷰](#p18) | 추정기·전처리·시간 모델·평가 | 리뷰 |
| [P24 · Deep State-Space Model](#p24) | DSSM + 양방향 LSTM | 잡음·결측 후속 분석 |

## 관절 추출과 실행 프레임워크

<a id="p05"></a>
### P05 · BlazePose

**BlazePose: On-device Real-time Body Pose tracking**

**저자·출처:** Valentin Bazarevsky 외. arXiv:2006.10204, 2020, 프리프린트.<br>
**링크:** [논문](https://arxiv.org/abs/2006.10204) · [확인한 본문 v1](https://arxiv.org/pdf/2006.10204v1)<br>
**확인 범위:** 본문 확인 — arXiv:2006.10204v1의 §2.2–2.5, §3, Table 1, 결론.

- **기술·흐름:** 얼굴 검출을 인체 위치의 단서로 삼는 검출기와, 인물 영역에서 33개 랜드마크를 추적하는 네트워크로 구성합니다. 추적이 실패하면 검출기를 다시 실행합니다. 학습 때 히트맵·오프셋과 좌표 회귀를 함께 쓰지만 추론 때 히트맵 분기를 제거해 비용을 줄입니다.
- **자료·평가:** 일반 자세 약 60,000장과 운동 자세 약 25,000장으로 학습합니다. AR 1,000장·요가 1,000장 평가에서는 출력 33개 전체가 아니라 COCO와 공통인 17개 관절을 사용합니다. 몸통 크기로 정규화한 PCK@0.2를 계산합니다.
- **결과:** Full 모델은 AR 84.1%, Yoga 84.5%를 보고합니다. Table 1과 각주에서 확인한 Full **10 FPS**·Lite **31 FPS**는 Pixel 2 단일 코어 조건입니다. OpenPose 행은 다른 장치이므로 단순한 동일 장치 속도 비교로 읽으면 안 됩니다.
- **범위·해석:** **2020년 이 논문은 2D 자세 추정 연구**이며 결론에서 3D 확장을 향후 방향으로 제시합니다. 현재 MediaPipe의 3D 출력이나 GHUM 모델을 이 논문의 실험 결과로 소급하지 않습니다.
- **한계·활용 제안:** 머리가 보인다는 검출 가정은 이불·옆누움에서 실패할 수 있습니다. 현재 배포 모델의 이름·버전을 고정하고 침대 영상에서 검출 실패율을 따로 측정합니다.


<a id="p06"></a>
### P06 · RTMPose

**RTMPose: Real-Time Multi-Person Pose Estimation based on MMPose**

**저자·출처:** Tao Jiang 외. arXiv:2303.07399, 2023, 프리프린트.<br>
**링크:** [논문](https://arxiv.org/abs/2303.07399) · [확인한 본문 v2](https://arxiv.org/pdf/2303.07399v2)<br>
**확인 범위:** 본문 확인 — arXiv:2303.07399v2, §3, §4.3, Tables 11–12.

- **기술·흐름:** CSPNeXt 특징 추출기, SimCC 방식의 x/y 1차원 좌표 분류, GAU attention을 사용합니다. top-down 방식이므로 사람 영역을 먼저 얻어야 합니다. 논문의 전체 경로는 RTMDet-nano 검출기와 RTMPose를 연결합니다.
- **시험 조건:** 관절 모델 입력 256×192와 검출기 입력 320×320을 구분하며, Tables 11–12는 서로 다른 추론 경로에서 batch 1의 시간을 보고합니다.
- **결과·검증:** Table 11의 관절 추정기 단독 지연과 Table 12의 검출기 포함 전체 경로 지연은 서로 다릅니다. 관절 모델만의 속도를 전체 카메라 시스템의 FPS로 쓰지 않습니다. 두 표의 AP도 평가 경로가 달라 동일 조건 수치처럼 합치지 않습니다.
- **한계·활용 제안:** COCO 등 일반 자세 벤치마크이며 침대 이불 조건 검증이 아닙니다. MediaPipe와 비교할 때는 인물 검출·전처리·좌표 변환까지 포함한 지연과 동일 관절 정의를 사용합니다.


- **학습·추적:** COCO와 AI Challenger를 함께 사용하는 학습 설정을 비교합니다. 검출을 K프레임마다 수행하고 중간 프레임에는 이전 자세로 영역을 갱신하며, OneEuro 필터로 시간적 흔들림을 줄입니다. 평활화 전후의 좌표와 지연은 별도 평가 대상입니다([본문 §3.2–3.5](https://arxiv.org/html/2303.07399v2)).


<a id="p26"></a>
### P26 · BlazePose GHUM Holistic

**BlazePose GHUM Holistic: Real-time 3D Human Landmarks and Pose Estimation**

**저자·출처:** Ivan Grishchenko 외, 2022. [CVPR 2022 연계 CV4ARVR 워크숍 Extended Abstract](https://xr.cornell.edu/workshop/2022/papers) / arXiv:2206.11678.<br>
**원문:** [확인한 본문](https://arxiv.org/html/2206.11678v1)<br>
**확인 범위:** 본문 §2–4, Tables 1–4 확인. 코드 실행·재현은 수행하지 않았습니다.

- **방법·정답 생성:** 2D 관절·실루엣에 GHUM 인체 모델을 맞춰 3D 학습 정답을 구성합니다. 사람이 표시한 신체 부위 깊이 순서를 추가해 fitting 단계의 깊이 순서 오류를 25%에서 3%로 낮췄습니다. 이 수치는 최종 모델의 자세 분류 오류율이 아닙니다.
- **출력:** 골반 중심 상대 3D 몸 좌표와 각 손 21개 랜드마크. 손은 영역 재크롭 후 별도 모델로 추정합니다. GHUM Lifter는 33+21+21개 좌표를 자세·형상 매개변수로 변환합니다.
- **몸 관절 평가:** 카메라에서 2–4m 떨어진 단일 인물 요가 이미지, 공통 COCO 17관절 조건입니다.

| 모델 | 2D mAP | 3D MAE(mm) |
|---|---:|---:|
| Heavy | 68.1 | 36 |
| Full | 62.6 | 39 |
| Lite | 45.0 | 45 |

- **별도 실험:** Lifter는 선별된 GHUM fitting 정답이 있는 어려운 자세 이미지 10,000장으로 평가합니다. Table 4의 MPJPE와 위 Table 1의 MAE를 혼합하지 않습니다.
- **한계·프로젝트 해석:** 단안 추정·모델 fitting 정답을 깊이 센서 실측이나 침대 절대 좌표와 동일시하지 않습니다. 침대·이불 환경에서 36mm 오차가 보장된다는 증거는 아닙니다.


<a id="p27"></a>
### P27 · OpenPose / PAF

**Realtime Multi-Person 2D Pose Estimation using Part Affinity Fields**

**저자·출처:** Zhe Cao, Tomas Simon, Shih-En Wei, Yaser Sheikh. CVPR 2017; arXiv 최초 공개 2016. OpenPose의 기반 논문.<br>
**원문:** [확인한 본문](https://arxiv.org/html/1611.08050v2)<br>
**확인 범위:** 본문 §2–3, Tables 1–4, Fig.9 실패 사례 확인.

- **방법:** 관절 confidence map과 신체 부위 방향 벡터장(PAF)을 공동 학습합니다. 후보 관절 사이의 선분에서 PAF와 방향 일치도를 계산하고, 연결 후보를 사람별 스켈레톤으로 조립하는 bottom-up 구조입니다.
- **자료·평가:** MPII 다중 인물 및 COCO 2016을 평가합니다. MPII 전체 시험 mAP 75.6, COCO test-dev AP 61.8을 보고합니다. 서로 다른 데이터의 지표를 합치지 않습니다.
- **속도:** 논문의 제한된 입력·19명 조건에서 처리 속도를 평가했습니다. CNN 처리와 관절 연결 시간을 구분해야 하며, 표의 연결 최적화 시간만을 전체 추론 시간으로 쓰지 않습니다.
- **실패·한계:** 드문 자세, 관절 누락·오검출, 겹치는 관절, 서로 다른 사람의 관절 연결, 조각상·동물의 오검출 사례를 제시합니다. 작은 인물에서 top-down 방식보다 불리한 이유도 설명합니다.
- **프로젝트 해석:** 연결 원리와 비교 기준으로 유용하지만 단일 침대 인물에 가장 좋은 모델이라는 근거는 아닙니다. 다른 OpenPose 버전·게재 논문의 성능을 이 2017 원문과 혼합하지 않습니다.


<a id="p28"></a>
### P28 · HRNet

**Deep High-Resolution Representation Learning for Human Pose Estimation**

**저자·출처:** Ke Sun, Bin Xiao, Dong Liu, Jingdong Wang. CVPR 2019. HRNet.<br>
**원문:** [확인한 본문](https://arxiv.org/html/1902.09212v1)<br>
**확인 범위:** 본문 §3–4, Tables 1·6 및 평가 절차 확인.

- **방법:** 고해상도 경로를 계속 유지하며 저해상도 경로를 병렬 추가하고 정보를 반복 융합합니다. 마지막 고해상도 특징에서 Gaussian 정답 히트맵에 대한 MSE로 관절 위치를 학습합니다.
- **평가:** COCO·MPII·PoseTrack. COCO는 사람 검출기를 사용하고 원본·좌우 반전 히트맵을 평균합니다. MPII는 제공된 인물 박스와 다중 스케일 평가를 사용합니다.

| COCO 검증 설정 | AP |
|---|---:|
| W32, ImageNet 사전학습, 256×192 | 74.4 |
| W48, ImageNet 사전학습, 384×288 | 76.3 |
| 융합 제거 실험: 마지막만 / 단계 사이 / 단계 사이·내부 | 70.8 / 71.9 / 73.4 |

- **결과 해석:** 반복 융합의 효과를 제거 실험으로 제시합니다. 모델 크기·입력 크기·사전학습·반전 평가가 달라지는 결과를 동일 조건으로 비교하지 않습니다.
- **한계·프로젝트 해석:** 높은 공간 해상도가 침대 가림 해결을 보장하지는 않습니다. 관절 좌표 품질 비교 후보이며, 인물 검출을 포함한 비용과 실제 침대 관절 누락을 별도 측정해야 합니다.


<a id="p29"></a>
### P29 · MediaPipe 프레임워크

**MediaPipe: A Framework for Building Perception Pipelines**

**저자·출처:** Camillo Lugaresi 외. arXiv:1906.08172, 2019.<br>
**원문:** [확인한 본문](https://arxiv.org/html/1906.08172v1)<br>
**확인 범위:** 본문 §3 구조, §4 실행·동기화, §5 도구, §6 적용 사례 확인.

- **역할:** 관절 모델 자체가 아니라 다양한 인식 모델과 처리를 연결하는 프레임워크 연구입니다.
- **구조:** Packet은 타임스탬프와 데이터를, Stream은 시간 순 데이터 흐름을, Calculator는 처리 단위를 나타냅니다. Graph가 이를 연결합니다.
- **실행:** 여러 입력을 타임스탬프로 동기화하며 처리 흐름의 스케줄링을 지원합니다. Tracer와 Visualizer로 지연·버퍼 누적·처리 흐름을 분석합니다.
- **적용 사례:** 느린 검출과 빠른 추적을 병렬로 실행하고 검출 결과를 대응 프레임과 맞추는 객체 검출 사례를 설명합니다. 33관절 정확도나 침대 자세 분류의 성능표를 제시하는 논문은 아닙니다.
- **프로젝트 해석:** 촬영 시각과 좌표 결과를 일치시켜 저장하는 시스템 설계 참고입니다. MediaPipe 프레임워크 논문, BlazePose 2D 모델, GHUM 3D 모델의 근거를 구분합니다. 2019년 구조 설명을 현재 API 사용법으로 그대로 적용하지 않습니다.


<a id="p37"></a>
### P37 · OpenPose 2017 기반 확장 연구(2021 TPAMI)

**OpenPose: Realtime Multi-Person 2D Pose Estimation Using Part Affinity Fields**

**저자·출처:** Zhe Cao, Gines Hidalgo, Tomas Simon, Shih-En Wei, Yaser Sheikh. *IEEE Transactions on Pattern Analysis and Machine Intelligence*, 43(1), 172–186, 2021.<br>
**원문:** [DOI](https://doi.org/10.1109/TPAMI.2019.2929257) · [arXiv 전체 공개판 PDF](https://arxiv.org/pdf/1812.08008) · [PubMed](https://pubmed.ncbi.nlm.nih.gov/31331883/)<br>
**재검증 범위 (2026-10-26):** **공개 논문 PDF 전체의 모델·실험·결과를 대조** — §3 PAF·추정 구조, §5.2 Table 5(구조 비교), §5.3 Table 6(속도) 및 Fig. 12(사람 수에 따른 지연), 한계. 최종 IEEE 조판본의 전표 수치 대조는 별도 확인 필요.

- **P27과 관계:** P27은 [CVPR 2017 원형](#p27), P37은 **같은 연구진의 후속 확장 연구**. 기술군은 같아도 정식 게재 제목/저자/실험이 달라 **별도 한 편**으로 센다.
- **본문 — 모델:** 기존 PAF와 confidence map 동시 다단계 정교화 대신 **PAF 정교화 중심으로 구조 개선**; 몸통·발 키포인트 **공동 검출 모델**을 제안. OpenPose 시스템은 신체·손·얼굴 관절 기능도 설명하지만, 모든 관절이 단일 동일 네트워크에서 항상 추출되는 것은 아님.
- **본문 — 실험/성능:** Table 5의 COCO validation 상 5 PAF - 1 confidence map 구조 **AP 65.3**(논문 설정; COCO AP), 3 PAF - 1 confidence map **AP 64.6**입니다. Table 6의 처리 속도 비교는 특정 실험 조건의 결과로, 침대 환경에서의 실시간 처리 속도로 전용하지 않습니다.
- **본문 — 계산 특성:** Fig. 12는 OpenPose CNN 계산이 인물 수 변화에 상대적으로 덜 민감하지만 관절 연결 계산 비용은 남는다고 비교. 논문의 다인물·보행/일반 포즈 성능을 **단일 침대 위 이불 가림이나 누운 자세 성능으로 가정 금지**.
- **프로젝트 활용 제안:** 연구용 OpenPose의 모델 버전·발 관절 유무·전처리 전체 경로를 고정하여 다른 2D 관절 후보와 비교. 실제 침대 영상의 관절 누락/시간 비용은 독자적으로 측정.

## 후속 시계열·행동 분석과 리뷰

<a id="p07"></a>
### P07 · ST-GCN

**Spatial Temporal Graph Convolutional Networks for Skeleton-Based Action Recognition**

**저자·출처:** Sijie Yan, Yuanjun Xiong, Dahua Lin. AAAI, 2018.<br>
**링크:** [저자 공개 원문](https://arxiv.org/abs/1801.07455) · [확인한 본문 v2](https://arxiv.org/pdf/1801.07455v2)<br>
**확인 범위:** 본문 확인 — 모델 구조, 실험 설정, Tables 2–3.

- **기술·흐름:** 관절을 노드, 뼈 연결을 공간 간선, 같은 관절의 인접 프레임 연결을 시간 간선으로 구성합니다. 9개 ST-GCN 층(출력 채널 64·128·256 각 3층), 시간 커널 9, 학습 가능한 간선 가중치, 전역 pooling과 softmax로 행동을 분류합니다.
- **입력·자료:** Kinetics는 **OpenPose의 18개 2D 관절(x, y, confidence)**을 사용합니다. NTU RGB+D는 **Kinect의 25개 3D 관절**을 사용합니다. 같은 ST-GCN이라도 입력의 측정 방식과 차원이 다릅니다.
- **평가·결과:** NTU 60개 행동에서 사람을 나누는 cross-subject 81.5%, 시점을 나누는 cross-view 88.3%를 보고합니다. Kinetics 400개 행동의 top-1은 30.7%입니다. 이들은 관절 검출 정확도가 아니라 주어진 행동 클래스의 분류 정확도입니다.
- **검증·한계:** 일반 행동 자료 성능이며, 누운 자세·이탈 예측·장시간 오경보 평가가 아닙니다.
- **활용 제안:** 좌표 시계열의 구조적 관계를 쓰는 후속 기준선입니다. 먼저 적은 데이터에서 규칙·MLP·TCN과 비교하고 실제 가림에 의한 연속 결측을 별도로 시험합니다.


<a id="p08"></a>
### P08 · 2s-AGCN

**Two-Stream Adaptive Graph Convolutional Networks for Skeleton-Based Action Recognition**

**저자·출처:** Lei Shi, Yifan Zhang, Jian Cheng, Hanqing Lu. CVPR, 2019.<br>
**링크:** [저자 공개 원문](https://arxiv.org/abs/1805.07694) · [확인한 본문 v3](https://arxiv.org/pdf/1805.07694v3)<br>
**확인 범위:** 본문 확인 — §5.1–5.3, Tables 1–4.

- **기술·흐름:** 관절 좌표와 뼈의 방향·길이 벡터를 서로 다른 흐름으로 처리한 뒤 결과를 결합합니다. 고정 인체 연결뿐 아니라 학습 가능한 공통 그래프와 입력 표본별 관계를 사용합니다.
- **자료·학습:** NTU의 Kinect 25관절과 Kinetics의 OpenPose 18관절을 사용합니다. NTU는 최대 300프레임 길이, Kinetics는 150프레임을 사용합니다. NTU 설정은 batch 64, Nesterov SGD, 50 epochs이며 학습률을 30·40 epoch에 낮춥니다.
- **결과:** NTU cross-subject 88.5%, cross-view 95.1%, Kinetics top-1 36.1%를 보고합니다.
- **검증·해석:** 제거 실험에서 ST-GCN 기준선도 전처리·학습 설정을 바꾸면 NTU cross-view 88.3%에서 92.7%로 개선됩니다. 논문 간 전체 차이를 모두 적응형 그래프의 효과라고 설명하지 않습니다. 모델 구조뿐 아니라 학습 조건도 비교해야 합니다.
- **한계·활용 제안:** 가려진 관절 좌표를 실제로 복원했다는 증거는 아닙니다. 작은 침대 데이터에서는 두 흐름의 추가 복잡도와 과적합을 확인하고, 같은 전처리·분할로 한 흐름 및 고정 그래프와 비교합니다.


<a id="p09"></a>
### P09 · PoseC3D

**Revisiting Skeleton-based Action Recognition**

**저자·출처:** Haodong Duan 외. CVPR, 2022(프리프린트 2021). 모델명 PoseC3D.<br>
**링크:** [논문](https://arxiv.org/abs/2104.13586) · [확인한 본문 v2](https://arxiv.org/pdf/2104.13586v2)<br>
**확인 범위:** 본문 확인 — §3·§4, Appendix D/Table 13. 코드에서 널리 쓰는 PoseC3D 명칭과 본문의 PoseConv3D 표현을 함께 확인했습니다.

- **기술·흐름:** 기본 영상 경로는 **Faster R-CNN(ResNet-50) 인물 검출 → HRNet 관절 추정 → Gaussian 관절 히트맵 시계열 → SlowOnly 3D CNN**입니다. 여기서 3D는 시간·영상 높이·너비이며 실측 3차원 관절을 의미하지 않습니다.
- **실험:** FineGYM, NTU, Kinetics 등을 사용하고, 관절 품질·해상도·샘플링·검출 박스의 영향을 비교합니다. FineGYM 주요 실험은 운동선수의 **정답 박스**를 사용합니다. 부록에서는 검출·추적 박스 조건도 따로 비교합니다.
- **결과·검증:** 검출기와 관절 추정기가 포함된 실제 운용 경로와 정답 박스를 준 조건을 분리해야 합니다. 1-clip과 여러 clip 평균, 앙상블 결과도 서로 다른 평가 설정입니다. 임의의 사지 관절 누락에 대한 강건성 시험을 지속적인 이불 가림의 해결 증거로 읽지 않습니다.
- **한계·활용 제안:** 좌표 GCN의 대안이지만 인물·관절 추정과 히트맵 생성의 총비용이 있습니다. 동일한 입력 관절로 GCN·TCN과 비교해야 표현 방식의 효과를 분리할 수 있습니다.


<a id="p10"></a>
### P10 · TCN 비교 연구

**An Empirical Evaluation of Generic Convolutional and Recurrent Networks for Sequence Modeling**

**저자·출처:** Shaojie Bai, J. Zico Kolter, Vladlen Koltun. arXiv:1803.01271, 2018, 프리프린트.<br>
**링크:** [논문](https://arxiv.org/abs/1803.01271) · [확인한 본문 v2](https://arxiv.org/pdf/1803.01271v2)<br>
**확인 범위:** 본문 확인 — §3–5, Table 1, Appendix A.

- **기술·흐름:** 미래 값을 보지 않는 인과적 1D 합성곱, 팽창 합성곱, 잔차 연결, weight normalization과 dropout으로 TCN을 구성합니다. 팽창률·커널·층수로 과거를 보는 범위가 결정됩니다.
- **실험 설계:** adding/copy-memory, 순차 MNIST, 음악 모델링, 언어 모델링 등 여러 과제에서 LSTM·GRU·RNN과 비교합니다. 가능한 한 파라미터 규모를 맞추지만 과제별 하이퍼파라미터와 기억 길이는 다릅니다.
- **결과·검증:** 순차 MNIST 99.0%, permuted MNIST 97.2% 등은 해당 과제의 성능입니다. 침대 자세나 이탈 탐지 성능이 아니며, 모든 특화 모델보다 항상 우수하다는 결론도 아닙니다.
- **한계·활용 제안:** LSTM만 선택하지 않고 TCN을 비교할 근거로 사용합니다. 우리 시험에서는 동일 관절·같은 시간 창·같은 참가자 분할로 평가하고, 모델의 과거 수용 범위와 실제 경고 지연을 함께 기록합니다.


<a id="p18"></a>
### P18 · 스켈레톤 낙상 탐지 리뷰

**Advances in Skeleton-Based Fall Detection in RGB Videos: From Handcrafted to Deep Learning Approaches**

**저자·출처:** Van-Ha Hoang, Jong Weon Lee, Md. Jalil Piran, Chun-Su Park. *IEEE Access*, 11, 92322–92352, 2023.<br>
**링크:** [DOI: 10.1109/ACCESS.2023.3307138](https://doi.org/10.1109/ACCESS.2023.3307138) · [공식 기관 초록](https://pure.skku.edu/en/publications/advances-in-skeleton-based-fall-detection-in-rgb-videos-from-hand/) · [저자 비교표 저장소](https://github.com/hahv/Advances-in-Skeleton-Based-Fall-Detection-in-RGB-Videos)<br>
**확인 범위:** 본문 확인 — [저자 게재 승인본](https://www.researchgate.net/publication/373286744_Advances_in_Skeleton-Based_Fall_Detection_in_RGB_Videos_from_Handcrafted_to_Deep_Learning_Approaches), §IV의 관절 추출·전처리·분류·평가 지표 및 비교표. 최종 편집 전 버전입니다.

- **기술 지도:** OpenPose·AlphaPose·MoveNet·MediaPipe 등의 관절 추출과, 규칙·MLP·CNN·LSTM/GRU·GCN 계열의 낙상 분류를 분리해 정리합니다. 이 모델들을 모두 연결한 하나의 저자 구현이 아닙니다.
- **구체 내용:** 불필요한 사람·관절 제거, 결측 보간, 몸 중심·영상 크기 정규화, 시간 창 길이·stride, 창의 라벨 부여, 데이터 분할을 비교합니다. 따라서 모델 이름만 같아도 전처리와 평가 단위가 다를 수 있습니다.
- **검증·해석:** 본문의 MediaPipe/AlphaPose 기능 설명은 2023년 당시 상태입니다. 현재 라이브러리 기능으로 그대로 인용하지 않습니다. 비교표의 서로 다른 데이터·분할 성능도 직접 순위로 바꾸지 않습니다.
- **활용 제안:** 자체 실험에 관절 획득·결측 처리·시간 창·사람 단위 분할을 함께 기록하고, 낙상 분류 정확도 외에 장시간 오경보를 평가합니다. 이 리뷰의 낙상 탐지를 침대 이탈 예측과 같은 과제로 취급하지 않습니다.


<a id="p24"></a>
### P24 · Deep State-Space Model

**Deep State-Space Model for Noise Tolerant Skeleton-Based Action Recognition**

**저자·출처:** Kazuki Kawamura, Takashi Matsubara, Kuniaki Uehara. *IEICE Transactions on Information and Systems*, E103.D(6), 1217–1225, 2020.<br>
**링크:** [DOI: 10.1587/transinf.2019MVP0012](https://doi.org/10.1587/transinf.2019MVP0012) · [J-STAGE](https://www.jstage.jst.go.jp/article/transinf/E103.D/6/E103.D_2019MVP0012/_article/-char/ja) · [저자 기관 PDF](https://da.lib.kobe-u.ac.jp/da/kernel/90007346/90007346.pdf)<br>
**확인 범위:** 본문 확인 — §4의 NTU 실험, Tables 1–2, §4.5 및 Figs.7–8.

- **기술·흐름:** **Deep State-Space Model(DSSM)**로 스켈레톤의 시간적 잠재 상태를 학습하고 **양방향 LSTM**으로 행동을 분류합니다. 관절 추출기 자체를 새로 제안하는 방식은 아닙니다.
- **자료·평가:** NTU RGB+D의 Kinect 25개 3D 관절과 60개 행동을 사용합니다. 사람 분리(cross-subject), 시점 분리(cross-view)를 평가하고, 가우시안 잡음과 무작위 프레임 제거 조건을 추가합니다.
- **결과·검증:** 정상 입력에서는 제안 모델이 67.45%·80.68%이고 비교 ST-GCN은 81.5%·88.3%입니다. 따라서 일반 정확도가 가장 높은 모델이라는 설명은 맞지 않습니다. 장점은 잡음·프레임 누락이 커질 때 상대적인 성능 저하가 작다는 점입니다.
- **한계:** 무작위로 프레임을 지운 실험은 이불 때문에 같은 관절이 오래 가려지는 현상과 다릅니다. 양방향 모델의 전체 구간 평가는 미래 정보가 없는 실시간 경고 조건과 구별해야 합니다.
- **활용 제안:** 단순 결측 마스크·시간 필터보다 실제로 이득이 있는지 비교합니다. 학습된 잠재 표현을 가려진 관절의 관측 정답으로 취급하지 않고, 오프라인과 실시간 지연을 별도로 평가합니다.
