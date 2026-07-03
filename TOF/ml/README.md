# ToF 자세/재실 분류 모델 (로컬 실행)

VL53L5CX ×2 (4×4 존)의 거리값으로 **자세(정자세/옆으로/앉음)와 재실(빈 침대)** 을 분류하는
로컬 머신러닝 모델. **인터넷·API·요금 없이 라즈베리파이에서 바로 학습·추론**한다.

## 왜 로컬 RandomForest인가 (OpenAI 아님)
| 항목 | 로컬 RandomForest | OpenAI(LLM) |
|------|------------------|-------------|
| 이런 32차원 숫자 분류 | **정확·적합** | 부적합 |
| 비용 | **무료** | 실시간 폴링 시 요금 폭탄 |
| 속도/오프라인 | **1ms 미만, 오프라인 OK** | 네트워크 의존·지연 |
| 프라이버시 | **데이터가 로컬에만** | 환자 데이터 외부 전송 |
→ 침대 모니터링(의료성)엔 로컬 ML이 정답. 추론 1회 1ms 미만이라 파이에서 여유롭게 돈다.

---

## 파일
```
TOF/ml/
  train_posture.py     # 데이터셋으로 학습 → posture_model.joblib
  predict_posture.py   # 학습된 모델로 실시간 자세 예측
  posture_model.joblib # 학습된 모델(이미 포함, 2.4MB) — 바로 추론 가능
  README.md            # (이 문서)
TOF/dataset/           # 학습 데이터(19,000 프레임) — train이 여기서 읽음
```

## 특징(feature) 설계
- 한 시점 = **[tof1 d0..d15, tof2 d0..d15] = 32차원 거리(mm)**
- 무효값 `-1`은 그대로 사용(트리 모델이 잘 처리)
- 클래스: `empty, supine, side_left, side_right, sitting` (초기 `lying`은 라벨 모호 → 기본 제외)

---

## 라즈베리파이에서 실행 (한 번만 셋업)

### 1) 최신 코드 받기
```bash
cd ~/Majubom
git pull origin main
```

### 2) 의존성 설치 (파이에서 몇 분 소요, 1회)
```bash
pip install scikit-learn joblib numpy
#   설치가 느리거나 실패하면(구형 파이):
#   sudo apt install python3-sklearn python3-numpy
```

### 3) (선택) 다시 학습 — 이미 학습된 모델이 포함돼 있어 **생략 가능**
```bash
cd ~/Majubom/TOF/ml
python train_posture.py                 # dataset 전체로 학습·평가, 모델 저장
#   옵션:
#   python train_posture.py --filtered  # median 필터본으로 학습(추론도 필터 필요)
#   python train_posture.py --split random   # (낙관적) 랜덤 분할 평가
```

### 4) 실시간 자세 예측 (서버가 돌고 있어야 함)
```bash
cd ~/Majubom/TOF/ml
python predict_posture.py                       # localhost:5001 폴링
#   다른 PC에서: python predict_posture.py --server http://<파이IP>:5001
#   한 번만:     python predict_posture.py --once
```
출력 예:
```
자세: supine      (확신 88.5%)
자세: side_left   (확신 91.2%)
자세: empty       (확신 99.0%)
```

---

## 현재 성능 (이 데이터 기준)

`python train_posture.py` (시간분할: 각 클래스 앞 70% 학습 / 뒤 30% 평가)

```
정확도(테스트): 91.7%

              precision  recall  f1
     empty      0.980    1.000  0.990
  side_left     1.000    1.000  1.000
  side_right    0.721    0.964  0.825
    sitting     0.995    0.964  0.980
    supine      0.967    0.660  0.785
```
- **empty / side_left / sitting 은 거의 완벽** → 재실·주요 자세 구분은 잘 됨
- **약점: supine ↔ side_right 혼동** (정자세 일부를 오른쪽 옆으로로 오인)
  - 세션 후반 supine 자세가 살짝 오른쪽으로 쏠렸거나, 이 배치에선 두 자세 형상이 비슷해서로 추정
  - 개선: supine을 더 다양한 위치로 재수집 / 특징 추가(존별 targets, 좌우 무게중심)

### ⚠️ 정확도 해석 주의
- 이 91.7%는 **한 사람·한 세션** 데이터라 **실제 일반화 성능보다 낙관적**이다.
- 배포 정확도는 반드시 **다른 사람 / 다른 날 세션**으로 재평가해야 한다.

---

## 서버에 통합 (선택) — 대시보드에 자세 표시
`server/tof_server.py`에서 모델을 로드해 POST마다 예측을 붙일 수 있다:
```python
# tof_server.py 상단
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "TOF", "ml"))
from predict_posture import load_model, predict
_posture_model = load_model()

# /tof/presence 응답에 추가 (또는 receive_tof 안에서):
label, conf = predict(_posture_model, latest)   # latest = {"tof1":..,"tof2":..}
#   → jsonify(..., posture=label, posture_conf=conf)
```
그 뒤 대시보드(`site/index.html`)에서 `/tof/presence`의 `posture`를 표시하면 됨.

---

## 다음 단계 (성능 올리기)
1. **일반화 데이터**: 다른 사람 체격·침대 위 다른 위치로 각 클래스 보강 → supine/side 혼동 완화
2. **특징 보강**: 존별 `targets`, 좌우/상하 무게중심, 유효존 수 등 파생 특징 추가
3. **시간 정보**: 낙상·전이 감지는 프레임 단위가 아니라 **시퀀스**(LSTM/1D-CNN) 필요 — 별도 라벨 수집
4. **역할 분담**: 낙상 = mmWave, 호흡·심박 이상 = CSI (ToF는 자세·재실 담당)

## 재현/데이터
- 데이터 수집: `TOF/capture_dataset.py`, 스파이크 제거: `TOF/preprocess_dataset.py`
- 데이터 설명: `TOF/dataset/README.md`
