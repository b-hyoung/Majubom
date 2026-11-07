# Pi C920 + Windows USB2.0 실행·이전 방법

[프로젝트 홈](../../README.md) · [테스트 화면과 판정 의미](LIVE_MEASUREMENT.md#보정판-없는-걸터앉음-테스트)

## 장치 연결

- C920은 Raspberry Pi에 그대로 둡니다. Pi의 `bed-pose-camera.service`가 영상을 전송합니다.
- USB2.0 PC CAMERA는 분석할 Windows 컴퓨터에 직접 꽂습니다. 이전 Mac에서는 분리합니다.
- Windows와 Pi는 서로 통신할 수 있는 같은 네트워크에 연결합니다.
- 관절 추론·CLIP·자세 판정은 Windows에서 실행합니다. Windows가 Pi에 분석을 맡기는 구조가 아닙니다.

Mac에 꽂힌 카메라를 다른 Windows에서 자동으로 볼 수는 없습니다. USB 케이블만 새
컴퓨터로 옮기고 렌즈 위치·방향·해상도를 유지했다면 기존 침대 영역을 참고할 수 있습니다.
카메라를 움직였거나 영상 범위가 달라지면 영역을 다시 지정합니다.

## 1. 처음 설치

Python 3.12 64비트와 Git을 준비하고 PowerShell에서 실행합니다.

```powershell
git clone https://github.com/b-hyoung/Majubom.git
cd Majubom
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".\camera-pose[live,stereo,clip]"
cd camera-pose
```

소스 업데이트는 측정을 종료한 뒤 저장소 루트에서 `git pull --ff-only`로 진행합니다.
Pi의 센서 운영 서버·펌웨어를 재설치할 필요는 없습니다.

## 2. 모델 준비

기존 Mac의 `camera-pose/checkpoints` 폴더를 USB 등으로 Windows의 같은 위치에 복사할 수
있습니다. 가상환경 `.venv`는 복사하지 않고 각 운영체제에서 새로 설치합니다.

모델을 복사하지 않았을 때만, `camera-pose` 폴더에서 다음을 실행합니다.

```powershell
New-Item -ItemType Directory -Force checkpoints | Out-Null
Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task" -OutFile "checkpoints/pose_landmarker_full.task"
& ..\.venv\Scripts\python.exe -m bed_pose clip-setup
```

기존 CLIP 폴더가 있으면 `clip-setup`을 다시 실행하지 않습니다. 준비한 모델은 Git에
포함하지 않습니다. MediaPipe는 CPU, CLIP은 설치된 PyTorch 환경에서 CUDA→MPS→CPU
순서로 선택합니다. Windows는 사용 가능한 CUDA 또는 CPU로 실행하며 CUDA 설치·GPU 성능은
새 컴퓨터에서 별도 확인합니다. 특정 NVIDIA GPU가 필수인 실행 파일은 사용하지 않습니다.

## 3. Pi 접속 설정 옮기기

현재 Mac의 다음 파일을 Windows로 별도 복사합니다.

```text
Mac:     ~/.config/bed-pose/pi-camera-url.txt
Windows: %USERPROFILE%\.config\bed-pose\pi-camera-url.txt
```

이 파일에는 Pi MJPEG URL과 접근 토큰이 들어 있습니다. 파일 내용을 문서·스크린샷·Git에
넣지 않습니다. Windows의 `.config\bed-pose` 폴더가 없으면 만듭니다.
Pi 주소가 달라졌다면 로컬 파일에서 호스트 주소만 현재 Pi 주소로 수정합니다.
기존 토큰은 바꾸지 않습니다. 현재 서비스 상태는 Pi에서 다음으로 확인합니다.

```bash
systemctl --user status bed-pose-camera.service
```

이 설정 파일은 사용자별 로컬 파일이므로 저장소 clone만으로 복사되지 않습니다.

## 4. 실행

`camera-pose` 폴더에서 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-PiUsb.ps1
```

기본 카메라 이름은 `USB2.0 PC CAMERA`입니다. 장치 이름이 다르면 다음처럼 실제 이름을
지정합니다. 같은 이름이 여러 개면 임의 장치를 선택하지 않고 중단합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-PiUsb.ps1 --camera-name "USB2.0 PC CAMERA"
```

같은 설치가 정상 실행 중이면 기존 작업실을 다시 엽니다. 사용자 측정·녹화를 자동으로
시작하지 않습니다. 종료는 화면의 **작업실 종료** 또는 실행 터미널의 Ctrl+C입니다.

## 5. 침대 영역과 테스트

`config/installation-20261107/pi-usb-test.json`은 이번 설치의 임시 영역과 높이 참조입니다.
카메라 A는 Pi C920, B는 분석 컴퓨터의 USB2.0이며 두 입력 모두640×480입니다.
현재 Mac USB 영역에는 화면 아래쪽에서 잘린 경계가 포함돼 있어 정확한 실측 모서리나
카메라 보정값으로 취급하지 않습니다. 카메라 약2.1m·침대 윗면0.65m는 기존 입력을
보존한 참조이며 현재 관절의 실측 높이가 아닙니다.

화면의 파란 매트리스 경계를 먼저 확인합니다. 잘못되면 해당 영상을 누르고
**침대 영역 지정 / 점 위치 수정**으로 수정합니다. 입력 변경은 현재 작업실에 즉시
적용됩니다. 다음 실행에도 적용하려면 로컬 복사본의 `regions[].points`에 수정 좌표를
보존하고 `--test-profile`로 그 파일을 지정합니다. 이미지 좌표는 원본640×480 기준입니다.
같은 이름의 설치 설정에는 동일한 물리 배치만 사용합니다.

```powershell
Copy-Item config\installation-20261107\pi-usb-test.json outputs\pi-usb-local.json
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-PiUsb.ps1 --test-profile outputs\pi-usb-local.json
```

화면에서 다음을 각각 잠시 유지하며 확인합니다.

1. 침대에 걸터앉아 양발을 아래로 내림: 걸터앉음 근거와 무릎·발목 값 확인.
2. 다리를 매트리스 위에 올려 앉음: 침대 위 앉음 근거 확인.
3. 서거나 한쪽 시야를 가림: 서 있음 / 관측 불가와 카메라 제외 여부 확인.

머리 관절은 걸터앉음의 필수 조건이 아닙니다. 그러나 SDK가 몸통·다리까지 놓쳤다면
그 시점의 부분 관측으로 침대 위 앉음을 확정하지 않습니다. 유효한 다른 시점의 근거를
사용하고, 둘 다 부족하면 **앉음·다리 위치 확인 중** 또는 관절 관측 대기로 표시합니다.
다리 내려감은 몸통 길이 대비 모델 추정 비율입니다. 정확한 cm·방 좌표·실제 정확도나
학습 정답을 인증하는 결과가 아닙니다.

## 검증 범위

Mac + 실제 두 카메라의 연결·임시 영역·추론 실행, 한 걸터앉음 사례의 수정 후 안정 후보를
확인했습니다. 머리/다리 누락·부분 시점 충돌·다리를 올린 합성 장면은 개발 검사로 확인합니다.
Windows용 실행 파일은 같은 Python 경로를 사용하며 실제 Windows 카메라 연결·속도와
현재0.6의 실제 가림 성능은 새 컴퓨터에서 확인해야 합니다.
