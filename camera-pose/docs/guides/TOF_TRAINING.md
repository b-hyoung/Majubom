# 5·6단계: 카메라 자세 + ToF 저장, CNN/GRU 및 트리 비교

## 논문 실측 기록 · 2026-11-03

`bed_pose.tof_learning train-session --dataset <dataset_v3> --baseline <복사한 기준.json> --out outputs/<날짜>`는 학습·외부 시험 평가·CPU 내보내기가 모두 성공한 뒤 `paper_record.md`와 원본 값 `paper_record.json`을 생성한다. 센서별 CNN 구조, 실제 실행 epoch, optimizer, batch, 내부 검증 early stopping, 증강, 트리별 전체 매개변수·가중치, 패키지 버전, 실제 학습 GPU, 참여자·세션·클래스별 짝 수, 분할, 다섯 모델의 외부 시험 지표와 카메라 비교를 기록한다. `--camera`에 시간 정렬 가능한 카메라 JSONL을 전달하지 않으면 카메라 일치율은 빈칸이다. 참가자 수는 세션 subject 식별자 기준이며 별도 신원 인증이 아니다.

미리 만든 빈 `paper_record.md`만 있는 출력 폴더는 학습에 사용할 수 있다. 기존 실제 결과가 있거나 학습이 중단된 폴더에는 덮어쓰지 않는다. 학습 전 빈 기록표는 완료 실측이 아니며 값을 채우지 않는다. Pi 5 CPU 추론 시간은 Pi 실측을 아직 연결하지 않아 비워 두며 노트북 CPU 시간을 대신 쓰지 않는다. 실제 녹화·학습 성능은 아직 미확인이다.

## 최신: 카메라·ToF 동시 비교 재개

2026-11-02 사용자가 ToF 수신을 다시 허용하고 카메라와 비교하여 촬영할 것을 요청했다. Pi의 기존 ToF5001 수신 서버만 시작하여 실제 tof1/tof2 각64칸과 갱신 시각을 확인했다. 펌웨어·모델·baseline은 변경하지 않았으며 학습은 시작하지 않았다. 서버 시작에 따라 기존 운영 DB 수신 기록은 원래 정책대로 계속된다.

노트북 실행기에서 환경변수 `BEDPOSE_TOF_URL=http://192.168.0.4:5001`을 지정하면 별도 읽기 스레드가 `/tof/latest`와 `/tof/presence`를 조회한다. 카메라 입력과 별도 `sensor_crosscheck`를 모니터에 표시하며 원시 예측/후처리/점수/각8×8맵을 구분한다. 최대500ms 신선도·250ms 센서 시차·각16유효칸·기존 모델 점수0.8 조건은 실험 정책이다. 낮은 점수·결측·오래된 결과는 확인 근거로 사용하지 않는다. 카메라가 없고 조건을 통과한 ToF만 있으면 보조 출력을 별도 표시하지만 camera_posture와 엄격 학습 승인은 바꾸지 않는다. 일치도는 정답 정확도가 아니며 실제 검증셋을 나누는 통계적 교차검증과도 다르다.

왼쪽/오른쪽은 설치된 센서 방향과 인체 가림 때문에 단순 좌우 반전이 보장되지 않는다. 각 자세 준비 응답 후 두 맵·시각·예측을 저장하여 원본/가로반전/세로반전/양축반전의 공통 유효칸 차이를 비교하고, 공통 침대 배경의 영향과 반복/다른 사람의 변화도 확인해야 한다. 현재 반전 관계가 검증됐다는 뜻은 아니다. 기존 모델은 엎드림을 포함하지 않으므로 이를 검증하는 ToF 정답으로 쓰지 않는다.

**2026-11-02 최신 요청:** ToF 재학습 전 [카메라 여섯 자세95% 검증](CAMERA_ACCURACY_GATE.md)을 우선한다. 실제95% 결과는 없으며 사용자가 지금 촬영하지 말라고 하여 실측·재학습은 대기한다. Pi192.168.0.4의 SSH와 기존 모델은 확인했지만 점검 당시 수집 API 서비스가 실행되지 않아 실시간 ToF 수신은 아직 확인하지 못했다.

