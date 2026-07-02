"""
majubom.db  — SQLite 데이터베이스 헬퍼
테이블:
  beds          — 침대(환자) 등록
  csi_readings  — CSI 측정값 + 서버 계산값 (z-score, alert_level)
  baselines     — 침대별 rolling 30일 통계 (평균/표준편차)
"""

import sqlite3
import os
import math
from datetime import datetime, timedelta, timezone

DB_PATH      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "majubom.db")
BASELINE_DAYS = 30   # baseline 집계 기간 (일)
TOF_ZONES     = 16   # ToF 4x4 존 개수 (distances_mm 를 d0..d15 컬럼으로 펼침)


# ── 연결 ──────────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── 초기화 ────────────────────────────────────────────────────────────
def init_db():
    """서버 시작 시 1회 호출 — 테이블/인덱스 없으면 생성."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS beds (
                bed_id     TEXT PRIMARY KEY,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS csi_readings (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp            TEXT    NOT NULL,
                bed_id               TEXT    NOT NULL,
                -- raw 측정값
                hr_bpm               REAL,
                resp_rpm             REAL,
                autocorr_strength    REAL,
                -- quality
                reliable             INTEGER,          -- 0/1 boolean
                samples_count        INTEGER,
                duration_sec         REAL,
                -- presence
                presence_count       INTEGER,
                presence_confidence  REAL,
                gate_active          INTEGER,          -- 0/1 boolean
                -- 서버 계산값
                z_hr                 REAL,
                z_resp               REAL,
                z_strength           REAL,
                total_abs            REAL,
                alert_level          TEXT,
                created_at           TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (bed_id) REFERENCES beds(bed_id)
            );

            CREATE INDEX IF NOT EXISTS idx_csi_bed_ts
                ON csi_readings(bed_id, timestamp);

            CREATE TABLE IF NOT EXISTS baselines (
                bed_id         TEXT PRIMARY KEY,
                hr_mu          REAL,
                hr_sigma       REAL,
                resp_mu        REAL,
                resp_sigma     REAL,
                strength_mu    REAL,
                strength_sigma REAL,
                age_days       REAL,
                sample_count   INTEGER,
                updated_at     TEXT,
                FOREIGN KEY (bed_id) REFERENCES beds(bed_id)
            );
        """)

        # ToF/mmWave raw 테이블 — 배열(distances/targets)은 존별 컬럼으로 펼침(JSON 미사용)
        _d_cols = ",\n                ".join(f"d{i} INTEGER" for i in range(TOF_ZONES))
        _t_cols = ",\n                ".join(f"t{i} INTEGER" for i in range(TOF_ZONES))
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS tof_readings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                sensor      TEXT NOT NULL,          -- tof1 / tof2
                resolution  TEXT,                   -- 4x4 / 8x8
                {_d_cols},                           -- distances_mm 존별 (mm, 실패존 -1)
                {_t_cols},                           -- targets 존별 (0/1)
                min_mm      INTEGER,                 -- 유효존 최솟값(요약)
                valid_zones INTEGER,                 -- 유효존 개수(요약)
                occupied    INTEGER,                 -- 점유 존 개수
                in_bed      INTEGER,                 -- 침상 재실 0/1
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_tof_sensor_ts
                ON tof_readings(sensor, timestamp);

            CREATE TABLE IF NOT EXISTS mmw_readings (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp           TEXT NOT NULL,
                target_id           TEXT NOT NULL,
                -- raw 보행 지표
                speed               REAL,
                speed_cv            REAL,
                sway                REAL,
                freeze_ratio        REAL,
                height_drop         REAL,
                stride_length       REAL,
                stride_cv           REAL,
                -- quality
                reliable            INTEGER,          -- 0/1
                samples_count       INTEGER,
                duration_sec        REAL,
                -- presence
                presence_count      INTEGER,
                presence_confidence REAL,
                gate_active         INTEGER,          -- 0/1
                -- 서버 계산값
                total_abs           REAL,
                alert_level         TEXT,
                created_at          TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_mmw_target_ts
                ON mmw_readings(target_id, timestamp);
        """)
    print(f"[DB] 초기화 완료 → {DB_PATH}")


# ── 침대 등록 ─────────────────────────────────────────────────────────
def upsert_bed(bed_id: str):
    """bed_id 없으면 신규 등록, 있으면 무시."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO beds (bed_id) VALUES (?)", (bed_id,)
        )


