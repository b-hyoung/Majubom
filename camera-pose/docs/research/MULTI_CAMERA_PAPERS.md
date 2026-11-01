# 다중 카메라·연속 영상 논문 모음

[연구 목차](README.md) · [전체 논문 색인](LITERATURE_REVIEW.md) · [두 카메라 구현](../design/MULTI_CAMERA_DESIGN.md)

**확인일: 2026-10-31.** 같은 사람을 다른 각도에서 촬영하는 다중 시점, 시간적 결합, 동기화 연구를 모았습니다. 기존 N/P/E 번호는 유지합니다. **핵심 8편 중 관련 방법·실험 본문 확인 5편, 부분 확인 1편, 본문 미확인 2편**입니다. 본문 일부 확인과 원문 전체·이미지·코드 검증은 다릅니다. 논문 간 다른 데이터셋의 수치를 성능 순위로 비교하지 않으며 자체 재현은 하지 않았습니다.

## 기존 문서에서 찾은 항목과 중복 정리

| 기존 위치 | 확인한 항목 | 이 문서의 취급 |
|---|---|---|
| [N01](LITERATURE_REVIEW.md#n01) | MediaPipe 스테레오 3D 복원 평가 | 두 카메라의 3D 복원 연구. 첨부 출판 PDF 본문 텍스트 확인으로 갱신 |
| [N02](LITERATURE_REVIEW.md#n02) | Learnable Triangulation | 앞선 신규 검색과 **같은 논문**. 중복 등록하지 않고 v1 본문 재확인 |
| [E02](LITERATURE_REVIEW.md#full-text-audit-2026-10-29) | UPose3D | 기존에 본문 텍스트를 검토한 시점·시간 결합 연구. v3 관련 절 재확인 |
| [N08](LITERATURE_REVIEW.md#n08) | MediaPipe 스테레오의 각도 의존성 연구 | N01과 DOI가 다른 후보. 원문 미확인 상태 유지, 자료 중복 여부 미정 |
| [N05](LITERATURE_REVIEW.md#n05) | Zhang 카메라 보정 | 다중 시점의 기하 준비를 위한 기초 자료. 두 카메라 사람 추정 실험과 구분 |
| [S13](TECHNOLOGY_CANDIDATES.md#s13) | Anipose 공식 문서 | 기존 항목은 **도구 문서**이며 논문 본문 확인 기록이 아님. 사람 침대 실증의 직접 근거로 사용하지 않음 |

2026-10-31 앞선 임시 검색 메모는 N02의 기존 본문 확인 이력을 누락했습니다. 이 문서에서는 **기존 확인 이력·이번 접근 성공 여부·실제 확인한 절**을 분리합니다. 이번에 PDF가 열리지 않는다는 이유로 과거 확인 이력을 삭제하지 않습니다.

## 핵심 8편의 상태

MC 번호는 이 모음 안에서만 사용하는 탐색 번호이며 기존 색인 번호를 대체하지 않습니다.

| 항목 | 논문 | 분류·검토 목적 | 본문 확인 상태 |
|---|---|---|---|
| [MC01](#mc01) / N01 | MediaPipe Stereo Accuracy, Sensors 2024 | 2대의 MediaPipe 관측 결합 후보 | **본문 텍스트 확인**: 첨부 출판 PDF 18쪽, §1–4·부록 A·참고문헌 |
| [MC02](#mc02) / N02 | Learnable Triangulation, ICCV 2019 | 보정된 여러 시점의 관절·특징 결합 | **관련 방법·실험 확인**: arXiv v1 HTML §3–4 |
| [MC03](#mc03) / E02 | UPose3D, 2024 공개판 | 불확실성·시점·시간 결합 | **관련 방법·실험 확인**: arXiv v3 HTML §3–5·부록 B.2 |
| [MC04](#mc04) / N08 | MediaPipe Stereo Angle Dependency, FUSION 2024 | 카메라 각도 조건 검토 후보 | **본문 미확인·판단 제외** |
| [MC05](#mc05) | VoxelPose, ECCV 2020 | 공통 3D 공간에서 특징 결합 | **관련 방법·실험 확인**: arXiv v4 HTML §3–7 |
| [MC06](#mc06) | MTF-Transformer, TPAMI 2023 | 다중 시점과 연속 프레임의 학습 결합 | **관련 방법·실험 확인**: arXiv v2 HTML §III–IV |
| [MC07](#mc07) | Fast and Robust Multi-Person 3D Pose Estimation and Tracking, TPAMI 2022 | 카메라 간 동일인 대응·시간 추적 검토 후보 | **본문 미확인·판단 제외** |
| [MC08](#mc08) | Weakly Synchronized Images, AAAI 2025 | 카메라 시각 차이 검토 후보 | **부분 확인**: PDF 첫 페이지·서론 일부, 방법·실험 미확인 |

**확인 경계:** 위 5편은 초록을 넘어 방법·실험 본문과 관련 텍스트 표를 읽었습니다. 모든 페이지·모든 그림·영상·별도 보충 자료를 전부 검토했다는 뜻은 아닙니다. arXiv 판본에서 확인한 내용을 최종 출판본 전체의 검증으로 바꾸지 않습니다. 부분/미확인 3편의 성능·우열·구체 구현 주장은 선정 근거에서 제외합니다.

<a id="mc01"></a>
### MC01 / N01 · MediaPipe 스테레오 3D 복원

**Accuracy Evaluation of 3D Pose Reconstruction Algorithms Through Stereo Camera Information Fusion for Physical Exercises with MediaPipe Pose.** Sebastian Dill 외. *Sensors* 24(23), 7772, 2024.

[DOI 10.3390/s24237772](https://doi.org/10.3390/s24237772) · [출판사](https://www.mdpi.com/1424-8220/24/23/7772) · [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11644880/) · [기존 확인 기록](LITERATURE_REVIEW.md#n01)

**확인 상태·판본:** 2026-10-31 사용자 첨부 `sensors-24-07772-v2.pdf`의 18쪽 전체 추출 텍스트(§1–4, 부록 A, 참고문헌)를 읽었습니다. 표의 추출 텍스트와 그림 설명은 확인했으나 그림 이미지 자체·보충 자료·코드는 검증하지 않았고 실험 재현도 하지 않았습니다. PDF 표기 출판일은 2024-12-04입니다. 파일명의 `v2`만으로 학술적 개정 이력을 확정하지 않습니다. 이전에는 §2.1–2.4 일부 발췌만 확인했고 웹 원문 접근 제한이 있었습니다. 이번 첨부로 그 범위를 갱신합니다. 원본 PDF는 저장소에 업로드하지 않습니다.

**첨부 식별:** SHA-256 `26a70fcef777b059b0396387a34e14b7dba506104610ea02f06d4210ccf567fd`.

**방법·실험에서 확인한 사실**

- §2.1(pp. 3–4): 12명 중 MoCap 문제로 3명 제외, 최종 9명·81개 녹화·스쿼트 810회. 정면·측면 스마트폰 두 대를 약 90°로 배치하고 1080×1920·30fps로 촬영했습니다. 비교 기준은 11대 Qualisys 카메라·31개 마커·100fps의 별도 MoCap입니다. 침대 누운 자세 실험은 아닙니다.
- §2.2·Table 1(pp. 4–6): MediaPipe 출력 33개 전체의 정확도를 평가한 것이 아니라 어깨·팔꿈치·손목·골반·무릎·발목의 좌우 **12개 관절**을 MoCap의 대응 위치와 비교합니다. 일부 기준 위치는 여러 마커를 조합한 값입니다.
- §2.3(pp. 6–8): 내부 파라미터와 렌즈 왜곡 계수로 2D 관절을 보정·정규화합니다. 대응점에서 essential matrix를 LMS로 추정하고 회전·이동을 복구한 뒤 DLT/SVD로 3D를 재구성합니다. **사전 스테레오 보정 없음은 내부 보정·시점 대응·기하 조건까지 불필요하다는 뜻이 아닙니다.**
- §2.4(p. 8): 각 방식의 좌표를 GT 좌표계에 최소제곱 최적화로 정렬한 잔차를 RMSE로 평가합니다. 이를 무정렬 실공간 위치 오차나 일반 MPJPE와 동일한 값으로 취급하지 않습니다. 정렬의 스케일 처리·단위 복원 세부는 추가 확인이 필요합니다.

**보고 결과와 해석 경계**

- §3.1·Figure 4 설명(p. 9): 에피폴라 복원의 중앙값 RMSE는 무필터 30.9mm, 필터 적용 30.1mm이며 단안 방식과의 차이는 유의했습니다(모든 p < 10⁻⁶). **단순 직교 결합과의 차이는 유의하지 않았습니다**(무필터 p=0.057, 필터 p=0.120). 결론의 우월성 표현보다 이 비교 결과를 우선해 해석합니다.
- Figure 4 설명: 필터는 4차 Butterworth 저역통과·차단 주파수 2Hz입니다. 추가 필터의 개선은 유의하지 않았으며 이후 분석은 무필터입니다. 인과적 구현 여부·미래 프레임 사용 여부는 명시적으로 확인되지 않아 실시간 무지연 근거로 사용하지 않습니다.
- §3.2·Figure 8 설명(pp. 11–12): 에피폴라 복원의 좌·우 무릎 각도 중앙값 RMSE는 각각 10.3°·7.7°입니다. 결론의 10.7° 서술을 이 두 값으로 대체하거나 하나의 공통 정확도로 묶지 않습니다. 임상 측정법의 최소 유의 차이와 RMSE도 서로 다른 지표입니다.
- §3.1(p. 10): 한 시점의 화면 밖 신체와 배경 재킷의 팔 오검출이 재구성을 악화시켰습니다. 두 카메라가 언제나 개선을 보장하지 않습니다.
- §3.2·§4: 예시 피험자의 각도 곡선으로 운동 수행 차이를 설명하지만, 정식 올바름/잘못됨 분류 검증은 후속 과제로 남깁니다. 자세 분류 정확도·F1이 검증됐다고 쓰지 않습니다.
- §4·Data Availability(p. 14): 개인정보 문제로 데이터는 쉽게 이용할 수 없다고 명시합니다. 공개 데이터·코드로 재현 가능한 상태라고 가정하지 않습니다.

**프로젝트 적용 제안:** 두 시점 관절에서 깊이를 보완하는 연구 근거로 사용합니다. 현재 **CLIP 점수 통합**, 누움·옆누움·걸터앉음 분류, 임의 카메라 배치의 효과는 검증하지 않았습니다. 정면/측면 관절을 단순히 섞는 구현을 자동 채택하지 않고, 동일인 대응·시간 정렬·오검출 거부·기하 검증이 선행되어야 합니다. ToF/mmWave 결과는 카메라 예측 입력과 분리해 독립 비교합니다.

**남은 공백:** 두 30fps 영상과 100fps MoCap의 구체적 동기화·리샘플링, GT 정렬의 자유도와 절대 스케일, 필터 인과성, 구현 코드·재현 자산을 추가 확인해야 합니다. N08은 본 논문 참고문헌 [24]의 선행 FUSION 연구임을 확인했으나 N08 자체 본문·자료 중복·각도별 결과는 여전히 미확인입니다.

<a id="mc02"></a>
### MC02 / N02 · Learnable Triangulation

**Learnable Triangulation of Human Pose.** Karim Iskakov, Egor Burkov, Victor Lempitsky, Yury Malkov. ICCV 2019, 7718–7727.

[DOI 10.1109/ICCV.2019.00781](https://doi.org/10.1109/ICCV.2019.00781) · [이번에 확인한 arXiv v1 HTML](https://arxiv.org/html/1905.05754v1) · [학회 서지](https://openaccess.thecvf.com/content_ICCV_2019/html/Iskakov_Learnable_Triangulation_of_Human_Pose_ICCV_2019_paper.html)

**본문 확인:** v1, 2019-05-14. §3·Figure 1 설명·가중 삼각측량 식·§4와 Table 1 텍스트. 알려진 투영행렬과 동기화 영상을 사용하고, 시점별 관절 신뢰도를 학습해 결합합니다. §3은 **각 시각을 독립 처리하고 시간 정보를 사용하지 않는다**고 명시합니다. §4의 Human3.6M은 4대 동기 카메라이고 평가 일부는 가용한 3/4대를 사용합니다. 사람 영역 정답 상자 사용 조건도 있습니다. CMU Panoptic도 평가합니다.

**프로젝트 해석:** 관절마다 시점 품질이 다른 문제의 방법론 근거입니다. 연속 프레임 안정화나 두 RGB 카메라의 침대 자세 분류를 검증한 연구로 전용하지 않습니다. 학습된 신뢰도를 MediaPipe visibility로 치환한 방식은 별도 실험입니다. ICCV 최종 PDF 전체는 이번에 대조하지 않았습니다.

<a id="mc03"></a>
### MC03 / E02 · UPose3D

**UPose3D: Uncertainty-Aware 3D Human Pose Estimation with Cross-View and Temporal Cues.** Vandad Davoodnia, Saeed Ghorbani, Marc-André Carbonneau, Alexandre Messier, Ali Etemad. 2024 공개판.

[arXiv DOI 10.48550/arXiv.2404.14634](https://doi.org/10.48550/arXiv.2404.14634) · [이번에 확인한 v3 HTML](https://arxiv.org/html/2404.14634v3) · [기존 전체 텍스트 검토 기록](LITERATURE_REVIEW.md#full-text-audit-2026-10-29)

**본문 확인:** v3, 2024-07-10. §3은 관절 위치·불확실성, 알려진 카메라 내부/외부값을 통한 시점 투영, 시간 정보를 결합합니다. §4–5는 Human3.6M·RICH와 시점 수 확장 평가를 설명합니다. §5.5의 처리 시간 비교는 공통 2D 추정기를 제외합니다. 부록 B.2는 시간 창의 **가운데 프레임** 추정을 기술합니다.

**프로젝트 해석:** 관측 품질과 시점·시간 정보를 분리해 결합하는 간접 근거입니다. MediaPipe confidence가 이 논문의 학습된 불확실성과 같지는 않습니다. 카메라 보정 없이 즉시 적용하거나 과거 전용 무지연 처리로 해석하지 않습니다. 침대 3자세 정확도 실증은 이번 확인 범위에 없습니다. §5.3의 일부 수치 설명과 HTML Table 3에는 차이/행 구조 확인 문제가 있어 여기서는 새 정량 개선폭을 인용하지 않습니다.

<a id="mc04"></a>
### MC04 / N08 · MediaPipe 스테레오의 각도 의존성

**Evaluation of Accuracy and Angle Dependency of 3D Pose Estimation through Stereo Camera Information Fusion with MediaPipe Pose.** Sebastian Dill, Maurice Rohr, Gökhan Güney, Christoph Hoog Antink. FUSION 2024.

[DOI 10.23919/FUSION59988.2024.10706380](https://doi.org/10.23919/FUSION59988.2024.10706380) · [기존 후보 기록](LITERATURE_REVIEW.md#n08)

**본문 미확인:** 서지·학회 목록만 재확인했습니다. MC01과 제목·DOI가 달라 별도 후보로 유지합니다. 자료 중복·각도 범위·최적 배치·성능은 원문 확인 전 판단하지 않습니다.

<a id="mc05"></a>
### MC05 · VoxelPose

**VoxelPose: Towards Multi-Camera 3D Human Pose Estimation in Wild Environment.** Hanyue Tu, Chunyu Wang, Wenjun Zeng. ECCV 2020, 197–212.

[DOI 10.1007/978-3-030-58452-8_12](https://doi.org/10.1007/978-3-030-58452-8_12) · [이번에 확인한 v4 HTML](https://arxiv.org/html/2004.06239v4) · [학회 PDF](https://www.ecva.net/papers/eccv_2020/papers_ECCV/papers/123460188.pdf)

**본문 확인:** v4, 2020-08-24. §3–4는 시점별 관절 heatmap을 공통 3D voxel 공간에 투영하고 CPN·PRN으로 사람과 관절을 추정합니다. §5에는 Campus 3대, Shelf 5대, Panoptic의 선택된 5대가 기재됩니다. §7.2·Table 2는 1/3/5대 조건을 비교하며, 한 시점만 관측되는 실패도 설명합니다. 시간 모델을 통한 연속 추적 실증으로 해석하지 않습니다.

**프로젝트 해석:** 카메라별 확정 라벨 이전에 특징을 결합하는 대안의 근거입니다. 알려진 공간 투영·학습·3D 연산이 필요하며, 현재 CLIP 점수 평균과 다릅니다. 카메라가 많을수록 우리 침대 분류가 항상 좋아진다거나, 현재 장치에서 실시간이라는 결론은 내리지 않습니다. 학회 최종 PDF 전체 대조·그림 육안 검증·재현은 미수행입니다.

<a id="mc06"></a>
### MC06 · MTF-Transformer

**Adaptive Multi-View and Temporal Fusing Transformer for 3D Human Pose Estimation.** **Hui Shuai**, Lele Wu, Qingshan Liu. *IEEE TPAMI* 45, 4122–4135, 2023; 온라인 출판 2022.

[DOI 10.1109/TPAMI.2022.3188716](https://doi.org/10.1109/TPAMI.2022.3188716) · [이번에 확인한 arXiv v2 HTML](https://arxiv.org/html/2110.05092v2)

**본문 확인:** v2, 2022-07-04. §III의 2D 관절·신뢰도 입력, 시점 결합 MFT와 시간 결합 TFT를 확인했습니다. §IV는 Human3.6M·TotalCapture·KTH 자료와 시점 수·시간 길이 비교를 제시합니다. Table VI는 두 시점 학습 후 1–4시점 평가를 구분합니다. §III-C는 **가운데 프레임** 출력을 명시합니다.

**프로젝트 해석:** 여러 카메라와 연속 프레임의 결합을 함께 다루는 직접적인 방법론 후보입니다. 추론 시 명시적 카메라 보정을 사용하지 않는 구성도 다루지만, 학습·시점 대응·시간 조건까지 필요 없다는 뜻은 아닙니다. 파라미터 사용 유무의 실험 행을 구분해야 합니다. 학습된 3D 추정이며 침대 3자세 분류·CLIP 단순 평균의 검증이 아닙니다. 최종 TPAMI PDF 전체는 미대조입니다. 앞선 임시 메모의 ‘Hongjun Shuai’ 표기는 본문 저자명 **Hui Shuai**로 정정합니다.

<a id="mc07"></a>
### MC07 · 다중 시점의 동일인 대응·추적 후보

**Fast and Robust Multi-Person 3D Pose Estimation and Tracking From Multiple Views.** Junting Dong, Qi Fang, Wen Jiang, Yurou Yang, Qixing Huang, Hujun Bao, Xiaowei Zhou. *IEEE TPAMI* 44, 6981–6992, 2022; 온라인 2021.

[DOI 10.1109/TPAMI.2021.3098052](https://doi.org/10.1109/TPAMI.2021.3098052) · [출판사](https://ieeexplore.ieee.org/document/9492024/) · [저자 서지](https://jtdong.com/)

**본문 미확인:** 출판사·저자 페이지·검색 도구에서 서지만 확인했습니다. 카메라 간 동일인 대응과 추적을 조사할 후보로 남기며 구체 방법·성능은 채택하지 않습니다. **2019 CVPR의 5인 저자 논문과 2022 TPAMI의 7인 저자 확장판을 혼동하지 않습니다.**

<a id="mc08"></a>
### MC08 · 약한 동기화 연구 후보

**Multi-View 3D Human Pose Estimation with Weakly Synchronized Images.** Ling Li, Ruiwen Gu, Chongyang Wang, Junliang Xing, Xinchun Yu, Xiao-Ping Zhang. AAAI 2025, 39(5), 4833–4841.

[DOI 10.1609/aaai.v39i5.32511](https://doi.org/10.1609/aaai.v39i5.32511) · [학회 서지](https://ojs.aaai.org/index.php/AAAI/article/view/32511) · [공식 PDF](https://ojs.aaai.org/index.php/AAAI/article/download/32511/34666)

**부분 확인:** 공식 9쪽 PDF가 한 차례 식별되고 첫 페이지·서론 일부 텍스트를 읽었습니다. 이후 방법·실험 페이지 요청은 접근 차단 화면으로 바뀌어 검증하지 못했습니다. **본문 전체/방법·실험 확인으로 집계하지 않습니다.** 허용 시각 오차·보정 요구·모델 성능은 판단에서 제외합니다. 하드웨어 동기화 장치가 필요 없다는 초록을 ‘시각 차이 확인이 불필요하다’는 뜻으로 해석하지 않습니다.

## 다중 센서·기초 자료는 따로 보기

아래는 기존 문서 확인 이력을 분류한 것이며 이번에 원문을 새로 검증했다는 뜻은 아닙니다.

| 기존 항목 | 구분 | 두 각도의 RGB 결합과의 차이 |
|---|---|---|
| [P01 SLP](BED_POSTURE_PAPERS.md#p01) | RGB·LWIR·깊이·압력 데이터 | 다중 모달 수집을 두 시점 RGB 결합의 실증으로 취급하지 않음 |
| [P02 RGB-D](BED_POSTURE_PAPERS.md#p02) | RGB 관절과 대응 깊이 | 단일 RGB-D 장치의 색/깊이는 다른 각도의 두 RGB 카메라와 다름 |
| [P31 RGB/열화상](BED_POSTURE_PAPERS.md#p31) | 모달리티·영상 특징 융합 | 같은 입력 내 두 네트워크 특징 융합 결과도 있어, 두 시점 동시 융합 정확도로 전용하지 않음 |
| [P33 Bodies at Rest](BED_POSTURE_PAPERS.md#p33) | 압력 중심 추론 | RGB-D의 평가 용도와 압력 기반 추론을 구분 |
| [P35 Multimodal In-bed](BED_POSTURE_PAPERS.md#p35) | 다중 모달 3D 자세·형상 | 2020 공개판과 미확인 2022 최종판을 분리. 두 RGB 시점 연구로 세지 않음 |
| [P34 몸 회전 영상](BED_POSTURE_PAPERS.md#p34) | 시간적 단서 후보 | 본문 미확인. 연속 프레임과 여러 카메라를 동일 개념으로 취급하지 않음 |
| [N05 Zhang 보정](LITERATURE_REVIEW.md#n05) | 카메라 보정 기초 | 기존 기술보고서 §2.2·§3 확인 이력. 다중 카메라 자세 정확도 실증이 아님 |
| [S13 Anipose](TECHNOLOGY_CANDIDATES.md#s13) | 공식 도구 문서 | 기존 문서에는 도구 확인만 기록됨. 논문 본문 확인·사람 침대 실증으로 승격하지 않음 |

## 프로젝트에 적용할 때의 판단

**논문에서 확인한 범위:** 시점별 관측의 품질 차이, 공통 공간/특징 결합, 시간 문맥을 다루는 방법과 해당 연구 조건의 평가가 있습니다. 이것이 현재의 **CLIP 점수 동일 평균**이나 침대 바로 누움·옆누움·걸터앉음 성능을 직접 검증하지는 않습니다.

**자체 설계 제안:**

1. 같은 사람·같은 시간대를 대응시킨 단일 시점 A/B와 현재 점수 결합을 먼저 비교합니다. 목표 자세·외부 센서 결과를 정답으로 자동 대체하지 않습니다.
2. 가림 보완이 필요한 관절 결합은 카메라 기하·관절 정의·재투영 오차 검증이 별도로 필요합니다. 다른 영상의 2D 좌표를 그대로 평균하지 않습니다.
3. 시간 결합은 과거 전용과 가운데 프레임 방식을 구분해 지연을 측정합니다. 프레임 연속 수신이나 GPU 묶음 처리는 시간 추론 모델이 아닙니다.
4. 단일 카메라 동작을 유지하고 실제 정답 기준 오분류·보류·가림·설치 시점별 차이를 평가한 뒤 방법을 선택합니다. 이번 문헌 정리는 모델·가중치·판정 코드를 변경하지 않습니다.

## 접근과 확인 기록

- 검색 출발점: Consensus·SciSpace의 전날/당일 검색 결과와 기존 N/P/E 문서. 자동 보고서는 검증된 원문을 대체하지 않습니다.
- 이번 성공 경로: **arXiv 공개 HTML 판본**. PDF 실패만으로 본문 전체를 미확인 처리했던 앞선 임시 메모를 정정합니다.
- 이번 제한: CVF PDF 접근 제한, MTF PDF 용량 제한, MDPI 429/PMC 확인 화면, AAAI PDF 후속 페이지 접근 차단. 이 제한을 해결했다고 주장하지 않습니다.
- 확인한 네 판본의 본문 텍스트에 근거한 요약만 새로 채택했습니다. 그림 이미지·동영상·소스 코드 실행·데이터 다운로드·실험 재현은 하지 않았습니다.
- 이 목록은 기존 P01–P37 집계를 소급 변경하지 않습니다. N/E 항목을 다시 신규 논문으로 세지 않습니다.
