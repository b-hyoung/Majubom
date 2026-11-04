#!/usr/bin/env python3
"""실제 녹화(dataset_v3)로 체형·누운 위치가 다른 '합성 참가자'를 만든다(2026-11-04, 방법 1: 실측 변형).

왜: 참가자가 S01·S02 두 명뿐이라 한 명으로 학습해 다른 한 명을 맞히는 사람 단위 시험에서
  누운 자세를 거의 못 맞힌다(S02→S01 51%). 사람마다 다른 몸 두께·크기·누운 위치를 흉내 내 학습에 보탠다.
어떻게(프레임마다, 센서마다):
  몸 = 빈 침대 기준 − 현재 거리 > BODY_MM 인 칸(+ 기준은 유효한데 현재 측정 실패인 칸 = 몸이 만든 구멍).
  몸이 없던 자리는 기준 + 잡음으로 채우고, 몸 깊이에 두께 배율을 곱해 칸 단위로 옮긴(shift) 자리에 다시 얹는다.
  '큰 체형'은 몸 가장자리 한 칸을 이웃 깊이의 60%로 넓힌다. 잡음 8mm, 측정 실패 2% 무작위.
  배율·이동은 합성 세션마다 한 번 정해 그 세션 내내 같다(한 사람이 한 자리에 누운 것처럼).
한계: 센서가 비스듬히 보므로 실제로는 몸이 두꺼워지면 가려지는 칸도 바뀌는데 이것은 흉내 내지 못한다.
  두 센서의 칸 방향이 침대 어느 쪽인지 확인 전이라 이동은 센서마다 따로 뽑는다. 빈 침대 세션은 만들지 않는다.
  합성 자료는 학습 보조 전용이다 — 시험·정확도 보고에는 실제 참가자만 쓴다.

사용:
  python3 TOF/ml/synth_subjects.py            # dataset_v3 → dataset_v3_synth (S03syn←S01, S04syn←S02)
출력 행에는 synthetic: true, source_session, synth 매개변수가 들어 있다. 원본은 읽기만 한다.
"""
import argparse, csv, json, os, random

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOF = os.path.dirname(HERE)
BODY_MM = 40          # 기준보다 이만큼 가까우면 몸 칸
NOISE_MM = 8
DROPOUT = 0.02
# 합성 참가자: 원본 참가자, 몸 두께 배율 범위, 크게 넓히기
SUBJECTS = {"S03syn": dict(source="S01", scale=(0.75, 0.95), grow=False),
            "S04syn": dict(source="S02", scale=(1.05, 1.25), grow=True)}


def _shift(a, dr, dc, fill):
    out = np.full_like(a, fill)
    rs, rd = (slice(0, 8 - dr), slice(dr, 8)) if dr >= 0 else (slice(-dr, 8), slice(0, 8 + dr))
    cs, cd = (slice(0, 8 - dc), slice(dc, 8)) if dc >= 0 else (slice(-dc, 8), slice(0, 8 + dc))
    out[rd, cd] = a[rs, cs]
    return out


def transform(raw, base, p, rng):
    """raw·base: 64칸(mm, -1 실패). p: scale, dr, dc, grow. → 합성 64칸(int, -1 실패)."""
    r, b = np.asarray(raw, float).reshape(8, 8), np.asarray(base, float).reshape(8, 8)
    rv, bv = r > 0, b > 0
    depth = np.where(rv & bv, b - r, 0.0)
    body = depth > BODY_MM
    hole = bv & ~rv                                    # 기준엔 있었는데 지금 측정 실패(몸·반사 문제)
    depth = np.where(body, depth, 0.0) * p["scale"]
    if p["grow"]:
        nb = np.zeros_like(depth)
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = np.maximum(nb, _shift(depth, dr, dc, 0.0))
        edge = (depth == 0) & (nb > 0)
        depth = np.where(edge, 0.6 * nb, depth)
    depth = _shift(depth, p["dr"], p["dc"], 0.0)
    hole = _shift(hole.astype(float), p["dr"], p["dc"], 0.0) > 0
    # 바탕: 몸이 없던 칸은 현재 값, 몸·구멍이 있던 칸은 기준값(빈 침대)으로
    bg = np.where(body | (bv & ~rv), b, r)
    out = np.where((depth > 0) & bv, np.maximum(b - depth, 100.0), bg)
    out = np.where(hole, -1.0, out)
    ok = out > 0
    out = np.where(ok, out + rng.normal(0, NOISE_MM, out.shape), out)
    out = np.where(ok & (rng.random(out.shape) < DROPOUT), -1.0, out)
    return [int(round(v)) if v > 0 else -1 for v in out.reshape(64)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(TOF, "dataset_v3"))
    ap.add_argument("--sessions", default=None, help="원본 세션 목록 CSV(기본: src/sessions.csv)")
    ap.add_argument("--baseline", default=os.path.join(HERE, "posture_cnn_bedC_20261104", "training_baseline.json"))
    ap.add_argument("--out", default=os.path.join(TOF, "dataset_v3_synth"))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if os.path.exists(args.out) and os.listdir(args.out):
        raise SystemExit(f"{args.out}가 비어 있지 않음 — 덮어쓰지 않는다. 다른 --out을 쓰세요.")
    os.makedirs(args.out, exist_ok=True)
    base = json.load(open(args.baseline, encoding="utf-8"))
    rows = list(csv.DictReader(open(args.sessions or os.path.join(args.src, "sessions.csv"), encoding="utf-8-sig")))
    rng, pick = np.random.default_rng(args.seed), random.Random(args.seed)
    out_rows = []
    for name, cfg in SUBJECTS.items():
        for m in rows:
            if m["subject"] != cfg["source"] or m["label"] == "empty":
                continue
            p = {"scale": round(pick.uniform(*cfg["scale"]), 3), "grow": cfg["grow"],
                 "shift": {s: (pick.choice((-1, 0, 1)), pick.choice((-1, 0, 1))) for s in ("tof1", "tof2")}}
            sess = f"{name}_{m['label']}_from_{m['session']}"
            cond = f"합성_{m['cond']}"
            fn = f"tof_{sess}.jsonl"
            n = {"tof1": 0, "tof2": 0}
            with open(os.path.join(args.src, m["file"]), encoding="utf-8-sig") as fi, \
                 open(os.path.join(args.out, fn), "w", encoding="utf-8") as fo:
                for line in fi:
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    s = d["sensor"]
                    pp = dict(scale=p["scale"], grow=p["grow"], dr=p["shift"][s][0], dc=p["shift"][s][1])
                    d["distances_mm"] = transform(d["distances_mm"], base[s], pp, rng)
                    d.update(session=sess, subject=name, cond=cond, synthetic=True,
                             source_session=m["session"], synth=p)
                    d.pop("targets", None)
                    fo.write(json.dumps(d, ensure_ascii=False) + "\n")
                    n[s] += 1
            out_rows.append({**m, "session": sess, "subject": name, "cond": cond, "file": fn,
                             "tof1_frames": n["tof1"], "tof2_frames": n["tof2"]})
    with open(os.path.join(args.out, "sessions.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"합성 세션 {len(out_rows)}개 → {args.out}")


if __name__ == "__main__":
    main()
