"""Dashboard reads and explicit, asynchronous AI report requests.

Only bed_01 owns the shared ToF stream and is never given invented medical data.
A fixed set of demo patients (DEMO_PATIENTS) carry frontend-simulated sensor data
(see site/dashboard-assets/simulation.js) and clearly fictional profiles/meds/risk
factors for presentation purposes — never confused with bed_01's real data.
Jobs are process-local; persisted reports remain available after a server restart.
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import db

LIVE_BED_ID = "bed_01"
DEFAULT_TARGET = "room_01"  # mmWave/send_mmw.py --target default


def init_dashboard_schema():
    """Restore missing profile tables without replacing existing patient records."""
    db.init_db()
    with db.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS patients (
                bed_id TEXT PRIMARY KEY, name TEXT,
                age INTEGER, sex TEXT, room TEXT, diagnosis TEXT,
                mmw_target_id TEXT, notes TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS medications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bed_id TEXT NOT NULL, name TEXT, dose TEXT, frequency TEXT,
                start_date TEXT, end_date TEXT, active INTEGER DEFAULT 1,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS risk_factors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bed_id TEXT NOT NULL, factor TEXT, severity TEXT,
                active INTEGER DEFAULT 1, notes TEXT
            );
        """)
        # Previously created databases may use a narrower schema for these tables.
        for table, needed in (
            ("patients", ("name", "mmw_target_id", "room_no", "age", "sex", "room", "diagnosis", "notes")),
            ("medications", ("frequency", "notes")),
            ("risk_factors", ("factor", "severity", "notes")),
        ):
            columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name in needed:
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} TEXT")
        conn.execute("INSERT OR IGNORE INTO beds (bed_id) VALUES (?)", (LIVE_BED_ID,))
        conn.execute("""INSERT OR IGNORE INTO patients (bed_id, name, mmw_target_id)
                        VALUES (?, ?, ?)""", (LIVE_BED_ID, "연결 환자", DEFAULT_TARGET))
        conn.execute("""UPDATE patients SET mmw_target_id = ? WHERE bed_id = ?
                        AND (mmw_target_id IS NULL OR mmw_target_id = '')""",
                     (DEFAULT_TARGET, LIVE_BED_ID))


