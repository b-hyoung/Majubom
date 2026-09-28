# 2026-09-18 논문 대안 검증

프로젝트 전체 소스·문서·펌웨어 125개 파일과 데이터 흐름을 검토한 뒤 만든 **별도 CPU 실험**이다.
현재 서비스에 반영한 변경은 `ai_agent.investigate()`의 하이브리드 검색 연결과 BM25 무매치/동점 처리다.
새 CMP 기반 후보, 재학습 LSTM-AE, 특징 융합, LMM, ADWIN 계열 감시기는 비교·재생 실행까지 구현했다.
실제 낙상 라벨이 없는 후보를 운영 낙상 판정으로 바꾸지는 않았다.

## 실행

저장소 루트, 기존 Python 환경(numpy, scipy, scikit-learn, torch, lancedb, rank_bm25, requests)에서:

```powershell
python -m research.alternatives.evaluate --rag
python -m unittest research.alternatives.test_alternatives -v
```

평가 스크립트는 BLAS/OpenMP/PyTorch를 1스레드 CPU로 제한한다. CUDA, TabPFN,
기존 `posture_model.joblib`·`lstm_ae.pt`를 학습·저장하지 않는다. 데이터베이스는 `mode=ro`로 읽는다.
첫 검색 평가의 캐시가 없을 때만 `--allow-api`를 추가하면 공개 논문 질의 18개의 임베딩을 한 번 요청한다.
환자 기록은 전송하지 않는다. 코퍼스 재임베딩, 운영 DB 변경, 서비스 실행/중단 기능은 없다.

## 라벨의 뜻과 파일

| 파일 | 내용 | 정답으로 주장할 수 있는 범위 |
|---|---|---|
| `artifacts/project_audit.json` | 전체 검토 파일·해시, DB 건수, 보호 대상 해시 | 검토 시점 스냅샷 |
| `artifacts/posture_sources.json` | 18개 촬영 파일, 원본 6,806프레임·시간 동기화 2,726쌍 | 수집 시 자세 지시 |
| `artifacts/posture_labels.jsonl` | 비중첩 20쌍짜리 윈도우 92개 | 누움 대비 sitting/edge_sit 자세 구분용 대리 라벨 |
| `artifacts/exit_labels.jsonl` | mmWave 569개 전체: 양성 23·음성 208·판단보류 338 | 3초 안에 시작해 2초 이상 지속된 ToF RF empty 전이 **약라벨** |
| `artifacts/fusion_labels.jsonl` | 과거 ToF 두 센서와 연결된 187개 약라벨 | 시계 동기화 미검증인 탐색 데이터 |
| `artifacts/review_queue.jsonl` | 확정/불확정 전이 후보 48개, 검수 필드는 빈 값 | 실제 관찰 근거를 가진 사람이 검수할 후보 |
| `artifacts/drift_control_labels.jsonl` | 안정·인위적 평균변화 수열 1,600개 | 소프트웨어 검증용 합성 변화점 |
| `rag_qrels.json` | 출처가 연결된 질의 18개와 관련 논문 | 독립 전문가 검수 전의 검색 개발 정답셋 |
| `artifacts/evaluation.json` | 실제 비교 결과·제약 | 과제별 범위 내 결과만 해석 |

`clinical_outcome`은 전부 `null`이다. 촬영 자세, RF 약라벨, 시뮬레이션, LLM 분석, 임상 낙상은 서로 바꿔 쓸 수 없다.
현재 운영 DB에 실측 raw/사고보고/정제 라벨이 없고, 낙상 이벤트는 `bed_sim_*` 시뮬레이션이다.
백업에는 raw 데이터가 있지만 독립적인 낙상 정답은 없다.

`review_queue.jsonl`은 `reviewed_target`, `reviewer`, `evidence_uri`를 비워 두었다. 실제 관찰/녹화/일지를
확인한 사람이 해당 시각의 **침상 이탈**을 검수해야 한다. 자세·이탈 라벨만으로 낙상을 확정하면 안 된다.
피험자 식별자도 없으므로 촬영 파일이나 데모 환자를 새로운 실제 피험자로 만들지 않는다.

## 누수 및 품질 방지

