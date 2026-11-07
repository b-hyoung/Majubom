# 마주봄 전체 구조

> **2026-10-31:** `main`의 `mmWave/`는 새 작업을 위해 비워졌습니다. 기존 수집기·설정·실험 데이터는 `backup/mmwave-before-reset` 브랜치에 보관되어 있으며, 아래 mmWave 실행 경로는 복원 전까지 사용할 수 없습니다. 서버의 mmWave 수신 코드는 남아 있지만 해당 수집기가 없어 독립적으로 실측을 수집하지 않습니다. 이후 Pi에서 실제로 돌리는 최신 mmWave 코드(천장 2.5m·하향 90° 설정, 노트북 스트림, 낙상 판정 `fall_detect.py`)는 `claude/sensor-remount-posture-v2` 브랜치에 있습니다(2026-10-31). main의 server/·tools/ 일부(예: `tools/set_bed_center.py`, `server/bed_position.py`가 읽는 `mmWave/bed_center.json`, 노트북 스트림 `/api/stream`)는 그 수집기와 함께 쓸 때 동작합니다.

AI 작업 진입점은 `AGENTS.md`와 `docs/AI_START_HERE.md`다.
이 문서는 실제 로컬 파일을 확인해 생성한 목록과 역할 설명을 함께 제공한다.
목록에는 데이터·모델·역사 문서도 포함한다. `.git`, 가상환경, 캐시, IDE 설정,
PlatformIO 빌드, node_modules, 로그, LanceDB 저장 엔진 내부 파일은 생략한다.
자동 생성 구간은 직접 편집하지 않고 `python3 tools/update_project_structure.py`로 갱신한다.
코드 역할이 달라지면 아래 역할/경로 설명도 반드시 수정한다.

## 폴더와 책임

| 경로 | 책임/진입점 |
|---|---|
| TOF/src/ | ESP32-S3 Arduino 펌웨어와 PlatformIO 프로젝트. main.ino가 원본; 이전 Windows 전처리 생성물 main.ino.cpp는 제거했고 PlatformIO가 빌드 시 임시 생성한다 |
| TOF/ml/ | 자세 추론 predict_posture.py(운영: 세 사람 실측 RF posture_rf_bedC_20261104_3p/ → 없으면 CNN 특징+RF posture_cnn_bedC_20261104/ → RF posture_model.joblib, 노트북 원본 incoming/), RF 학습(train_posture_v2.py, 이전 모델은 archive/), ROI/침대 좌표(ROI 마스킹 비교 실험·노트북 안내 roi_experiment_20261106/), LSTM-AE 참고 이상탐지, 특징 로깅, 후보 모델 비교 |
| TOF/dataset*/ | 라벨별 ToF 거리 CSV/JSONL, 기존 4×4 및 빈 침대 데이터 보관. dataset_v3 = 새 침상 실측, dataset_v3_synth = 합성 참가자(학습 보조 전용, synth_subjects.py), dataset_v4 = 센서 1.5m로 올린 뒤 6인 재녹화(정리된 값, 노트북 학습 안내 LAPTOP_TRAIN_PROMPT.md) |
| TOF/의 Python/HTML | 수집(capture_dataset; 2026-10 새 설치용 세션 녹화 record_posture → dataset_v2/), 전처리(preprocess_dataset), viewer_proxy 및 자세/3D 실험 화면 |
| mmWave/ | TI IWR6843AOP cfg(현재 천장 AOP_ceiling_2m5.cfg), USB TLV 리더 send_mmw, 침대 가운데 bed_center.json(tools/set_bed_center.py, server/bed_position.py), 설정 quick_cfg, record, 자세 probe/통합 리더 실험 |
| mmWave/exit_seq/ | 이탈 시퀀스 기록·공동 로깅·증강·시계열/속도 분석 및 실험 인수인계 |
| server/ | ToF(:5001)/mmWave(:5002) HTTP 서버, SQLite, 센서 판정, 통합 위험, 대시보드 API, AI 리포트/검색 |
| server/reports/ | 저장된 보고서 예시/산출물 |
| server/lancedb_store/ | 논문 검색용 벡터 저장소, 재생성 내부 파일 |
| site/ | 실제 웹 대시보드 index.html, 디자인 참고 dashboard_design.html, assets와 JS/브라우저 테스트 |
| docs/ | 실측 경로 코드 가이드(CODE_GUIDE_LIVE.html)·AI 시작 안내·전체 구조·현장 점검·운영 개념·관련 연구·원격 제어·연구 검토 질문(질문.md)·나중에 할 일(나중에_할일.md) 및 역사 specs |
| research/alternatives/ | 후보 알고리즘 실험, 평가 데이터와 산출물; 운영 코드와 구분 |
| test_8x8/ | 8×8 검증용 firmware와 :5011 실험 서버. 운영 TOF/src와 별개 |
| tools/ | 구조 문서 자동 갱신/검사 도구, 원시 기록 날짜별 압축 보관 archive_raw.py(cron 매일 03:30), 코드 가이드 생성 build_code_guide.py(+코드 조각 도구 guide_lib.py), 노트북에서 :5012/api/stream 프레임을 받아 저장하는 laptop_stream_client.py |
| cloud_dashboard.py | Pi ToF 대시보드를 별도 포트로 프록시하는 보조 Flask 앱(설정 확인 후 사용) |
| README.md / SYSTEM_ARCHITECTURE.md | 프로젝트 소개 / 센서·판정·DB·UI·AI 데이터 흐름 설명 |
| 0704*, 0707*, ai_progress_presentation.html | 과거 작업/인수인계 및 발표 자료; 현재 운영 설정의 기준으로 사용하지 않음 |

## 서버의 주요 연결

- 로우데이터 분기: tof_server.py와 send_mmw.py → server/live_diagnostics.py → :5012 mmWave/diagnostic_server.py → site/sensor-diagnostics.html/js. 기존 운영 경로와 병렬로 최신값만 메모리에 유지한다.
- 노트북 스트림: tof_server.py·send_mmw.py → live_diagnostics.FrameStreamPublisher → :5012/api/stream-ingest → GET /api/stream(NDJSON) → tools/laptop_stream_client.py(노트북 저장). Pi에는 저장하지 않는다.
- 계산 관측: server/diagnostic_observer.py → 기존 ToF/mmWave 관측 API → :5012/api/operations → 센서별 수식·기준선·저장 결과 탭. 추가 DB 저장/재평가 없음.
- 환자 EMR: demo_emr.json → demo_emr.py → SQLite emr_records → emr_kb.py → 환자별 LanceDB emr_chunks → /emr 화면. API 의미 임베딩은 별도 emr_semantic_chunks이며 로컬 어휘 벡터와 구분한다. docs/EMR_DATA_MODEL.md 필독.
- AI 관측: server/report_trace.py → 실제 ai_agent/ai_report/paper_kb/integrated_analysis 호출 → 환자별 메모리 타임라인 → AI 리포트 탭.
- diagnostic_decode.py는 mmWave 원시 압축값/정제 좌표를 디코딩한다. 독립 USB 진단 모드는 선택 기능이며 기본 동시 화면 모드는 HTTP 수신만 한다.
- CLAUDE.md는 Claude에 AGENTS.md와 필독 문서를 연결한다. docs/SENSOR_DIAGNOSTICS.md는 두 화면 실행 안내, docs/SYSTEM_ARCHITECTURE.html은 HTML 구성도다.
- run_all.py → tof_server.py + mmw_server.py (mmWave USB 리더는 별도).
- tof_server.py → predict_posture/roi/tof_anomaly(선택) + tof_logic/behavior_fsm + db.
- send_mmw.py → mmw_server.py → mmw_logic.py + gait_prior.py + db.
- tof_server.py → dashboard_api.py → site/index.html 및 dashboard-assets, 환자/보고서 API.
- ai_agent.py/ai_report.py → integrated_analysis.py, paper_kb.py/retrieval.py/analysis_kb.py,
  report_review.py와 DB: 센서 판정 수집, 근거 검색, 보고서 생성·검토.
- maintenance_ai.py는 변화 감시/검토, sim_state.py는 시뮬레이션 경로.
- db.py는 데이터 저장/환자·센서·활동·보고서 테이블을 담당한다.
- test_*.py와 site/tests는 영역별 검증 코드. 변경 대상에 맞는 테스트를 선택한다.

## 전체 파일 목록