현재 구현은 연구용 수집·학습 파이프라인이며 기존 운영 ToF/CLIP/카메라 판정을 교체하지 않습니다. 6개 클래스는 `supine, prone, left_side, right_side, sitting, edge_sitting`입니다. 기존 Majubom의 `side_left/side_right/edge_sit` 이름은 명시적으로 매핑할 수 있지만 **empty를 prone으로 합치지 않습니다.** 이전 4×4 데이터와 계획 자세 라벨 파일을 현재 검토 정답으로 자동 가져오지 않습니다.

## 설치 완료 위치

- 프로젝트: `Majubom/camera-pose/`
- 전용 학습 환경: `Majubom/.venv-training/` (Git 제외)
- 기존 카메라 저장소를 단독으로 받으면 학습 환경은 프로젝트 내부 `.venv-training/`에 만듭니다. 스크립트가 두 배치를 지원합니다. 아래 `..\.venv-training` 명령 예시는 Majubom 통합 배치 기준이며 단독 배치는 `.\.venv-training`을 사용합니다.
- 이전 독립 프로젝트 원본은 `camera-pose/outputs/original-project/`에 로컬 보존했으며, 기존 위치의 README에서 새 위치를 안내합니다.
- 확인 장비: RTX 5070 Laptop GPU 8GB, compute capability 12.0
- CUDA PyTorch `2.11.0+cu128`에서 CNN·CNN+GRU 역전파 실행 확인
- XGBoost `3.4.1` 실제 학습 장치 `cuda:0` 확인
- LightGBM `4.7.0` 배포 wheel은 GPU Tree Learner 미포함으로 확인 → **CPU** 사용
- Random Forest: scikit-learn **CPU**, 최대 8개 스레드

이 노트북은 설치를 마쳤습니다. 다른 Windows PC에서는 프로젝트 폴더에서 다음을 실행합니다. 학습 전용 venv이므로 카메라 실시간 환경을 바꾸지 않습니다.

```powershell
.\training\Setup-Training.ps1
& ..\.venv-training\Scripts\python.exe -m bed_pose.tof_learning doctor
```

정확한 설치 버전은 `training/requirements.lock.txt`에 있습니다. 새 환경에서 같은 버전이 필요하면 CUDA 인덱스를 포함한 이 파일로 설치합니다. 설정 스크립트는 공식 CUDA PyTorch와 호환 범위의 라이브러리를 설치하며 위 `doctor`로 실제 연산을 확인합니다. 인터넷 패키지 가용성·다른 GPU 호환성은 재확인이 필요합니다.

## 2026-11-02 학습을 시작하기 전

1. Pi에서 실제 사용하는 IP와 `:5012/api/stream/status`를 확인합니다. 이 PC에서 문서의 이전 주소 `192.168.0.4:5012`는 연결되지 않았습니다. **실제 ToF 수신 성공으로 확인하지 않았습니다.**
2. Majubom의 `docs/SENSOR_DIAGNOSTICS.md`대로 ToF 수신 서버·진단 스트림 서버가 켜져 있어야 합니다. 현재 main에는 과거 mmWave 수집기가 비워져 있으므로 Pi에서 실제 구동하는 브랜치를 확인합니다. 이 프로젝트가 센서 펌웨어를 업로드하거나 운영 DB 정책을 변경하지 않습니다.
3. `tof1/tof2`가 각 64칸을 보내고 센서 위치·회전·ID가 수집 세션 동안 고정돼 있는지 확인합니다. 설치를 바꾸면 새 세션을 씁니다.
4. 카메라 모니터에서 실제 자세·침대 영역·보정 상태를 확인합니다. 현재 카메라 3D는 정확도 미검증이며 높은 관절 신뢰도만으로 정답 인증이 되지 않습니다.
5. 적어도 학습/검증/시험 그룹 각각에 6개 자세와 충분히 긴 연속 프레임을 확보합니다. 가능하면 최소 3명 이상을 분리하고 예시는 5명을 씁니다. 한 사람만 있으면 세션 단위 분리는 가능하지만 사람 간 일반화 성능이라고 보고하면 안 됩니다.

