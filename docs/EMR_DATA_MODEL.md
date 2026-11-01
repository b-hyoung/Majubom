# 환자 EMR · 원문과 벡터 저장 구조

현재 소스에서 EMR 원문은 SQLite `server/majubom.db`의 `emr_records`에 저장된다.
이전에는 논문 `paper_chunks`와 AI 분석 `analysis_chunks` 벡터 코드만 있었으며,
개인 EMR 벡터 경로는 이번에 `server/emr_kb.py`로 추가했다.

## 화면과 실제 예시

`http://<Pi-IP>:5001/emr`에서 환자를 선택하면 원문 타임라인·실제 SQL 열·관계·벡터 상태와 검색을 확인한다.
간호사 화면과 로우데이터 화면에서 링크로 이동한다.
Claude가 작성한 `server/demo_emr.json`에는 기존 가상 환자 5명의 입원 평가·재활 평가·간호 인계 15건이 있다.
모든 출처와 원문은 가상 예시로 표시하며 실제 환자/실측 관찰로 취급하지 않는다.
`bed_01`에는 예시 EMR을 넣지 않는다. 기존 등록 투약·위험요인을 활용하며 새 처방을 생성하지 않는다.

## SQL 원문 구조

| 테이블 | 연결 필드 | 저장 내용 |
|---|---|---|
| patients | bed_id (PK) | 이름·나이·성별·진단·키/몸무게·mmWave 배정 |
| emr_records | id (PK), bed_id | record_date, source, record_type, text, created_at |
| medications | id, bed_id | 등록 투약 정보 |
| risk_factors | id, bed_id | 등록 위험요인 |
| gait_priors | id, bed_id, target_id | EMR/문헌 기반 기대 보행값 |
| ai_analysis | id, patient_id | 기존 보고서와 실제 입력 스냅샷 |

화면의 schema는 고정 모형이 아니라 현재 DB의 `PRAGMA table_info`를 조회한다.
위 관계는 논리 연결이며 기존 테이블에 존재하지 않는 FOREIGN KEY를 새로 있다고 표시하지 않는다.
원문 입력은 기존 `POST /api/dashboard/patients/<bed_id>/emr`를 사용한다.
SQL 저장 후 로컬 EMR 벡터 갱신을 시도하고 `vector_index`로 실제 성공/실패를 별도 반환한다.
벡터 실패가 성공한 원문 저장을 취소하거나 성공으로 위장하지 않는다.

## LanceDB 개인 EMR

저장 디렉터리는 `server/lancedb_store`, 컬렉션은 다음처럼 분리한다.

- `paper_chunks`: 치매/센서 등 논문 근거, 기존 API 의미 임베딩.
- `analysis_chunks`: 과거 AI 분석 결과, 기존 코드.
- `emr_chunks`: 개인 EMR의 로컬 어휘·문자 벡터, 현재 실제 예시를 저장하는 컬렉션.
- `emr_semantic_chunks`: 명시적 semantic 인덱스 요청 시 API 의미 임베딩을 저장할 별도 컬렉션.

현재 서버에 API 키가 없어 semantic 벡터는 생성하지 않았다.
로컬은 `local-text-hash-v1`, 단어 + 문자 2/3-gram을 SHA256 부호 버킷에 넣은 512차원 L2 정규화 벡터다.
외부 API나 모델 다운로드 없이 실제 LanceDB 벡터로 저장·코사인 검색한다.
**어휘 유사도이며 신경망 의미 임베딩이 아니다.** 두 종류를 섞어 비교하지 않는다.

벡터 행에는 chunk_id, patient_id, record_id, record_date, source, record_type,
original_text, content_hash, fictional, embedding_model, vector가 있다.
원문 800자 단위, 다음 시작 700자로 나누어 최대 100자 겹친다.
환자 이름은 벡터 텍스트에서 치환하지만 완전한 비식별화를 보장하는 처리는 아니다.
SQL 원문은 그대로 유지한다. chunk_id는 환자/원문 ID/부분번호를 포함한다.
현재 모델과 텍스트 해시가 일치하면 다시 임베딩하지 않으며, 변경분은 해당 키로 upsert한다.
검색은 patient_id를 먼저 필터링하고 현재 SQL 텍스트 해시와 맞지 않는 벡터는 결과에서 제외한다.
화면은 원문/청크 수, 실제 저장 수, 현재 해시 일치 수, 일부 실제 벡터 수치와 차원을 구분한다.

## API와 재생성

- `GET .../emr-storage`: 환자 원문/등록 정보/SQL 구조/벡터 상태. 추가 임베딩·저장 없음.
- `GET .../emr-search?q=...`: 해당 환자만 로컬 벡터 검색. 점수는 어휘 코사인 유사도이며 확률/임상 위험이 아니다.
- `POST .../emr-index` JSON `{"method":"local"}`: 원문의 로컬 벡터 갱신.
- `POST .../emr-index` JSON `{"method":"semantic"}`: 기존 paper_kb.embed_texts를 재사용한 API 의미 임베딩. 키/의존성 없으면 실제 실패를 반환한다.

가상 EMR SQL과 로컬 벡터는 서버 시작 시 멱등으로 추가하며 기존 기록을 덮어쓰지 않는다. 벡터 라이브러리가 없으면 SQL만 유지하고 벡터는 미준비로 표시한다.
새 설치에서 가상 벡터까지 생성하려면 프로젝트 루트에서 실행한다.

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/seed_demo_emr.py
```

이 도구는 고정 가상 환자만 대상으로 원문/벡터를 생성하고 반복 실행 시 중복하지 않는다.
런타임 SQL DB와 개인 EMR 벡터 인덱스는 Git에서 제외한다. 가상 JSON만 코드와 함께 유지한다.

## 리포트 연결과 검증

`ai_agent.investigate`가 실제 논문 검색을 진행한 질의마다 해당 환자 EMR 로컬 벡터를 함께 검색한다.
성공 결과는 chunk_id별 중복을 제거하고, 오류/갱신 상태는 요약해서 조사 반환의 emr_retrieval → 리포트 입력 `환자_EMR_검색_결과`로 전달하며,
논문 근거 ID와 EMR 기록 ID를 섞지 않는다. 기존 EMR 원문 입력도 유지한다. 문헌 충분성 판단에는 EMR 검색 결과를 넣지 않는다. 프롬프트 버전 integrated_v7_emr로 구분한다.
report_trace는 실제 emr_search/emr_index 실행을 관찰한다.
EMR 인덱스가 없거나 검색이 실패하면 결과의 실패 상태를 넘기고 다른 환자 데이터로 대체하지 않는다.
기존 논문 인덱스/AI 키 미설정 상태에서 리포트 전체 실행 성공을 주장하지 않는다.

`server/test_emr_kb.py`는 임시 SQLite/LanceDB로 실제 벡터·멱등 저장·환자 분리·변경 원문 제외·GET 비저장·입력 API를 검증한다.
실제 임상 DB나 외부 AI API를 테스트에 사용하지 않는다.
