# Claude 작업 시작 규칙

@AGENTS.md
@docs/AI_START_HERE.md
@docs/PROJECT_STRUCTURE.md
@SYSTEM_ARCHITECTURE.md

작업 시작 전에 위 파일을 읽고 대상 영역의 필독 문서도 확인한다.
코드를 수정하면서 파일 구조·역할·데이터 흐름·설정이 바뀌면 관련 안내를 같은 작업에서 갱신한다.
완료 전에 `python3 tools/update_project_structure.py`와 `--check`, `git diff --check`를 실행한다.
mmWave 진단은 `docs/SENSOR_DIAGNOSTICS.md` 참조. 추가 로우데이터 분기는 파일/DB에 저장하지 않는다. 기존 간호사 대시보드의 수집·저장 경로는 함께 유지한다.