## 5단계 수집

먼저 읽기 전용 연결 검사를 실행할 수 있습니다.

```powershell
.\training\Check-Connection.ps1 -Pi 192.168.0.4 -Seconds 10
.\training\Check-Connection.ps1 -Pi 192.168.0.4 -Seconds 10 -TemporaryCameraLabel supine
```

실제 두 ToF NDJSON의 중복/유효칸/heartbeat 시계·지연, 서버 기준 센서 시각과 두 센서 간 시차, 카메라 관절 품질을 확인합니다. 결과는 `outputs/connection-check-*.json`에만 저장합니다. `TemporaryCameraLabel`은 진단 비교 입력에만 적용하며 실시간 카메라 상태나 학습 파일을 바꾸지 않습니다. 임시 입력 검사와 독립 카메라 예측 일치는 별도 필드이고 둘 다 정답 정확도를 뜻하지 않습니다. 오래된 프레임·모델/라벨 없음·낮은 신뢰도·카메라 보류는 일치율에서 제외합니다.

거리 변동이 작아도 가만히 누운 사람일 수 있습니다. 이 검사는 안정 구간의 중앙값 후보만 계산하고 **빈 침대를 인증하거나 운영 baseline을 저장하지 않습니다.** 실제 적용 전 Pi 문서·현재 설치·침상 비어 있음과 레이더/카메라 근거를 확인하고 기존 baseline을 별도 보존해야 합니다. 연결 실패는 실패로 남기며 실제 장비 검증이나 학습 성공으로 기록하지 않습니다.

프로젝트 루트 `camera-pose`에서:

```powershell
.\training\Start-Collection.ps1 -Pi '실제-Pi-IP' -Subject S01 -Session S01-day1
```

기본 `verified` 모드는 다음 조건을 모두 요구합니다.

- 안정된 6자세, 최소 1.5초 유지, 필요한 관절 품질 0.8 이상
- 부분 가림/방향 충돌 경고 없음, full evidence
- 카메라 `accuracy_verified=true` 및 `eligible_for_recording=true`
- 최근 카메라 수신 관측 300ms 이내
- 두 ToF에서 유효 칸 비율 각 75% 이상, 센서 간 수신 시차 100ms 이내
- 카메라와 두 센서의 최대 수신 시차 150ms 이내
- heartbeat를 받은 뒤, Pi 수신 시각과 최소 시계 오프셋 기반 네트워크 잔여 지연 −100~200ms 범위
- 중복 ToF 프레임 재사용 안 함, 4×4/잘못된 상태 길이/비유한 값 거부

**현재 카메라 경로는 미검증이므로 엄격 모드에서 0개 저장되는 것이 정상입니다.** 이 제한을 몰래 우회하지 않습니다. 현재 자료로 내일 파일럿을 준비하려면 의사 라벨 저장을 명시적으로 선택한 뒤 별도로 검토합니다.

```powershell
.\training\Start-Collection.ps1 -Pi '실제-Pi-IP' -Subject S01 -Session S01-day1 -LabelPolicy pseudo
```

`pseudo`도 나머지 품질/시간 조건은 유지합니다. 파일에는 `label_source=camera_pseudo`, `ground_truth=null`, `review_required=true`를 넣습니다. 이것은 사람 검토가 끝난 정답이 아닙니다. 학습 전에는 검토가 필요하고, 시험/검증은 미검토 카메라 라벨로 평가하지 않습니다.