# Fixed bed_ids matched exactly by site/dashboard-assets/simulation.js's SCENARIOS map —
# these patients never have real hardware attached; their sensor cards are entirely
# client-side simulated and always rendered with a visible "시뮬레이션" tag so they are
# never mistaken for bed_01's real feed. Profiles/meds/risk factors are fictional,
# used only to demonstrate the risk-factor/medication/AI-report UI end to end.
DEMO_PATIENTS = [
    {
        "bed_id": "bed_sim_calm", "name": "최영호", "age": 71, "sex": "M", "room": "301",
        "diagnosis": "혈관성 치매(경도) · 고혈압",
        "medications": [
            ("Donepezil 5mg", "5mg", "1일 1회 (저녁)", "AChEI · 서맥·어지럼 가능"),
            ("Amlodipine 5mg", "5mg", "1일 1회 (아침)", "CCB · 기립성저혈압 가능"),
        ],
        "risk_factors": [
            ("혈관성 치매(경도 인지저하)", "medium", None),
            ("고령(71세)", "low", None),
            ("독립 보행 가능", "low", None),
        ],
    },
    {
        "bed_id": "bed_sim_bedexit", "name": "조경자", "age": 84, "sex": "F", "room": "303",
        "diagnosis": "알츠하이머병(중등도) · 뇌졸중 후유증(우측 편마비)",
        "medications": [
            ("Rivaroxaban 20mg", "20mg", "1일 1회 (저녁)", "DOAC · 출혈 위험, 낙상 시 위험 증가"),
            ("Memantine 10mg", "10mg", "1일 2회", "NMDA 길항제 · 어지럼 가능"),
        ],
        "risk_factors": [
            ("우측 편마비(뇌졸중 후유증)", "high", None),
            ("항응고제 복용(출혈 위험)", "medium", None),
            ("보행 보조기 사용", "medium", None),
            ("과거 낙상 이력", "high", "2024-11 · 자택에서 낙상, 고관절 타박상"),
        ],
    },
    {
        "bed_id": "bed_sim_gaitcaution", "name": "윤상철", "age": 69, "sex": "M", "room": "304",
        "diagnosis": "치매(경도 인지장애 동반) · 심부전(NYHA II) · 제2형 당뇨병",
        "medications": [
            ("Furosemide 40mg", "40mg", "1일 1회 (아침)", "이뇨제 · 야간뇨 빈도 증가"),
            ("Carvedilol 12.5mg", "12.5mg", "1일 2회", "베타차단제 · 어지럼 가능"),
            ("Metformin 1000mg", "1000mg", "1일 2회", "당뇨 조절"),
        ],
        "risk_factors": [
            ("심부전(NYHA II)", "medium", None),
            ("이뇨제 복용(야간뇨 빈도↑)", "medium", None),
            ("경도 인지장애", "medium", None),
        ],
    },
    {
        "bed_id": "bed_sim_risk", "name": "한미영", "age": 75, "sex": "F", "room": "305",
        "diagnosis": "알츠하이머병(경도) · 제2형 당뇨병 · 요로감염(치료 중)",
        "medications": [
            ("Ciprofloxacin 500mg", "500mg", "1일 2회", "항생제 · 요로감염 치료 중"),
            ("Insulin Glargine 20U", "20U", "1일 1회 (취침 전)", "기저인슐린 · 저혈당 위험"),
            ("Lorazepam 0.5mg PRN", "0.5mg", "필요 시", "벤조디아제핀 · 진정·낙상위험 증가"),
        ],
        "risk_factors": [
            ("급성 요로감염(섬망 유발 가능)", "high", None),
            ("인지저하(알츠하이머 경도)", "medium", None),
            ("진정제 PRN 처방", "high", None),
            ("과거 낙상 이력", "high", "2025-03 · 병동 내 화장실 이동 중 낙상"),
        ],
    },
    {
        "bed_id": "bed_sim_fall", "name": "강순자", "age": 81, "sex": "F", "room": "306",
        "diagnosis": "치매(중등도) · 슬관절 전치환술 후(수술 5일차)",
        "medications": [
            ("Oxycodone 5mg PRN", "5mg", "필요 시", "마약성 진통제 · 진정·낙상위험 증가"),
            ("Enoxaparin 40mg", "40mg", "1일 1회", "DVT 예방 주사"),
            ("Donepezil 10mg", "10mg", "1일 1회 (저녁)", "AChEI · 어지럼 가능"),
        ],
        "risk_factors": [
            ("수술 후 회복기(POD#5)", "high", None),
            ("마약성 진통제 PRN", "high", None),
            ("중등도 치매", "high", None),
            ("과거 낙상 이력", "high", "2025-06 · 자택 화장실에서 낙상, 응급실 내원"),
        ],
    },
]
DEMO_BED_IDS = {p["bed_id"] for p in DEMO_PATIENTS}


