"""First posture model on camera + mmWave matched data, tested on recordings it never saw (2026-11-08).

Input: the matched.csv files written by tools/match_cam_mmw.py, one folder per recording
(camera-pose/recordings/cam-mmw/<mmWave session>/matched.csv).

Rows: one per camera-A frame (A_PiC920). Camera-B joints are attached from the nearest B frame
within 150 ms, so every model is scored on exactly the same frames.
Dropped: the 3 s transition after each cue, segments marked 제외, frames outside any cue.

Models (scikit-learn HistGradientBoostingClassifier; it handles missing joints / mmWave natively):
  mmwave   mmWave person track only (z, head_z, speed, vz, points, bed distance, x, y)
  cam_a    camera A MediaPipe joints + derived torso/box features
  cam_b    camera B, same features
  fusion   all of the above
plus the cameras' existing rule verdicts (stable_posture label) mapped to the same 5 postures.

Evaluation: leave-one-recording-out. Each recording is predicted by a model trained on the others.
Frames inside one recording are near-duplicates, so a random frame split would overstate accuracy.

Usage:
  python tools/train_cam_mmw_baseline.py camera-pose/recordings/cam-mmw --out <report folder>
"""
import argparse
import bisect
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, recall_score

POSTURES = ["섬", "침대 위 앉음", "걸터앉음", "누움", "옆누움"]
RULE_TO_POSTURE = {"standing": "섬", "sitting": "침대 위 앉음", "edge_sitting": "걸터앉음",
                   "lying": "누움", "supine": "누움", "side": "옆누움"}
JOINTS = ["nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner", "right_eye",
          "right_eye_outer", "left_ear", "right_ear", "mouth_left", "mouth_right", "left_shoulder",
          "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_pinky",
          "right_pinky", "left_index", "right_index", "left_thumb", "right_thumb", "left_hip", "right_hip",
          "left_knee", "right_knee", "left_ankle", "right_ankle", "left_heel", "right_heel",
          "left_foot_index", "right_foot_index"]
MMW = ["mmw_z", "mmw_head_z", "mmw_speed", "mmw_vz", "mmw_npts", "mmw_bed_dist", "mmw_x", "mmw_y"]
PAIR_S = 0.15


def num(v):
    return float(v) if v not in ("", None) else math.nan


def seconds(hms):
    t = datetime.strptime(hms, "%H:%M:%S.%f")
    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6


def mid(r, a, b, axis):
    vals = [num(r[f"{j}_{axis}"]) for j in (a, b)]
    vals = [v for v in vals if not math.isnan(v)]
    return sum(vals) / len(vals) if vals else math.nan


def camera_features(r):
    """66 raw joint coordinates + derived torso / box features (NaN when the person is not seen)."""
    raw = [num(r[f"{j}_{a}"]) for j in JOINTS for a in ("x", "y")]
    xs = [v for v in raw[0::2] if not math.isnan(v)]
    ys = [v for v in raw[1::2] if not math.isnan(v)]
    sx, sy = mid(r, "left_shoulder", "right_shoulder", "x"), mid(r, "left_shoulder", "right_shoulder", "y")
    hx, hy = mid(r, "left_hip", "right_hip", "x"), mid(r, "left_hip", "right_hip", "y")
    kx, ky = mid(r, "left_knee", "right_knee", "x"), mid(r, "left_knee", "right_knee", "y")
    dx, dy = hx - sx, hy - sy
    w = max(xs) - min(xs) if xs else math.nan
    h = max(ys) - min(ys) if ys else math.nan
    derived = [len(xs), dx, dy, math.hypot(dx, dy), math.atan2(dy, dx) if not math.isnan(dx + dy) else math.nan,
               ky - hy, kx - hx, w, h, w / h if h else math.nan, num(r["nose_y"]) - hy]
    return raw + derived


def camera_names():
    names = [f"{j}_{a}" for j in JOINTS for a in ("x", "y")]
    return names + ["n_joints", "torso_dx", "torso_dy", "torso_len", "torso_angle", "knee_dy", "knee_dx",
                    "box_w", "box_h", "box_aspect", "nose_minus_hip_y"]


def load_recording(folder):
    a_rows, b_rows = [], []
    for r in csv.DictReader(open(folder / "matched.csv", encoding="utf-8-sig")):
        (a_rows if r["camera"] == "A_PiC920" else b_rows).append(r)
    b_rows.sort(key=lambda r: seconds(r["time"]))
    b_t = [seconds(r["time"]) for r in b_rows]
    samples = []
    for r in a_rows:
        if r["truth"] not in POSTURES or r["transition"] == "1":
            continue
        t = seconds(r["time"])
        i = bisect.bisect_left(b_t, t)
        near = [k for k in (i - 1, i) if 0 <= k < len(b_rows) and abs(b_t[k] - t) <= PAIR_S]
        b = b_rows[min(near, key=lambda k: abs(b_t[k] - t))] if near else None
        nan_cam = [math.nan] * len(camera_names())
        samples.append(dict(
            truth=r["truth"],
            mmwave=[num(r[c]) for c in MMW],
            cam_a=camera_features(r),
            cam_b=camera_features(b) if b else nan_cam,
            rule_a=RULE_TO_POSTURE.get(r["cam_stable"]),
            rule_b=RULE_TO_POSTURE.get(b["cam_stable"]) if b else None,
        ))
    return samples