def list_beds() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM beds ORDER BY bed_id").fetchall()
        return [dict(r) for r in rows]


# ── 통계 헬퍼 ────────────────────────────────────────────────────────
def _stats(values: list[float]) -> tuple:
    """평균, 표준편차 반환. 데이터 부족 시 None 반환."""
    n = len(values)
    if n == 0:
        return None, None
    mu = sum(values) / n
    if n < 2:
        return mu, None
    variance = sum((x - mu) ** 2 for x in values) / (n - 1)
    sigma = math.sqrt(variance)
    return mu, (sigma if sigma > 0 else None)


# ── baseline 갱신 ────────────────────────────────────────────────────
def update_baseline(bed_id: str):
    """최근 BASELINE_DAYS일의 reliable 데이터로 baseline을 재계산·저장."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=BASELINE_DAYS)
    ).isoformat()

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT hr_bpm, resp_rpm, autocorr_strength, timestamp
            FROM csi_readings
            WHERE bed_id   = ?
              AND reliable  = 1
              AND gate_active = 1
              AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (bed_id, cutoff)).fetchall()

        if not rows:
            return

        hrs   = [r["hr_bpm"]           for r in rows if r["hr_bpm"] is not None]
        resps = [r["resp_rpm"]          for r in rows if r["resp_rpm"] is not None]
        strs  = [r["autocorr_strength"] for r in rows if r["autocorr_strength"] is not None]

        # 최초 측정일로부터 경과 일수 (baseline 신뢰도 판단용)
        first_ts = rows[0]["timestamp"]
        try:
            first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - first_dt).total_seconds() / 86400
        except Exception:
            age_days = 0.0

        hr_mu,   hr_sigma   = _stats(hrs)
        resp_mu, resp_sigma = _stats(resps)
        str_mu,  str_sigma  = _stats(strs)

        now_str = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO baselines
                (bed_id, hr_mu, hr_sigma, resp_mu, resp_sigma,
                 strength_mu, strength_sigma, age_days, sample_count, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(bed_id) DO UPDATE SET
                hr_mu          = excluded.hr_mu,
                hr_sigma       = excluded.hr_sigma,
                resp_mu        = excluded.resp_mu,
                resp_sigma     = excluded.resp_sigma,
                strength_mu    = excluded.strength_mu,
                strength_sigma = excluded.strength_sigma,
                age_days       = excluded.age_days,
                sample_count   = excluded.sample_count,
                updated_at     = excluded.updated_at
        """, (
            bed_id, hr_mu, hr_sigma, resp_mu, resp_sigma,
            str_mu, str_sigma, age_days, len(rows), now_str
        ))


def get_baseline(bed_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM baselines WHERE bed_id = ?", (bed_id,)
        ).fetchone()
        return dict(row) if row else None


# ── 알람 계산 ────────────────────────────────────────────────────────
def compute_alert(
    bed_id: str,
    hr_bpm,
    resp_rpm,
    autocorr_strength,
    reliable: bool,
    gate_active: bool,
) -> tuple:
    """
    z-score 계산 및 alert_level 결정.
    반환: (z_hr, z_resp, z_str, total_abs, alert_level)

    alert_level 값:
      critical    — hr_bpm 절대 임계 초과 / total_abs >= 6
      warning     — total_abs 4~6
      caution     — total_abs 2~4
      normal      — total_abs < 2
      learning    — baseline 14일 미만 (학습 중)
      paused      — presence.gate_active==False (보호자 방문 등)
      unreliable  — quality.reliable==False
    """
    # ① 절대 임계 (baseline 무관, 즉시 알람)
    if hr_bpm is not None and (hr_bpm > 140 or hr_bpm < 40):
        return None, None, None, None, "critical"

    # ② 신뢰할 수 없는 측정
    if not reliable:
        return None, None, None, None, "unreliable"

    # ③ 2명 이상 감지 → 알람 보류
    if not gate_active:
        return None, None, None, None, "paused"

    # ④ baseline 조회 및 학습 기간 확인
    bl = get_baseline(bed_id)
    if bl is None or (bl.get("age_days") or 0) < 14:
        return None, None, None, None, "learning"

    def _z(val, mu, sigma):
        if val is None or mu is None or sigma is None:
            return None
        return (val - mu) / sigma

    z_hr   = _z(hr_bpm,           bl["hr_mu"],       bl["hr_sigma"])
    z_resp = _z(resp_rpm,          bl["resp_mu"],     bl["resp_sigma"])
    z_str  = _z(autocorr_strength, bl["strength_mu"], bl["strength_sigma"])

    parts     = [abs(z) for z in (z_hr, z_resp, z_str) if z is not None]
    total_abs = sum(parts) if parts else None

    if total_abs is None:
        level = "learning"
    elif total_abs < 2:
        level = "normal"
    elif total_abs < 4:
        level = "caution"
    elif total_abs < 6:
        level = "warning"
    else:
        level = "critical"

    return z_hr, z_resp, z_str, total_abs, level