- ToF 두 센서는 같은 촬영 파일 안에서 250ms 이내, 각 원본 행을 한 번만 짝짓는다.
- 1초 이상 끊긴 구간을 자른 뒤 비중첩 윈도우를 만든다. 현재 empty 촬영은 이 연속성 조건을 통과하지 못한다.
- 누운 자세의 마지막 촬영은 시험용, 이전 촬영 후반은 정상 임계값 계산용이다. 원본 행 중복은 0개다.
- 정규화는 학습 데이터, 이상 임계값은 정상 calibration 데이터로만 정한다.
- mmWave는 과거 500ms 이내의 ToF raw만 연결한다. 미래 특징이나 ToF 출력 라벨은 융합 입력에 넣지 않는다.
- 60/20/20 시간 구간 사이 입력 이력 3초와 라벨 관찰 종료 시각을 모두 검사하여 경계 15개를 제외한다.
- 점군 내부 프레임 시각이 저장되어 있지 않아 3초 입력 이력은 보수적 가정이다. 시계 동기화도 별도 검증이 필요하다.
- 신뢰도 0.8 조건에서는 사용 가능한 재실/이탈 라벨이 **0개**다. 엄격 판정 결과를 보존한 채, 필터 없는 약라벨 실험을 별도로 표시한다.

## 결과와 배포 판단

- 자세 대리 과제: 시험 59개에서 CMP 후보 F1 1.000, 새 CPU LSTM-AE 0.9545. 실제 이상/낙상 성능이 아니다.
- 융합 대리 과제: 시험 15개(양성 5), early-feature F1 0.000, 단일 ToF 0.6667, 확률 late-max 0.5000.
  동일 임계 0.5에서 양성을 놓쳤으므로 운영 `max()`를 대체하지 않는다. 시험 결과를 보고 임계값을 맞추지 않았다.
- ADWIN1 계열: 합성 정상 800개에서 알람 0, 평균변화 이후 17샘플 지연 감지. 실제 과거 보행 613개 중
  562개 사용, 품질 37개/범위 14개 제외, 변화 후보 0. 임상 민감도는 측정 불가다.
- LMM: 다중 실제 피험자 이력이 없어 실데이터 적합은 보류, 합성 8개 집단으로 REML·수축 추정 동작 확인.
- 검색: 고유 논문 Recall@5는 dense/BM25/hybrid 모두 1.0. MRR@5는 0.963/1.0/1.0.
  작은 개발 정답셋이며 현재 서비스의 chunk top-k와 고유 논문 top-k는 구분한다.

## 논문과 구현 차이

- [Bijlani et al. 2022](https://aging.jmir.org/2022/3/e38211/): 개인별 문맥 제한 거리 비교에 착안한 AB-join.
  원 논문의 일별 PIR·다차원 CMP 전체 재현이 아니며, 재실 문맥·12개 거리요약 특징으로 바꾼 후보다.
- [Malhotra et al. 2016](https://arxiv.org/abs/1607.00148): LSTM 인코더-디코더 구조 참고.
  이번 비교는 16 hidden·40 epochs·MSE이며, 원 논문의 오차 확률 모형이나 배포 체크포인트 재현이 아니다.
- [Li et al. 2026](https://www.nature.com/articles/s41598-026-56750-9): 특징 조기 결합과 결정 지연 결합 비교 원리만 참고.
  웨어러블 CNN-LSTM을 작은 ToF/mmWave 자료에 그대로 옮기지 않고 로지스틱회귀 기준모형을 비교했다.
- [Bifet & Gavaldà 2007](https://www.cs.upc.edu/~Gavalda/papers/adwin06.pdf): Hoeffding 평균 차이 절단 검사.
  최대 256개 원시 관측을 보관하는 ADWIN1 계열 변형이며 압축 버킷 ADWIN2와 다르다.
- [Roshan et al. 2025](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0323133):
  개인차/집단차를 분리하는 Gaussian random-intercept LMM. 논문의 EM 대신 REML, 공변량 없는 기준모형이다.
- [Cormack et al. 2009](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf): RRF 순위 결합 공식.
  BM25 무매치 투표 제외와 동점 동순위 처리를 추가했다.
- [Sawarkar et al. 2024](https://arxiv.org/abs/2404.07220): dense+sparse 검색 비교 동기.
  ELSER/Elasticsearch 실험을 재현한 것이 아니라 LanceDB+BM25 구현을 우리 질의로 비교했다.

상세 한국어 구현 기록은 `docs/ai_plan.html`의 **I13 / N6 (2026-09-18)**에서 확인한다.
