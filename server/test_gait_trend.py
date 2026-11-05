"""gait_trend.py — 합성 보행 기록으로 추세 예측·저하 신호·과거 재현 검증을 확인한다."""
import random
import sqlite3
import unittest
from datetime import datetime, timedelta

import gait_trend as G

NOW = datetime(2026, 11, 30, 12, 0, 0)


def make_db(day_values, per_day=20, noise=0.08, seed=1, extra=None):
    """day_values: [(며칠 전, 그날 실제 평균 속도 m/s)] → 하루 per_day개의 6초 구간(보폭 = 속도×1.1)."""
    rnd = random.Random(seed)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE mmw_readings (timestamp TEXT, target_id TEXT, speed REAL, stride_length REAL,
                    reliable INTEGER, walking INTEGER, patient_locked INTEGER)""")
    for days_ago, mean in day_values:
        day = NOW - timedelta(days=days_ago)
        for i in range(per_day):
            ts = (day.replace(hour=9) + timedelta(minutes=10 * i)).isoformat(timespec="seconds")
            v = mean + rnd.gauss(0, noise)
            conn.execute("INSERT INTO mmw_readings VALUES (?,?,?,?,?,?,?)", (ts, "t", v, v * 1.1, 1, 1, 1))
    for row in extra or []:
        conn.execute("INSERT INTO mmw_readings VALUES (?,?,?,?,?,?,?)", row)
    return conn


class GaitTrendTests(unittest.TestCase):
    def test_steady_walking_is_normal(self):
        conn = make_db([(d, 0.60) for d in range(30, 0, -1)])
        r = G.assess(conn, "t", now=NOW)
        self.assertEqual((r["status"], r["level"]), ("ok", "normal"))
        self.assertAlmostEqual(r["metrics"]["speed"]["current"], 0.60, delta=0.03)

    def test_fast_decline_is_caught(self):
        # 마지막 10일 동안 하루 1 cm/s씩 감소 → 7일 누적 −7 cm/s (Phillips 경고 기준 5.1 cm/s 초과)
        vals = [(d, 0.60) for d in range(30, 10, -1)] + [(d, 0.60 - 0.01 * (11 - d)) for d in range(10, 0, -1)]
        levels = [G.assess(make_db(vals, seed=s), "t", now=NOW)["metrics"]["speed"] for s in range(20)]
        self.assertGreaterEqual(sum(m["level"] != "normal" for m in levels), 19)     # 주의 이상
        self.assertGreaterEqual(sum(m["level"] == "warning" for m in levels), 12)    # 경고(합성 시험 29/40 수준)
        self.assertLess(levels[0]["forecast7"]["mean"], levels[0]["current"])

    def test_steady_walking_rarely_alarms(self):
        flat = [(d, 0.60) for d in range(30, 0, -1)]
        alarms = sum(G.assess(make_db(flat, seed=s), "t", now=NOW)["level"] != "normal" for s in range(20))
        self.assertLessEqual(alarms, 1)

    def test_too_few_days_is_insufficient(self):
        r = G.assess(make_db([(d, 0.6) for d in range(10, 0, -1)]), "t", now=NOW)
        self.assertEqual(r["status"], "insufficient")
        self.assertIsNone(r["level"])

    def test_non_walking_and_unlocked_windows_are_ignored(self):
        ts = (NOW - timedelta(days=1)).replace(hour=15).isoformat(timespec="seconds")
        extra = [(ts, "t", 0.05, 0.0, 1, 0, 1)] * 30 + [(ts, "t", 1.9, 2.0, 1, 1, 0)] * 30   # 서 있기·다른 사람
        series = G.daily_series(make_db([(1, 0.6)], extra=extra), "t", now=NOW)
        self.assertAlmostEqual(series[-1]["speed"], 0.6, delta=0.05)
        self.assertEqual(series[-1]["n"], 20)

    def test_days_with_few_windows_are_skipped(self):
        series = G.daily_series(make_db([(2, 0.6)], per_day=3), "t", now=NOW)
        self.assertEqual(series, [])

    def test_backtest_reports_error_and_interval_coverage(self):
        vals = [(d, 0.70 - 0.002 * (40 - d)) for d in range(40, 0, -1)]
        series = G.daily_series(make_db(vals), "t", days=60, now=NOW)
        bt = G.backtest(series, "speed", horizon=7)
        self.assertGreater(bt["n"], 10)
        self.assertLess(bt["mae"], 0.05)          # 7일 앞 예측 평균 오차 5 cm/s 미만
        self.assertGreaterEqual(bt["coverage80"], 0.6)

    def test_stride_decline_is_not_judged(self):
        # C안: 보폭 추세는 판정에서 뺀다. 속도는 그대로인데 보폭만 크게 줄어도 정상.
        conn = make_db([(d, 0.60) for d in range(30, 0, -1)])
        start = (NOW - timedelta(days=10)).isoformat(timespec="seconds")
        conn.execute("UPDATE mmw_readings SET stride_length = 0.3 WHERE timestamp >= ?", (start,))
        r = G.assess(conn, "t", now=NOW)
        self.assertEqual(r["level"], "normal")
        self.assertNotIn("stride", r["metrics"])

    def test_prior_from_admission_records_is_used(self):
        conn = make_db([(d, 0.45) for d in range(20, 0, -1)])
        r = G.assess(conn, "t", prior={"metrics": {"speed": {"mean": 0.47, "tau": 0.1}}}, now=NOW)
        self.assertEqual(r["prior_used"], ["speed"])


if __name__ == "__main__":
    unittest.main()
