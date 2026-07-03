# ToF 자세/재실 데이터셋

VL53L5CX ×2 (머리맡 tof1 · 발치 tof2, 각 4×4=16존) 에서 수집한 자세·재실 학습용 raw 데이터.

## 수집 환경
- 센서 배치: 머리맡 0.30m / 발치 0.72m 높이, 기울기 90°/65°, 센서 간 2.12m, 침대 폭 0.95m
- 프레임레이트: 약 5Hz/센서 (ESP32 10Hz 설정, HTTP 오버헤드로 실측 ~5Hz)
- 각 프레임 = 한 센서의 한 측정 (tof1/tof2 교대로 기록)

## 클래스 (1단계)
| label | 의미 | 프레임 |
|-------|------|--------|
| `empty` | 빈 침대 (이불 흐트러짐 포함) | 3000 |
| `supine` | 정자세(등 대고 누움) | 3000 |
| `side_left` | 왼쪽 옆으로 누움 | 3000 |
| `side_right` | 오른쪽 옆으로 누움 | 3000 |
| `sitting` | 상체 세움/앉음 | 3000 |
| `lying` | 초기 캡처(라벨 모호 — supine과 병합 또는 제외 권장) | 4000 |

## 파일 형식
- `tof_<label>_<timestamp>.jsonl` — 한 줄 = 한 프레임
  ```json
  {"t":"2026-07-03T19:37:07.830","sensor":"tof1","resolution":"4x4",
   "distances_mm":[-1,...,206,219,...],"targets":[1,0,...],"label":"lying"}
  ```
- `tof_<label>_<timestamp>.csv` — `timestamp,sensor,resolution,label,d0..d15,t0..t15` (판다스/DB 호환)
- `*_filtered_w5.*` — 존별 causal median(window 5)로 스파이크 제거한 버전
  - 원본 대비 큰 스파이크(>100mm) ~4% 제거, 나머지는 미세 평활
  - **주의: 학습에 필터본을 쓰면 실시간 추론도 동일 필터(median w5)를 써야 함**

## 값 규약
- `distances_mm`: 각 존 거리(mm). **-1 = 측정 실패(무효)** — 스파이크 아님, 별도 처리(마스킹/보간) 필요
- `targets`: 각 존 검출 타겟 수
- 좌표계(파생): X=머리(−)↔발(+), Y=좌(−)↔우(+), Z=매트리스 위 높이

## 재현 (수집/전처리 스크립트)
```bash
cd TOF
python capture_dataset.py <label> <count>     # 예: python capture_dataset.py empty 3000
python preprocess_dataset.py <입력.jsonl> 5   # median w5 필터본 생성
```

## TODO (다음 단계)
- 2단계: 다른 사람 체격 / 침대 위 다른 위치로 각 클래스 보강 (일반화)
- 낙상·전이는 시퀀스 라벨 필요 (프레임 단위 아님) — mmWave 병행 권장
- 이상징후(호흡·심박)는 CSI 담당
