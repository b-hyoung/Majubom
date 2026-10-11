# 마주봄 AI 작업 규칙

이 저장소에서 코드를 작성·수정·이동·삭제하기 전에 반드시 다음을 읽는다.

1. `docs/AI_START_HERE.md`: 작업 절차, 현재 장치와 데이터 경로, 영역별 필독 문서.
2. `docs/PROJECT_STRUCTURE.md`: 전체 파일 구조와 운영/실험/데이터 경계.
3. `SYSTEM_ARCHITECTURE.md`: 센서부터 판정·DB·대시보드·AI 리포트까지의 연결.
4. 변경 대상 영역의 문서와 실제 호출자/수신자를 확인한다(아래 안내의 영역별 표).

## 변경과 문서 갱신은 같은 작업

- 파일 추가·삭제·이동 후 `python3 tools/update_project_structure.py`로 구조 문서를 갱신한다.
- 역할, API, 데이터 흐름, 실행 방법, 의존성, 하드웨어/네트워크 가정이 바뀌면
  `docs/PROJECT_STRUCTURE.md`의 설명과 `docs/AI_START_HERE.md`, 관련 영역 문서도 함께 수정한다.
- 마무리 전에 `python3 tools/update_project_structure.py --check`와 `git diff --check`를 실행한다.
- 폴더별 지침이 추가되면 해당 지침도 읽는다. 사용자 요청은 이 문서보다 우선한다.
- 센서 실측 수신, 빈 값/보류, 시뮬레이션을 구분한다. HTTP 200이나 USB 인식만으로 센서 수신 성공을 선언하지 않는다.
- 현재 ToF는 ESP32-S3 한 보드당 VL53L5CX 하나, 두 보드의 `tof1`/`tof2`로 구분한다.
  Pi는 유선 LAN이며 ESP32는 공유기 2.4GHz Wi-Fi를 사용한다. 서버 실행과 펌웨어 업로드는 별도다.
- mmWave DATA UART는 리더 한 개만 사용한다. 수신 서버만 실행해도 USB 데이터가 자동 수집되는 것으로 가정하지 않는다.
- 네트워크 이름·IP는 환경마다 확인하고 비밀번호·API 키를 문서/로그에 기록하지 않는다.
- 원시 데이터·DB·baseline·학습 모델은 구조 정리를 이유로 삭제하거나 덮어쓰지 않는다.
- 역사 문서의 CSI/4x4/듀얼센서 배선/Windows 경로를 현재 구현으로 취급하지 않는다.

- 간호사 대시보드와 별도 로우데이터 대시보드는 동시에 사용한다. 추가 진단 분기만 메모리 최신값이며 기존 운영 DB 수집은 유지한다. 자세한 실행은 `docs/SENSOR_DIAGNOSTICS.md`를 읽는다.
