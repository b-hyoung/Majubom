"""
augment_tof.py — TOF 자세 거리벡터 센서 노이즈 증강
====================================================================
입력: ../dataset/tof_<label>_*.jsonl  (한 줄 = 한 센서 프레임)
  {"t","sensor","resolution","distances_mm":[16],"targets":[16],"label"}

SMOTE 대신 '물리적 센서 노이즈 모델'로 증강 (이 데이터는 카운트가 아니라 다양성이
부족 → 노이즈 강건성만 정직하게 올림). 기법:
  · 지터   : 유효 존(>0) 거리에 가우시안 σ=JIT mm (ToF 측정 노이즈)
  · 드롭아웃: 확률 DROP 로 유효 존 하나를 -1(무효)로 (간헐적 반사 소실 모사)
  · -1(무효)은 그대로 보존, targets 도 함께 갱신

출력: ../dataset/aug/tof_<label>_aug.jsonl  (train_posture 의 glob 밖 → 자동 혼입 방지)
  → 학습에 넣을 땐 명시적으로 병합. test 오염 주의(증강은 train 전용).

사용:
  python augment_tof.py --mult 2            # 프레임당 2배 증강본 생성
  python augment_tof.py --mult 3 --jit 8 --drop 0.05
"""
import argparse, glob, json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
DDIR = os.path.join(HERE, "..", "dataset")
ZONES = 64


def aug_frame(dist, targets, rng, jit, drop):
    """유효 존 지터 + 드롭아웃. (-1 은 보존) → (new_dist, new_targets)."""
    nd, nt = list(dist), list(targets)
    for i in range(len(nd)):
        v = nd[i]
        if v is None or v < 0:
            nd[i] = -1; continue
        if rng.random() < drop:              # 존 소실
            nd[i] = -1
            if i < len(nt): nt[i] = 0
        else:
            nd[i] = max(0, round(v + rng.gauss(0, jit)))   # 지터
    return nd, nt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mult", type=int, default=2, help="프레임당 증강본 개수")
    ap.add_argument("--jit", type=float, default=8.0, help="거리 지터 σ (mm)")
    ap.add_argument("--drop", type=float, default=0.05, help="존 드롭아웃 확률")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    files = [f for f in sorted(glob.glob(os.path.join(DDIR, "*.jsonl")))
             if "filtered" not in os.path.basename(f) and "/aug/" not in f.replace("\\", "/")]
    outdir = os.path.join(DDIR, "aug")
    os.makedirs(outdir, exist_ok=True)

    per_label_out = {}   # label -> file handle
    counts = {}
    try:
        for f in files:
            for ln in open(f, encoding="utf-8"):
                o = json.loads(ln)
                lab = o["label"]
                if lab not in per_label_out:
                    p = os.path.join(outdir, f"tof_{lab}_aug.jsonl")
                    per_label_out[lab] = open(p, "w", encoding="utf-8")
                    counts[lab] = 0
                dist = (list(o["distances_mm"]) + [-1] * ZONES)[:ZONES]
                tgt = (list(o.get("targets", [])) + [0] * ZONES)[:ZONES]
                for k in range(args.mult):
                    nd, nt = aug_frame(dist, tgt, rng, args.jit, args.drop)
                    rec = {"t": o["t"], "sensor": o["sensor"],
                           "resolution": o.get("resolution", "4x4"),
                           "distances_mm": nd, "targets": nt,
                           "label": lab, "aug": True}
                    per_label_out[lab].write(json.dumps(rec, ensure_ascii=False) + "\n")
                    counts[lab] += 1
    finally:
        for h in per_label_out.values():
            h.close()

    print(f"증강 출력 → {outdir}")
    print("클래스별 증강 프레임 수:", {k: counts[k] for k in sorted(counts)})
    print("⚠️ 학습에 병합 시 aug 는 train 전용. test 오염 주의.")


if __name__ == "__main__":
    main()
