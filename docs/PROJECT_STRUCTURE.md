# 마주봄 전체 구조

> **2026-10-31:** `main`의 `mmWave/`는 새 작업을 위해 비워졌습니다. 기존 수집기·설정·실험 데이터는 `backup/mmwave-before-reset` 브랜치에 보관되어 있으며, 아래 mmWave 실행 경로는 복원 전까지 사용할 수 없습니다. 서버의 mmWave 수신 코드는 남아 있지만 해당 수집기가 없어 독립적으로 실측을 수집하지 않습니다. 이후 Pi에서 실제로 돌리는 최신 mmWave 코드(천장 2.5m·하향 90° 설정, 노트북 스트림, 낙상 판정 `fall_detect.py`)는 `claude/sensor-remount-posture-v2` 브랜치에 있습니다(2026-10-31). main의 server/·tools/ 일부(예: `tools/set_bed_center.py`, `server/bed_position.py`가 읽는 `mmWave/bed_center.json`, 노트북 스트림 `/api/stream`)는 그 수집기와 함께 쓸 때 동작합니다.

AI 작업 진입점은 `AGENTS.md`와 `docs/AI_START_HERE.md`다.
이 문서는 실제 로컬 파일을 확인해 생성한 목록과 역할 설명을 함께 제공한다.
목록에는 데이터·모델·역사 문서도 포함한다. `.git`, 가상환경, 캐시, IDE 설정,
PlatformIO/Python 빌드, node_modules, 로그, LanceDB 저장 엔진 내부 파일과 카메라 로컬 recordings/outputs/checkpoints는 생략한다.
자동 생성 구간은 직접 편집하지 않고 `python3 tools/update_project_structure.py`로 갱신한다.
코드 역할이 달라지면 아래 역할/경로 설명도 반드시 수정한다.

## 폴더와 책임

