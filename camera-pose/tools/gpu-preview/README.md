# MediaPipe GPU 카메라 검증 화면

Windows에서 카메라 영상과 MediaPipe Pose Landmarker의 GPU 실행을 확인하는 **실험용 독립 화면**입니다. 이 검증 화면은 측정·기록을 하지 않습니다. 별도의 [작업실 GPU 실험 모드](../../docs/guides/LIVE_MEASUREMENT.md#windows-mediapipe-gpu-실험-모드)에서는 분석·기록에 연결합니다. 카메라 영상은 로컬에서 처리하고 저장하지 않습니다.

Node.js와 Chrome 또는 Edge가 필요합니다. 아래 명령은 프로젝트 루트의 PowerShell에서 실행합니다. 모델 경로는 로컬 `.task` 파일을 지정합니다.

```powershell
Set-Location tools/gpu-preview
npm.cmd install
npm.cmd start -- "C:\Users\admin\Documents\GitHub\bed-pose-skeleton-ai\pose_landmarker_full.task"
```

열린 화면에서 **카메라 시작**을 누르고 카메라 사용을 허용합니다. 장치 이름이 표시된 뒤 다른 카메라를 선택하려면 중지 후 다시 시작합니다. 렌더러와 검출 수, 시작 후 평균 처리 프레임 수가 표시됩니다. 처리율은 정식 성능 측정이 아닙니다. 종료하려면 창을 닫습니다. 브라우저가 자동으로 열리지 않으면 콘솔에 출력된 로컬 주소를 지원 브라우저에서 열고 서버는 Ctrl+C로 종료합니다.

Chrome의 고성능 GPU 선택을 요청하지만 기기·브라우저 정책에 따라 다른 GPU가 선택될 수 있습니다. 반드시 화면의 렌더러를 확인하세요. GPU가 확인되지 않거나 모델 실행에 실패하면 오류를 표시하고 CPU로 자동 전환하지 않습니다. 카메라를 보내거나 녹화하지 않습니다.

## 현재 검증

2026-10-31 Windows, Chrome, NVIDIA RTX 4060 Laptop GPU에서 카메라 640×480 프레임에 GPU delegate를 적용하고 한 명의 자세 검출을 확인했습니다. 이는 관절 검출 품질이나 침대 환경 정확도 검증은 아닙니다. 작업실의 실루엣 마스크와 실제 카메라 성능 검증은 남아 있습니다.
