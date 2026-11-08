# 새 노트북으로 카메라 + mmWave 측정 환경 옮기기 (2026-11-08)

지금 구성(C920은 Pi, USB2.0 PC CAMERA는 노트북, mmWave는 Pi 리더 + 노트북 테스트 뷰어)을 성능이 좋은 Windows 노트북으로 그대로 옮기는 순서다.
**Pi 쪽(mmWave 리더·서버, 카메라 영상 서비스)은 바꾸지 않는다.** 노트북 쪽만 다시 만든다.

## 옮길 묶음

이전 노트북에서 `D:\majubom-transfer-20261108\`(약 1.9GB)를 USB 등으로 옮긴다.

| 폴더 | 내용 | 새 노트북에서 둘 곳 |
|---|---|---|
| `camera-pose\checkpoints\` | MediaPipe `pose_landmarker_full.task`, CLIP `clip-vit-base-patch16\` | `<저장소>\camera-pose\checkpoints\` |
| `camera-pose\recordings\` | 카메라 측정 기록(2026-11-08 시험 포함) | `<저장소>\camera-pose\recordings\` |
| `mmw_viewer\` | mmWave 테스트 뷰어 코드, 침대·방·문 영역 설정, `sessions\` 녹화 | 아무 곳(예: `C:\Users\<사용자>\Downloads\mmw_viewer\`) |
| `majubom-reports\` | 알고리즘·낙상 비교 HTML 보고서와 스크립트 | 아무 곳 |
| `claude-memory\` | Claude Code 작업 기억 | `C:\Users\<사용자>\.claude\projects\<작업 폴더 이름>\memory\` (Claude Code를 처음 실행한 작업 폴더에 따라 이름이 정해짐. 이전 노트북은 `C--Windows-System32`) |

묶음에 넣지 않은 비밀값(새 노트북에서 직접 만든다): SSH 키, Pi 카메라 접속 파일(`pi-camera-url.txt`, 토큰 포함), 카메라 작업실 접속 파일(`outputs\mac-pi-connection.json`).

## 1. 공통 준비

- Python 3.12 64비트, Git, NVIDIA 드라이버를 설치한다. `nvidia-smi`로 GPU가 보이는지 확인한다.
- **Pi와 같은 Wi-Fi**에 연결한다(Pi `192.168.0.4`). 다른 네트워크면 Pi 영상·mmWave를 받을 수 없다.

## 2. 저장소

```powershell
git clone --branch seungmin https://github.com/b-hyoung/Majubom.git D:\Majubom
```

`seungmin`에는 mmWave 코드와 카메라 Windows 실행 수정(`camera-pose/tools/start_mac_monitor.py`의 `os.fchmod`)이 모두 들어 있다.

## 3. SSH 키 (Pi 접속)

새 키를 만들어 Pi에 등록한다. Pi 비밀번호는 본인이 직접 입력한다.

```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh pi@192.168.0.4 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
ssh -o BatchMode=yes pi@192.168.0.4 "echo ok"     # 비밀번호 없이 ok가 나오면 성공
```

## 4. 카메라 환경

`camera-pose/docs/guides/PI_USB_WINDOWS.md`를 따른다. 저장소 루트에서:

```powershell
cd D:\Majubom
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".\camera-pose[live,stereo,clip]"
# CLIP을 GPU로: PyTorch만 CUDA 빌드로 바꾼다(RTX 50 계열은 cu128. 다른 GPU는 pytorch.org에서 맞는 주소 확인)
& .\.venv\Scripts\python.exe -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu128
& .\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"   # True여야 함
```

- 묶음의 `camera-pose\checkpoints\`와 `camera-pose\recordings\`를 `D:\Majubom\camera-pose\` 안에 복사한다.
- 실행은 `Start-PiUsb.ps1`이다. `Start-CameraGPU.ps1`은 C920을 노트북에 직접 꽂는 구성(`tools/start_gpu_monitor.py:38`)이라 지금 구성과 맞지 않는다.
- MediaPipe는 원래 CPU로 돈다(문서 기준). GPU는 CLIP에 쓰인다.

## 5. Pi 카메라 접속 파일

SSH가 되면 Claude Code 입력창에서 아래 bash 명령을 `!`를 붙여 실행한다. 토큰은 화면에 출력되지 않는다.

```
! mkdir -p "$HOME/.config/bed-pose" && t=$(ssh pi@192.168.0.4 'cat ~/.local/share/bed-pose-camera/token' | tr -d '\r\n ') && printf 'http://192.168.0.4:8091/%s' "$t" > "$HOME/.config/bed-pose/pi-camera-url.txt" && echo "저장됨, 토큰 길이 ${#t}"; unset t
```

토큰 길이가 32 이상이면 정상이다(이전 노트북에서는 48).

## 6. USB 카메라와 실행

- USB2.0 PC CAMERA를 **이전과 같은 자리·방향**으로 새 노트북에 꽂는다. 그러면 임시 침대 영역(`config/installation-20261107/pi-usb-test.json`)을 그대로 쓸 수 있다.
- 실행:

```powershell
cd D:\Majubom\camera-pose
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-PiUsb.ps1
```

`MAC_PI_MONITOR_READY: Pi C920 + USB2.0 PC CAMERA`가 나오면 준비 완료다. 화면의 파란 매트리스 경계가 두 화면 모두 침대와 맞는지 확인한다.

## 7. mmWave 테스트 뷰어

```powershell
$env:PYTHONPATH='D:\Majubom\mmWave'
cd <복사한 mmw_viewer>
python viewer_server.py --reader D:\Majubom\mmWave\send_mmw.py
```

- 브라우저 `http://localhost:8765`. 뷰어는 Pi 리더의 복사본(`127.0.0.1:5003`, ssh로 접속)을 읽으므로 Pi 리더와 함께 돈다.
- 필요한 패키지: `numpy`(같은 `.venv`의 python을 써도 된다).

## 8. 동시 녹화 (카메라 + mmWave)

1. mmWave 뷰어 녹화를 먼저 켠다.
2. 카메라 작업실에서 **두 카메라 통합 판정 설정 → 두 입력 선택 → 양쪽 침대 영역 확인 → 허용 수신 시각 차이 250ms → 그룹 측정 시작**.
   블랙박스는 끈다. 학습 자료를 모을 때는 영상 녹화를 꺼서 프레임 수를 늘린다.
3. 자세를 바꿀 때마다 시각을 기록한다(정답 라벨).
4. 그룹 측정 종료 → mmWave 녹화 종료.
5. 맞춰 보기: `python tools/cam_mmw_join.py <카메라 측정 폴더> <mmWave 녹화 폴더> --marks 자세=HH:MM:SS.fff ...`

결과와 주의점은 `mmWave/CAMERA_MMWAVE_SYNC_2026-11-08.md`에 있다.

## 확인 목록

- [ ] `nvidia-smi`에 GPU가 보임
- [ ] `ssh -o BatchMode=yes pi@192.168.0.4 "echo ok"` 성공
- [ ] `torch.cuda.is_available()`이 True
- [ ] `pi-camera-url.txt` 토큰 길이 32 이상
- [ ] `Start-PiUsb.ps1`에서 `MAC_PI_MONITOR_READY` 출력, 두 화면 침대 영역이 맞음
- [ ] 뷰어 `http://localhost:8765`에서 mmWave 프레임 수신(약 18fps)
- [ ] Pi 리더가 `test_bed`로 기록 중(`ssh pi@192.168.0.4 "grep -o -- '--target [a-z_0-9]*' /etc/systemd/system/majubom-mmw-reader.service"`)
