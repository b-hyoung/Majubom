"""
서버 일괄 실행 — CSI(5003) + ToF(5001)를 한 번에 띄움
======================================================
각 서버(csi_server.py / tof_server.py)는 그대로 두고, 프로세스만 함께 spawn.

사용:
  python run_all.py            # server/ 폴더에서 실행
종료:
  Ctrl+C                       # 모든 서버 함께 종료
필요:
  pip install -r ../requirements.txt   # flask, flask-cors

대시보드: http://localhost:5003/dashboard
※ mmWave(:5002)는 아직 미구현이라 제외.
"""
import os
import sys
import signal
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
POSIX = os.name == "posix"

# (표시이름, 스크립트, 포트)
SERVERS = [
    ("CSI", "csi_server.py", 5003),
    ("ToF", "tof_server.py", 5001),
]


def spawn(script):
    """서버를 자체 프로세스 그룹으로 실행 (flask debug 리로더 자식까지 함께 종료하려고)."""
    path = os.path.join(HERE, script)
    kw = {"cwd": HERE}
    if POSIX:
        kw["start_new_session"] = True
    else:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen([sys.executable, path], **kw)


def kill(proc):
    if proc.poll() is not None:
        return
    try:
        if POSIX:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
    except (ProcessLookupError, OSError):
        pass


procs = []  # [(name, script, proc), ...]


def shutdown(*_):
    print("\n[run_all] 종료 중…")
    for _, _, p in procs:
        kill(p)
    for _, _, p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    print("[run_all] 모두 종료.")
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

print("=" * 52)
for name, script, port in SERVERS:
    p = spawn(script)
    procs.append((name, script, p))
    print(f"  ▶ {name:4} → :{port}   ({script}, pid {p.pid})")
print("  · mmWave(:5002) 미구현 → 제외")
print("=" * 52)
print("  대시보드 : http://localhost:5003/dashboard")
print("  종료     : Ctrl+C")
print("=" * 52)

# 자식이 죽으면 알림 (flask 미설치 등). 모두 죽으면 런처도 종료.
while True:
    time.sleep(1)
    dead = [(n, s, p) for (n, s, p) in procs if p.poll() is not None]
    for n, s, p in dead:
        print(f"[run_all] ⚠ {n} 종료됨 (exit {p.returncode}). "
              f"flask 설치 확인: pip install -r ../requirements.txt")
        procs.remove((n, s, p))
    if not procs:
        print("[run_all] 실행 중인 서버 없음 — 런처 종료.")
        break