저장 위치는 `data/paired/<session>/samples.jsonl`, `manifest.json`, `status.json`입니다. 세션 디렉터리가 있으면 덮어쓰지 않습니다. 거부 건수와 사유, upstream dropped 최대값을 남깁니다. **카메라 RGB/JPEG, API 토큰, Wi-Fi 비밀번호는 학습 파일에 저장하지 않습니다.** 별도 원본 영상이 필요하면 기존 녹화 기능으로 명시적으로 켜고 로컬에서 관리합니다.

## 시간의 의미

센서는 Majubom `FrameStreamPublisher`의 `payload.distances_mm` 및 선택적인 `targets/target_status`로 거리/유효 마스크/상태를 저장하고 mount·Pi 수신 시각을 보존합니다. 상위 스트림의 별도 `raw_distances_mm`는 이 학습 파일에 중복 저장하지 않습니다. Pi→노트북 NDJSON 형식의 `type=frame`, `kind=tof1/tof2`, `seq` 또는 수신 시각을 사용합니다.

카메라 `learning_observation`은 추론 완료 시각 대신 **실제 프레임 수신 단조 시각을 현재 PC wall clock에 연결**해 제공합니다. ToF는 같은 PC의 스트림 수신 시각을 씁니다. Pi와 PC의 시계 차이는 heartbeat의 최소 오프셋으로 지연 진단만 하며 하드웨어 노출 동기화나 정확한 일방향 네트워크 지연 보정으로 주장하지 않습니다. 센서 포착→Pi와 카메라 노출→수신 지연은 여전히 남습니다. 값은 manifest와 sample의 synchronization에 보존됩니다.

## 검토 정답

우선 편집용 템플릿을 만들 수 있습니다. 생성되는 모든 행은 미확인 상태입니다.

```powershell
& ..\.venv-training\Scripts\python.exe -m bed_pose.tof_learning review-template data/paired/S01-day1/samples.jsonl --out data/reviews/S01-template.jsonl
```

독립적인 영상 검토/현장 확인을 한 구간은 다음과 같이 별도 검토 파일로 선언합니다. 시간은 samples의 `timestamp_unix`입니다. 시작 계획이나 모델 예측만 보고 확인하지 마세요.

```powershell
& ..\.venv-training\Scripts\python.exe -m bed_pose.tof_learning review-confirm data/paired/S01-day1/samples.jsonl --session S01-day1 --start 1793600000 --end 1793600010 --label supine --reviewer minhyuk --evidence '독립 영상 확인: 파일/시간 구간' --out data/reviews/S01-supine-reviewed.jsonl
```

`confirmed=true`, `reviewer`, 실제 검토 라벨이 있는 sample_id만 정답으로 씁니다. 소프트웨어가 검토 행위를 인증하지는 못합니다. 여러 검토 파일은 중복 sample_id 없이 한 JSONL로 합쳐 `--reviews`에 전달합니다. 이 답변/사용자가 지금 누웠다는 발언으로 검토 정답을 자동 생성하지 않았습니다.

## 6단계 학습

`training/split.example.json`을 `data/split.json`으로 복사하고 실제 참가자 ID를 지정합니다. **같은 참가자를 둘 이상의 분할에 넣으면 거부**합니다. `group_by=session_id`는 세션 분리 대안이며 동일 참가자 편향이 남습니다. 동일 프레임·겹치는 창을 다른 분할로 보내지 않고, 세션/라벨 변경·300ms 넘는 공백을 넘는 창은 만들지 않습니다.

```powershell
.\training\Start-Training.ps1 -Inputs @('data/paired/S01-day1/samples.jsonl','data/paired/S02-day1/samples.jsonl','data/paired/S03-day1/samples.jsonl','data/paired/S04-day1/samples.jsonl','data/paired/S05-day1/samples.jsonl') -Reviews data/reviews/confirmed.jsonl -Split data/split.json
```

