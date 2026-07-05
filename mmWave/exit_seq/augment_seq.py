"""
augment_seq.py — mmWave 이탈 점군 시퀀스 증강 (소수 클래스 균형)
====================================================================
원본 seq_dataset.jsonl.gz 는 클래스 불균형이 큼:
  empty 306 / supine 128 / sitting 73 / side_right 38 / side_left 24
→ 물리적으로 타당한 '강체 변환 + 노이즈'로 소수 클래스를 목표 개수까지 늘림.

기본 = '기하-안전' 증강 (한 시퀀스 = 30프레임×64점×[x,y,z,doppler]):
  · 타임워프  : 프레임축 임의 배속 리샘플 (이탈 동작 속도차) ← 실측상 F1 최다 기여
  · 점 지터   : 각 점 x,y,z 에 가우시안 σ=JIT m (센서 노이즈)
  · 패딩 점([0,0,0,0])은 그대로 보존 (빈 프레임 표시라 건드리면 안 됨)

⚠️ 회전/스케일은 기본 OFF. 센서·침대가 '고정'이라 yaw 회전/스케일은 물리적으로
   불가능한 배치를 만들어 오히려 성능을 떨어뜨림(analyze_aug.py 실측: F1 -7).
   generic 점군용으로 굳이 쓰려면 --rigid.

⚠️ 정직 평가: 증강 샘플은 학습(train)에만 넣어야 함. 출력에 "aug":true 를 달아두니,
   시간분할 평가 시 test 에서 aug==true 는 제외할 것 (안 그러면 낙관적으로 부풂).

사용:
  python augment_seq.py                      # 각 소수클래스를 200개까지 증강
  python augment_seq.py --target 150 --seed 0
  python augment_seq.py --in seq_dataset.jsonl.gz --out seq_dataset_aug.jsonl.gz
"""
import argparse, gzip, json, math, os, random


def _open(path, mode="rt"):
    return gzip.open(path, mode, encoding="utf-8") if path.endswith(".gz") \
        else open(path, mode, encoding="utf-8")


def is_pad(p):
    return p[0] == 0.0 and p[1] == 0.0 and p[2] == 0.0 and p[3] == 0.0


def time_warp(seq, rng, lo=0.8, hi=1.25):
    """프레임축을 임의 배속(f)으로 리샘플 — 이탈 동작 속도차 모사(고정 30프레임)."""
    f = rng.uniform(lo, hi)
    L = len(seq)
    src = [min(L - 1, max(0, round(i * f))) for i in range(L)]
    return [seq[i] for i in src]


def augment_seq(seq, rng, jit, rot_deg, scale_lo, scale_hi, shift):
    """한 시퀀스에 강체변환+지터 1회 적용해 새 시퀀스 반환."""
    ang = math.radians(rng.uniform(-rot_deg, rot_deg))
    ca, sa = math.cos(ang), math.sin(ang)
    s = rng.uniform(scale_lo, scale_hi)
    dx, dy = rng.uniform(-shift, shift), rng.uniform(-shift, shift)
    out = []
    for frame in seq:
        nf = []
        for p in frame:
            if is_pad(p):
                nf.append([0.0, 0.0, 0.0, 0.0]); continue
            x, y, z, d = p
            x, y, z = x * s, y * s, z * s                 # 스케일
            xr = x * ca - y * sa + dx                      # yaw 회전 + 이동
            yr = x * sa + y * ca + dy
            xr += rng.gauss(0, jit); yr += rng.gauss(0, jit); z += rng.gauss(0, jit)
            nf.append([round(xr, 3), round(yr, 3), round(z, 3),
                       round(d + rng.gauss(0, 0.02), 3)])
        out.append(nf)
    return out


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--in", dest="inp", default=os.path.join(here, "seq_dataset.jsonl.gz"))
    ap.add_argument("--out", default=os.path.join(here, "seq_dataset_aug.jsonl.gz"))
    ap.add_argument("--target", type=int, default=200, help="클래스별 목표 개수(원본+증강)")
    ap.add_argument("--jit", type=float, default=0.02, help="점 지터 σ (m)")
    ap.add_argument("--rigid", action="store_true", help="회전+스케일+이동 켜기(고정셋업엔 비권장)")
    ap.add_argument("--rot", type=float, default=12.0, help="yaw 회전 최대 (deg, --rigid 시)")
    ap.add_argument("--scale-lo", type=float, default=0.95)
    ap.add_argument("--scale-hi", type=float, default=1.05)
    ap.add_argument("--shift", type=float, default=0.10, help="xy 평행이동 최대 (m, --rigid 시)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    # 로드 + 클래스별 분류
    recs = []
    by_label = {}
    with _open(args.inp) as f:
        for ln in f:
            o = json.loads(ln)
            lab = o.get("tof_posture", o.get("label"))
            recs.append(o)
            by_label.setdefault(lab, []).append(o)
    print("원본 클래스 분포:", {k: len(v) for k, v in sorted(by_label.items())})

    # 출력: 원본 전부 + 소수클래스 증강
    n_aug = 0
    with _open(args.out, "wt") as w:
        for o in recs:                       # 원본 그대로 (aug 없음)
            w.write(json.dumps(o, ensure_ascii=False) + "\n")
        for lab, items in sorted(by_label.items()):
            need = args.target - len(items)
            for i in range(max(0, need)):
                base = items[rng.randrange(len(items))]
                aug = {k: base[k] for k in base if k != "seq"}
                if args.rigid:
                    seq = augment_seq(base["seq"], rng, args.jit, args.rot,
                                      args.scale_lo, args.scale_hi, args.shift)
                else:                                   # 기본: 타임워프 + 지터
                    seq = augment_seq(time_warp(base["seq"], rng), rng, args.jit,
                                      0.0, 1.0, 1.0, 0.0)
                aug["seq"] = seq
                aug["aug"] = True
                w.write(json.dumps(aug, ensure_ascii=False) + "\n")
                n_aug += 1

    final = {k: max(len(v), args.target) if len(v) < args.target else len(v)
             for k, v in by_label.items()}
    print(f"증강 {n_aug}개 생성 → {os.path.basename(args.out)}")
    print("증강 후 분포:", {k: v for k, v in sorted(final.items())})
    print("⚠️ 학습 시 aug==true 는 train 에만! test 에서 제외할 것.")


if __name__ == "__main__":
    main()