# ── CSI 저장 ─────────────────────────────────────────────────────────
def insert_csi(
    bed_id, timestamp,
    hr_bpm, resp_rpm, autocorr_strength,
    reliable, samples_count, duration_sec,
    presence_count, presence_confidence, gate_active,
    z_hr, z_resp, z_strength, total_abs, alert_level,
):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO csi_readings
                (timestamp, bed_id, hr_bpm, resp_rpm, autocorr_strength,
                 reliable, samples_count, duration_sec,
                 presence_count, presence_confidence, gate_active,
                 z_hr, z_resp, z_strength, total_abs, alert_level)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            timestamp, bed_id, hr_bpm, resp_rpm, autocorr_strength,
            int(bool(reliable)), samples_count, duration_sec,
            presence_count, presence_confidence, int(bool(gate_active)),
            z_hr, z_resp, z_strength, total_abs, alert_level,
        ))


def insert_tof(sensor, timestamp, resolution, distances, targets,
               occupied=None, in_bed=None):
    """ToF 한 프레임 저장. distances/targets 는 존별 컬럼(d0.., t0..)으로 펼침."""
    d = list(distances or [])
    if len(d) > TOF_ZONES:
        print(f"[DB] WARN tof distances {len(d)} > {TOF_ZONES}, truncated ({sensor})")
    d = (d + [None] * TOF_ZONES)[:TOF_ZONES]

    t = list(targets or [])
    t = (t + [None] * TOF_ZONES)[:TOF_ZONES]

    valid = [x for x in d if isinstance(x, (int, float)) and x and x > 0]
    min_mm = int(min(valid)) if valid else None
    valid_zones = len(valid)

    d_cols = [f"d{i}" for i in range(TOF_ZONES)]
    t_cols = [f"t{i}" for i in range(TOF_ZONES)]
    cols = ["timestamp", "sensor", "resolution"] + d_cols + t_cols + \
           ["min_mm", "valid_zones", "occupied", "in_bed"]
    vals = [timestamp, sensor, resolution] + d + t + [
        min_mm, valid_zones,
        int(occupied) if occupied is not None else None,
        int(bool(in_bed)) if in_bed is not None else None,
    ]
    placeholders = ",".join(["?"] * len(cols))
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO tof_readings ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )


def insert_mmw(target_id, timestamp, raw, quality, presence,
               total_abs=None, alert_level=None):
    """mmWave 한 프레임 저장 (scalar 컬럼)."""
    raw = raw or {}
    quality = quality or {}
    presence = presence or {}
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO mmw_readings
                (timestamp, target_id,
                 speed, speed_cv, sway, freeze_ratio, height_drop,
                 stride_length, stride_cv,
                 reliable, samples_count, duration_sec,
                 presence_count, presence_confidence, gate_active,
                 total_abs, alert_level)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            timestamp, target_id,
            raw.get("speed"), raw.get("speed_cv"), raw.get("sway"),
            raw.get("freeze_ratio"), raw.get("height_drop"),
            raw.get("stride_length"), raw.get("stride_cv"),
            int(bool(quality.get("reliable", True))),
            quality.get("samples_count"), quality.get("duration_sec"),
            presence.get("count"), presence.get("confidence"),
            int(bool(presence.get("gate_active", True))),
            total_abs, alert_level,
        ))


# ── 조회 ──────────────────────────────────────────────────────────────
def get_csi_latest_all() -> list[dict]:
    """침대별 최신 CSI 1건씩."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.*
            FROM csi_readings c
            INNER JOIN (
                SELECT bed_id, MAX(timestamp) AS ts
                FROM csi_readings
                GROUP BY bed_id
            ) m ON c.bed_id = m.bed_id AND c.timestamp = m.ts
        """).fetchall()
        return [dict(r) for r in rows]


def get_csi_log(bed_id: str | None = None, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        if bed_id:
            rows = conn.execute("""
                SELECT * FROM csi_readings
                WHERE bed_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (bed_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM csi_readings
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