<!-- BEGIN GENERATED TREE -->
```text
Majubom/
├── deploy/
│   ├── cloudflare/
│   │   ├── code-guide-worker.js
│   │   ├── majubom-ai-guide-worker.js
│   │   └── publish_code_guide.sh
│   └── majubom-mmw-reader.service
├── docs/
│   ├── captures/
│   │   └── 2026-10-11/
│   │       ├── 00_system_architecture.png
│   │       ├── 03_collect_preprocess_zoom.png
│   │       ├── 03a_raw_tof_grid.png
│   │       ├── 03b_raw_mmwave_pointcloud.png
│   │       ├── 04b_mmwave_algorithms.png
│   │       ├── 05a_dashboard_live.png
│   │       ├── 05b_dashboard_fall_alert_sim.png
│   │       ├── 06_dashboard_mobile_web.png
│   │       └── README.md
│   ├── superpowers/
│   │   └── specs/
│   │       ├── 2026-06-15-csi-server-integration-design.md
│   │       ├── 2026-06-23-majubom-interactive-presentation-design.md
│   │       └── 2026-06-24-csi-mmwave-fusion-honest-direction.md
│   ├── AI_GUIDE.html
│   ├── AI_START_HERE.md
│   ├── CODE_GUIDE_LIVE.html
│   ├── codex_remote-control.md
│   ├── DEEP_LEARNING_PLAN.md
│   ├── EMR_DATA_MODEL.md
│   ├── operating_concept_day_night.md
│   ├── PROJECT_STRUCTURE.md
│   ├── related_work_gait_fall.md
│   ├── SENSOR_CHECK_2026-10-06.md
│   ├── SENSOR_DIAGNOSTICS.md
│   ├── SYSTEM_ARCHITECTURE.html
│   ├── 나중에_할일.md
│   ├── 논문메모_ROI_20261106.md
│   ├── 논문메모_ToF_20261104.md
│   ├── 진행상황_및_예측연구계획.md
│   └── 질문.md
├── mmWave/
│   ├── tests/
│   └── .gitkeep
├── output/
│   └── presentation/
│       └── 마주봄_발표_4장.pptx
├── research/
│   └── alternatives/
│       ├── artifacts/
│       │   ├── anomaly_predictions.jsonl
│       │   ├── drift_control_labels.jsonl
│       │   ├── evaluation.json
│       │   ├── exit_labels.jsonl
│       │   ├── fusion_labels.jsonl
│       │   ├── fusion_predictions.jsonl
│       │   ├── historical_drift_candidates.jsonl
│       │   ├── posture_labels.jsonl
│       │   ├── posture_sources.json
│       │   ├── project_audit.json
│       │   ├── query_embeddings.npz
│       │   ├── rag_per_query.json
│       │   └── review_queue.jsonl
│       ├── __init__.py
│       ├── data.py
│       ├── evaluate.py
│       ├── models.py
│       ├── rag_qrels.json
│       ├── README.md
│       └── test_alternatives.py
├── server/
│   ├── backups/
│   │   ├── ai_analysis_bed01_20261022.json
│   │   ├── tof_baseline_before_20261022_1125.json
│   │   ├── tof_baseline_before_20261022_2026.json
│   │   ├── tof_baseline_before_20261023_0835.json
│   │   ├── tof_baseline_before_20261023_1424.json
│   │   ├── tof_baseline_before_20261029_115451.json
│   │   ├── tof_baseline_before_20261029_161246.json
│   │   ├── tof_baseline_before_20261029_161619.json
│   │   ├── tof_baseline_before_20261031_163820.json
│   │   └── tof_baseline_before_20261031_164540.json
│   ├── lancedb_store/
│   │   ├── analysis_chunks.lance/
│   │   │   └── [LanceDB 자동 생성 내부 파일 생략]
│   │   ├── emr_chunks.lance/
│   │   │   └── [LanceDB 자동 생성 내부 파일 생략]
│   │   └── paper_chunks.lance/
│   │       └── [LanceDB 자동 생성 내부 파일 생략]
│   ├── raw_archive/
│   │   ├── 2026-10-08/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-09/
│   │   │   └── mmw_readings.jsonl.gz
│   │   ├── 2026-10-11/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-12/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-13/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-14/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-15/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-16/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-17/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-18/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-19/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-20/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-21/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-22/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-23/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-28/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-29/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-30/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   ├── 2026-10-31/
│   │   │   ├── mmw_readings.jsonl.gz
│   │   │   └── tof_readings.jsonl.gz
│   │   └── 2026-11-02/
│   │       └── tof_readings.jsonl.gz
│   ├── reports/
│   │   ├── 2026-08-08.json
│   │   ├── 2026-08-15.json
│   │   ├── 2026-08-22.json
│   │   ├── 2026-08-29.json
│   │   └── cumulative_2026-08-29.json
│   ├── ai_agent.py
│   ├── ai_report.py
│   ├── analysis_kb.py
│   ├── bed_position.py
│   ├── behavior_fsm.py
│   ├── dashboard_api.py
│   ├── data_integrity.py
│   ├── db.py
│   ├── demo_emr.json
│   ├── demo_emr.py
│   ├── diagnostic_observer.py
│   ├── emr_kb.py
│   ├── export_report_reviews.py
│   ├── fall_report.py
│   ├── fall_risk.py
│   ├── gait_evidence.json
│   ├── gait_prior.md
│   ├── gait_prior.py
│   ├── gait_trend.py
│   ├── integrated_analysis.py
│   ├── live_diagnostics.py
│   ├── maintenance_ai.py
│   ├── majubom.db
│   ├── majubom.db.4x4bak.gz
│   ├── mmw_baseline.json
│   ├── mmw_logic.py
│   ├── mmw_server.py
│   ├── paper_kb.py
│   ├── report_charts.py
│   ├── report_review.py
│   ├── report_trace.py
│   ├── retrieval.py
│   ├── run_all.py
│   ├── server.md
│   ├── sim_month_output.json
│   ├── sim_state.py
│   ├── test_dashboard_api.py
│   ├── test_e2e_pipeline.py
│   ├── test_emr_kb.py
│   ├── test_fall_report.py
│   ├── test_fall_risk.py
│   ├── test_gait_prior.py
│   ├── test_gait_trend.py
│   ├── test_patient_report_context.py
│   ├── test_report_review.py
│   ├── test_report_trace.py
│   ├── test_sensor_observability.py
│   ├── test_tof_autocal.py
│   ├── test_tof_diagnostics.py
│   ├── test_tof_toss.py
│   ├── tof_3d.html
│   ├── tof_autocal.py
│   ├── tof_baseline.json
│   ├── tof_baseline_history.jsonl
│   ├── tof_baseline_tilt20_20261008.json
│   ├── tof_logic.py
│   ├── tof_server.py
│   └── tof_toss.py
├── site/
│   ├── dashboard-assets/
│   │   ├── dashboard.css
│   │   ├── dashboard.js
│   │   ├── data.js
│   │   ├── dev.js
│   │   ├── report-review.js
│   │   └── simulation.js
│   ├── tests/
│   │   ├── .gitignore
│   │   ├── dashboard.spec.cjs
│   │   ├── data.test.cjs
│   │   ├── playwright.config.cjs
│   │   └── report-review.test.cjs
│   ├── dashboard_design.html
│   ├── DASHBOARD_WORK.md
│   ├── dev.html
│   ├── emr-dashboard.html
│   ├── index.html
│   ├── sensor-diagnostics.html
│   └── sensor-diagnostics.js
├── test_8x8/
│   ├── firmware/
│   │   ├── main.ino
│   │   └── platformio.ini
│   ├── server/
│   │   └── test_tof_server.py
│   └── README.md
├── TOF/
│   ├── dataset/
│   │   ├── README.md
│   │   ├── tof_edge_sit_20260707_145011.csv
│   │   ├── tof_edge_sit_20260707_145011.jsonl
│   │   ├── tof_edge_sit_20260707_172105.csv
│   │   ├── tof_edge_sit_20260707_172105.jsonl
│   │   ├── tof_edge_sit_20260707_172337.csv
│   │   ├── tof_edge_sit_20260707_172337.jsonl
│   │   ├── tof_edge_sit_20260707_172439.csv
│   │   ├── tof_edge_sit_20260707_172439.jsonl
│   │   ├── tof_edge_sit_20260707_193015.csv
│   │   ├── tof_edge_sit_20260707_193015.jsonl
│   │   ├── tof_empty_20260707_175731.csv
│   │   ├── tof_empty_20260707_175731.jsonl
│   │   ├── tof_side_left_20260707_143411.csv
│   │   ├── tof_side_left_20260707_143411.jsonl
│   │   ├── tof_side_left_20260707_171608.csv
│   │   ├── tof_side_left_20260707_171608.jsonl
│   │   ├── tof_side_right_20260707_144545.csv
│   │   ├── tof_side_right_20260707_144545.jsonl
│   │   ├── tof_side_right_20260707_144651_fromEdge.jsonl
│   │   ├── tof_side_right_20260707_171744.csv
│   │   ├── tof_side_right_20260707_171744.jsonl
│   │   ├── tof_side_right_20260707_194753.csv
│   │   ├── tof_side_right_20260707_194753.jsonl
│   │   ├── tof_sitting_20260707_145148.csv
│   │   ├── tof_sitting_20260707_145148.jsonl
│   │   ├── tof_sitting_20260707_171946.csv
│   │   ├── tof_sitting_20260707_171946.jsonl
│   │   ├── tof_supine_20260707_143006.csv
│   │   ├── tof_supine_20260707_143006.jsonl
│   │   ├── tof_supine_20260707_162613.csv
│   │   ├── tof_supine_20260707_162613.jsonl
│   │   ├── tof_supine_20260707_171450.csv
│   │   ├── tof_supine_20260707_171450.jsonl
│   │   ├── tof_supine_20260707_194150.csv
│   │   └── tof_supine_20260707_194150.jsonl
│   ├── dataset_4x4_backup/
│   │   ├── tof_empty_20260703_195213.csv
│   │   ├── tof_empty_20260703_195213.jsonl
│   │   ├── tof_empty_20260703_195213_filtered_w5.csv
│   │   ├── tof_empty_20260703_195213_filtered_w5.jsonl
│   │   ├── tof_lying_20260703_193707.csv
│   │   ├── tof_lying_20260703_193707.jsonl
│   │   ├── tof_lying_20260703_193707_filtered_w5.csv
│   │   ├── tof_lying_20260703_193707_filtered_w5.jsonl
│   │   ├── tof_side_left_20260703_200431.csv
│   │   ├── tof_side_left_20260703_200431.jsonl
│   │   ├── tof_side_left_20260703_200431_filtered_w5.csv
│   │   ├── tof_side_left_20260703_200431_filtered_w5.jsonl
│   │   ├── tof_side_right_20260703_201001.csv
│   │   ├── tof_side_right_20260703_201001.jsonl
│   │   ├── tof_side_right_20260703_201001_filtered_w5.csv
│   │   ├── tof_side_right_20260703_201001_filtered_w5.jsonl
│   │   ├── tof_sitting_20260703_201559.csv
│   │   ├── tof_sitting_20260703_201559.jsonl
│   │   ├── tof_sitting_20260703_201559_filtered_w5.csv
│   │   ├── tof_sitting_20260703_201559_filtered_w5.jsonl
│   │   ├── tof_supine_20260703_195854.csv
│   │   ├── tof_supine_20260703_195854.jsonl
│   │   ├── tof_supine_20260703_195854_filtered_w5.csv
│   │   └── tof_supine_20260703_195854_filtered_w5.jsonl
│   ├── dataset_empty_full/
│   │   └── tof_empty_20260707_175731.jsonl
│   ├── dataset_empty_old/
│   │   ├── tof_empty_20260707_142706.csv
│   │   ├── tof_empty_20260707_142706.jsonl
│   │   ├── tof_empty_20260707_161246.csv
│   │   └── tof_empty_20260707_161246.jsonl
│   ├── dataset_v2/
│   │   ├── sessions.csv
│   │   ├── tof_S01_edge_sit_이불없음_머리쪽_r14_20261008_195554.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_머리쪽_r15_20261008_195634.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_머리쪽_r1_20261008_174516.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_머리쪽_r2_20261008_174551.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_머리쪽_r3_20261008_174631.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_발쪽_r10_20261008_195140.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_발쪽_r11_20261008_195221.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_발쪽_r1_20261008_173746.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_발쪽_r2_20261008_173821.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_발쪽_r3_20261008_173901.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_옆쪽_r12_20261008_195356.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_옆쪽_r13_20261008_195436.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_옆쪽_r1_20261008_174241.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_옆쪽_r2_20261008_174316.jsonl
│   │   ├── tof_S01_edge_sit_이불없음_옆쪽_r3_20261008_174356.jsonl
│   │   ├── tof_S01_side_left_이불없음_r10_20261008_192950.jsonl
│   │   ├── tof_S01_side_left_이불없음_r11_20261008_193030.jsonl
│   │   ├── tof_S01_side_left_이불없음_r12_20261008_193110.jsonl
│   │   ├── tof_S01_side_left_이불없음_r13_20261008_193150.jsonl
│   │   ├── tof_S01_side_left_이불없음_r14_20261008_193231.jsonl
│   │   ├── tof_S01_side_left_이불없음_r15_20261008_193311.jsonl
│   │   ├── tof_S01_side_left_이불없음_r1_20261008_172642.jsonl
│   │   ├── tof_S01_side_left_이불없음_r2_20261008_172717.jsonl
│   │   ├── tof_S01_side_left_이불없음_r3_20261008_172757.jsonl
│   │   ├── tof_S01_side_left_이불없음_r4_20261008_191420.jsonl
│   │   ├── tof_S01_side_left_이불없음_r5_20261008_191501.jsonl
│   │   ├── tof_S01_side_left_이불없음_r6_20261008_191541.jsonl
│   │   ├── tof_S01_side_left_이불없음_r7_20261008_191621.jsonl
│   │   ├── tof_S01_side_left_이불없음_r8_20261008_192830.jsonl
│   │   ├── tof_S01_side_left_이불없음_r9_20261008_192910.jsonl
│   │   ├── tof_S01_side_right_이불없음_r10_20261008_192407.jsonl
│   │   ├── tof_S01_side_right_이불없음_r11_20261008_192447.jsonl
│   │   ├── tof_S01_side_right_이불없음_r12_20261008_192527.jsonl
│   │   ├── tof_S01_side_right_이불없음_r13_20261008_192607.jsonl
│   │   ├── tof_S01_side_right_이불없음_r14_20261008_192647.jsonl
│   │   ├── tof_S01_side_right_이불없음_r15_20261008_192727.jsonl
│   │   ├── tof_S01_side_right_이불없음_r1_20261008_172954.jsonl
│   │   ├── tof_S01_side_right_이불없음_r2_20261008_173030.jsonl
│   │   ├── tof_S01_side_right_이불없음_r3_20261008_173110.jsonl
│   │   ├── tof_S01_side_right_이불없음_r4_20261008_191759.jsonl
│   │   ├── tof_S01_side_right_이불없음_r5_20261008_191840.jsonl
│   │   ├── tof_S01_side_right_이불없음_r6_20261008_191920.jsonl
│   │   ├── tof_S01_side_right_이불없음_r7_20261008_192000.jsonl
│   │   ├── tof_S01_side_right_이불없음_r8_20261008_192246.jsonl
│   │   ├── tof_S01_side_right_이불없음_r9_20261008_192326.jsonl
│   │   ├── tof_S01_sitting_이불없음_r10_20261008_200238.jsonl
│   │   ├── tof_S01_sitting_이불없음_r11_20261008_200318.jsonl
│   │   ├── tof_S01_sitting_이불없음_r12_20261008_200358.jsonl
│   │   ├── tof_S01_sitting_이불없음_r13_20261008_200439.jsonl
│   │   ├── tof_S01_sitting_이불없음_r14_20261008_200519.jsonl
│   │   ├── tof_S01_sitting_이불없음_r15_20261008_200559.jsonl
│   │   ├── tof_S01_sitting_이불없음_r1_20261008_173240.jsonl
│   │   ├── tof_S01_sitting_이불없음_r2_20261008_173315.jsonl
│   │   ├── tof_S01_sitting_이불없음_r3_20261008_173355.jsonl
│   │   ├── tof_S01_sitting_이불없음_r4_20261008_195837.jsonl
│   │   ├── tof_S01_sitting_이불없음_r5_20261008_195917.jsonl
│   │   ├── tof_S01_sitting_이불없음_r6_20261008_195958.jsonl
│   │   ├── tof_S01_sitting_이불없음_r7_20261008_200038.jsonl
│   │   ├── tof_S01_sitting_이불없음_r8_20261008_200118.jsonl
│   │   ├── tof_S01_sitting_이불없음_r9_20261008_200158.jsonl
│   │   ├── tof_S01_supine_이불없음_r11_20261008_190028.jsonl
│   │   ├── tof_S01_supine_이불없음_r12_20261008_190108.jsonl
│   │   ├── tof_S01_supine_이불없음_r13_20261008_190148.jsonl
│   │   ├── tof_S01_supine_이불없음_r14_20261008_190229.jsonl
│   │   ├── tof_S01_supine_이불없음_r1_20261008_172234.jsonl
│   │   ├── tof_S01_supine_이불없음_r2_20261008_172309.jsonl
│   │   ├── tof_S01_supine_이불없음_r3_20261008_172349.jsonl
│   │   ├── tof_S01_supine_이불없음_무릎_r15_20261008_190928.jsonl
│   │   ├── tof_S01_supine_이불없음_무릎_r16_20261008_191008.jsonl
│   │   ├── tof_S01_supine_이불없음_무릎_r17_20261008_191049.jsonl
│   │   ├── tof_S01_supine_이불없음_무릎_폰_r18_20261008_191214.jsonl
│   │   ├── tof_S01_supine_이불없음_무릎_폰_r19_20261008_191254.jsonl
│   │   ├── tof_S01_supine_이불없음_폰_r10_20261008_185844.jsonl
│   │   ├── tof_S01_supine_이불없음_폰_r4_20261008_185056.jsonl
│   │   ├── tof_S01_supine_이불없음_폰_r5_20261008_185137.jsonl
│   │   ├── tof_S01_supine_이불없음_폰_r6_20261008_185217.jsonl
│   │   ├── tof_S01_supine_이불없음_폰_r7_20261008_185257.jsonl
│   │   ├── tof_S01_supine_이불없음_폰_r8_20261008_185724.jsonl
│   │   ├── tof_S01_supine_이불없음_폰_r9_20261008_185804.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_r1_20261013_220255.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_r2_20261013_220330.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_r3_20261013_220405.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_r4_20261013_220441.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_r5_20261013_220516.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_머리가까이_r1_20261013_221148.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_머리가까이_r2_20261013_221223.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_머리가까이_r3_20261013_221258.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_머리가까이_r4_20261013_221333.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_머리가까이_r5_20261013_221408.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_발가까이_r1_20261013_220819.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_발가까이_r2_20261013_220854.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_발가까이_r3_20261013_220929.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_발가까이_r4_20261013_221004.jsonl
│   │   ├── tof_S02_edge_sit_이불없음_옆쪽_발가까이_r5_20261013_221039.jsonl
│   │   ├── tof_S02_side_left_이불덮음_r1_20261013_224344.jsonl
│   │   ├── tof_S02_side_left_이불덮음_r2_20261013_224419.jsonl
│   │   ├── tof_S02_side_left_이불덮음_r3_20261013_224455.jsonl
│   │   ├── tof_S02_side_left_이불덮음_r4_20261013_224530.jsonl
│   │   ├── tof_S02_side_left_이불덮음_r5_20261013_224605.jsonl
│   │   ├── tof_S02_side_left_이불없음_r10_20261013_211204.jsonl
│   │   ├── tof_S02_side_left_이불없음_r11_20261013_211239.jsonl
│   │   ├── tof_S02_side_left_이불없음_r12_20261013_211314.jsonl
│   │   ├── tof_S02_side_left_이불없음_r13_20261013_211349.jsonl
│   │   ├── tof_S02_side_left_이불없음_r14_20261013_211424.jsonl
│   │   ├── tof_S02_side_left_이불없음_r15_20261013_211459.jsonl
│   │   ├── tof_S02_side_left_이불없음_r1_20261013_210648.jsonl
│   │   ├── tof_S02_side_left_이불없음_r2_20261013_210723.jsonl
│   │   ├── tof_S02_side_left_이불없음_r3_20261013_210758.jsonl
│   │   ├── tof_S02_side_left_이불없음_r4_20261013_210833.jsonl
│   │   ├── tof_S02_side_left_이불없음_r5_20261013_210908.jsonl
│   │   ├── tof_S02_side_left_이불없음_r6_20261013_210943.jsonl
│   │   ├── tof_S02_side_left_이불없음_r7_20261013_211018.jsonl
│   │   ├── tof_S02_side_left_이불없음_r8_20261013_211054.jsonl
│   │   ├── tof_S02_side_left_이불없음_r9_20261013_211129.jsonl
│   │   ├── tof_S02_side_right_이불덮음_r1_20261013_224744.jsonl
│   │   ├── tof_S02_side_right_이불덮음_r2_20261013_224819.jsonl
│   │   ├── tof_S02_side_right_이불덮음_r3_20261013_224854.jsonl
│   │   ├── tof_S02_side_right_이불덮음_r4_20261013_224929.jsonl
│   │   ├── tof_S02_side_right_이불덮음_r5_20261013_225005.jsonl
│   │   ├── tof_S02_side_right_이불없음_r10_20261013_212236.jsonl
│   │   ├── tof_S02_side_right_이불없음_r11_20261013_212311.jsonl
│   │   ├── tof_S02_side_right_이불없음_r12_20261013_212347.jsonl
│   │   ├── tof_S02_side_right_이불없음_r13_20261013_212422.jsonl
│   │   ├── tof_S02_side_right_이불없음_r14_20261013_212457.jsonl
│   │   ├── tof_S02_side_right_이불없음_r15_20261013_212532.jsonl
│   │   ├── tof_S02_side_right_이불없음_r1_20261013_211720.jsonl
│   │   ├── tof_S02_side_right_이불없음_r2_20261013_211756.jsonl
│   │   ├── tof_S02_side_right_이불없음_r3_20261013_211831.jsonl
│   │   ├── tof_S02_side_right_이불없음_r4_20261013_211906.jsonl
│   │   ├── tof_S02_side_right_이불없음_r5_20261013_211941.jsonl
│   │   ├── tof_S02_side_right_이불없음_r6_20261013_212016.jsonl
│   │   ├── tof_S02_side_right_이불없음_r7_20261013_212051.jsonl
│   │   ├── tof_S02_side_right_이불없음_r8_20261013_212126.jsonl
│   │   ├── tof_S02_side_right_이불없음_r9_20261013_212201.jsonl
│   │   ├── tof_S02_sitting_이불없음_r10_20261013_215747.jsonl
│   │   ├── tof_S02_sitting_이불없음_r11_20261013_215822.jsonl
│   │   ├── tof_S02_sitting_이불없음_r12_20261013_215857.jsonl
│   │   ├── tof_S02_sitting_이불없음_r13_20261013_215932.jsonl
│   │   ├── tof_S02_sitting_이불없음_r14_20261013_220007.jsonl
│   │   ├── tof_S02_sitting_이불없음_r15_20261013_220042.jsonl
│   │   ├── tof_S02_sitting_이불없음_r1_20261013_215231.jsonl
│   │   ├── tof_S02_sitting_이불없음_r2_20261013_215306.jsonl
│   │   ├── tof_S02_sitting_이불없음_r3_20261013_215341.jsonl
│   │   ├── tof_S02_sitting_이불없음_r4_20261013_215416.jsonl
│   │   ├── tof_S02_sitting_이불없음_r5_20261013_215451.jsonl
│   │   ├── tof_S02_sitting_이불없음_r6_20261013_215526.jsonl
│   │   ├── tof_S02_sitting_이불없음_r7_20261013_215601.jsonl
│   │   ├── tof_S02_sitting_이불없음_r8_20261013_215636.jsonl
│   │   ├── tof_S02_sitting_이불없음_r9_20261013_215712.jsonl
│   │   ├── tof_S02_supine_이불덮음_r1_20261013_223801.jsonl
│   │   ├── tof_S02_supine_이불덮음_r2_20261013_223836.jsonl
│   │   ├── tof_S02_supine_이불덮음_r3_20261013_223911.jsonl
│   │   ├── tof_S02_supine_이불덮음_r4_20261013_223946.jsonl
│   │   ├── tof_S02_supine_이불덮음_r5_20261013_224021.jsonl
│   │   ├── tof_S02_supine_이불없음_r10_20261013_210058.jsonl
│   │   ├── tof_S02_supine_이불없음_r11_20261013_210134.jsonl
│   │   ├── tof_S02_supine_이불없음_r12_20261013_210209.jsonl
│   │   ├── tof_S02_supine_이불없음_r13_20261013_210244.jsonl
│   │   ├── tof_S02_supine_이불없음_r14_20261013_210319.jsonl
│   │   ├── tof_S02_supine_이불없음_r15_20261013_210354.jsonl
│   │   ├── tof_S02_supine_이불없음_r1_20261013_205543.jsonl
│   │   ├── tof_S02_supine_이불없음_r2_20261013_205618.jsonl
│   │   ├── tof_S02_supine_이불없음_r3_20261013_205653.jsonl
│   │   ├── tof_S02_supine_이불없음_r4_20261013_205728.jsonl
│   │   ├── tof_S02_supine_이불없음_r5_20261013_205803.jsonl
│   │   ├── tof_S02_supine_이불없음_r6_20261013_205838.jsonl
│   │   ├── tof_S02_supine_이불없음_r7_20261013_205913.jsonl
│   │   ├── tof_S02_supine_이불없음_r8_20261013_205948.jsonl
│   │   ├── tof_S02_supine_이불없음_r9_20261013_210023.jsonl
│   │   ├── tof_S02_supine_이불없음_무릎_r1_20261013_222420.jsonl
│   │   ├── tof_S02_supine_이불없음_무릎_r2_20261013_222455.jsonl
│   │   ├── tof_S02_supine_이불없음_무릎_r3_20261013_222530.jsonl
│   │   ├── tof_S02_supine_이불없음_무릎_폰_r1_20261013_222641.jsonl
│   │   ├── tof_S02_supine_이불없음_무릎_폰_r2_20261013_222716.jsonl
│   │   ├── tof_S02_supine_이불없음_폰_r1_20261013_221900.jsonl
│   │   ├── tof_S02_supine_이불없음_폰_r2_20261013_221935.jsonl
│   │   ├── tof_S02_supine_이불없음_폰_r3_20261013_222010.jsonl
│   │   ├── tof_S02_supine_이불없음_폰_r4_20261013_222046.jsonl
│   │   ├── tof_S02_supine_이불없음_폰_r5_20261013_222121.jsonl
│   │   ├── tof_S02_supine_이불없음_폰_r6_20261013_222156.jsonl
│   │   ├── tof_S02_supine_이불없음_폰_r7_20261013_222231.jsonl
│   │   ├── tof_없음_empty_이불없음_r10_20261008_171257.jsonl
│   │   ├── tof_없음_empty_이불없음_r11_20261008_171332.jsonl
│   │   ├── tof_없음_empty_이불없음_r12_20261008_171407.jsonl
│   │   ├── tof_없음_empty_이불없음_r13_20261008_171442.jsonl
│   │   ├── tof_없음_empty_이불없음_r14_20261008_171517.jsonl
│   │   ├── tof_없음_empty_이불없음_r15_20261008_171552.jsonl
│   │   ├── tof_없음_empty_이불없음_r16_20261012_133457.jsonl
│   │   ├── tof_없음_empty_이불없음_r17_20261012_133530.jsonl
│   │   ├── tof_없음_empty_이불없음_r1_20261008_170741.jsonl
│   │   ├── tof_없음_empty_이불없음_r2_20261008_170816.jsonl
│   │   ├── tof_없음_empty_이불없음_r3_20261008_170851.jsonl
│   │   ├── tof_없음_empty_이불없음_r4_20261008_170926.jsonl
│   │   ├── tof_없음_empty_이불없음_r5_20261008_171001.jsonl
│   │   ├── tof_없음_empty_이불없음_r6_20261008_171036.jsonl
│   │   ├── tof_없음_empty_이불없음_r7_20261008_171112.jsonl
│   │   ├── tof_없음_empty_이불없음_r8_20261008_171147.jsonl
│   │   └── tof_없음_empty_이불없음_r9_20261008_171222.jsonl
│   ├── dataset_v2_lowrate_excluded/
│   │   ├── README.md
│   │   ├── sessions.csv
│   │   ├── tof_S01_supine_이불없음_무릎_r15_20261008_190845.jsonl
│   │   ├── tof_S02_side_left_이불없음_r1_20261008_180834.jsonl
│   │   ├── tof_S02_side_left_이불없음_r1_20261008_180950.jsonl
│   │   ├── tof_S02_side_left_이불없음_r2_20261008_181026.jsonl
│   │   ├── tof_S02_side_left_이불없음_r3_20261008_181106.jsonl
│   │   ├── tof_S02_supine_이불없음_r1_20261008_180106.jsonl
│   │   ├── tof_S02_supine_이불없음_r1_20261008_180551.jsonl
│   │   ├── tof_S02_supine_이불없음_r2_20261008_180142.jsonl
│   │   ├── tof_S02_supine_이불없음_r2_20261008_180626.jsonl
│   │   ├── tof_S02_supine_이불없음_r3_20261008_180222.jsonl
│   │   └── tof_S02_supine_이불없음_r3_20261008_180706.jsonl
│   ├── dataset_v2_tilt20_archive/
│   │   ├── sessions.csv
│   │   ├── tof_S01_supine_이불없음_r1_20261008_165503.jsonl
│   │   ├── tof_S01_supine_이불없음_r2_20261008_165538.jsonl
│   │   ├── tof_S01_supine_이불없음_r3_20261008_165618.jsonl
│   │   ├── tof_없음_empty_이불없음_r10_20261008_164552.jsonl
│   │   ├── tof_없음_empty_이불없음_r11_20261008_164628.jsonl
│   │   ├── tof_없음_empty_이불없음_r12_20261008_164703.jsonl
│   │   ├── tof_없음_empty_이불없음_r13_20261008_164738.jsonl
│   │   ├── tof_없음_empty_이불없음_r14_20261008_164813.jsonl
│   │   ├── tof_없음_empty_이불없음_r15_20261008_164848.jsonl
│   │   ├── tof_없음_empty_이불없음_r1_20261008_163824.jsonl
│   │   ├── tof_없음_empty_이불없음_r2_20261008_164111.jsonl
│   │   ├── tof_없음_empty_이불없음_r3_20261008_164147.jsonl
│   │   ├── tof_없음_empty_이불없음_r4_20261008_164222.jsonl
│   │   ├── tof_없음_empty_이불없음_r5_20261008_164257.jsonl
│   │   ├── tof_없음_empty_이불없음_r6_20261008_164332.jsonl
│   │   ├── tof_없음_empty_이불없음_r7_20261008_164407.jsonl
│   │   ├── tof_없음_empty_이불없음_r8_20261008_164442.jsonl
│   │   └── tof_없음_empty_이불없음_r9_20261008_164517.jsonl
│   ├── dataset_v3/
│   │   ├── sessions.csv
│   │   ├── tof_S01_edge_sit_보이는쪽_가운데_r2_20261103_191818.jsonl
│   │   ├── tof_S01_edge_sit_보이는쪽_가운데_r2_20261104_191039_3ea00085.capture.json
│   │   ├── tof_S01_edge_sit_보이는쪽_가운데_r2_20261104_191039_3ea00085.jsonl
│   │   ├── tof_S01_edge_sit_보이는쪽_머리가까이_r1_20261103_191710.jsonl
│   │   ├── tof_S01_edge_sit_보이는쪽_머리가까이_r1_20261104_190924_8bde9187.capture.json
│   │   ├── tof_S01_edge_sit_보이는쪽_머리가까이_r1_20261104_190924_8bde9187.jsonl
│   │   ├── tof_S01_edge_sit_보이는쪽_발가까이_r3_20261103_191915.jsonl
│   │   ├── tof_S01_edge_sit_보이는쪽_발가까이_r3_20261104_191146_a341d8df.capture.json
│   │   ├── tof_S01_edge_sit_보이는쪽_발가까이_r3_20261104_191146_a341d8df.jsonl
│   │   ├── tof_S01_side_left_가운데_r1_20261103_185711.jsonl
│   │   ├── tof_S01_side_left_가운데_r1_20261104_185646_81241b9c.capture.json
│   │   ├── tof_S01_side_left_가운데_r1_20261104_185646_81241b9c.jsonl
│   │   ├── tof_S01_side_left_오른쪽가장자리_r3_20261103_190059.jsonl
│   │   ├── tof_S01_side_left_오른쪽가장자리_r3_20261104_185911_8f3268c2.capture.json
│   │   ├── tof_S01_side_left_오른쪽가장자리_r3_20261104_185911_8f3268c2.jsonl
│   │   ├── tof_S01_side_left_왼쪽가장자리_r2_20261103_185900.jsonl
│   │   ├── tof_S01_side_left_왼쪽가장자리_r2_20261104_185757_9c31b8d0.capture.json
│   │   ├── tof_S01_side_left_왼쪽가장자리_r2_20261104_185757_9c31b8d0.jsonl
│   │   ├── tof_S01_side_right_가운데_r1_20261103_190236.jsonl
│   │   ├── tof_S01_side_right_가운데_r1_20261104_190036_1da6505b.capture.json
│   │   ├── tof_S01_side_right_가운데_r1_20261104_190036_1da6505b.jsonl
│   │   ├── tof_S01_side_right_오른쪽가장자리_r3_20261103_190511.jsonl
│   │   ├── tof_S01_side_right_오른쪽가장자리_r3_20261104_190358_59e16a8b.capture.json
│   │   ├── tof_S01_side_right_오른쪽가장자리_r3_20261104_190358_59e16a8b.jsonl
│   │   ├── tof_S01_side_right_왼쪽가장자리_r2_20261103_190352.jsonl
│   │   ├── tof_S01_side_right_왼쪽가장자리_r2_20261104_190220_409226ac.capture.json
│   │   ├── tof_S01_side_right_왼쪽가장자리_r2_20261104_190220_409226ac.jsonl
│   │   ├── tof_S01_sitting_가운데_r1_20261103_190857.jsonl
│   │   ├── tof_S01_sitting_가운데_r1_20261104_190516_f38950dd.capture.json
│   │   ├── tof_S01_sitting_가운데_r1_20261104_190516_f38950dd.jsonl
│   │   ├── tof_S01_sitting_머리쪽_r2_20261103_191428.jsonl
│   │   ├── tof_S01_sitting_머리쪽_r2_20261104_190639_e3c507e2.capture.json
│   │   ├── tof_S01_sitting_머리쪽_r2_20261104_190639_e3c507e2.jsonl
│   │   ├── tof_S01_sitting_발쪽_r3_20261103_191538.jsonl
│   │   ├── tof_S01_sitting_발쪽_r3_20261104_190812_6d78f0d5.capture.json
│   │   ├── tof_S01_sitting_발쪽_r3_20261104_190812_6d78f0d5.jsonl
│   │   ├── tof_S01_supine_가운데_r1_20261103_184527.jsonl
│   │   ├── tof_S01_supine_가운데_r1_20261104_185048_c7241f79.capture.json
│   │   ├── tof_S01_supine_가운데_r1_20261104_185048_c7241f79.jsonl
│   │   ├── tof_S01_supine_오른쪽가장자리_r3_20261103_185513.jsonl
│   │   ├── tof_S01_supine_오른쪽가장자리_r3_20261104_185528_d1d49f7e.capture.json
│   │   ├── tof_S01_supine_오른쪽가장자리_r3_20261104_185528_d1d49f7e.jsonl
│   │   ├── tof_S01_supine_왼쪽가장자리_r2_20261103_184723.jsonl
│   │   ├── tof_S01_supine_왼쪽가장자리_r2_20261103_184921.jsonl
│   │   ├── tof_S01_supine_왼쪽가장자리_r2_20261104_185417_463e966f.capture.json
│   │   ├── tof_S01_supine_왼쪽가장자리_r2_20261104_185417_463e966f.jsonl
│   │   ├── tof_S02_edge_sit_보이는쪽_가운데_r2_20261104_125254_458c2817.capture.json
│   │   ├── tof_S02_edge_sit_보이는쪽_가운데_r2_20261104_125254_458c2817.jsonl
│   │   ├── tof_S02_edge_sit_보이는쪽_머리가까이_r1_20261104_125048_3cc0d925.capture.json
│   │   ├── tof_S02_edge_sit_보이는쪽_머리가까이_r1_20261104_125048_3cc0d925.jsonl
│   │   ├── tof_S02_edge_sit_보이는쪽_발가까이_r3_20261104_125453_51142e87.capture.json
│   │   ├── tof_S02_edge_sit_보이는쪽_발가까이_r3_20261104_125453_51142e87.jsonl
│   │   ├── tof_S02_side_left_가운데_r1_20261104_123351_be512be5.capture.json
│   │   ├── tof_S02_side_left_가운데_r1_20261104_123351_be512be5.jsonl
│   │   ├── tof_S02_side_left_오른쪽가장자리_r3_20261104_123641_c48b8fd3.capture.json
│   │   ├── tof_S02_side_left_오른쪽가장자리_r3_20261104_123641_c48b8fd3.jsonl
│   │   ├── tof_S02_side_left_왼쪽가장자리_r2_20261104_123513_9c3985da.capture.json
│   │   ├── tof_S02_side_left_왼쪽가장자리_r2_20261104_123513_9c3985da.jsonl
│   │   ├── tof_S02_side_right_가운데_r1_20261104_123818_753df92d.capture.json
│   │   ├── tof_S02_side_right_가운데_r1_20261104_123818_753df92d.jsonl
│   │   ├── tof_S02_side_right_오른쪽가장자리_r3_20261104_124149_b4f43e63.capture.json
│   │   ├── tof_S02_side_right_오른쪽가장자리_r3_20261104_124149_b4f43e63.jsonl
│   │   ├── tof_S02_side_right_왼쪽가장자리_r2_20261104_124014_266153db.capture.json
│   │   ├── tof_S02_side_right_왼쪽가장자리_r2_20261104_124014_266153db.jsonl
│   │   ├── tof_S02_sitting_가운데_r1_20261104_124322_89a82bcf.capture.json
│   │   ├── tof_S02_sitting_가운데_r1_20261104_124322_89a82bcf.jsonl
│   │   ├── tof_S02_sitting_머리쪽_r2_20261104_124522_5c41e1cb.capture.json
│   │   ├── tof_S02_sitting_머리쪽_r2_20261104_124522_5c41e1cb.jsonl
│   │   ├── tof_S02_sitting_발쪽_r3_20261104_124841_066df9d7.capture.json
│   │   ├── tof_S02_sitting_발쪽_r3_20261104_124841_066df9d7.jsonl
│   │   ├── tof_S02_supine_가운데_r1_20261104_094925.jsonl
│   │   ├── tof_S02_supine_가운데_r1_20261104_122840_f4f67433.capture.json
│   │   ├── tof_S02_supine_가운데_r1_20261104_122840_f4f67433.jsonl
│   │   ├── tof_S02_supine_오른쪽가장자리_r3_20261104_123223_4bddf136.capture.json
│   │   ├── tof_S02_supine_오른쪽가장자리_r3_20261104_123223_4bddf136.jsonl
│   │   ├── tof_S02_supine_왼쪽가장자리_r2_20261104_123010_ce55cd7b.capture.json
│   │   ├── tof_S02_supine_왼쪽가장자리_r2_20261104_123010_ce55cd7b.jsonl
│   │   ├── tof_S05_edge_sit_보이는쪽_가운데_r2_20261104_181417_e98295a1.capture.json
│   │   ├── tof_S05_edge_sit_보이는쪽_가운데_r2_20261104_181417_e98295a1.jsonl
│   │   ├── tof_S05_edge_sit_보이는쪽_머리가까이_r1_20261104_181157_0e030047.capture.json
│   │   ├── tof_S05_edge_sit_보이는쪽_머리가까이_r1_20261104_181157_0e030047.jsonl
│   │   ├── tof_S05_edge_sit_보이는쪽_발가까이_r3_20261104_181603_eef8d7b4.capture.json
│   │   ├── tof_S05_edge_sit_보이는쪽_발가까이_r3_20261104_181603_eef8d7b4.jsonl
│   │   ├── tof_S05_side_left_가운데_r1_20261104_175552_b8857636.capture.json
│   │   ├── tof_S05_side_left_가운데_r1_20261104_175552_b8857636.jsonl
│   │   ├── tof_S05_side_left_오른쪽가장자리_r3_20261104_175941_405ab597.capture.json
│   │   ├── tof_S05_side_left_오른쪽가장자리_r3_20261104_175941_405ab597.jsonl
│   │   ├── tof_S05_side_left_왼쪽가장자리_r2_20261104_175747_a1ee1dee.capture.json
│   │   ├── tof_S05_side_left_왼쪽가장자리_r2_20261104_175747_a1ee1dee.jsonl
│   │   ├── tof_S05_side_right_가운데_r1_20261104_180119_e2fbafc6.capture.json
│   │   ├── tof_S05_side_right_가운데_r1_20261104_180119_e2fbafc6.jsonl
│   │   ├── tof_S05_side_right_오른쪽가장자리_r3_20261104_180429_a1b91f45.capture.json
│   │   ├── tof_S05_side_right_오른쪽가장자리_r3_20261104_180429_a1b91f45.jsonl
│   │   ├── tof_S05_side_right_왼쪽가장자리_r2_20261104_180243_2a4dbce4.capture.json
│   │   ├── tof_S05_side_right_왼쪽가장자리_r2_20261104_180243_2a4dbce4.jsonl
│   │   ├── tof_S05_sitting_가운데_r1_20261104_180609_9513069e.capture.json
│   │   ├── tof_S05_sitting_가운데_r1_20261104_180609_9513069e.jsonl
│   │   ├── tof_S05_sitting_머리쪽_r2_20261104_180758_ac2e0507.capture.json
│   │   ├── tof_S05_sitting_머리쪽_r2_20261104_180758_ac2e0507.jsonl
│   │   ├── tof_S05_sitting_발쪽_r3_20261104_180925_2828f0c3.capture.json
│   │   ├── tof_S05_sitting_발쪽_r3_20261104_180925_2828f0c3.jsonl
│   │   ├── tof_S05_supine_가운데_r1_20261104_175100_5000edc6.capture.json
│   │   ├── tof_S05_supine_가운데_r1_20261104_175100_5000edc6.jsonl
│   │   ├── tof_S05_supine_오른쪽가장자리_r3_20261104_175431_c6c7109b.capture.json
│   │   ├── tof_S05_supine_오른쪽가장자리_r3_20261104_175431_c6c7109b.jsonl
│   │   ├── tof_S05_supine_왼쪽가장자리_r2_20261104_175249_f75b6d4f.capture.json
│   │   ├── tof_S05_supine_왼쪽가장자리_r2_20261104_175249_f75b6d4f.jsonl
│   │   ├── tof_없음_empty_빈침대_r1_20261103_182144.jsonl
│   │   ├── tof_없음_empty_빈침대_r1_20261104_094559.jsonl
│   │   ├── tof_없음_empty_빈침대_r1_20261104_095332_ccca99cc.capture.json
│   │   ├── tof_없음_empty_빈침대_r1_20261104_095332_ccca99cc.jsonl
│   │   ├── tof_없음_empty_빈침대_r1_20261104_095652_c5808ea1.capture.json
│   │   ├── tof_없음_empty_빈침대_r1_20261104_095652_c5808ea1.jsonl
│   │   ├── tof_없음_empty_빈침대_r1_20261104_174827_41116a77.capture.json
│   │   ├── tof_없음_empty_빈침대_r1_20261104_174827_41116a77.jsonl
│   │   ├── tof_없음_empty_빈침대_r1_20261104_184914_b7c39cbb.capture.json
│   │   ├── tof_없음_empty_빈침대_r1_20261104_184914_b7c39cbb.jsonl
│   │   ├── tof_없음_empty_빈침대_r2_20261103_182214.jsonl
│   │   ├── tof_없음_empty_빈침대_r2_20261104_094629.jsonl
│   │   ├── tof_없음_empty_빈침대_r2_20261104_095358_50c86905.capture.json
│   │   ├── tof_없음_empty_빈침대_r2_20261104_095358_50c86905.jsonl
│   │   ├── tof_없음_empty_빈침대_r2_20261104_095734_70f342e6.capture.json
│   │   ├── tof_없음_empty_빈침대_r2_20261104_095734_70f342e6.jsonl
│   │   ├── tof_없음_empty_빈침대_r2_20261104_174904_990c720c.capture.json
│   │   ├── tof_없음_empty_빈침대_r2_20261104_174904_990c720c.jsonl
│   │   ├── tof_없음_empty_빈침대_r2_20261104_184931_243cb912.capture.json
│   │   ├── tof_없음_empty_빈침대_r2_20261104_184931_243cb912.jsonl
│   │   ├── tof_없음_empty_빈침대_r3_20261103_182244.jsonl
│   │   ├── tof_없음_empty_빈침대_r3_20261104_094659.jsonl
│   │   ├── tof_없음_empty_빈침대_r3_20261104_095426_982234c6.capture.json
│   │   ├── tof_없음_empty_빈침대_r3_20261104_095426_982234c6.jsonl
│   │   ├── tof_없음_empty_빈침대_r3_20261104_095815_bf9d131b.capture.json
│   │   ├── tof_없음_empty_빈침대_r3_20261104_095815_bf9d131b.jsonl
│   │   ├── tof_없음_empty_빈침대_r3_20261104_174921_9d1de89e.capture.json
│   │   ├── tof_없음_empty_빈침대_r3_20261104_174921_9d1de89e.jsonl
│   │   ├── tof_없음_empty_빈침대_r3_20261104_184948_f26bb3a1.capture.json
│   │   └── tof_없음_empty_빈침대_r3_20261104_184948_f26bb3a1.jsonl
│   ├── dataset_v3_synth/
│   │   ├── sessions.csv
│   │   ├── tof_S03syn_edge_sit_from_S01_edge_sit_보이는쪽_가운데_r2_20261103_191818.jsonl
│   │   ├── tof_S03syn_edge_sit_from_S01_edge_sit_보이는쪽_머리가까이_r1_20261103_191710.jsonl
│   │   ├── tof_S03syn_edge_sit_from_S01_edge_sit_보이는쪽_발가까이_r3_20261103_191915.jsonl
│   │   ├── tof_S03syn_side_left_from_S01_side_left_가운데_r1_20261103_185711.jsonl
│   │   ├── tof_S03syn_side_left_from_S01_side_left_오른쪽가장자리_r3_20261103_190059.jsonl
│   │   ├── tof_S03syn_side_left_from_S01_side_left_왼쪽가장자리_r2_20261103_185900.jsonl
│   │   ├── tof_S03syn_side_right_from_S01_side_right_가운데_r1_20261103_190236.jsonl
│   │   ├── tof_S03syn_side_right_from_S01_side_right_오른쪽가장자리_r3_20261103_190511.jsonl
│   │   ├── tof_S03syn_side_right_from_S01_side_right_왼쪽가장자리_r2_20261103_190352.jsonl
│   │   ├── tof_S03syn_sitting_from_S01_sitting_가운데_r1_20261103_190857.jsonl
│   │   ├── tof_S03syn_sitting_from_S01_sitting_머리쪽_r2_20261103_191428.jsonl
│   │   ├── tof_S03syn_sitting_from_S01_sitting_발쪽_r3_20261103_191538.jsonl
│   │   ├── tof_S03syn_supine_from_S01_supine_가운데_r1_20261103_184527.jsonl
│   │   ├── tof_S03syn_supine_from_S01_supine_오른쪽가장자리_r3_20261103_185513.jsonl
│   │   ├── tof_S03syn_supine_from_S01_supine_왼쪽가장자리_r2_20261103_184723.jsonl
│   │   ├── tof_S03syn_supine_from_S01_supine_왼쪽가장자리_r2_20261103_184921.jsonl
│   │   ├── tof_S04syn_edge_sit_from_S02_edge_sit_보이는쪽_가운데_r2_20261104_125254_458c2817.jsonl
│   │   ├── tof_S04syn_edge_sit_from_S02_edge_sit_보이는쪽_머리가까이_r1_20261104_125048_3cc0d925.jsonl
│   │   ├── tof_S04syn_edge_sit_from_S02_edge_sit_보이는쪽_발가까이_r3_20261104_125453_51142e87.jsonl
│   │   ├── tof_S04syn_side_left_from_S02_side_left_가운데_r1_20261104_123351_be512be5.jsonl
│   │   ├── tof_S04syn_side_left_from_S02_side_left_오른쪽가장자리_r3_20261104_123641_c48b8fd3.jsonl
│   │   ├── tof_S04syn_side_left_from_S02_side_left_왼쪽가장자리_r2_20261104_123513_9c3985da.jsonl
│   │   ├── tof_S04syn_side_right_from_S02_side_right_가운데_r1_20261104_123818_753df92d.jsonl
│   │   ├── tof_S04syn_side_right_from_S02_side_right_오른쪽가장자리_r3_20261104_124149_b4f43e63.jsonl
│   │   ├── tof_S04syn_side_right_from_S02_side_right_왼쪽가장자리_r2_20261104_124014_266153db.jsonl
│   │   ├── tof_S04syn_sitting_from_S02_sitting_가운데_r1_20261104_124322_89a82bcf.jsonl
│   │   ├── tof_S04syn_sitting_from_S02_sitting_머리쪽_r2_20261104_124522_5c41e1cb.jsonl
│   │   ├── tof_S04syn_sitting_from_S02_sitting_발쪽_r3_20261104_124841_066df9d7.jsonl
│   │   ├── tof_S04syn_supine_from_S02_supine_가운데_r1_20261104_122840_f4f67433.jsonl
│   │   ├── tof_S04syn_supine_from_S02_supine_오른쪽가장자리_r3_20261104_123223_4bddf136.jsonl
│   │   └── tof_S04syn_supine_from_S02_supine_왼쪽가장자리_r2_20261104_123010_ce55cd7b.jsonl
│   ├── dataset_v4/
│   │   ├── LAPTOP_TRAIN_PROMPT.md
│   │   ├── sessions.csv
│   │   ├── tof_S01_edge_sit_보이는쪽_가운데_r2_20261105_185241.jsonl
│   │   ├── tof_S01_edge_sit_보이는쪽_머리가까이_r1_20261105_185144.jsonl
│   │   ├── tof_S01_edge_sit_보이는쪽_발가까이_r3_20261105_185328.jsonl
│   │   ├── tof_S01_empty_빈침대_r1_20261105_182836.jsonl
│   │   ├── tof_S01_empty_빈침대_r2_20261105_182908.jsonl
│   │   ├── tof_S01_empty_빈침대_r3_20261105_182940.jsonl
│   │   ├── tof_S01_side_left_가운데_r1_20261105_184249.jsonl
│   │   ├── tof_S01_side_left_오른쪽가장자리_r3_20261105_184436.jsonl
│   │   ├── tof_S01_side_left_왼쪽가장자리_r2_20261105_184345.jsonl
│   │   ├── tof_S01_side_right_가운데_r1_20261105_184528.jsonl
│   │   ├── tof_S01_side_right_오른쪽가장자리_r3_20261105_184717.jsonl
│   │   ├── tof_S01_side_right_왼쪽가장자리_r2_20261105_184623.jsonl
│   │   ├── tof_S01_sitting_가운데_r1_20261105_184813.jsonl
│   │   ├── tof_S01_sitting_머리쪽_r2_20261105_184901.jsonl
│   │   ├── tof_S01_sitting_발쪽_r3_20261105_185026.jsonl
│   │   ├── tof_S01_supine_가운데_r1_20261105_183922.jsonl
│   │   ├── tof_S01_supine_오른쪽가장자리_r3_20261105_184120.jsonl
│   │   ├── tof_S01_supine_왼쪽가장자리_r2_20261105_184020.jsonl
│   │   ├── tof_S02_edge_sit_보이는쪽_가운데_r2_20261105_190733.jsonl
│   │   ├── tof_S02_edge_sit_보이는쪽_머리가까이_r1_20261105_190639.jsonl
│   │   ├── tof_S02_edge_sit_보이는쪽_발가까이_r3_20261105_190821.jsonl
│   │   ├── tof_S02_empty_빈침대_r1_20261105_183012.jsonl
│   │   ├── tof_S02_empty_빈침대_r2_20261105_183044.jsonl
│   │   ├── tof_S02_empty_빈침대_r3_20261105_183116.jsonl
│   │   ├── tof_S02_side_left_가운데_r1_20261105_185817.jsonl
│   │   ├── tof_S02_side_left_오른쪽가장자리_r3_20261105_190001.jsonl
│   │   ├── tof_S02_side_left_왼쪽가장자리_r2_20261105_185911.jsonl
│   │   ├── tof_S02_side_right_가운데_r1_20261105_190057.jsonl
│   │   ├── tof_S02_side_right_오른쪽가장자리_r3_20261105_190249.jsonl
│   │   ├── tof_S02_side_right_왼쪽가장자리_r2_20261105_190157.jsonl
│   │   ├── tof_S02_sitting_가운데_r1_20261105_190346.jsonl
│   │   ├── tof_S02_sitting_머리쪽_r2_20261105_190441.jsonl
│   │   ├── tof_S02_sitting_발쪽_r3_20261105_190547.jsonl
│   │   ├── tof_S02_supine_가운데_r1_20261105_185550.jsonl
│   │   ├── tof_S02_supine_오른쪽가장자리_r3_20261105_185732.jsonl
│   │   ├── tof_S02_supine_왼쪽가장자리_r2_20261105_185649.jsonl
│   │   ├── tof_S03_edge_sit_보이는쪽_가운데_r2_20261105_192628.jsonl
│   │   ├── tof_S03_edge_sit_보이는쪽_머리가까이_r1_20261105_192532.jsonl
│   │   ├── tof_S03_edge_sit_보이는쪽_발가까이_r3_20261105_192726.jsonl
│   │   ├── tof_S03_empty_빈침대_r1_20261105_183148.jsonl
│   │   ├── tof_S03_empty_빈침대_r2_20261105_183221.jsonl
│   │   ├── tof_S03_empty_빈침대_r3_20261105_183253.jsonl
│   │   ├── tof_S03_side_left_가운데_r1_20261105_191629.jsonl
│   │   ├── tof_S03_side_left_오른쪽가장자리_r3_20261105_191818.jsonl
│   │   ├── tof_S03_side_left_왼쪽가장자리_r2_20261105_191728.jsonl
│   │   ├── tof_S03_side_right_가운데_r1_20261105_191918.jsonl
│   │   ├── tof_S03_side_right_오른쪽가장자리_r3_20261105_192134.jsonl
│   │   ├── tof_S03_side_right_왼쪽가장자리_r2_20261105_192026.jsonl
│   │   ├── tof_S03_sitting_가운데_r1_20261105_192237.jsonl
│   │   ├── tof_S03_sitting_머리쪽_r2_20261105_192328.jsonl
│   │   ├── tof_S03_sitting_발쪽_r3_20261105_192418.jsonl
│   │   ├── tof_S03_supine_가운데_r1_20261105_191348.jsonl
│   │   ├── tof_S03_supine_오른쪽가장자리_r3_20261105_191532.jsonl
│   │   ├── tof_S03_supine_왼쪽가장자리_r2_20261105_191435.jsonl
│   │   ├── tof_S04_edge_sit_보이는쪽_가운데_r2_20261105_194303.jsonl
│   │   ├── tof_S04_edge_sit_보이는쪽_머리가까이_r1_20261105_194206.jsonl
│   │   ├── tof_S04_edge_sit_보이는쪽_발가까이_r3_20261105_194358.jsonl
│   │   ├── tof_S04_empty_빈침대_r1_20261105_183325.jsonl
│   │   ├── tof_S04_empty_빈침대_r2_20261105_183357.jsonl
│   │   ├── tof_S04_empty_빈침대_r3_20261105_183429.jsonl
│   │   ├── tof_S04_side_left_가운데_r1_20261105_193334.jsonl
│   │   ├── tof_S04_side_left_오른쪽가장자리_r3_20261105_193524.jsonl
│   │   ├── tof_S04_side_left_왼쪽가장자리_r2_20261105_193425.jsonl
│   │   ├── tof_S04_side_right_가운데_r1_20261105_193626.jsonl
│   │   ├── tof_S04_side_right_오른쪽가장자리_r3_20261105_193821.jsonl
│   │   ├── tof_S04_side_right_왼쪽가장자리_r2_20261105_193722.jsonl
│   │   ├── tof_S04_sitting_가운데_r1_20261105_193917.jsonl
│   │   ├── tof_S04_sitting_머리쪽_r2_20261105_194016.jsonl
│   │   ├── tof_S04_sitting_발쪽_r3_20261105_194108.jsonl
│   │   ├── tof_S04_supine_가운데_r1_20261105_193032.jsonl
│   │   ├── tof_S04_supine_오른쪽가장자리_r3_20261105_193235.jsonl
│   │   ├── tof_S04_supine_왼쪽가장자리_r2_20261105_193131.jsonl
│   │   ├── tof_S05_edge_sit_보이는쪽_가운데_r2_20261105_195705.jsonl
│   │   ├── tof_S05_edge_sit_보이는쪽_머리가까이_r1_20261105_195621.jsonl
│   │   ├── tof_S05_edge_sit_보이는쪽_발가까이_r3_20261105_195803.jsonl
│   │   ├── tof_S05_empty_빈침대_r1_20261105_183501.jsonl
│   │   ├── tof_S05_empty_빈침대_r2_20261105_183533.jsonl
│   │   ├── tof_S05_empty_빈침대_r3_20261105_183605.jsonl
│   │   ├── tof_S05_side_left_가운데_r1_20261105_194813.jsonl
│   │   ├── tof_S05_side_left_오른쪽가장자리_r3_20261105_195017.jsonl
│   │   ├── tof_S05_side_left_왼쪽가장자리_r2_20261105_194918.jsonl
│   │   ├── tof_S05_side_right_가운데_r1_20261105_195106.jsonl
│   │   ├── tof_S05_side_right_오른쪽가장자리_r3_20261105_195247.jsonl
│   │   ├── tof_S05_side_right_왼쪽가장자리_r2_20261105_195152.jsonl
│   │   ├── tof_S05_sitting_가운데_r1_20261105_195335.jsonl
│   │   ├── tof_S05_sitting_머리쪽_r2_20261105_195442.jsonl
│   │   ├── tof_S05_sitting_발쪽_r3_20261105_195533.jsonl
│   │   ├── tof_S05_supine_가운데_r1_20261105_194520.jsonl
│   │   ├── tof_S05_supine_오른쪽가장자리_r3_20261105_194711.jsonl
│   │   ├── tof_S05_supine_왼쪽가장자리_r2_20261105_194619.jsonl
│   │   ├── tof_S06_edge_sit_보이는쪽_가운데_r2_20261105_202051.jsonl
│   │   ├── tof_S06_edge_sit_보이는쪽_머리가까이_r1_20261105_201953.jsonl
│   │   ├── tof_S06_edge_sit_보이는쪽_발가까이_r3_20261105_202135.jsonl
│   │   ├── tof_S06_empty_빈침대_r1_20261105_202224.jsonl
│   │   ├── tof_S06_empty_빈침대_r2_20261105_202256.jsonl
│   │   ├── tof_S06_empty_빈침대_r3_20261105_202328.jsonl
│   │   ├── tof_S06_side_left_가운데_r1_20261105_200333.jsonl
│   │   ├── tof_S06_side_left_오른쪽가장자리_r3_20261105_200540.jsonl
│   │   ├── tof_S06_side_left_왼쪽가장자리_r2_20261105_200437.jsonl
│   │   ├── tof_S06_side_right_가운데_r1_20261105_200636.jsonl
│   │   ├── tof_S06_side_right_오른쪽가장자리_r3_20261105_200831.jsonl
│   │   ├── tof_S06_side_right_왼쪽가장자리_r2_20261105_200727.jsonl
│   │   ├── tof_S06_sitting_가운데_r1_20261105_201134.jsonl
│   │   ├── tof_S06_sitting_머리쪽_r2_20261105_201501.jsonl
│   │   ├── tof_S06_sitting_발쪽_r3_20261105_201701.jsonl
│   │   ├── tof_S06_supine_가운데_r1_20261105_200022.jsonl
│   │   ├── tof_S06_supine_오른쪽가장자리_r3_20261105_200230.jsonl
│   │   ├── tof_S06_supine_왼쪽가장자리_r2_20261105_200115.jsonl
│   │   ├── tof_없음_empty_빈침대_r1_20261105_182500.jsonl
│   │   ├── tof_없음_empty_빈침대_r2_20261105_182533.jsonl
│   │   └── tof_없음_empty_빈침대_r3_20261105_182606.jsonl
│   ├── ml/
│   │   ├── archive/
│   │   │   ├── lstm_ae_oldmount_20260707.pt
│   │   │   ├── posture_model_8x8_oldmount_20260707.joblib
│   │   │   ├── posture_model_v2_bedB_20261031.joblib
│   │   │   ├── posture_model_v2_S01only_20261008.joblib
│   │   │   ├── posture_model_v2_S01only_20261008_report.json
│   │   │   └── README.md
│   │   ├── incoming/
│   │   │   ├── 2026-11-04/
│   │   │   │   ├── bedc-s01retake-s02-s05-ratios/
│   │   │   │   ├── bedc-s01retake-s02-s05-ratios-ZNXqhr/
│   │   │   │   │   ├── cnn-xgboost-synth50/
│   │   │   │   │   │   ├── cnn_cpu.ts
│   │   │   │   │   │   ├── cnn_xgboost.json
│   │   │   │   │   │   ├── metrics.json
│   │   │   │   │   │   ├── paper_record.json
│   │   │   │   │   │   ├── preprocessing.json
│   │   │   │   │   │   ├── run_manifest.json
│   │   │   │   │   │   └── synthetic_training_sessions.json
│   │   │   │   │   ├── rf-real/
│   │   │   │   │   │   ├── metrics.json
│   │   │   │   │   │   ├── paper_record.json
│   │   │   │   │   │   ├── preprocessing.json
│   │   │   │   │   │   ├── raw_rf.joblib
│   │   │   │   │   │   └── run_manifest.json
│   │   │   │   │   ├── comparison.md
│   │   │   │   │   ├── README.md
│   │   │   │   │   ├── real_training_sessions.csv
│   │   │   │   │   ├── SHA256SUMS
│   │   │   │   │   └── training_baseline.json
│   │   │   │   ├── manual-edge-sit-4205cde2/
│   │   │   │   │   ├── metrics.json
│   │   │   │   │   ├── preprocessing.json
│   │   │   │   │   └── raw_rf.joblib
│   │   │   │   ├── cnn_cpu.pt
│   │   │   │   ├── cnn_lightgbm.joblib
│   │   │   │   ├── cnn_rf.joblib
│   │   │   │   ├── cnn_xgboost.joblib
│   │   │   │   ├── cnn_xgboost.json
│   │   │   │   ├── export_validation.json
│   │   │   │   ├── infer.py
│   │   │   │   ├── metrics.json
│   │   │   │   ├── paper_record.json
│   │   │   │   ├── paper_record.md
│   │   │   │   ├── preprocessing.json
│   │   │   │   ├── raw_rf.joblib
│   │   │   │   ├── README.md
│   │   │   │   ├── run_manifest.json
│   │   │   │   ├── session_metadata.json
│   │   │   │   ├── SHA256SUMS
│   │   │   │   ├── split_audit.json
│   │   │   │   ├── training_baseline.json
│   │   │   │   └── training_sessions.csv
│   │   │   └── 2026-11-05/
│   │   │       └── v4-150cm-6p-5seeds-6lE7WB/
│   │   │           ├── benchmark_tof_bundle.py
│   │   │           ├── metrics.json
│   │   │           ├── pi_cpu_benchmark.json
│   │   │           ├── preprocessing.json
│   │   │           ├── raw_rf.joblib
│   │   │           ├── README.md
│   │   │           ├── real_training_sessions.csv
│   │   │           ├── run_manifest.json
│   │   │           ├── SHA256SUMS
│   │   │           ├── training_baseline.json
│   │   │           ├── verification_input.npy
│   │   │           └── verification_probabilities.json
│   │   ├── posture_cnn_bedC_20261104/
│   │   │   ├── cnn_cpu.pt
│   │   │   ├── cnn_rf.joblib
│   │   │   ├── metrics.json
│   │   │   ├── preprocessing.json
│   │   │   ├── README.md
│   │   │   ├── SHA256SUMS
│   │   │   ├── training_baseline.json
│   │   │   └── training_sessions.csv
│   │   ├── posture_rf_bedC_20261104_3p/
│   │   │   ├── comparison.md
│   │   │   ├── metrics.json
│   │   │   ├── paper_record.json
│   │   │   ├── preprocessing.json
│   │   │   ├── raw_rf.joblib
│   │   │   ├── README.md
│   │   │   ├── README_laptop.md
│   │   │   ├── real_training_sessions.csv
│   │   │   ├── run_manifest.json
│   │   │   ├── SHA256SUMS
│   │   │   └── training_baseline.json
│   │   ├── posture_rf_bedC_20261104_edge/
│   │   │   ├── metrics.json
│   │   │   ├── preprocessing.json
│   │   │   ├── raw_rf.joblib
│   │   │   ├── README.md
│   │   │   ├── SHA256SUMS
│   │   │   └── training_baseline.json
│   │   ├── roi_experiment_20261106/
│   │   │   ├── res/
│   │   │   │   └── v4/
│   │   │   │       ├── act2_0.json
│   │   │   │       ├── act2_0.npz
│   │   │   │       ├── act2_1.json
│   │   │   │       ├── act2_1.npz
│   │   │   │       ├── act2_42.json
│   │   │   │       ├── act2_42.npz
│   │   │   │       ├── act5_0.json
│   │   │   │       ├── act5_0.npz
│   │   │   │       ├── act5_1.json
│   │   │   │       ├── act5_1.npz
│   │   │   │       ├── act5_42.json
│   │   │   │       ├── act5_42.npz
│   │   │   │       ├── base_off_0.json
│   │   │   │       ├── base_off_0.npz
│   │   │   │       ├── base_off_1.json
│   │   │   │       ├── base_off_1.npz
│   │   │   │       ├── base_off_42.json
│   │   │   │       ├── base_off_42.npz
│   │   │   │       ├── base_valid_0.json
│   │   │   │       ├── base_valid_0.npz
│   │   │   │       ├── base_valid_1.json
│   │   │   │       ├── base_valid_1.npz
│   │   │   │       ├── base_valid_42.json
│   │   │   │       ├── base_valid_42.npz
│   │   │   │       ├── cols_0.json
│   │   │   │       ├── cols_0.npz
│   │   │   │       ├── cols_1.json
│   │   │   │       ├── cols_1.npz
│   │   │   │       ├── cols_42.json
│   │   │   │       ├── cols_42.npz
│   │   │   │       ├── none_0.json
│   │   │   │       ├── none_0.npz
│   │   │   │       ├── none_1.json
│   │   │   │       ├── none_1.npz
│   │   │   │       ├── none_2.json
│   │   │   │       ├── none_2.npz
│   │   │   │       ├── none_42.json
│   │   │   │       ├── none_42.npz
│   │   │   │       ├── roi_py_0.json
│   │   │   │       ├── roi_py_0.npz
│   │   │   │       ├── roi_py_1.json
│   │   │   │       ├── roi_py_1.npz
│   │   │   │       ├── roi_py_42.json
│   │   │   │       └── roi_py_42.npz
│   │   │   ├── tl/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── data.py
│   │   │   │   ├── session_data.py
│   │   │   │   └── session_models.py
│   │   │   ├── aggregate.py
│   │   │   ├── LAPTOP_ROI_PROMPT.md
│   │   │   ├── queue.sh
│   │   │   ├── run_roi.py
│   │   │   ├── summary.json
│   │   │   ├── tables.py
│   │   │   └── tables_output.md
│   │   ├── analyze_4x4_backup.py
│   │   ├── augment_tof.py
│   │   ├── backup_4x4_analysis.json
│   │   ├── bed_geometry.py
│   │   ├── bed_scan_preview.html
│   │   ├── bed_scan_result.json
│   │   ├── calibrate_roi.py
│   │   ├── compare_rf_tabpfn.py
│   │   ├── feature_logger.py
│   │   ├── lstm_ae.pt
│   │   ├── lstm_ae_v2_report.json
│   │   ├── lstm_anomaly.py
│   │   ├── lstm_infer.py
│   │   ├── posture_model.joblib
│   │   ├── posture_model_v2_report.json
│   │   ├── posture_stats.py
│   │   ├── predict_posture.py
│   │   ├── README.md
│   │   ├── retrain_with_tabpfn.py
│   │   ├── roi.py
│   │   ├── synth_subjects.py
│   │   ├── tof_anomaly.py
│   │   ├── train_lstm_ae_v2.py
│   │   ├── train_posture.py
│   │   ├── train_posture_v2.py
│   │   └── zone_probe.py
│   ├── src/
│   │   ├── main.ino
│   │   ├── platformio.ini
│   │   ├── secrets.example.h
│   │   └── secrets.h
│   ├── capture_dataset.py
│   ├── preprocess_dataset.py
│   ├── record_posture.py
│   ├── tof.md
│   ├── tof_3d_posture.html
│   ├── TROUBLESHOOTING.md
│   └── viewer_proxy.py
├── tools/
│   ├── ai_guide_parts.py
│   ├── archive_raw.py
│   ├── build_ai_guide.py
│   ├── build_code_guide.py
│   ├── guide_lib.py
│   ├── laptop_stream_client.py
│   ├── seed_demo_emr.py
│   ├── set_bed_center.py
│   └── update_project_structure.py
├── .env
├── .gitignore
├── 0704할일.md
├── 0707_TOF_진행상황_인수인계.md
├── AGENTS.md
├── ai_progress_presentation.html
├── CLAUDE.md
├── cloud_dashboard.py
├── README.md
├── requirements.txt
└── SYSTEM_ARCHITECTURE.md
```
<!-- END GENERATED TREE -->
