"""노트북에서 Pi의 ToF·mmWave 프레임을 실시간으로 받아 노트북에 저장한다(2026-10-31).

용도: 침상 양쪽 카메라로 자세 정답(label)을 만들고, 같은 시각의 센서 프레임과 맞춰 학습 정확도를 높이는 실험.
Pi(:5012 diagnostic_server)는 저장하지 않고 흘려보내기만 한다. 저장은 이 스크립트가 노트북에 한다.
표준 라이브러리만 쓴다(Windows/macOS/Linux, Python 3.8+).

받기:
    python laptop_stream_client.py --pi 192.168.0.4
    python laptop_stream_client.py --pi 192.168.0.4 --sensors tof1,tof2 --out D:/majubom_stream

저장: <out>/<노트북 시각>/tof1.jsonl, tof2.jsonl, mmwave.jsonl, heartbeat.jsonl
    모든 줄에 pi_rx_unix(Pi가 받은 시각)와 laptop_rx_unix(노트북이 받은 시각)가 있다.
    카메라 영상은 노트북 시계로 찍히므로 laptop_rx_unix 또는 pi_rx_unix + 시계 차이(heartbeat.jsonl)로 맞춘다.
    mmwave 줄의 raw_b64는 레이더 프레임 원본 바이트(헤더+TLV 전부), tracks는 Pi 리더가 쓴 바닥 좌표 추적이다.

mmWave 원본을 점군으로 풀기(저장소가 노트북에 있을 때, numpy 필요):
    python laptop_stream_client.py --decode <세션 폴더>/mmwave.jsonl
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from datetime import datetime


def receive(args):
    session = os.path.join(args.out, datetime.now().strftime('%Y%m%d_%H%M%S'))
    os.makedirs(session, exist_ok=True)
    files = {}
    counts, dropped_total = {}, 0
    offset_min = None   # 노트북 시각 − Pi 시각의 최솟값(네트워크 지연이 가장 작을 때 ≈ 시계 차이)
    url = f'http://{args.pi}:{args.port}/api/stream?sensors={args.sensors}'
    print(f'[stream] {url} → {session}  (Ctrl+C로 종료)', flush=True)

    def out(name):
        if name not in files:
            files[name] = open(os.path.join(session, name + '.jsonl'), 'a', encoding='utf-8')
        return files[name]

    last_report = time.time()
    try:
        while True:
            try:
                with urllib.request.urlopen(url, timeout=10) as response:
                    print('[stream] 연결됨', flush=True)
                    for line in response:
                        now = time.time()
                        rec = json.loads(line)
                        rec['laptop_rx_unix'] = now
                        kind = rec.get('type')
                        if kind == 'frame':
                            counts[rec['kind']] = counts.get(rec['kind'], 0) + 1
                            out(rec['kind']).write(json.dumps(rec, ensure_ascii=False) + '\n')
                        elif kind in ('hello', 'heartbeat'):
                            offset = now - rec['pi_unix']
                            offset_min = offset if offset_min is None else min(offset_min, offset)
                            rec['offset_min_s'] = offset_min
                            dropped_total = max(dropped_total, rec.get('dropped', 0))
                            out('heartbeat').write(json.dumps(rec) + '\n')
                            for f in files.values():
                                f.flush()
                        if now - last_report >= 5:
                            last_report = now
                            rates = ' '.join(f'{k}={v}' for k, v in sorted(counts.items())) or '프레임 없음'
                            print(f'[stream] 누적 {rates} · Pi에서 버림 {dropped_total} · 시계차(노트북−Pi) {offset_min:+.3f}s', flush=True)
            except (OSError, ValueError) as exc:
                print(f'[stream] 끊김/연결 실패: {exc} — 2초 뒤 다시 연결', flush=True)
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        for f in files.values():
            f.close()
        print(f'[stream] 종료. 저장: {session}  누적 {counts}', flush=True)


def decode(path):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(repo, 'mmWave'))
    import send_mmw
    from diagnostic_decode import decode_frame
    target = path.replace('.jsonl', '') + '_decoded.jsonl'
    n = 0
    with open(path, encoding='utf-8') as src, open(target, 'w', encoding='utf-8') as dst:
        for line in src:
            rec = json.loads(line)
            raw = base64.b64decode(rec['raw_b64'])
            mount = rec.get('mount') or {}
            send_mmw.MOUNT_UPSIDE_DOWN = mount.get('upside_down', True)
            header = send_mmw.parse_frame_header(raw)
            frame = decode_frame(header, raw, mount.get('height_m', 2.5), mount.get('down_tilt_deg', 50.0))
            dst.write(json.dumps({'pi_rx_unix': rec['pi_rx_unix'], 'laptop_rx_unix': rec.get('laptop_rx_unix'),
                                  'frame_number': rec.get('frame_number'), 'points': frame['points'],
                                  'tracks': frame['tracks']}, ensure_ascii=False) + '\n')
            n += 1
    print(f'[decode] {n}프레임 → {target}', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pi', default='192.168.0.4', help='Pi IP (Pi에서 ip -brief address로 확인)')
    ap.add_argument('--port', type=int, default=5012)
    ap.add_argument('--sensors', default='tof1,tof2,mmwave')
    ap.add_argument('--out', default='majubom_stream', help='노트북 저장 폴더')
    ap.add_argument('--decode', metavar='MMWAVE_JSONL', help='받아 둔 mmwave.jsonl을 점군·추적으로 풀어 저장')
    args = ap.parse_args()
    if args.decode:
        decode(args.decode)
    else:
        receive(args)


if __name__ == '__main__':
    main()