def ensure_demo_patients():
    """Seed the fixed demo patients (profile + meds + risk factors) if not already present.
    Idempotent: never duplicates rows on repeated calls (e.g. every server start)."""
    with db.get_conn() as conn:
        for p in DEMO_PATIENTS:
            bed_id = p["bed_id"]
            conn.execute("INSERT OR IGNORE INTO beds (bed_id) VALUES (?)", (bed_id,))
            conn.execute("""INSERT OR IGNORE INTO patients
                            (bed_id, name, age, sex, room, room_no, mmw_target_id, diagnosis, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                bed_id, p["name"], p["age"], p["sex"], p["room"], p["room"], f"sim_{bed_id}", p["diagnosis"],
                "데모 시뮬레이션 환자 — 프런트엔드에서 생성한 가상 센서 데이터",
            ))
            has_meds = conn.execute("SELECT 1 FROM medications WHERE bed_id = ? LIMIT 1", (bed_id,)).fetchone()
            if not has_meds:
                for name, dose, frequency, notes in p["medications"]:
                    conn.execute("""INSERT INTO medications (bed_id, name, dose, frequency, notes)
                                    VALUES (?, ?, ?, ?, ?)""", (bed_id, name, dose, frequency, notes))
            has_risks = conn.execute("SELECT 1 FROM risk_factors WHERE bed_id = ? LIMIT 1", (bed_id,)).fetchone()
            if not has_risks:
                severity_weight = {"high": 0.3, "medium": 0.2, "low": 0.1}
                for factor, severity, notes in p["risk_factors"]:
                    conn.execute("""INSERT INTO risk_factors (bed_id, label, weight, factor, severity, notes)
                                    VALUES (?, ?, ?, ?, ?, ?)""", (
                        bed_id, factor, severity_weight.get(severity, 0.1), factor, severity, notes,
                    ))


def _decode_rows(rows):
    decoded = []
    for original in rows:
        row = dict(original)
        for key, value in list(row.items()):
            if key.endswith("_json"):
                try:
                    row[key[:-5]] = json.loads(value) if value else None
                except (TypeError, json.JSONDecodeError):
                    row[key[:-5]] = None
                del row[key]
        decoded.append(row)
    return decoded


def _patient(row):
    patient = dict(row)
    patient["name"] = patient.get("name") or patient.get("patient_name") or patient["bed_id"]
    patient["is_live"] = patient["bed_id"] == LIVE_BED_ID
    patient["is_simulated"] = patient["bed_id"] in DEMO_BED_IDS
    return patient


def capabilities():
    """Local checks only: never contact a billable API or expose credentials."""
    missing = [module for module in ("requests", "lancedb", "rank_bm25")
               if importlib.util.find_spec(module) is None]
    configured = False
    if "requests" not in missing:
        import ai_report
        configured = bool(ai_report.get_api_key())
    indexed = os.path.isdir(os.path.join(os.path.dirname(__file__), "lancedb_store", "paper_chunks.lance"))
    if missing:
        reason = "AI 실행에 필요한 서버 라이브러리가 설치되지 않았습니다."
        error_type = "missing_dependency"
    elif not configured:
        reason = "서버에 AI API 키가 설정되지 않았습니다."
        error_type = "missing_key"
    elif not indexed:
        reason = "연구 근거 검색 인덱스가 준비되지 않았습니다."
        error_type = "no_index"
    else:
        reason = "AI 리포트를 생성할 수 있습니다."
        error_type = None
    return {"configured": configured, "available": not error_type,
            "index_available": indexed, "missing_dependencies": missing,
            "message": reason, "error_type": error_type,
            "prediction_type": "current_signals_and_recent_changes"}


def _run_analysis(bed_id):
    import integrated_analysis
    # One evidence round keeps interactive report requests bounded. Each external
    # request already has a 30-second timeout and returns a structured error.
    return integrated_analysis.generate_integrated_report(bed_id, max_rounds=1, top_k=3)


def create_blueprint(analysis_runner=None, status_provider=None):
    api = Blueprint("dashboard_api", __name__, url_prefix="/api/dashboard")
    run_analysis = analysis_runner or _run_analysis
    get_capabilities = status_provider or capabilities
    jobs = {}
    lock = threading.Lock()

    def failure(message, error_type, status=400):
        return jsonify(success=False, error_type=error_type, message=message), status

    def execute(job_id, bed_id):
        with lock:
            jobs[job_id]["status"] = "running"
        try:
            result = run_analysis(bed_id)
            if result.get("success"):
                state, error = "completed", None
            else:
                state = "failed"
                error = {"error_type": result.get("error_type", "analysis_failed"),
                         "message": result.get("message", "분석을 완료하지 못했습니다.")}
        except Exception:
            # Do not reflect exception strings, API bodies or credentials to the UI.
            result, state = None, "failed"
            error = {"error_type": "server_error",
                     "message": "분석 중 서버 오류가 발생했습니다. 서버 로그와 실행 환경을 확인해 주세요."}
        with lock:
            jobs[job_id].update(status=state, result=result, error=error,
                                completed_at=datetime.now(timezone.utc).isoformat())

    @api.post("/sim-events")
    def log_sim_event():
        """Records one frontend-simulated phase transition (site/dashboard-assets/simulation.js)
        into activity_log so the AI report can reference the same events a nurse would see on
        the Overview timeline. Restricted to the fixed demo bed_ids — never accepts arbitrary
        bed_id, so a client can't forge activity history for bed_01 or another real patient."""
        body = request.get_json(silent=True)
        bed_id = (body or {}).get("bed_id")
        if not isinstance(body, dict) or bed_id not in DEMO_BED_IDS:
            return failure("알 수 없는 시뮬레이션 환자입니다.", "bad_request")
        event_type = body.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            return failure("event_type이 필요합니다.", "bad_request")
        level = body.get("level") if body.get("level") in ("normal", "caution", "warning", "critical") else None
        title = body.get("title") if isinstance(body.get("title"), str) else None
        note = body.get("note") if isinstance(body.get("note"), str) else None
        event_id = db.insert_sensor_event(bed_id, event_type.strip(), level=level, title=title, note=note)
        return jsonify(success=True, id=event_id), 201

    @api.get("/status")
    def status():
        profile = db.get_patient(LIVE_BED_ID) or {}
        return jsonify(success=True, ai=get_capabilities(), live_bed_id=LIVE_BED_ID,
                       mmw_target_id=profile.get("mmw_target_id") or DEFAULT_TARGET,
                       sensor_stale_seconds={"tof": 10, "mmwave": 90},
                       jobs_persisted=False)

    @api.get("/patients")
    def patients():
        with db.get_conn() as conn:
            rows = conn.execute("""SELECT * FROM patients
                ORDER BY CASE WHEN bed_id = ? THEN 0 ELSE 1 END, bed_id""", (LIVE_BED_ID,)).fetchall()
        return jsonify(success=True, patients=[_patient(row) for row in rows], live_bed_id=LIVE_BED_ID)

    @api.post("/patients")
    def create_patient():
        """Add a profile-only patient row. Never attaches sensors or invented vitals —
        only bed_01 is wired to the shared hardware stream (see LIVE_BED_ID)."""
        body = request.get_json(silent=True)
        name = (body or {}).get("name")
        if not isinstance(body, dict) or not isinstance(name, str) or not name.strip():
            return failure("환자 이름을 입력해 주세요.", "bad_request")
        age = body.get("age")
        sex = body.get("sex")
        bed_id = "bed_" + uuid.uuid4().hex[:8]
        with db.get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO beds (bed_id) VALUES (?)", (bed_id,))
            conn.execute("""INSERT INTO patients (bed_id, name, age, sex, room, diagnosis, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""", (
                bed_id, name.strip(),
                int(age) if isinstance(age, (int, float)) else None,
                sex if sex in ("M", "F") else None,
                body.get("room") if isinstance(body.get("room"), str) else None,
                body.get("diagnosis") if isinstance(body.get("diagnosis"), str) else None,
                body.get("notes") if isinstance(body.get("notes"), str) else None,
            ))
        return jsonify(success=True, patient=_patient(db.get_patient(bed_id))), 201

    @api.get("/patients/<bed_id>")
    def patient_detail(bed_id):
        profile = db.get_patient(bed_id)
        if profile is None:
            return failure("환자를 찾을 수 없습니다.", "not_found", 404)
        target = profile.get("mmw_target_id")
        with db.get_conn() as conn:
            # ToF has no patient column: these shared readings belong only to bed_01.
            tof = conn.execute("""SELECT timestamp, sensor, resolution, min_mm,
                valid_zones, occupied, in_bed FROM tof_readings ORDER BY id DESC LIMIT 120""").fetchall() if bed_id == LIVE_BED_ID else []
            mmw = conn.execute("""SELECT timestamp, target_id, speed, speed_cv, sway,
                freeze_ratio, height_drop, stride_length, stride_cv, reliable,
                samples_count, total_abs, alert_level FROM mmw_readings
                WHERE target_id = ? ORDER BY id DESC LIMIT 60""", (target,)).fetchall() if target else []
        analyses = _decode_rows(db.list_ai_analysis(bed_id, limit=30))
        return jsonify(success=True, patient=_patient(profile), profile=_patient(profile),
                       medications=db.list_medications(bed_id), risk_factors=db.list_risk_factors(bed_id),
                       events=_decode_rows(db.get_activity_log(bed_id, limit=60)),
                       history={"tof": [dict(row) for row in reversed(tof)],
                                "mmwave": [dict(row) for row in reversed(mmw)]},
                       analyses=analyses, summary=db.get_patient_timeseries_summary(bed_id))

    @api.get("/patients/<bed_id>/events")
    def patient_events(bed_id):
        """"이전 이벤트" 날짜 필터 — date(YYYY-MM-DD)가 있으면 그 하루, 없으면 오늘 기준
        최근 7일치를 돌려준다(대시보드 기본값)."""
        if db.get_patient(bed_id) is None:
            return failure("환자를 찾을 수 없습니다.", "not_found", 404)
        date_str = request.args.get("date")
        now = datetime.now()
        if date_str:
            try:
                day = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                return failure("date는 YYYY-MM-DD 형식이어야 합니다.", "bad_request")
            since = day.strftime("%Y-%m-%d 00:00:00")
            until = (day + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
        else:
            since = (now - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
            until = None
        events = db.get_activity_log(bed_id, limit=500, since=since, until=until)
        return jsonify(success=True, events=_decode_rows(events))

    @api.post("/patients/<bed_id>/medications")
    def create_medication(bed_id):
        """Nurse-entered medication record — real data entry, not simulated content, so this
        applies to any patient including bed_01."""
        if db.get_patient(bed_id) is None:
            return failure("환자를 찾을 수 없습니다.", "not_found", 404)
        body = request.get_json(silent=True)
        name = (body or {}).get("name")
        if not isinstance(body, dict) or not isinstance(name, str) or not name.strip():
            return failure("약물명을 입력해 주세요.", "bad_request")
        med_id = db.insert_medication(
            bed_id, name.strip(),
            dose=body.get("dose") if isinstance(body.get("dose"), str) else None,
            frequency=body.get("frequency") if isinstance(body.get("frequency"), str) else None,
            notes=body.get("notes") if isinstance(body.get("notes"), str) else None,
        )
        return jsonify(success=True, medication=db.get_medication(med_id)), 201

    @api.put("/patients/<bed_id>/medications/<int:med_id>")
    def update_medication_route(bed_id, med_id):
        existing = db.get_medication(med_id)
        if existing is None or existing["bed_id"] != bed_id:
            return failure("투약 기록을 찾을 수 없습니다.", "not_found", 404)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return failure("잘못된 요청입니다.", "bad_request")
        if "name" in body and not (isinstance(body["name"], str) and body["name"].strip()):
            return failure("약물명을 입력해 주세요.", "bad_request")
        fields = {}
        for key in ("name", "dose", "frequency", "notes"):
            if key in body and (body[key] is None or isinstance(body[key], str)):
                fields[key] = body[key].strip() if isinstance(body[key], str) else body[key]
        db.update_medication(med_id, **fields)
        return jsonify(success=True, medication=db.get_medication(med_id))

    @api.delete("/patients/<bed_id>/medications/<int:med_id>")
    def delete_medication_route(bed_id, med_id):
        existing = db.get_medication(med_id)
        if existing is None or existing["bed_id"] != bed_id:
            return failure("투약 기록을 찾을 수 없습니다.", "not_found", 404)
        db.delete_medication(med_id)
        return jsonify(success=True)

    @api.get("/analyses")
    def list_analyses():
        bed_id = request.args.get("bed_id", LIVE_BED_ID)
        if db.get_patient(bed_id) is None:
            return failure("환자를 찾을 수 없습니다.", "not_found", 404)
        return jsonify(success=True, analyses=_decode_rows(db.list_ai_analysis(bed_id, limit=100)))

    @api.post("/analyses")
    def start_analysis():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("bed_id"), str):
            return failure("bed_id를 입력해 주세요.", "bad_request")
        bed_id = body["bed_id"]
        if db.get_patient(bed_id) is None:
            return failure("환자를 찾을 수 없습니다.", "not_found", 404)
        with lock:
            existing = next((dict(job) for job in jobs.values()
                             if job["bed_id"] == bed_id and job["status"] in ("pending", "running")), None)
        if existing:
            return jsonify(success=True, **existing), 202
        capability = get_capabilities()
        if not capability["available"]:
            return failure(capability["message"], capability["error_type"], 503)
        with lock:
            # Check again inside the insertion lock; simultaneous clicks share a job.
            existing = next((dict(job) for job in jobs.values()
                             if job["bed_id"] == bed_id and job["status"] in ("pending", "running")), None)
            if existing:
                return jsonify(success=True, **existing), 202
            for old in list(jobs):
                if len(jobs) < 100:
                    break
                if jobs[old]["status"] in ("completed", "failed"):
                    del jobs[old]
            job_id = uuid.uuid4().hex
            job = {"job_id": job_id, "bed_id": bed_id, "status": "pending",
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "result": None, "error": None}
            jobs[job_id] = job
        threading.Thread(target=execute, args=(job_id, bed_id), daemon=True,
                         name=f"dashboard-analysis-{job_id[:8]}").start()
        return jsonify(success=True, job_id=job_id, bed_id=bed_id, status="pending"), 202

    @api.get("/analyses/<job_id>")
    def job_status(job_id):
        with lock:
            job = dict(jobs[job_id]) if job_id in jobs else None
        if job is None:
            return failure("분석 작업을 찾을 수 없습니다. 서버가 재시작되었다면 저장된 리포트를 확인해 주세요.",
                           "not_found", 404)
        return jsonify(success=True, **job)

    return api


WEEKLY_INTERVAL_SECONDS = 7 * 24 * 3600
SCHEDULER_CHECK_INTERVAL_SECONDS = 3600  # re-check hourly; cheap no-op when nothing is due
SCHEDULER_INITIAL_DELAY_SECONDS = 60     # gives a real server time to settle — and keeps the
# whole (sub-second) unit test run from ever reaching a live check, so no test triggers a
# real, billable OpenAI call. Do not lower this without re-checking that assumption.
_scheduler_started = False


def _weekly_reports_due(now=None):
    """bed_id list whose most recent integrated_status analysis is >= 7 days old (or missing)."""
    now = now or datetime.now(timezone.utc)
    with db.get_conn() as conn:
        bed_ids = [row["bed_id"] for row in conn.execute("SELECT bed_id FROM patients")]
    due = []
    for bed_id in bed_ids:
        latest = db.get_latest_ai_analysis(bed_id, analysis_type="integrated_status")
        if not latest or not latest.get("created_at"):
            due.append(bed_id)
            continue
        try:
            last_dt = datetime.fromisoformat(str(latest["created_at"]))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            due.append(bed_id)
            continue
        if (now - last_dt) >= timedelta(seconds=WEEKLY_INTERVAL_SECONDS):
            due.append(bed_id)
    return due


def _run_due_weekly_reports():
    import integrated_analysis
    for bed_id in _weekly_reports_due():
        try:
            result = integrated_analysis.generate_integrated_report(bed_id, max_rounds=1, top_k=3)
            print(f"[scheduler] 주간 리포트 {'완료' if result.get('success') else '실패'}: {bed_id}")
        except Exception as e:
            print(f"[scheduler] 주간 리포트 생성 중 오류({bed_id}): {type(e).__name__}: {e}")


def _weekly_scheduler_loop():
    time.sleep(SCHEDULER_INITIAL_DELAY_SECONDS)
    while True:
        try:
            _run_due_weekly_reports()
        except Exception as e:
            print(f"[scheduler] 주간 리포트 확인 중 오류: {type(e).__name__}: {e}")
        time.sleep(SCHEDULER_CHECK_INTERVAL_SECONDS)


def start_weekly_scheduler():
    """Idempotent — safe to call every time register_dashboard runs (e.g. debug reloader)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_weekly_scheduler_loop, daemon=True, name="weekly-ai-report-scheduler").start()


def register_dashboard(app):
    init_dashboard_schema()
    ensure_demo_patients()
    start_weekly_scheduler()
    app.register_blueprint(create_blueprint())
