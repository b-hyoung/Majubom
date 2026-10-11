# 마주봄 전체 구조

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
| TOF/ml/ | 자세 Random Forest 학습·추론(운영 v2: train_posture_v2.py, 이전 모델은 archive/), ROI/침대 좌표, LSTM-AE 참고 이상탐지, 특징 로깅, 후보 모델 비교 |
| TOF/dataset*/ | 라벨별 ToF 거리 CSV/JSONL, 기존 4×4 및 빈 침대 데이터 보관 |
| TOF/의 Python/HTML | 수집(capture_dataset; 2026-10 새 설치용 세션 녹화 record_posture → dataset_v2/), 전처리(preprocess_dataset), viewer_proxy 및 자세/3D 실험 화면 |
| mmWave/ | TI IWR6843AOP cfg, USB TLV 리더 send_mmw, 설정 quick_cfg, record, 자세 probe/통합 리더 실험 |
| mmWave/exit_seq/ | 이탈 시퀀스 기록·공동 로깅·증강·시계열/속도 분석 및 실험 인수인계 |
| server/ | ToF(:5001)/mmWave(:5002) HTTP 서버, SQLite, 센서 판정, 통합 위험, 대시보드 API, AI 리포트/검색 |
| server/reports/ | 저장된 보고서 예시/산출물 |
| server/lancedb_store/ | 논문 검색용 벡터 저장소, 재생성 내부 파일 |
| site/ | 실제 웹 대시보드 index.html, 디자인 참고 dashboard_design.html, assets와 JS/브라우저 테스트 |
| docs/ | AI 시작 안내·전체 구조·현장 점검·운영 개념·관련 연구·원격 제어·연구 검토 질문(질문.md)·나중에 할 일(나중에_할일.md) 및 역사 specs |
| research/alternatives/ | 후보 알고리즘 실험, 평가 데이터와 산출물; 운영 코드와 구분 |
| test_8x8/ | 8×8 검증용 firmware와 :5011 실험 서버. 운영 TOF/src와 별개 |
| tools/ | 구조 문서 자동 갱신/검사 도구 |
| cloud_dashboard.py | Pi ToF 대시보드를 별도 포트로 프록시하는 보조 Flask 앱(설정 확인 후 사용) |
| README.md / SYSTEM_ARCHITECTURE.md | 프로젝트 소개 / 센서·판정·DB·UI·AI 데이터 흐름 설명 |
| 0704*, 0707*, ai_progress_presentation.html | 과거 작업/인수인계 및 발표 자료; 현재 운영 설정의 기준으로 사용하지 않음 |

## 서버의 주요 연결

