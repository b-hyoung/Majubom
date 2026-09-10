"""
analyze_4x4_backup.py — 2026-09-10 작업
====================================================================================
사용자가 확보한 대용량 원본 백업(server/majubom.db.4x4bak.gz, 압축 34MB → 해제 시 약 145MB,
tof_readings 866,594행, 2026-07-03~07-07 4일간 실제 운영 로그)을 분석한다.

이 DB는 라벨링된 자세 데이터셋(TOF/dataset/tof_<label>_*.jsonl, 5class supine/side_left/
side_right/sitting/empty)과는 다른 원본이다 — 자세 라벨이 없고, 대신 서버가 실시간으로 계산해
저장한 in_bed(재실 0/1)·occupied(점유 존 개수 0~16)만 있다. 그래서 "자세 분류"가 아니라
아래 3가지를 한다:
  1) 데이터 품질/센서 신뢰도 기술통계 — 대용량 실운영 로그에서만 확인 가능한 것
  2) in_bed=0(빈 침대) 구간만 골라 존별 baseline(평균·표준편차)을 훨씬 큰 표본으로 재계산
  3) in_bed 예측 모델(RandomForest) — 원시 16존 거리값만으로 재실 여부를 배우게 해서,
     지금의 고정 임계값 규칙(PRESENCE_DELTA_MM=150mm, PRESENCE_MIN_ZONES=4)과 비교

정직하게 명시: in_bed 라벨 자체가 사람이 확인한 정답이 아니라 그 규칙이 실시간으로 계산해
저장해둔 값이다. 따라서 여기서 나오는 "정확도"는 "모델이 그 규칙을 원시값만으로 재현할 수
있는가"를 보는 것이지, 임상적으로 검증된 새로운 정답을 만드는 게 아니다.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score

HERE = Path(__file__).resolve().parent
DB_PATH = HERE.parent.parent / "server" / "majubom_4x4bak.db"
OUT_JSON = HERE / "backup_4x4_analysis.json"
OUT_MODEL_TOF1 = HERE / "in_bed_model_tof1.joblib"
OUT_MODEL_TOF2 = HERE / "in_bed_model_tof2.joblib"

D_COLS = [f"d{i}" for i in range(16)]
T_COLS = [f"t{i}" for i in range(16)]


def load_rows(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute(
        f"SELECT timestamp, sensor, resolution, {','.join(D_COLS)}, {','.join(T_COLS)}, "
        f"min_mm, valid_zones, occupied, in_bed FROM tof_readings ORDER BY timestamp"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def quality_report(rows: list[dict]) -> dict:
    by_sensor: dict[str, dict] = {}
    for r in rows:
        s = r["sensor"]
        by_sensor.setdefault(s, {"n": 0, "valid_zones_sum": 0, "occupied_sum": 0,
                                  "in_bed_1": 0, "resolutions": {}})
        b = by_sensor[s]
        b["n"] += 1
        b["valid_zones_sum"] += r["valid_zones"] or 0
        b["occupied_sum"] += r["occupied"] or 0
        b["in_bed_1"] += 1 if r["in_bed"] else 0
        res = r["resolution"] or "?"
        b["resolutions"][res] = b["resolutions"].get(res, 0) + 1

    report = {"total_rows": len(rows),
              "time_range": [rows[0]["timestamp"], rows[-1]["timestamp"]] if rows else None,
              "by_sensor": {}}
    for s, b in by_sensor.items():
        report["by_sensor"][s] = {
            "n_frames": b["n"],
            "avg_valid_zones_of_16": round(b["valid_zones_sum"] / b["n"], 2),
            "avg_occupied_zones": round(b["occupied_sum"] / b["n"], 2),
            "in_bed_ratio": round(b["in_bed_1"] / b["n"], 4),
            "resolution_counts": b["resolutions"],
        }
    return report


def empty_bed_baseline(rows: list[dict]) -> dict:
    """in_bed=0(빈 침대로 판정된) 프레임만 골라 센서별 존별 평균·표준편차 계산.
    -1(무효 측정)은 제외하고, 유효 표본수가 30 미만인 존은 신뢰 불가로 표시."""
    per_sensor: dict[str, dict] = {}
    for r in rows:
        if r["in_bed"]:
            continue
        s = r["sensor"]
        per_sensor.setdefault(s, {f"d{i}": [] for i in range(16)})
        for i in range(16):
            v = r[f"d{i}"]
            if v is not None and v > 0:
                per_sensor[s][f"d{i}"].append(v)

    result = {}
    for s, zones in per_sensor.items():
        result[s] = {}
        for zk, vals in zones.items():
            if len(vals) >= 30:
                arr = np.array(vals, dtype=float)
                result[s][zk] = {"n": len(vals), "mean_mm": round(float(arr.mean()), 1),
                                  "std_mm": round(float(arr.std()), 1)}
            else:
                result[s][zk] = {"n": len(vals), "note": "표본 30개 미만 — 신뢰 불가"}
    return result


def train_in_bed_model(rows: list[dict], sensor: str) -> dict:
    """해당 센서 프레임만으로 in_bed 예측 RandomForest 학습. 같은 연속 세션이라
    leave-one-person-out은 불가능하므로, 시간순 80/20 분할(앞 80% 학습·뒤 20% 시험)로
    '같은 순간의 프레임을 학습·시험 양쪽에 섞어 넣어 생기는 과대평가'를 최소화한다."""
    sub = [r for r in rows if r["sensor"] == sensor]
    if len(sub) < 200:
        return {"success": False, "message": f"{sensor} 프레임이 너무 적음({len(sub)})"}

    X = np.array([[(r[c] if (r[c] is not None and r[c] > 0) else 0) for c in D_COLS] for r in sub],
                 dtype=float)
    y = np.array([int(r["in_bed"] or 0) for r in sub])

    n = len(sub)
    split = int(n * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        return {"success": False, "message": f"{sensor} 학습/시험 세트 중 한쪽에 클래스가 1종류뿐(시간 분할 특성상 발생 가능)"}

    clf = RandomForestClassifier(n_estimators=200, max_depth=12, random_state=42, n_jobs=-1)
    clf.fit(X_train, y_train)
    pred = clf.predict(X_test)

    cm = confusion_matrix(y_test, pred).tolist()
    result = {
        "success": True, "sensor": sensor,
        "n_train": len(X_train), "n_test": len(X_test),
        "accuracy": round(accuracy_score(y_test, pred), 4),
        "precision": round(precision_score(y_test, pred, zero_division=0), 4),
        "recall": round(recall_score(y_test, pred, zero_division=0), 4),
        "f1": round(f1_score(y_test, pred, zero_division=0), 4),
        "confusion_matrix": cm,  # [[TN,FP],[FN,TP]]
        "feature_importance_top5": sorted(
            zip(D_COLS, [round(float(x), 4) for x in clf.feature_importances_]),
            key=lambda kv: -kv[1])[:5],
    }
    model_path = OUT_MODEL_TOF1 if sensor == "tof1" else OUT_MODEL_TOF2
    joblib.dump(clf, model_path)
    result["model_path"] = str(model_path.relative_to(HERE.parent.parent))
    return result


def main():
    print("=== analyze_4x4_backup.py — 866,594행 원본 백업 분석 ===\n")
    con = sqlite3.connect(str(DB_PATH))
    rows = load_rows(con)
    con.close()
    print(f"[로드 완료] {len(rows)}행")

    quality = quality_report(rows)
    print("\n[데이터 품질]")
    print(json.dumps(quality, ensure_ascii=False, indent=2))

    baseline = empty_bed_baseline(rows)
    print("\n[빈 침대 baseline 표본수] tof1:",
          sum(1 for r in rows if r["sensor"] == "tof1" and not r["in_bed"]),
          "tof2:", sum(1 for r in rows if r["sensor"] == "tof2" and not r["in_bed"]))

    model_tof1 = train_in_bed_model(rows, "tof1")
    model_tof2 = train_in_bed_model(rows, "tof2")
    print("\n[in_bed 예측 모델 — tof1]", {k: v for k, v in model_tof1.items() if k != "feature_importance_top5"})
    print("[in_bed 예측 모델 — tof2]", {k: v for k, v in model_tof2.items() if k != "feature_importance_top5"})

    out = {
        "source_db": "server/majubom.db.4x4bak.gz (decompressed 145MB, 866,594 tof_readings)",
        "quality_report": quality,
        "empty_bed_baseline": baseline,
        "in_bed_model": {"tof1": model_tof1, "tof2": model_tof2},
        "caveat": "in_bed 라벨은 사람이 확인한 정답이 아니라 서버 임계값 규칙(PRESENCE_DELTA_MM=150mm, "
                  "PRESENCE_MIN_ZONES=4)이 실시간 계산해 저장한 값 — 정확도는 '모델이 그 규칙을 원시값만으로 "
                  "재현하는 능력'을 뜻하며 새로운 임상적 정답을 만든 것이 아님.",
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[저장 완료] {OUT_JSON}")


if __name__ == "__main__":
    main()