def features(samples, model):
    if model == "fusion":
        return np.array([s["mmwave"] + s["cam_a"] + s["cam_b"] for s in samples], dtype=float)
    return np.array([s[model] for s in samples], dtype=float)


def scores(y, p):
    y, p = list(y), [x if x is not None else "판정 없음" for x in p]
    present = [c for c in POSTURES if c in y]
    rec = recall_score(y, p, labels=present, average=None, zero_division=0)
    return dict(accuracy=round(accuracy_score(y, p), 3),
                macro_f1=round(f1_score(y, p, labels=present, average="macro", zero_division=0), 3),
                recall={c: round(float(v), 3) for c, v in zip(present, rec)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="folder with one sub-folder (matched.csv) per recording")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    recordings = {p.name: load_recording(p) for p in sorted(args.root.iterdir()) if (p / "matched.csv").exists()}
    recordings = {k: v for k, v in recordings.items() if v}
    models = ["mmwave", "cam_a", "cam_b", "fusion"]
    pooled = defaultdict(lambda: ([], []))
    per_rec = defaultdict(dict)
    counts = {k: dict(Counter(s["truth"] for s in v)) for k, v in recordings.items()}

    for held, test in recordings.items():
        train = [s for k, v in recordings.items() if k != held for s in v]
        y_train = [s["truth"] for s in train]
        y_test = [s["truth"] for s in test]
        for m in models:
            clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, l2_regularization=1.0,
                                                 class_weight="balanced", random_state=args.seed)
            x_train, x_test = features(train, m), features(test, m)
            keep = ~np.all(np.isnan(x_train), axis=0)   # joints the camera never reports are all-NaN
            clf.fit(x_train[:, keep], y_train)
            pred = list(clf.predict(x_test[:, keep]))
            per_rec[held][m] = scores(y_test, pred)["accuracy"]
            pooled[m][0].extend(y_test)
            pooled[m][1].extend(pred)
        for rule in ("rule_a", "rule_b"):
            pred = [s[rule] for s in test]
            per_rec[held][rule] = scores(y_test, pred)["accuracy"]
            pooled[rule][0].extend(y_test)
            pooled[rule][1].extend(pred)

    names = models + ["rule_a", "rule_b"]
    result = dict(recordings=counts, per_recording_accuracy=per_rec,
                  pooled={m: scores(*pooled[m]) for m in names},
                  confusion={m: confusion_matrix(pooled[m][0], [p or "판정 없음" for p in pooled[m][1]],
                                                 labels=POSTURES + ["판정 없음"]).tolist() for m in names},
                  labels=POSTURES + ["판정 없음"])
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    title = {"mmwave": "mmWave만", "cam_a": "카메라 A만", "cam_b": "카메라 B만", "fusion": "mmWave+A+B",
             "rule_a": "A 규칙 판정(현재)", "rule_b": "B 규칙 판정(현재)"}
    lines = ["# 카메라+mmWave 첫 자세 모델 (녹화 하나씩 빼고 시험)", "",
             f"녹화 {len(recordings)}개, 시험 프레임 {sum(len(v) for v in recordings.values())}개 (카메라 A 기준, 바꾸는 동작 3초·제외 구간 뺌)", "",
             "| 모델 | 정확도 | macro F1 | " + " | ".join(POSTURES) + " |", "|---|---|---|" + "---|" * len(POSTURES)]
    for m in names:
        s = result["pooled"][m]
        lines.append(f"| {title[m]} | {s['accuracy']:.2f} | {s['macro_f1']:.2f} | " +
                     " | ".join(f"{s['recall'].get(c, float('nan')):.2f}" for c in POSTURES) + " |")
    lines += ["", "자세 칸은 그 자세를 맞힌 비율(recall). 규칙 판정이 '없음/기타'면 틀린 것으로 셈.", "",
              "## 녹화별 정확도 (그 녹화를 빼고 학습)", "", "| 녹화 | 프레임 | " + " | ".join(title[m] for m in names) + " |",
              "|---|---|" + "---|" * len(names)]
    for k in recordings:
        lines.append(f"| {k} | {len(recordings[k])} | " + " | ".join(f"{per_rec[k][m]:.2f}" for m in names) + " |")
    lines += ["", "## 혼동 행렬: mmWave+A+B (행=실제, 열=예측)", "", "| 실제 \\ 예측 | " + " | ".join(result["labels"]) + " |",
              "|---|" + "---|" * len(result["labels"])]
    for lab, row in zip(POSTURES, result["confusion"]["fusion"]):
        lines.append(f"| {lab} | " + " | ".join(str(v) for v in row) + " |")
    report = "\n".join(lines) + "\n"
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