- 로우데이터 분기: tof_server.py와 send_mmw.py → server/live_diagnostics.py → :5012 mmWave/diagnostic_server.py → site/sensor-diagnostics.html/js. 기존 운영 경로와 병렬로 최신값만 메모리에 유지한다.
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
├── docs/
│   ├── captures/
│   │   └── 2026-10-11/
│   │       ├── 00_system_architecture.png
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
│   ├── AI_START_HERE.md
│   ├── codex_remote-control.md
│   ├── EMR_DATA_MODEL.md
│   ├── operating_concept_day_night.md
│   ├── PROJECT_STRUCTURE.md
│   ├── related_work_gait_fall.md
│   ├── SENSOR_CHECK_2026-10-06.md
│   ├── SENSOR_DIAGNOSTICS.md
│   ├── SYSTEM_ARCHITECTURE.html
│   ├── 나중에_할일.md
│   └── 질문.md
├── mmWave/
│   ├── exit_seq/
│   │   ├── .gitignore
│   │   ├── analyze_aug.py
│   │   ├── analyze_timeseries.py
│   │   ├── analyze_v2.py
│   │   ├── analyze_velocity.py
│   │   ├── augment_seq.py
│   │   ├── AUGMENTATION_NOTES.md
│   │   ├── co_log.py
│   │   ├── HANDOFF.md
│   │   ├── mPCT-LSTM_검증_전략.md
│   │   ├── record_seq.py
│   │   ├── seq_dataset.jsonl.gz
│   │   └── TODO_2026-07-05.md
│   ├── tests/
│   │   └── test_diagnostics.py
│   ├── AOP_bed_2m7_d15.cfg
│   ├── AOP_diag_2m5_d50.cfg
│   ├── AOP_diag_wideopen.cfg
│   ├── AOP_v_C_legs_safe.cfg
│   ├── AOP_wall_chest.cfg
│   ├── bed_roi_tilted.json
│   ├── diagnostic_decode.py
│   ├── diagnostic_server.py
│   ├── posture_dataset.jsonl
│   ├── posture_dataset_tilted.jsonl
│   ├── POSTURE_NOTES.md
│   ├── posture_probe.py
│   ├── quick_cfg.py
│   ├── record.py
│   ├── RISK_NOTES.md
│   ├── RUN.md
│   ├── send_mmw.py
│   └── unified_reader.py
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
│   ├── lancedb_store/
│   │   ├── analysis_chunks.lance/
│   │   │   └── [LanceDB 자동 생성 내부 파일 생략]
│   │   ├── emr_chunks.lance/
│   │   │   └── [LanceDB 자동 생성 내부 파일 생략]
│   │   └── paper_chunks.lance/
│   │       └── [LanceDB 자동 생성 내부 파일 생략]
│   ├── reports/
│   │   ├── 2026-08-08.json
│   │   ├── 2026-08-15.json
│   │   ├── 2026-08-22.json
│   │   ├── 2026-08-29.json
│   │   └── cumulative_2026-08-29.json
│   ├── ai_agent.py
│   ├── ai_report.py
│   ├── analysis_kb.py
│   ├── behavior_fsm.py
│   ├── dashboard_api.py
│   ├── data_integrity.py
│   ├── db.py
│   ├── demo_emr.json
│   ├── demo_emr.py
│   ├── diagnostic_observer.py
│   ├── emr_kb.py
│   ├── export_report_reviews.py
│   ├── gait_evidence.json
│   ├── gait_prior.md
│   ├── gait_prior.py
│   ├── integrated_analysis.py
│   ├── live_diagnostics.py
│   ├── maintenance_ai.py
│   ├── majubom.db
│   ├── majubom.db-journal
│   ├── majubom.db.4x4bak.gz
│   ├── mmw_baseline.json
│   ├── mmw_logic.py
│   ├── mmw_server.py
│   ├── paper_kb.py
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
│   ├── test_gait_prior.py
│   ├── test_patient_report_context.py
│   ├── test_report_review.py
│   ├── test_report_trace.py
│   ├── test_sensor_observability.py
│   ├── test_tof_diagnostics.py
│   ├── tof_3d.html
│   ├── tof_baseline.json
│   ├── tof_baseline_tilt20_20261008.json
│   ├── tof_logic.py
│   └── tof_server.py
├── site/
│   ├── dashboard-assets/
│   │   ├── dashboard.css
│   │   ├── dashboard.js
│   │   ├── data.js
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
│   │   ├── tof_없음_empty_이불없음_r10_20261008_171257.jsonl
│   │   ├── tof_없음_empty_이불없음_r11_20261008_171332.jsonl
│   │   ├── tof_없음_empty_이불없음_r12_20261008_171407.jsonl
│   │   ├── tof_없음_empty_이불없음_r13_20261008_171442.jsonl
│   │   ├── tof_없음_empty_이불없음_r14_20261008_171517.jsonl
│   │   ├── tof_없음_empty_이불없음_r15_20261008_171552.jsonl
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
│   ├── ml/
│   │   ├── archive/
│   │   │   ├── lstm_ae_oldmount_20260707.pt
│   │   │   ├── posture_model_8x8_oldmount_20260707.joblib
│   │   │   └── README.md
│   │   ├── analyze_4x4_backup.py
│   │   ├── augment_tof.py
│   │   ├── backup_4x4_analysis.json
│   │   ├── bed_scan_preview.html
│   │   ├── bed_scan_result.json
│   │   ├── calibrate_roi.py
│   │   ├── compare_rf_tabpfn.py
│   │   ├── feature_logger.py
│   │   ├── lstm_ae.pt
│   │   ├── lstm_anomaly.py
│   │   ├── lstm_infer.py
│   │   ├── posture_model.joblib
│   │   ├── posture_model_v2_report.json
│   │   ├── posture_stats.py
│   │   ├── predict_posture.py
│   │   ├── README.md
│   │   ├── retrain_with_tabpfn.py
│   │   ├── roi.py
│   │   ├── tof_anomaly.py
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
│   ├── seed_demo_emr.py
│   └── update_project_structure.py
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
