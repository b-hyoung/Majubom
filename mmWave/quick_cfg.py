#!/usr/bin/env python3
# 전원 켜진 직후 응답 창에 cfg를 빠르게 밀어넣기 (bounded read — readall 무한대기 회피)
import serial, time, sys
CLI = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
CFG = sys.argv[2] if len(sys.argv) > 2 else "AOP_bed_2m7_d15.cfg"
cli = serial.Serial(CLI, 115200, timeout=0.3)
cli.reset_input_buffer()
ok = rej = 0
for raw in open(CFG):
    line = raw.strip()
    if not line or line.startswith("%"):
        continue
    cli.write((line + "\n").encode())
    resp = ""
    t = time.time()
    while time.time() - t < 0.12:          # 짧게만 읽음(무한대기 방지)
        n = cli.in_waiting
        if n:
            resp += cli.read(n).decode(errors="ignore"); t = time.time()
        else:
            time.sleep(0.015)
    if "error" in resp.lower() or "not recognized" in resp.lower():
        rej += 1; print("REJECT:", line, flush=True)
    else:
        ok += 1
cli.close()
print(f"cfg 완료: ok {ok}, reject {rej}", flush=True)