| 경로 | 책임/진입점 |
|---|---|
| camera-pose/ | 독립 카메라 자세 연구·MediaPipe/CLIP·Three.js 3D 침상 모니터. 자체 AGENTS.md 적용. src/bed_pose/tof_learning은 읽기 전용 Pi 스트림+카메라 동기 수집·라벨 검토·CNN/CNN+GRU·LightGBM/RF/XGBoost 비교. training/의 PowerShell 도구와 docs/guides/TOF_TRAINING.md가 진입점. 실측 검증 전 운영 모델로 사용하지 않음 |
| camera-pose/src/bed_pose/pose/ | MediaPipe/browser GPU 어댑터, 원본 좌표 복원·부분 관절 재탐색·IMAGE 확인·GPU 연결 복구. person_guard.py는 명시적 로컬 설정의 근접 촬영용 CUDA 사람 검증. tools/pose_candidates.py·.venv-pose/는 후보 비교/검증기 설정, tools/check_pose_recovery.py는 실제 GPU 빈 장면/응답 소실 검사. ROI·모델 점수·실제 정답은 구분하며 관절 모델 자동 교체와 분리한다 |
| camera-pose/src/bed_pose/camera_diagnostics.py | RGB 표본의 밝기/클리핑 표시용 진단. live_engine의 프레임 부가정보→live_web→카메라 패널로 전달하며 원본/노출/관절/정답 기록을 변경하지 않음. 입력 대기와 추론 지연은 기존 processing_ms와 함께 표시 |
| TOF/src/ | ESP32-S3 Arduino 펌웨어와 PlatformIO 프로젝트. main.ino가 원본; 이전 Windows 전처리 생성물 main.ino.cpp는 제거했고 PlatformIO가 빌드 시 임시 생성한다 |
| TOF/ml/ | 자세 Random Forest 학습·추론(운영 v2: train_posture_v2.py, 이전 모델은 archive/), ROI/침대 좌표, LSTM-AE 참고 이상탐지, 특징 로깅, 후보 모델 비교 |
| TOF/dataset*/ | 라벨별 ToF 거리 CSV/JSONL, 기존 4×4 및 빈 침대 데이터 보관 |
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
- 카메라 학습: :5012/api/stream의 tof1/tof2 + 노트북 /api/room/live → camera-pose의 수집 품질/시차 gate → data/paired/ → 독립 검토 라벨·그룹 분할 → CNN/CNN+GRU 및 고정 특징의 트리 비교. 엄격 모드는 미검증 3D를 차단하며 검증·시험은 검토 정답만 허용한다. .venv-training/과 원시/모델 산출물은 로컬 전용이다.
- 연결 점검: camera-pose/training/Check-Connection.ps1 → tof_learning check가 ToF API/NDJSON·카메라 API를 읽고 로컬 outputs에 진단만 저장한다. 임시 입력 검사·독립 예측 일치·정답 정확도는 서로 구분하며, 거리 안정만으로 빈 침대 기준을 적용하지 않는다.
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
├── .codex-ppt-build/
│   ├── bed-double-2368D8.png
│   ├── bed-single-2368D8.png
│   ├── bell-2368D8.png
│   ├── build.mjs
│   ├── camera-2368D8.png
│   ├── candidate.pptx
│   ├── candidate.pptx.inspect.ndjson
│   ├── inspection.ndjson
│   ├── monitor-BFD8FF.png
│   ├── radar-2368D8.png
│   ├── slide-1.png
│   ├── slide-2.png
│   ├── slide-3.png
│   ├── slide-4.png
│   ├── validation.json
│   └── watch-2368D8.png
├── .github/
│   └── workflows/
│       └── camera-pose.yml
├── assets/
│   └── equipment_layout/
│       ├── .gitignore
│       ├── blender_capture.png
│       ├── build_scene.py
│       ├── majubom_equipment_clean.png
│       ├── MajuBom_Equipment_Layout.blend
│       ├── MajuBom_Equipment_Layout.blend1
│       ├── majubom_equipment_layout.png
│       ├── majubom_equipment_top.png
│       ├── open_scene.py
│       ├── preview.png
│       └── README.md
├── camera-pose/
│   ├── .github/
│   │   └── workflows/
│   │       └── tests.yml
│   ├── docs/
│   │   ├── archive/
│   │   │   ├── IMPLEMENTATION_HISTORY.md
│   │   │   ├── LYING_ORIENTATION_RULES.md
│   │   │   ├── README.md
│   │   │   └── REFERENCE_POSTURE.md
│   │   ├── assets/
│   │   │   ├── system-overview.png
│   │   │   └── system-overview.svg
│   │   ├── design/
│   │   │   ├── ARCHITECTURE.md
│   │   │   ├── AUTOMATIC_POSTURE.md
│   │   │   ├── CODE_DESIGN.md
│   │   │   ├── DATA_AND_VALIDATION.md
│   │   │   ├── MEASUREMENT_PROTOCOL.md
│   │   │   ├── MULTI_CAMERA_DESIGN.md
│   │   │   ├── PERSON_CROP.md
│   │   │   ├── POSTURE_EVIDENCE_COMPARISON.md
│   │   │   ├── POSTURE_INTERFACE.md
│   │   │   ├── POSTURE_STABILITY.md
│   │   │   ├── PREVIEW_CONCURRENCY.md
│   │   │   ├── README.md
│   │   │   ├── SILHOUETTE_SUPPORT.md
│   │   │   ├── TWO_VIEW_INPUTS.md
│   │   │   └── WEIGHTED_POSTURE.md
│   │   ├── development/
│   │   │   ├── AI_TASK_PROMPT.md
│   │   │   ├── BENCHMARK_DETAIL_SPEC.md
│   │   │   ├── MEDIAPIPE_ADAPTER.md
│   │   │   ├── POSTPROCESSING.md
│   │   │   └── README.md
│   │   ├── examples/
│   │   │   └── benchmark-v0.1/
│   │   │       ├── frame_inference_error.json
│   │   │       ├── frame_no_detection.json
│   │   │       ├── frame_not_processed.json
│   │   │       ├── frame_sequence_inference_error.json
│   │   │       ├── metric_computed_fps.json
│   │   │       ├── metric_computed_ratio.json
│   │   │       ├── metric_unavailable.json
│   │   │       └── run_config.json
│   │   ├── guides/
│   │   │   ├── ACCURACY_IMPROVEMENTS.md
│   │   │   ├── AUTOMATIC_CALIBRATION.md
│   │   │   ├── BED_REGION.md
│   │   │   ├── CAMERA_CAPTURE.md
│   │   │   ├── EXPERIMENTAL_3D_PREVIEW.md
│   │   │   ├── LIVE_MEASUREMENT.md
│   │   │   ├── PREVIEW_EXCEPTION_HANDLING.md
│   │   │   ├── PREVIEW_QUALITY.md
│   │   │   ├── README.md
│   │   │   ├── RECORD_REVIEW.md
│   │   │   ├── ROOM_MONITOR.md
│   │   │   ├── SCREEN_CALIBRATION.md
│   │   │   ├── SINGLE_POSTURE_PLAN.md
│   │   │   ├── SIX_POSTURE_3D.md
│   │   │   ├── STEREO_DEPTH.md
│   │   │   └── TOF_TRAINING.md
│   │   ├── research/
│   │   │   ├── BED_POSTURE_PAPERS.md
│   │   │   ├── LITERATURE_REVIEW.md
│   │   │   ├── LYING_SHOULDER_FEATURE_REVIEW.md
│   │   │   ├── MULTI_CAMERA_PAPERS.md
│   │   │   ├── POSE_BENCHMARK_PLAN.md
│   │   │   ├── POSE_COMPUTE_AND_RESOURCE_COMPARISON.md
│   │   │   ├── POSE_RECOVERY_REVIEW_2026-11-01.md
│   │   │   ├── README.md
│   │   │   ├── SKELETON_TECHNOLOGY_PAPERS.md
│   │   │   └── TECHNOLOGY_CANDIDATES.md
│   │   ├── reviews/
│   │   │   ├── AUTOMATIC_POSTURE_REVIEW.md
│   │   │   ├── IMPLEMENTATION_REVIEW.md
│   │   │   ├── MANNEQUIN_POSTURE_CRITERIA_2026-10-31.md
│   │   │   ├── POSTURE_CONDITION_AUDIT_2026-10-31.md
│   │   │   ├── README.md
│   │   │   └── RECORD_REVIEW_2026-10-31.md
│   │   ├── schemas/
│   │   │   └── benchmark-v0.1.schema.json
│   │   ├── DECISIONS.md
│   │   ├── PROJECT_STATUS.md
│   │   ├── README.md
│   │   └── REQUIREMENTS.md
│   ├── src/
│   │   └── bed_pose/
│   │       ├── pose/
│   │       │   ├── __init__.py
│   │       │   ├── base.py
│   │       │   ├── browser_gpu.py
│   │       │   ├── fake.py
│   │       │   ├── mediapipe.py
│   │       │   ├── person_guard.py
│   │       │   ├── recovery.py
│   │       │   └── rotation_rescue.py
│   │       ├── schemas/
│   │       │   └── benchmark-v0.1.schema.json
│   │       ├── static/
│   │       │   └── monitor/
│   │       │       ├── checkerboard.html
│   │       │       ├── index.html
│   │       │       ├── monitor.css
│   │       │       └── monitor.js
│   │       ├── templates/
│   │       │   ├── live_workspace.html
│   │       │   └── viewer.html
│   │       ├── tof_learning/
│   │       │   ├── __init__.py
│   │       │   ├── __main__.py
│   │       │   ├── check.py
│   │       │   ├── collect.py
│   │       │   ├── data.py
│   │       │   ├── models.py
│   │       │   └── train.py
│   │       ├── __init__.py
│   │       ├── __main__.py
│   │       ├── automatic_calibration.py
│   │       ├── automatic_posture.py
│   │       ├── bed_association.py
│   │       ├── bed_geometry.py
│   │       ├── bed_posture_3d.py
│   │       ├── blackbox_retention.py
│   │       ├── blackbox_storage.py
│   │       ├── body_state.py
│   │       ├── calibration.py
│   │       ├── calibration_methods.py
│   │       ├── camera_diagnostics.py
│   │       ├── camera_video.py
│   │       ├── cli.py
│   │       ├── clip_assets.py
│   │       ├── clip_benchmark.py
│   │       ├── clip_pool.py
│   │       ├── clip_posture.py
│   │       ├── config.py
│   │       ├── contracts.py
│   │       ├── decision_evidence.py
│   │       ├── demo.py
│   │       ├── evaluation.py
│   │       ├── evidence_report.py
│   │       ├── experimental_preview.py
│   │       ├── fusion_inputs.py
│   │       ├── group_posture.py
│   │       ├── group_session.py
│   │       ├── image_quality.py
│   │       ├── io_utils.py
│   │       ├── joint_inspection.py
│   │       ├── latest_camera.py
│   │       ├── live_app.py
│   │       ├── live_engine.py
│   │       ├── live_factory.py
│   │       ├── live_gui.py
│   │       ├── live_recording.py
│   │       ├── live_sources.py
│   │       ├── live_web.py
│   │       ├── lying_orientation.py
│   │       ├── mapping.py
│   │       ├── measurement_plan.py
│   │       ├── multiview_posture.py
│   │       ├── native_pose_cues.py
│   │       ├── person_crop.py
│   │       ├── person_detector.py
│   │       ├── pose_display.py
│   │       ├── posture.py
│   │       ├── posture_accuracy.py
│   │       ├── posture_decision.py
│   │       ├── posture_diagnostics.py
│   │       ├── posture_evidence.py
│   │       ├── posture_stability.py
│   │       ├── preview_alignment.py
│   │       ├── preview_quality.py
│   │       ├── preview_worker.py
│   │       ├── processing_rate.py
│   │       ├── record_library.py
│   │       ├── record_timeline.py
│   │       ├── recording.py
│   │       ├── reference_posture.py
│   │       ├── rendering.py
│   │       ├── replay_compare.py
│   │       ├── room_monitor.py
│   │       ├── runner.py
│   │       ├── screen_pattern.py
│   │       ├── separated_posture.py
│   │       ├── silhouette.py
│   │       ├── silhouette_overlay.py
│   │       ├── stereo_depth.py
│   │       ├── target_tracking.py
│   │       ├── time_alignment.py
│   │       ├── validation.py
│   │       ├── video.py
│   │       ├── viewer.py
│   │       ├── viewpoint_posture.py
│   │       └── weighted_posture.py
│   ├── tests/
│   │   ├── browser_body_state.py
│   │   ├── browser_camera_decoupling.py
│   │   ├── browser_camera_video.py
│   │   ├── browser_clip_posture.py
│   │   ├── browser_multicamera_setup.py
│   │   ├── browser_multiview_posture.py
│   │   ├── browser_person_crop.py
│   │   ├── browser_posture_separation.py
│   │   ├── browser_posture_stability.py
│   │   ├── browser_preview_recovery.py
│   │   ├── browser_record_comparison.py
│   │   ├── test_automatic_calibration.py
│   │   ├── test_automatic_posture.py
│   │   ├── test_automatic_rule_review.py
│   │   ├── test_bed_association.py
│   │   ├── test_bed_geometry.py
│   │   ├── test_bed_posture_3d.py
│   │   ├── test_blackbox_api.py
│   │   ├── test_blackbox_retention.py
│   │   ├── test_blackbox_segments.py
│   │   ├── test_blackbox_storage.py
│   │   ├── test_body_state.py
│   │   ├── test_browser_gpu.py
│   │   ├── test_calibration_methods.py
│   │   ├── test_camera_decoupling.py
│   │   ├── test_camera_diagnostics.py
│   │   ├── test_camera_settings.py
│   │   ├── test_camera_video.py
│   │   ├── test_clip_optimization.py
│   │   ├── test_clip_pool.py
│   │   ├── test_clip_posture.py
│   │   ├── test_clip_runtime.py
│   │   ├── test_decision_evidence.py
│   │   ├── test_display_efficiency.py
│   │   ├── test_evidence_posture.py
│   │   ├── test_experimental_preview.py
│   │   ├── test_forced_posture.py
│   │   ├── test_foundation.py
│   │   ├── test_fusion_inputs.py
│   │   ├── test_group_session.py
│   │   ├── test_input_workspace.py
│   │   ├── test_joint_inspection.py
│   │   ├── test_knee_edge.py
│   │   ├── test_live.py
│   │   ├── test_lying_orientation.py
│   │   ├── test_measurement_plan.py
│   │   ├── test_mediapipe_adapter.py
│   │   ├── test_multiview_posture.py
│   │   ├── test_native_pose_cues.py
│   │   ├── test_person_crop.py
│   │   ├── test_pi_camera.py
│   │   ├── test_pose_display.py
│   │   ├── test_pose_recovery.py
│   │   ├── test_postprocessing.py
│   │   ├── test_posture.py
│   │   ├── test_posture_accuracy.py
│   │   ├── test_posture_audit.py
│   │   ├── test_posture_diagnostics.py
│   │   ├── test_posture_evidence.py
│   │   ├── test_posture_stability.py
│   │   ├── test_preview_concurrency.py
│   │   ├── test_preview_edge_cases.py
│   │   ├── test_preview_quality.py
│   │   ├── test_record_image.py
│   │   ├── test_record_library.py
│   │   ├── test_record_timeline.py
│   │   ├── test_reference_posture.py
│   │   ├── test_room_monitor.py
│   │   ├── test_rotation_rescue.py
│   │   ├── test_runtime_performance.py
│   │   ├── test_separated_posture.py
│   │   ├── test_silhouette.py
│   │   ├── test_silhouette_overlay.py
│   │   ├── test_single_camera_preview.py
│   │   ├── test_standalone_quality.py
│   │   ├── test_stereo_depth.py
│   │   ├── test_target_selection.py
│   │   ├── test_target_tracking.py
│   │   ├── test_time_alignment.py
│   │   ├── test_tof_collection.py
│   │   ├── test_tof_connection_check.py
│   │   ├── test_tof_learning.py
│   │   ├── test_video_pipeline.py
│   │   ├── test_viewer.py
│   │   ├── test_viewpoint_posture.py
│   │   ├── test_web_workspace.py
│   │   └── test_weighted_posture.py
│   ├── tools/
│   │   ├── gpu-preview/
│   │   │   ├── bridge.cjs
│   │   │   ├── bridge.html
│   │   │   ├── bridge.js
│   │   │   ├── package-lock.json
│   │   │   ├── package.json
│   │   │   ├── preview.html
│   │   │   ├── preview.js
│   │   │   ├── README.md
│   │   │   └── server.cjs
│   │   ├── room-monitor/
│   │   │   ├── public/
│   │   │   │   └── checkerboard.html
│   │   │   ├── src/
│   │   │   │   ├── AutomaticCalibration.tsx
│   │   │   │   ├── CalibrationPanel.tsx
│   │   │   │   ├── main.tsx
│   │   │   │   ├── monitor.css
│   │   │   │   ├── poseSmoothing.ts
│   │   │   │   ├── Posture3D.tsx
│   │   │   │   ├── RelativeRig.tsx
│   │   │   │   ├── scene.ts
│   │   │   │   └── types.ts
│   │   │   ├── index.html
│   │   │   ├── package-lock.json
│   │   │   ├── package.json
│   │   │   ├── smoothing.test.mjs
│   │   │   ├── tsconfig.json
│   │   │   └── vite.config.js
│   │   ├── bed-pose-camera.service
│   │   ├── check_pose_recovery.py
│   │   ├── pi_camera_stream.py
│   │   └── pose_candidates.py
│   ├── training/
│   │   ├── Check-Connection.ps1
│   │   ├── requirements-pose.txt
│   │   ├── requirements.lock.txt
│   │   ├── requirements.txt
│   │   ├── Setup-PoseBenchmarks.ps1
│   │   ├── Setup-Training.ps1
│   │   ├── smoke_training.py
│   │   ├── split.example.json
│   │   ├── Start-Collection.ps1
│   │   └── Start-Training.ps1
│   ├── .gitattributes
│   ├── .gitignore
│   ├── AGENTS.md
│   ├── pyproject.toml
│   ├── README.md
│   └── ROADMAP.md
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
│   ├── AI_START_HERE.md
│   ├── CODE_GUIDE_LIVE.html
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
│   └── .gitkeep
├── output/
│   └── presentation/
│       ├── 마주봄_30초_발표대본.txt
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
│   ├── lancedb_store/
│   │   ├── analysis_chunks.lance/
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
│   │   ├── artifacts/
│   │   │   ├── test-results/
│   │   │   │   └── .last-run.json
│   │   │   ├── dashboard-dark.png
│   │   │   ├── dashboard-light.png
│   │   │   ├── dashboard-mobile.png
│   │   │   └── results.json
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
│   ├── ml/
│   │   ├── archive/
│   │   │   ├── lstm_ae_oldmount_20260707.pt
│   │   │   ├── posture_model_8x8_oldmount_20260707.joblib
│   │   │   ├── posture_model_v2_S01only_20261008.joblib
│   │   │   ├── posture_model_v2_S01only_20261008_report.json
│   │   │   └── README.md
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
│   │   ├── tof_anomaly.py
│   │   ├── train_lstm_ae_v2.py
│   │   ├── train_posture.py
│   │   ├── train_posture_v2.py
│   │   └── zone_probe.py
│   ├── src/
│   │   ├── main.ino
│   │   ├── platformio.ini
│   │   └── secrets.example.h
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
