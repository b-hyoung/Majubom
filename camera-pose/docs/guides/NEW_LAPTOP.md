# 다른 노트북으로 전체 카메라 작업 옮기기

현재 Pi C920 + Windows USB2.0 프로그램 실행은 [Windows 이전 안내](PI_USB_WINDOWS.md)를
따릅니다. 아래는 과거 `minhyuk` 브랜치의 암호화 백업 복원 절차입니다. `main`에는
원본 영상이 든 `transfer` 백업을 포함하지 않으며, 필요한 경우 아래처럼 백업 브랜치를
별도로 받아 복원합니다. 새 컴퓨터에서 프로그램만 실행할 때는 이 백업이 필요하지 않습니다.

2026-11-03: 소스·Three.js 빌드·테스트는 `minhyuk`에 추적한다. 모델,
원본 카메라 촬영 이미지, 관절/분류 기록, 보정 촬영, 침대 경계, 좌표축,
원래 작업 폴더의 outputs/recordings/checkpoints는 아래 암호화 백업에도 보관한다.
공개 저장소이므로 원본 촬영 자료와 런타임 접속 정보를 평문으로 올리지 않는다.
새로운 ToF 모델 학습 및 실제 정확도 검증이 완료됐다는 뜻은 아니다.

## 1. 브랜치와 대용량 백업 받기

Git LFS, Python 3.12 64비트, NVIDIA 드라이버가 필요하다. 이 GPU 설치 경로는
Windows + NVIDIA CUDA용이다. 다른 GPU/운영체제에서 동일한 GPU 동작을 보장하지 않는다.

```powershell
git lfs install
git clone --branch minhyuk https://github.com/b-hyoung/Majubom.git
cd Majubom
git lfs pull
python -m pip install cryptography==46.0.3
python camera-pose/tools/workspace_backup.py restore --backup camera-pose/transfer/camera-workspace-20261103.enc --key-file "D:/Majubom-camera-backup-key-20261103.txt" --destination restored-camera-pose
```

키 파일은 기존 노트북 바탕화면의 `Majubom-camera-backup-key-20261103.txt`를
USB 등으로 별도 복사한다. 위 `D:/...`는 실제 복사 위치로 바꾼다.
키를 분실하면 복원할 수 없다. GitHub나 공개 채팅에 키 내용을 올리지 않는다.
복원은 비어 있는 새 폴더만 허용하며 AES-GCM 인증 후 파일별 SHA-256도 검증한다.

## 2. 원본 자료 적용 및 환경 재설치

새 clone에서 다음을 실행한다. 복원 원본 전체는 `restored-camera-pose`에 남는다.

```powershell
Copy-Item restored-camera-pose/checkpoints camera-pose/ -Recurse
Copy-Item restored-camera-pose/recordings camera-pose/ -Recurse
Copy-Item restored-camera-pose/outputs camera-pose/ -Recurse
cd camera-pose
./Setup-NewLaptop.ps1
./Start-CameraGPU.ps1
```

설치 스크립트는 기존 모델을 보존·검증하고 CUDA PyTorch, MediaPipe, CLIP,
별도 CUDA 사람 검증기를 설치하며 기존 노트북 절대 경로를 새 위치로 다시 연결한다.
원본 모델이 없으면 고정 CLIP 리비전과 체크섬으로 공개 모델을 재다운로드한다.
카메라 A는 C920, B는 USB2.0 PC CAMERA 이름으로 바인딩한다.
다른 카메라를 쓰면 `tools/start_gpu_monitor.py`의 선택 이름부터 변경해야 한다.

물리 카메라/침대 배치가 바뀌면 복원된 기존 보정·침대 경계는 새 환경에 유효하지 않다.
이때 recordings 원본은 보관만 하고 다음처럼 새 빈 실행 폴더를 만든다.

```powershell
Rename-Item recordings/live live-previous-installation
./Start-CameraGPU.ps1
```

새 환경에 보정 후보가 없으면 현재 런처는 3D 미리보기를 대기 상태로 시작한다.
새 환경에서 침대 경계와 카메라 보정을 다시 설정해야 한다.
기존 환경에서도 보정 후보는 **미검증**, 엄격 삼각측량/학습 승인과 별개다.
모델의 신뢰도와 실제 정답률은 동일하지 않으며 100% 정확도를 보장하지 않는다.

## 포함 범위와 재생성 항목

- 암호화 백업: camera-pose 전체에서 소스·문서·설정·checkpoints·recordings·outputs 포함.
  기존 출력 안의 이전 프로젝트 사본과 실험 결과도 포함한다.
- `.git`, Python 가상환경/캐시/빌드, node_modules, 백업 자신의 transfer 폴더는 제외한다.
  이들은 사용자 촬영/모델 결과가 아니며 다른 노트북에서는 환경 재설치로 재생성한다.
- 백업은 Pi 디스크 또는 루트 Majubom 운영 DB의 백업이 아니다.
- 토큰은 암호화 백업 안에만 남는다. 복원된 live-connection.json의 이전 접속 링크는
  다른 장치에 전달하지 않는다. 필요하면 실행 전 해당 파일을 별도 보관하고 새 토큰을 생성한다.