기본값은 window=16, stride=4, batch=128, epochs=60, validation macro-F1 early stopping patience=10, seed=42, CUDA+AMP입니다. VRAM 부족이면 `python -m bed_pose.tof_learning train ... --batch 32`로 줄입니다. 기본 학습은 데이터/참가자가 없으면 명확하게 중단하며 가짜 성능을 만들지 않습니다. 실제 훈련은 내일 사용자가 실행하도록 준비했으며 자동 예약하지 않았습니다.

모델 비교는 같은 분할/같은 창을 사용합니다.

1. **2D CNN**: 현재 프레임의 두 센서 공간 패턴(거리2+유효 마스크2 = 4×8×8) → 특징64 → 6클래스
2. **CNN+GRU**: 과거→현재 16프레임의 CNN 특징 → 단방향 GRU64 → 6클래스. 미래 프레임 사용 안 함
3. 각각의 encoder를 **훈련 분할에서만** 지도 학습하고 최적 validation 체크포인트를 고정 → 동일 특징으로 LightGBM/Random Forest/XGBoost 학습

즉 neural head 2개와 특징+트리 6개, 총 8개 조합입니다. ToF 값은 0~4000mm 고정 범위로 정규화하고 무효칸은 0+별도 마스크로 표현합니다. 클래스 가중치도 train에서만 계산합니다. test를 학습/early stopping/하이퍼파라미터 선정에 쓰지 않습니다. 실험 후 가장 좋은 test 모델을 계속 골라 재조정하면 test가 누출되므로 새 독립 시험 세션이 필요합니다.

기본은 검토 정답만 사용합니다. `--allow-pseudo-train`을 명시하면 train에만 의사 라벨을 허용할 수 있지만, validation/test에는 여전히 검토 정답만 허용합니다. 의사 라벨을 쓰면 카메라 오답을 ToF가 학습할 위험을 보고해야 합니다.

## 산출물

- `cnn.pt`, `cnn_gru.pt`: 학습 encoder/head와 라벨/채널/정규화 설정
- `*_lightgbm.joblib`, `*_random_forest.joblib`, `*_xgboost.json`
- `metrics.json`, `comparison.csv`: accuracy·balanced accuracy·macro-F1, 클래스별 지표·6×6 혼동행렬
- `split_audit.json`: 분할별 sample_id/그룹·클래스 분포
- `*_history.json`, `*_runtime.json`: loss/validation 점수·실행 시간·CUDA 최대 할당 메모리
- `run_manifest.json`: 패키지/GPU/seed/입력·검토·분할 해시·설정·합성 여부

실측 데이터가 없어 실제 알고리즘 성능 순위는 아직 없습니다. 합성 576프레임으로 CUDA CNN·GRU 2epoch, 세 트리의 특징 학습 및 저장/비교표 생성까지 확인했습니다. 이 결과는 `outputs/synthetic-training-check/`에 로컬로만 저장하며 **정확도 보고 자료로 쓰지 않습니다.** Majubom 루트 `.github/workflows/camera-pose.yml`은 카메라 검사·웹 빌드·합성 CPU 학습을 정의합니다. CI의 원격 실행 결과와 이 노트북의 CUDA 확인은 별개입니다.

공식 근거: [PyTorch CUDA 설치](https://pytorch.org/get-started/previous-versions/), [XGBoost GPU hist/device](https://xgboost.readthedocs.io/en/stable/gpu/index.html), [LightGBM Windows GPU 제약](https://lightgbm.readthedocs.io/en/latest/Installation-Guide.html). 라이브러리의 GPU 표기만 믿지 않고 이 노트북의 실제 연산 결과를 doctor에 저장했습니다.

Bed C 실험 비교는 GPU 모니터 실행 시 BEDPOSE_TOF_URL과 BEDPOSE_TOF_RF_MODEL을 지정한다. 노트북에서 ToF1·ToF2 원시 거리 128개를 S01 RF에 입력하며, 카메라·ToF 비교 패널에 실험 모델임을 표시한다.
