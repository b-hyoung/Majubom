"""실측 경로 코드 가이드(docs/CODE_GUIDE_LIVE.html) 생성기 — 2026-10-13 전면 개편.

파트(ToF·자세 분류·이상탐지·mmWave·보행 지표·낙상 감지·보행 저하 예측·위험도·대시보드·AI 리포트·운영)를
카드로 고르면 그 파트만 펼쳐지고, 단계마다 '쉬운 설명 → 엔지니어 메모·수식 → 실제 코드(줄 번호)'를 보여준다.
코드 조각은 tools/guide_lib.py가 생성 시점의 소스에서 직접 잘라 온다. 숫자(모델 성능·실측 재생 결과 등)도
가능하면 생성 시점에 파일·모델에서 다시 계산한다(라이브러리가 없으면 그 부분만 '계산 못 함'으로 남긴다).

실행(모델 수치까지 넣으려면 .venv 파이썬):  .venv/bin/python tools/build_code_guide.py
"""
import json
import math
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_lib import ROOT, block, esc, js, line_of, py, read, ref  # noqa: E402

OUT = os.path.join(ROOT, "docs", "CODE_GUIDE_LIVE.html")
FW, T, ROI, PP, TR, AN, LA = ("TOF/src/main.ino", "server/tof_server.py", "TOF/ml/roi.py", "TOF/ml/predict_posture.py",
                              "TOF/ml/train_posture_v2.py", "TOF/ml/tof_anomaly.py", "TOF/ml/lstm_anomaly.py")
S, CFG, DD, FD = "mmWave/send_mmw.py", "mmWave/AOP_diag_2m5_d50.cfg", "mmWave/diagnostic_decode.py", "mmWave/fall_detect.py"
M, ML, GT, FR, DA = "server/mmw_server.py", "server/mmw_logic.py", "server/gait_trend.py", "server/fall_risk.py", "server/dashboard_api.py"
DJ, SJ, DBJ = "site/dashboard-assets/data.js", "site/dashboard-assets/simulation.js", "site/dashboard-assets/dashboard.js"
IA, RT, AG, DB = "server/integrated_analysis.py", "server/retrieval.py", "server/ai_agent.py", "server/db.py"


# ───────────────────────── 생성 시점 계산 ─────────────────────────
def facts():
    f = {}
    f["posture_report"] = json.loads(read("TOF/ml/posture_model_v2_report.json"))
    f["lstm_report"] = json.loads(read("TOF/ml/lstm_ae_v2_report.json"))
    try:   # 실제 학습된 자세 모델에서 첫 나무의 위쪽 질문과 중요 칸
        import joblib
        import numpy as np
        d = joblib.load(os.path.join(ROOT, "TOF/ml/posture_model.joblib"))
        m = d["model"]
        t = m.estimators_[0].tree_
        name = lambda i: f"{'tof1(발)' if i < 64 else 'tof2(머리)'} {i % 64}번 칸(행 {(i % 64) // 8}, 열 {(i % 64) % 8})"
        rows = []

        def walk(node, depth, path):
            if depth > 2 or t.children_left[node] == -1:
                return
            rows.append((depth, path, f"{name(t.feature[node])} 거리 ≤ {t.threshold[node]:.0f} mm ?"))
            walk(t.children_left[node], depth + 1, path + "예→")
            walk(t.children_right[node], depth + 1, path + "아니오→")
        walk(0, 0, "")
        imp = m.feature_importances_
        top = np.argsort(imp)[::-1][:8]
        f["rf"] = {"n_trees": len(m.estimators_), "max_features": str(m.max_features), "classes": [str(c) for c in m.classes_],
                   "tree0": rows, "top": [(name(int(i)), float(imp[i])) for i in top],
                   "depth": float(np.mean([e.tree_.max_depth for e in m.estimators_])),
                   "leaves": float(np.mean([e.tree_.n_leaves for e in m.estimators_]))}
    except Exception as e:
        f["rf"] = {"error": f"{type(e).__name__}: {e}"}
    try:   # 낙상 감지: 실측 기록 재생
        sys.path.insert(0, os.path.join(ROOT, "mmWave"))
        import fall_detect as F
        trials = json.loads(read("mmWave/tests/data/fall_trials_20261012.json"))["trials"]
        res = {}
        for n, frames in trials.items():
            det, evs = F.FallDetector(), []
            for tt, trs in frames:
                evs += det.update(tt, [{"tid": a, "posX": x, "posY": y, "posZ": z} for a, x, y, z in trs])
            res[n] = [(round(e["at"], 2), e["stage"], e.get("kind"), e.get("drop_sec"), e.get("low_sec"), e["z"], e["ref"]) for e in evs]
        f["fall_replay"] = res
        f["fall_consts"] = {k: getattr(F, k) for k in ("REF_DEFAULT", "REF_MIN", "REF_WINDOW_SEC", "SMOOTH_SEC", "FAST_SEC",
                                                        "HIGH_FRAC", "LOW_FRAC", "RECOVER_FRAC", "CONFIRM_SEC",
                                                        "UPRIGHT_LOOKBACK_SEC", "NEAR_UPRIGHT_M")}
    except Exception as e:
        f["fall_replay"] = {"error": f"{type(e).__name__}: {e}"}
    try:   # 보행 저하 예측: 합성 시험(같은 시나리오 20회) + 과거 재현 검증
        sys.path.insert(0, os.path.join(ROOT, "server"))
        import gait_trend as G
        import test_gait_trend as TG
        flat = [(d, 0.60) for d in range(30, 0, -1)]
        mild = [(d, 0.60 - 0.005 * max(0, 21 - d)) for d in range(30, 0, -1)]
        fast = [(d, 0.60) for d in range(30, 10, -1)] + [(d, 0.60 - 0.01 * (11 - d)) for d in range(10, 0, -1)]
        table = []
        for noise, per in ((0.08, 20), (0.12, 8), (0.15, 5)):
            lv = lambda v, s: G.assess(TG.make_db(v, per_day=per, noise=noise, seed=s), "t", now=TG.NOW)
            n = 20
            fp = sum(lv(flat, s)["level"] != "normal" for s in range(n))
            mc = sum(lv(mild, s)["metrics"]["speed"]["level"] != "normal" for s in range(n))
            fl = [lv(fast, s)["metrics"]["speed"]["level"] for s in range(n)]
            table.append((per, noise, n, fp, mc, sum(x == "warning" for x in fl), sum(x != "normal" for x in fl)))
        series = G.daily_series(TG.make_db([(d, 0.70 - 0.002 * (40 - d)) for d in range(40, 0, -1)]), "t", days=60, now=TG.NOW)
        f["gt"] = {"table": table, "backtest": G.backtest(series, "speed", 7),
                   "consts": {k: getattr(G, k) for k in ("MIN_WINDOWS_PER_DAY", "MIN_DAYS", "CONFIDENT", "WINDOW_SD_FLOOR")},
                   "metrics": G.METRICS}
    except Exception as e:
        f["gt"] = {"error": f"{type(e).__name__}: {e}"}
    # 레이더 cfg에서 거리·속도 사양 계산
    cfg = {ln.split()[0]: ln.split()[1:] for ln in read(CFG).splitlines() if ln and not ln.startswith("%")}
    p = cfg["profileCfg"]
    slope, adc, fs = float(p[7]), int(p[9]), float(p[10])          # MHz/us, 샘플 수, ksps
    bw = slope * adc / (fs / 1000.0)                                # MHz
    f["radar"] = {"start_ghz": float(p[1]), "slope": slope, "adc": adc, "fs": fs, "bw_mhz": bw,
                  "res_cm": 3e8 / (2 * bw * 1e6) * 100, "max_m": fs * 1e3 * 3e8 / (2 * slope * 1e12),
                  "frame_ms": float(cfg["frameCfg"][4]), "fps": 1000.0 / float(cfg["frameCfg"][4]),
                  "boundary": " ".join(cfg["boundaryBox"]), "sensor": " ".join(cfg["sensorPosition"])}
    return f


# ───────────────────────── 화면 조각 ─────────────────────────
def step(num, title, plain, detail="", codes="", open_=False):
    return (f'<details class="step"{" open" if open_ else ""}><summary><span class="num">{num}</span>{title}</summary>'
            f'<div class="plain">{plain}</div>' + (f'<div class="detail"><div class="detail-h">엔지니어 메모</div>{detail}</div>' if detail else "")
            + codes + "</details>")


def easy(text):
    return f'<div class="easy"><b>쉽게 말하면</b> {text}</div>'


def formula(text):
    return f'<div class="formula">{text}</div>'


def table(head, rows):
    return ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")


def part(pid, title, sub, body):
    return f'<section class="part" id="{pid}" hidden><div class="part-head"><a href="#home" class="back">← 전체 지도</a><h2>{title}</h2><p class="sub">{sub}</p></div>{body}</section>'


def warn(text):
    return f'<div class="warn"><b>한계·주의</b> {text}</div>'


def cite(text):
    return f'<div class="cite">{text}</div>'


# ───────────────────────── 파트 본문 ─────────────────────────
def part_overview(f):
    return part("flow", "0. 전체 흐름 — 센서에서 간호사 화면·리포트까지", "무엇이 어디서 돌아가는지 한 장으로", f"""
{easy("침대 양 끝의 ToF 2개가 '침대 위 몸 모양'을, 방 구석의 레이더가 '방 안 사람 위치·걸음'을 잰다. 라즈베리파이 5가 이 값을 받아 자세·재실·낙상·보행을 판정하고, 간호사 대시보드에 보여주며, 보행 기록으로 앞으로의 보행 저하를 예측해 AI 리포트에 넣는다.")}
{table(["단계", "장치·프로그램", "주소·파일", "하는 일"], [
    ["1", "VL53L5CX ToF ×2 + ESP32-S3", ref(FW), "8×8=64칸 거리(mm)를 초당 약 15번 Wi-Fi로 전송"],
    ["2", "IWR6843AOP 60GHz 레이더", ref(CFG), "사람 위치·속도(추적)를 초당 약 18번 USB로 전송"],
    ["3", "send_mmw.py (systemd 서비스)", ref(S), "레이더 바이트 해석 → 좌표 변환 → 낙상 감지 → 6초 보행 지표"],
    ["4", "tof_server.py (:5001)", ref(T), "ToF 수신·재실·자세(RF)·침상 알림 + 대시보드·API 제공"],
    ["5", "mmw_server.py (:5002)", ref(M), "보행 Z-score 판정·저장, 낙상 사건 기록, 실시간 위치"],
    ["6", "SQLite majubom.db", ref(DB), "원시 기록·이벤트·위험도·리포트 저장"],
    ["7", "대시보드 (브라우저)", ref("site/index.html") + " · " + ref("site/dev.html"), "간호사용 /dashboard(1초마다 판정 결과, 0.2초마다 위치) · 개발용 /dev(원시값·진단)"],
    ["8", "gait_trend.py", ref(GT), "하루 보행 대표값 → 칼만 필터 → 7·14일 예측"],
    ["9", "integrated_analysis.py + LLM", ref(IA), "모든 판정·예측·논문 검색 결과로 리포트 작성"],
])}
<div class="flowline"><span>ToF 64칸×2</span>→<span>재실·자세·가장자리</span>→<span>침상 알림(위험군×거동)</span>→<span>대시보드</span></div>
<div class="flowline"><span>레이더 추적</span>→<span>좌표 변환</span>→<span>낙상 감지(프레임마다)</span>·<span>보행 6지표(6초마다)</span>→<span>Z-score·저장</span>→<span>보행 저하 예측(매일)</span>→<span>리포트</span></div>
{warn("실측 센서는 연결 환자 bed_01(레이더 대상 room_01) 한 명에게만 연결된다. 나머지 환자는 시뮬레이션·예시 기록이다. 이 문서의 '정확도' 숫자는 출처(우리 측정·합성 시험·논문)를 매번 같이 적는다.")}
""")


def part_tof(f):
    pr = f["posture_report"]
    return part("tof", "1. ToF 침대 센서 — 빛으로 잰 64칸 거리", "원시값이 어떻게 들어오고, 어디서 걸러지고, 어떻게 '침대에 사람 있음'이 되는지", f"""
{easy("ToF(Time of Flight)는 눈에 안 보이는 적외선을 쏘고 되돌아오는 시간으로 거리를 잰다. VL53L5CX는 시야를 가로 8×세로 8=64칸으로 나눠 칸마다 거리를 동시에 잰다. 침대 머리맡과 발치에 하나씩, 바닥에서 1.2m 높이에서 침대를 내려다본다(발 50°, 머리 30° 아래로).")}
{step("1-1", "센서와 설치 — 무엇을 재나", f"""
<p>VL53L5CX: 8×8 모드에서 <b>초당 최대 15번</b>, 시야 45°×45°(대각 63°), 칸 하나 = 45°÷8 ≈ <b>5.6°</b>. 거리 단위 mm.</p>
<p>설치(2026-10-08 실측): 둘 다 높이 1.2m. tof1 = 발치, 아래로 50° · tof2 = 머리맡, 아래로 30°. 두 보드는 각각 ESP32-S3에 연결되어 공유기 2.4GHz Wi-Fi로 보낸다.</p>""",
 f"""<ul><li>센서 프레임 주기는 {ref(FW, r"set_ranging_frequency_hz", "set_ranging_frequency_hz(15)")}로 고정(8×8 하드웨어 상한).</li>
<li>펌웨어의 <code>MOUNT_DOWN_TILT_DEG = 20</code>은 예전 값이다. 서버는 이 값을 쓰지 않고 {ref(T, r"^TOF_MOUNT", "TOF_MOUNT")}(50°/30°)로 표시한다(펌웨어 업로드 때 맞출 것 — docs/나중에_할일.md).</li></ul>""")}
{step("1-2", "원시값 — ESP32가 보내는 JSON 한 덩어리", f"""
<p>매 프레임 <code>POST http://Pi:5001/tof</code>로 아래 JSON을 보낸다.</p>
<pre class="sample">{{"sensor": "tof2", "resolution": "8x8", "mount_height_m": 1.2,
 "distances_mm": [-1, -1, 1647, ..., 816],     ← 64개, 서버가 쓰는 값
 "targets": [0, 0, 1, ..., 1],                 ← 칸마다 잡힌 물체 수
 "raw_distances_mm": [...], "target_status": [...]}}  ← 진단 화면용 원본</pre>
<p>(펌웨어 안에서 일어나는 일 전체는 <a href="#firmware">1A. ToF 펌웨어</a>.) <b>첫 번째 정제(센서·펌웨어 안):</b> 칸마다 센서가 붙이는 상태 코드 <code>target_status</code>가 <b>5(정상 측정)</b>일 때만 거리를 넣고, 아니면 <b>-1</b>(측정 실패)로 보낸다. 그래서 서버에서 -1은 '모름'이다.</p>""",
 "<ul><li>칸 번호 z = 행×8 + 열 (0~63). 행 0이 센서 시야의 위쪽(먼 쪽)이다. 좌우는 실측으로 뒤집혀 있어 roi.py가 보정한다.</li><li>HTTP 제한시간 200ms: Wi-Fi가 잠깐 끊겨도 다음 프레임을 막지 않게.</li></ul>",
 js(FW, r"^void postSensor", "postSensor() — JSON 만들기와 status 5 필터"))}
{step("1-3", "서버 수신 — 프레임 하나가 거치는 순서", f"""
<ol><li>센서 이름(tof1/tof2)과 64개 배열인지 확인 → 아니면 400 오류.</li>
<li>메모리의 '최신값'(<code>latest</code>)에 저장 — 화면·자세 판정은 이 값을 쓴다.</li>
<li>로우데이터 진단 화면(:5012)에 사본 전송(메모리만, 저장 안 함).</li>
<li>재실 판정(1-4) → LSTM-AE 버퍼(3장) → <b>저장 조건</b>을 만족하면 DB <code>tof_readings</code>에 한 줄.</li></ol>
<p><b>저장 조건</b>(2026-10-11): 침대에 사람이 있는 동안 + 나간 뒤 10초까지만 저장. 빈 침대 같은 값이 하루 수십만 줄 쌓이던 것을 막았다.</p>""",
 "", py(T, "receive_tof", "receive_tof() — /tof 수신 처리 전체") + py(T, "_should_store_raw", "_should_store_raw() — 저장 조건"))}
{step("1-4", "빈 침대 기준(baseline)과 '침대에 사람 있음' 판정", f"""
<p>먼저 빈 침대일 때 64칸 거리를 기준값으로 저장해 둔다(<code>POST /tof/calibrate</code>, 파일 server/tof_baseline.json). 그 뒤 프레임마다 칸별로 비교한다.</p>
{formula("칸 z가 '사람 칸'  ⇔  측정 거리 d<sub>z</sub> &lt; 기준 거리 b<sub>z</sub> − 150 mm   (단, b<sub>z</sub> ≤ 2300 mm인 칸만 = 침대 범위)")}
<p>사람이 누우면 몸 두께만큼 매트리스보다 가까워지니까 그 칸이 '사람 칸'이 된다. 벽·바닥까지 닿는 먼 칸(2.3m 초과)은 아예 세지 않는다.</p>
{formula("들어옴: 어느 센서든 사람 칸 ≥ 8개가 <b>3프레임 연속</b> → 재실<br>나감: 재실 중엔 4개만 넘어도 유지, 두 센서 모두 4개 미만이 <b>24프레임(≈2.4초) 연속</b> → 이탈")}
<p>들어올 땐 빨리, 나갈 땐 천천히(히스테리시스) — 뒤척일 때 '나갔다 들어왔다'로 깜빡이지 않게.</p>""",
 f"<ul><li>설정값 {ref(T, r'^PRESENCE_DELTA_MM', 'PRESENCE_DELTA_MM=150')}, MIN 8, STAY 4, ENTER 3, EXIT 24, ROI_MAX 2300.</li><li>2026-10-13 실측: 침대 가운데 걸터앉음 → 발 7~9칸·머리 11~14칸으로 재실. 침대 끝 가장자리에 앉으면 칸이 모자라 재실을 놓친 적이 있다.</li></ul>",
 block(T, r"^PRESENCE_DELTA_MM", r"^ROI_MAX_MM", "재실 판정 설정값") + py(T, "occupied_zones") + py(T, "update_presence"))}
{step("1-4b", "빈 침대 기준 자동 재캘리브레이션 — 베개·이불이 바뀌어도", f"""
<p>베개·이불 위치가 바뀌면 빈 침대 모양이 기준값과 달라져 재실 판정이 틀어진다. 그래서 환자가 침대를 떠날 때마다 기준을 다시 잡는다(2026-10-13).</p>
{formula("조건: ToF 침상 이탈 확정 → 10초 대기 → 그동안 레이더가 사람을 추적 중이고 그 사람이 이탈 후 0.3 m 이상 이동<br>"
         "수집: 이어서 5초 동안의 프레임 → 칸별 중앙값 (수집 중 최대−최소 &gt; 150 mm인 칸·유효 60% 미만 칸은 예전 값 유지)<br>"
         "안전장치: 예전 기준보다 30 cm 넘게 바뀐 칸이 16칸(25%) 초과 → 적용하지 않고 '빈 침대 기준 재확인 필요' 기록")}
<p><b>왜 '레이더로 0.3m 이동'까지 보나</b>: 이불 덮고 가만히 누운 마른 환자를 ToF가 놓쳐 '이탈'로 잘못 판단하면, 그 순간 기준을 잡을 경우 환자 모양이 기준에 섞여 이후 계속 '빈 침대'가 된다(가장 위험한 놓침). 레이더가 그 사람이 실제로 걸어 나간 것을 확인할 때만 잡는다.</p>""",
 "<ul><li>갱신할 때마다 이전·새 기준을 server/tof_baseline_history.jsonl에 남긴다(되돌리기·연구용). 최근 결과는 /tof/presence의 baseline_auto, 이벤트는 activity_log tof_baseline_auto.</li><li>수집 중 다시 누우면 취소, 이탈 후 10분 안에 레이더 조건이 안 맞으면 그 이탈은 건너뛴다.</li></ul>",
 block("server/tof_autocal.py", r"^WAIT_AFTER_EXIT_SEC", r"^BIG_CHANGE_MAX", "설정값") + py("server/tof_autocal.py", "radar_moved") + py("server/tof_autocal.py", "build_baseline")
 + py("server/tof_autocal.py", "compare") + py(T, "_autocal_worker", "_autocal_worker() — 1초 두 번 조건 확인·수집·적용"))}
{step("1-5", "칸 → 3D 좌표 (roi.py) — 각도와 거리로 '어디에 닿았나' 계산", f"""
<p>칸마다 '센서에서 어느 방향으로 몇 mm'인지 알면 그 지점의 3D 위치를 계산할 수 있다.</p>
{formula("방향 = 센서 정면(아래로 기운 각도) + tan(열 각도)·오른쪽 + tan(행 각도)·위쪽,   열·행 각도 = (3.5 − 열·행 번호) × 5.625°<br>3D 점 = 센서 위치 + 방향(단위 벡터) × 거리")}
<p>이걸로 두 가지를 한다: ① 침대 폭 밖(|좌우| &gt; 0.475+0.08 m)에 닿는 칸은 -1로 지워 자세 모델이 벽·바닥을 보지 않게 한다(<code>mask_offbed</code>). ② 사람 칸들의 좌우 평균으로 '가장자리 쏠림'을 본다(2-5).</p>
<p>2026-10-13 확인: 빈 침대 기록을 이 계산에 넣으면 <b>매트리스 높이 0.37m</b>가 나와 줄자 37cm와 일치했다(계산식 검증). 이 원리로 침대 폭도 잴 수 있다 → {ref("TOF/ml/bed_geometry.py")}(한쪽 가장자리만 보여 폭 ≥0.69m).</p>""",
 f"<ul><li>좌표: X=머리(−)↔발(+), Y=좌우, Z=높이. 두 센서 간격 SEP=2.12m, 침대 폭 BED_WID=0.95m(실측 확인 전 값).</li><li>머리 쪽 tof2는 매트리스가 0.54m로 나와 30°·1.2m 값 재확인 필요.</li></ul>",
 block(ROI, r"^SENSOR_H1", r"^Y_MARGIN", "설치값") + py(ROI, "_sensor") + py(ROI, "hit_point") + py(ROI, "mask_offbed"))}
""")


def part_firmware(f):
    return part("firmware", "1A. ToF 펌웨어 — ESP32 안에서 일어나는 일과 정제", "센서 칩이 하는 처리, 우리 펌웨어가 거르는 것, 전송 규칙, 올리는 법", f"""
{easy("ESP32-S3 보드 하나에 VL53L5CX 하나가 선(I2C)으로 붙어 있다. 펌웨어는 ① 켜질 때 센서 칩에 센서용 프로그램을 넣어 깨우고 ② 측정이 끝날 때마다 64칸 결과를 읽어 ③ '믿을 수 있는 칸'만 거리로 남기고 나머지는 -1로 바꾼 뒤 ④ Wi-Fi로 Pi에 보낸다. 정제는 센서 칩 안(빛 신호 → 거리·상태 코드)과 펌웨어(상태 코드 5만 통과) 두 곳에서 일어나고, 그 뒤는 서버가 한다.")}
{step("1A-1", "부팅 — 센서 깨우기(initSlow)", """
<ol><li>센서 전원 핀(LPn, GPIO 4)을 껐다 켜서(0.2초 끔 → 1초 대기) I2C 선이 멈춰 있던 상태를 풀어준다.</li>
<li>I2C(SDA 8, SCL 9, 400kHz) 주소를 0x08~0x77 전부 두드려 센서 주소를 자동으로 찾고, 기본 0x52가 아니면 맞춘다.</li>
<li><code>is_alive</code>로 응답 확인 → <code>vl53l5cx_init()</code>: 이때 라이브러리가 <b>센서 칩 안에서 돌 프로그램(펌웨어)을 I2C로 내려보낸다</b> — 그래서 부팅에 몇 초 걸린다.</li>
<li>8×8 해상도, 초당 15번 측정으로 설정하고 측정 시작. 실패하면 멈춰서 시리얼에 오류를 남긴다(자동 재시도 없음 → 전원 재인가).</li></ol>""",
 "<ul><li>라이브러리: STM32duino VL53L5CX ^1.2.3(ST 공식 ULD 드라이버 포장), ArduinoJson ^7 — platformio.ini.</li><li>해상도·주기 외의 센서 설정(적분 시간, 겹친 물체 분리 sharpener, 여러 물체 중 무엇을 먼저 줄지 target order, 측정 방식)은 라이브러리 기본값 그대로이고 코드에서 바꾸지 않는다.</li></ul>",
 js(FW, r"int initSlow", "initSlow() — 전원 토글·주소 찾기·센서 프로그램 내려보내기") + js(FW, r"^void setup", "setup() — 해상도 8×8·15Hz·Wi-Fi") + block("TOF/src/platformio.ini", r"^\[env", r"ArduinoJson", "platformio.ini — 보드·라이브러리"))}
{step("1A-2", "정제 ① — 센서 칩 안에서(우리가 바꾸지 않는 부분)", """
<p>센서는 칸마다 레이저 펄스를 수없이 쏘고 돌아온 빛 알갱이(광자)의 도착 시간을 히스토그램으로 모아, 봉우리 위치로 거리를 계산한다. 이 과정에서 칸마다 함께 만들어 주는 값:</p>
<ul><li><b>nb_target_detected</b>: 그 칸에서 찾은 물체 수(0이면 아무것도 못 찾음).</li>
<li><b>distance_mm</b>: 거리. 물체를 못 찾았거나 불확실해도 숫자는 들어 있을 수 있다.</li>
<li><b>target_status</b>: 이 거리를 얼마나 믿을지 상태 코드. <b>5 = 정상 측정</b>, 그 외는 신호 약함·주변광 과다·겹친 물체 의심·첫 측정 등.</li></ul>
<p>즉 '이 칸 값이 쓸 만한가'의 1차 판정은 센서 칩이 한다.</p>""",
 "<ul><li>칸당 물체 수 설정(VL53L5CX_NB_TARGET_PER_ZONE)은 라이브러리 기본값이라 배열 인덱스에 곱해 쓴다(<code>[NB × z]</code> = 그 칸의 첫 번째 물체).</li><li>ST 상태 코드 표는 5 외 일부 코드도 '대체로 유효'로 분류하지만 우리는 5만 받는다(보수적, 측정 실패 칸이 늘 수 있다). 바꾸려면 펌웨어와 학습 데이터를 함께 다시 만들어야 한다 — 자세 모델이 '-1인 칸'도 단서로 배웠기 때문(2장).</li></ul>")}
{step("1A-3", "정제 ② — 우리 펌웨어가 거르는 것(postSensor)", """
<ol><li>칸 z마다: 물체 수 &gt; 0 <b>그리고</b> 상태 코드 == 5 → 그 거리, 아니면 <b>-1</b>. 이것이 서버가 쓰는 <code>distances_mm</code>.</li>
<li>같은 칸의 원본 거리(<code>raw_distances_mm</code>)와 상태 코드(<code>target_status</code>)도 거르지 않고 같이 보낸다 → 로우데이터 화면에서 '왜 -1이 됐나'를 볼 수 있다. 운영 판정은 이 원본을 쓰지 않는다.</li>
<li>칸 순서는 센서 순서 그대로(행×8+열). 좌우 뒤집기·설치 각도 반영은 펌웨어에서 하지 않고 서버(roi.py)가 한다.</li>
<li>설치 높이·각도도 같이 보내지만 펌웨어 값(20°)은 예전 설치라 서버가 무시하고 실측값(발 50°·머리 30°)을 쓴다.</li></ol>
<p>펌웨어가 하지 않는 것: 평균내기·튀는 값 제거·빈 침대 비교·좌표 변환 — 전부 서버 몫이다(1장).</p>""",
 "", js(FW, r"^void postSensor", "postSensor() — status 5 필터와 JSON") + js(FW, r"^void printGrid", "printGrid() — 시리얼 확인용(같은 필터)"))}
{step("1A-4", "전송 규칙 — 속도·끊김·실패", """
<ul><li><b>속도</b>: 센서가 새 측정을 끝냈는지(<code>check_data_ready</code>) 계속 물어보고, 끝났을 때만 읽어서 보낸다. 최소 간격 10ms는 사실상 제한이 아니고 센서 15Hz가 상한이다.</li>
<li><b>Wi-Fi 끊김</b>: 매 반복마다 연결 확인, 끊겼으면 다시 연결(최대 10초 기다림). 그동안 측정값은 보내지 않고 버린다.</li>
<li><b>보내기 실패</b>: HTTP 제한 200ms. 실패한 프레임은 다시 보내지 않고 버린다(쌓아두지 않음) — 늦은 데이터보다 다음 최신 프레임이 낫다는 선택. 서버는 받은 시각으로 끊김을 판단한다.</li>
<li><b>시리얼 출력</b>은 1초에 한 번만(매 프레임 찍으면 전송이 느려졌다).</li></ul>""",
 "<ul><li>Pi 응답이 느려지면(예: 자세 계산을 요청 처리 안에서 하던 때) ESP가 다음 측정을 기다리는 동안 프레임을 놓쳐 12Hz→5Hz로 떨어진 적이 있다 → 서버의 무거운 계산은 별도 스레드로 뺐다.</li></ul>",
 js(FW, r"^void loop", "loop() — 준비 확인·읽기·전송") + js(FW, r"^void ensureWiFi", "ensureWiFi() — 재연결"))}
{step("1A-5", "설정과 올리는 법", """
<ul><li>보드마다 다른 값(센서 이름 tof1/tof2, Wi-Fi 이름·비밀번호, 서버 주소)은 <code>TOF/src/secrets.h</code>에 둔다(저장소에 올리지 않음, 예시는 secrets.example.h). 없으면 코드의 기본값(tof2, majubom, 192.168.0.4)을 쓴다.</li>
<li>올리기: 보드를 USB로 연결 → <code>.venv/bin/pio run -d TOF/src -t upload</code>. 보드마다 secrets.h의 이름을 바꿔 한 번씩.</li>
<li>현재 배정: tof1 = 발치, tof2 = 머리맡(2026-10-08 업로드). 서버에서 물리적으로 바뀐 경우 <code>SWAP_TOF</code>로 맞바꿀 수 있다.</li></ul>""",
 "", block(FW, r"^#if __has_include", r"^const char\* SERVER_URL", "설정값을 secrets.h에서 읽기"))}
{warn("펌웨어 쪽 정제는 '상태 코드 5만 통과' 하나뿐이다. 흔들림 제거·평균은 하지 않으므로 서버에서 튀는 값을 다룬다. 펌웨어의 설치 각도 상수(20°)는 실제와 달라 다음 업로드 때 고칠 것(docs/나중에_할일.md).")}
""")


def part_posture(f):
    pr, rf = f["posture_report"], f["rf"]
    if "error" in rf:
        rf_html = f"<p class='muted'>모델 내부 계산 못 함({esc(rf['error'])}) — .venv 파이썬으로 생성하면 보인다.</p>"
    else:
        rf_html = ("<p>실제 학습된 모델의 <b>첫 번째 나무</b> 위쪽 질문(생성 시점에 모델 파일에서 직접 읽음):</p><pre class='sample'>"
                   + "\n".join(("    " * d) + (p and f"[{p[:-1]}] " or "") + q for d, p, q in rf["tree0"]) + "</pre>"
                   f"<p>나무 {rf['n_trees']}그루, 평균 깊이 {rf['depth']:.1f}단계, 평균 잎(최종 답) {rf['leaves']:.0f}개. 질문마다 128칸 중 무작위 √128≈11칸만 후보로 본다(max_features={esc(rf['max_features'])}).</p>"
                   "<p><b>예→ 쪽 질문이 '≤ 291 mm'처럼 아주 작은 이유</b>: 측정 실패 칸은 -1이라 '≤ 291'이면 사실상 '이 칸이 측정 실패(또는 아주 가까움)인가?'를 묻는 것이다. 몸이 가리면 칸이 실패하기도 해서 이것도 자세 단서가 된다.</p>"
                   + table(["중요한 칸 TOP 8", "중요도(전체 합 1)"], [[esc(n), f"{v:.3f}"] for n, v in rf["top"]]))
    cm_rows = [[pr["confusion_labels"][i]] + [str(v) for v in row] for i, row in enumerate(pr["confusion"])]
    return part("posture", "2. 자세 분류 — Random Forest가 64칸×2를 보고 자세를 고르는 법", "알고리즘인지 수식인지, 무엇을 넣어 무엇이 나오는지, 얼마나 맞는지", f"""
{easy("Random Forest는 '수식 하나'가 아니라 <b>학습 알고리즘</b>이고, 학습이 끝나면 <b>'예/아니오 질문 나무' 200그루</b>가 남는다. 나무 하나는 '머리 쪽 51번 칸 거리가 770mm 이하인가?' 같은 질문을 10여 번 이어 물어 자세를 하나 고른다. 200그루의 답(확률)을 평균해서 가장 높은 자세를 고르고, 그 평균 확률이 '확신도'다.")}
{step("2-1", "입력과 출력", f"""
<p><b>입력</b>: 두 센서의 64칸 거리를 이어 붙인 <b>128개 숫자</b>(tof1 0~63 + tof2 0~63, 단위 mm, 측정 실패·침대 밖 = -1).</p>
<p><b>출력</b>: 6가지 중 하나 + 확신도 — empty(빈 침대) · supine(바로 누움) · side_left · side_right(옆으로 누움) · sitting(앉음) · edge_sit(걸터앉음).</p>""",
 "", py(PP, "frame_vec", "frame_vec() — 128개 입력 만들기(ROI 적용)") + py(PP, "predict", "predict() — 200그루 평균 확률 → 자세·확신도"))}
{step("2-2", "나무 하나는 어떻게 만들어지나 — 지니 불순도", f"""
<p>학습 데이터(자세 라벨이 붙은 프레임 수만 개)를 놓고, '어느 칸의 몇 mm로 나누면 양쪽이 가장 한 자세로 깨끗하게 모이나'를 찾아 반복해서 나눈다. '깨끗함'은 지니 불순도로 잰다.</p>
{formula("지니 불순도 G = 1 − Σ<sub>k</sub> p<sub>k</sub><sup>2</sup>   (p<sub>k</sub> = 그 묶음 안 자세 k의 비율)<br>모두 같은 자세면 G=0, 6자세가 똑같이 섞이면 G=1−6×(1/6)²≈0.83<br>나누기 선택 = (나누기 전 G) − (나눈 뒤 두 묶음 G의 가중평균)이 가장 큰 칸·기준값")}
<p><b>숲(Forest)이 되는 이유</b>: 나무마다 학습 데이터를 무작위로 다시 뽑고(부트스트랩), 질문 후보 칸도 무작위로 일부만 보게 해서 200그루가 서로 다르게 자란다. 한 나무가 틀려도 평균이 덜 틀린다(Breiman 2001).</p>
{rf_html}""",
 f"<ul><li>scikit-learn <code>RandomForestClassifier(n_estimators=200, class_weight='balanced', random_state=0)</code> — 자세별 데이터 수가 달라도 같은 무게로 배우게 balanced.</li><li>예측은 단일 스레드({ref(PP, r'model.n_jobs = 1', 'n_jobs=1')}): 프레임 하나에 병렬은 오히려 느렸다(응답 0.5초 → ESP 프레임 손실).</li></ul>",
 py(TR, "make_model", "make_model() — 모델 설정"))}
{step("2-3", "학습 데이터와 정확도 — 숫자를 어떻게 읽어야 하나", f"""
<p>데이터: {ref("TOF/record_posture.py")}로 5초 뒤 30초씩 녹화(TOF/dataset_v2). 학습 {pr['trained_at'][:10]} · 두 센서 프레임을 250ms 안에서 짝지은 <b>{pr['n_pairs']:,}쌍</b> · 피험자 {', '.join(pr['subjects'])}(없음=빈 침대).</p>
<p>검증: 같은 자세를 3회씩 찍은 '회차'를 묶음으로 하나씩 빼고 학습→평가(group={pr['group']}). <b>정확도 {pr['overall_accuracy']*100:.2f}%</b>.</p>
{table(["실제 \\ 예측"] + pr["confusion_labels"], cm_rows)}""",
 "", py(TR, "groups_for", "groups_for() — 평가 묶음 나누기"))}
{warn(esc(pr.get("note", "")) + " 즉 99.99%는 '같은 사람·같은 날 다른 회차'를 맞힌 숫자다. 다른 사람·이불·다른 침대에서의 정확도는 아직 모른다. 사람 2명 이상으로 '사람 단위 빼고 평가(LOSO)'를 해야 일반화 정확도를 말할 수 있다.")}
{step("2-4", "모델 결과를 화면용으로 다듬기 — 빈 침대 게이트·뒤척임·가장자리", f"""
<ol><li><b>빈 침대 게이트</b>: 두 센서 사람 칸이 0개면 모델 답을 무시하고 empty(확신 1.0). 모델이 빈 침대를 '발쪽 걸터앉음'으로 헷갈린 적이 있어서.</li>
<li><b>뒤척임</b>(2026-10-14 재설계, server/tof_toss.py): 자세가 아니라 <b>누운 채 자세를 바꿀 때의 몇 초 움직임</b>. ToF 침대 칸 모양이 마지막으로 멈춰 있던 모양과 45mm 넘게 달라지면 '움직임 시작', 1초 사이 차이가 35mm 미만으로 1.5초 이어지면 '멈춤'. 멈췄을 때 전·후 자세가 다른 누운 자세이고 멈춘 모양도 35mm 넘게 달라졌으면 그 움직인 구간이 뒤척임이다. 움직임 없이 자세 이름만 바뀐 것(모델이 왼쪽↔오른쪽을 헷갈림)은 세지 않는다. 기준값은 S01·S02 녹화(같은 자세 30초 안 차이 95%≤47·63mm, 잡음 19mm)로 정했다. 화면에는 왼쪽·오른쪽 누움을 '옆으로 누움' 하나로 보여준다.</li>
<li><b>가장자리 쏠림</b>: 누운 자세이고 사람 칸 ≥6개이며, 사람 칸들이 닿은 지점의 좌우 평균 |Y| &gt; 0.25m가 2번 연속이면 '침대 가장자리에 쏠려 누움 — 낙상 위험'.</li></ol>""",
 "<ul><li>자세 계산은 새 ToF 프레임이 왔을 때만 한 번(프레임 캐시) — 화면 조회·침상 알림이 같은 결과를 공유.</li><li>EDGE_Y 0.25m는 실측 튜닝 필요(침대 폭 0.95m 가정).</li></ul>",
 py(T, "_posture_fields") + py(T, "_toss_update") + py(T, "_body_mean_y") + py(T, "_fall_risk"))}
{cite("Breiman L. Random Forests. Machine Learning 2001;45:5–32. · 데이터·평가 파일: TOF/ml/posture_model_v2_report.json")}
""")


def part_anomaly(f):
    lr = f["lstm_report"]
    return part("anomaly", "3. 움직임 이상 탐지 — LSTM 오토인코더(참고 신호)", "평소 움직임을 다시 그려 보게 해서 못 그리면 '이상'", f"""
{easy("평소 모습만 보여주며 '요약했다가 다시 그려 보기'를 연습시킨 신경망이다. 평소와 비슷한 4초는 잘 다시 그리고(오차 작음), 처음 보는 이상한 움직임은 못 그린다(오차 큼). 그 오차가 기준을 넘으면 '이상'. 지금은 화면 판정에 쓰지 않는 참고 신호다.")}
{step("3-1", "입력 — 프레임 하나를 12개 숫자로 요약", f"""
<p>사람 칸(기준보다 가까운 칸, 침대 범위 안)을 roi.py로 3D 점으로 바꾼 뒤:</p>
{table(["특징", "뜻"], [["n_pts", "사람 점 개수"], ["cx, cy, cz", "점들의 평균 위치(앞뒤·좌우·높이, m)"], ["sx, sy", "앞뒤·좌우 퍼짐(표준편차)"],
                       ["x/y/z_range", "최댓값−최솟값"], ["in_bed", "재실(1/0)"], ["occ1, occ2", "센서별 사람 칸 수"]])}
<p>tof2(머리) 프레임이 올 때만 하나씩 쌓는다(두 센서 짝 기준, 초당 약 9개). 최근 <b>{lr['seq']}개 ≈ 4초</b>가 한 묶음.</p>""",
 "", py(AN, "frame_feature"))}
{step("3-2", "모델 — 인코더 LSTM → 48개 요약 → 디코더 LSTM", f"""
<p>LSTM은 '순서가 있는 데이터'를 앞에서부터 읽으며 기억을 갱신하는 신경망이다. 인코더가 36×12 숫자를 48개로 압축하고, 디코더가 그 48개만 보고 36×12를 다시 만든다.</p>
{formula("정규화: x' = (x − 평균) / 표준편차   (학습 데이터 기준)<br>오차 = 평균((다시 만든 값 − x')²)   → 오차 &gt; 임계 {lr['threshold']} 이면 anomaly, 임계의 70% 초과면 warn")}
<p>임계는 학습에 안 쓴 정상 검증 데이터 오차의 <b>99번째 백분위</b>다 → 정상에서도 약 1%는 이상으로 나온다(검증 {lr['val_over_threshold']*100:.0f}%).</p>""",
 f"<ul><li>학습 {lr['trained_at'][:10]}, 학습 묶음 {lr['train_windows']}개 · 검증 {lr['val_windows']}개, Adam lr=1e-3, 40 epoch.</li><li>점수 계산은 별도 스레드가 0.5초마다({ref(T, r'^def _anomaly_worker', '_anomaly_worker')}): 요청 처리 중에 돌리면 수신이 12Hz→5Hz로 느려졌다.</li><li>확인: 예전 20° 설치 데이터는 100% 이상, 발 센서를 가린 다른 사람 데이터는 0% — 설치가 바뀌면 다시 학습해야 한다.</li></ul>",
 py(LA, "LSTMAE", "LSTMAE — 모델 구조") + py(AN, "Detector.score_window", "score_window() — 오차와 판정") + block("TOF/ml/train_lstm_ae_v2.py", r"thr = float\(np.percentile", r"thr = float\(np.percentile", "임계값 = 검증 오차 99백분위"))}
{warn(esc(lr.get("note", "")) + " 화면·알림에는 쓰지 않는다(/tof/anomaly의 lstm 항목).")}
{cite("Hochreiter S, Schmidhuber J. Long Short-Term Memory. Neural Computation 1997;9(8):1735–1780. · Malhotra P et al. LSTM-based Encoder-Decoder for Multi-sensor Anomaly Detection. arXiv:1607.00148 (2016).")}
""")


def part_mmwave(f):
    r = f["radar"]
    return part("mmwave", "4. mmWave 레이더 — 바이트에서 '방 안 사람 좌표'까지", "주파수로 거리를 재는 원리, 바이트 해석, 좌표 변환, 오늘 고친 것", f"""
{easy("60GHz 전파를 쏘고 반사파를 받아 거리·방향·속도를 잰다. 레이더 칩 안 프로그램(TI 사람 추적)이 반사점들을 묶어 '사람 1명=추적 1개'로 만들고 위치·속도를 보낸다. 우리는 그 값을 바닥 기준 좌표로 바꿔 쓴다. 벽·바닥처럼 안 움직이는 것은 일부러 지운다 — 그래서 바닥은 못 본다.")}
{step("4-1", "원리와 지금 설정의 사양", f"""
<p>FMCW: 주파수가 시간에 따라 일정하게 올라가는 짧은 신호(처프)를 쏜다. 반사파는 늦게 돌아오니 같은 순간 주파수가 조금 다르다. 그 차이(비트 주파수)가 거리에 비례한다.</p>
{formula("거리 R = c · f<sub>비트</sub> / (2 · S)    (c 빛의 속도, S 주파수 기울기)<br>최대 거리 = 샘플링 속도 × c / (2S) = {r['fs']:.0f}k × 3×10⁸ / (2 × {r['slope']} MHz/µs) = <b>{r['max_m']:.1f} m</b><br>거리 해상도 = c / (2 × 대역폭) = c / (2 × {r['bw_mhz']:.0f} MHz) = <b>{r['res_cm']:.1f} cm</b> · 프레임 {r['frame_ms']:.0f} ms = <b>초당 {r['fps']:.1f}번</b>")}
<p>방향: 칩 안 안테나(송신 3·수신 4, Antenna-on-Package)에 도착 시간이 미세하게 달라 좌우·위아래 각도를 계산한다. 속도: 같은 점의 위상 변화(도플러).</p>""",
 f"<ul><li>설정 파일 {ref(CFG)}: 시작 {r['start_ghz']} GHz, sensorPosition {esc(r['sensor'])}(높이 2.5m, 아래로 50°), boundaryBox {esc(r['boundary'])}.</li><li>이미 동작 중인 레이더에 설정을 다시 보내면 출력이 멈춘다 → {ref(S, r'^def stream_active', 'stream_active()')}가 데이터가 나오면 보내지 않는다. 설정 변경은 레이더 USB를 뺐다 꽂아야 적용된다.</li></ul>",
 block(CFG, r"^profileCfg", r"^frameCfg", "처프·프레임 설정") + block(CFG, r"존 포맷", r"^presenceBoundaryBox", "추적 범위·설치값(2026-10-12 넓힘)"))}
{step("4-2", "원시 바이트 해석 — 매직워드·헤더·TLV", f"""
<p>USB(921600bps)로 바이트가 끝없이 들어온다. 프레임 시작 표시 <b>매직워드 02 01 04 03 06 05 08 07</b>을 찾고, 40바이트 헤더에서 프레임 길이·TLV 개수를 읽고, TLV(종류·길이·내용) 묶음을 하나씩 꺼낸다.</p>
{table(["TLV 종류", "내용", "사용"], [["1010", "추적 목록: 1명당 112바이트 = 번호(정수) + 실수 27개(위치 xyz·속도·가속도·…)", "위치·속도 → 모든 판정"],
                                    ["1020", "압축 반사점(고도각·방위각·도플러·거리·세기)", "진단 화면·보정 분석"],
                                    ["1011/1012/1021", "점 소속 번호·높이·존재 여부", "사용 안 함"]])}
<p>빈 슬롯(NaN·100m 초과·번호 252 초과)은 버린다.</p>""",
 "", py(S, "iter_frames") + py(S, "parse_frame_header") + py(S, "parse_target_list"))}
{step("4-3", "좌표 변환 — 오늘 가장 많이 고친 곳", f"""
<p><b>문제 1(2026-10-12)</b>: 설정에 높이·각도를 넣어도 이 펌웨어의 추적 좌표는 <b>레이더 시선 기준</b>이었다(침대에 앉은 사람 높이 −0.3m). → 우리가 직접 바닥 기준으로 회전한다.</p>
{formula("a = 하향각 50°,  레이더 좌표 (x, y, z) →<br>바닥 거리 Y = y·cos a + z·sin a,   높이 Z = 2.5 − y·sin a + z·cos a,   좌우 X = x")}
<p><b>문제 2</b>: 레이더가 <b>칩 면 안에서 180° 뒤집혀</b> 달려 있었다. 보정 측정(키 170cm가 바닥 거리 2m·3m에 서기): 사람이 시야 중심보다 위(+11~+32°)여야 하는데 −24.6°·−27.5°로 보고 → 위아래·좌우 부호를 뒤집으면 몸 중심 1.23m·1.05m로 맞음. 기본값 '거꾸로 보정'(바로 달면 <code>--upright</code>).</p>
<p><b>문제 3</b>: 펌웨어도 같은 착각으로 사람을 '바닥 아래'로 계산해 추적 범위 밖이라며 버렸다(3m 밖 추적 0회) → 추적 범위 높이를 −4~4m로 넓혔다. 이후 1m 옆으로 옮기면 정확히 1.00m 이동, 테이블에 앉은 사람(4.6m)도 추적.</p>""",
 "<ul><li>반사점(TLV 1020)도 같은 회전·부호 보정({ref(DD, r'^def decode_frame', 'decode_frame')}).</li><li>평면도에선 레이더 위치·방향(simulation.js ROOM, 45°)으로 다시 방 좌표로 바꾼다(9장).</li></ul>",
 block(S, r"^# 레이더를 거꾸로", r"^MOUNT_UPSIDE_DOWN", "거꾸로 장착 근거") + py(S, "track_to_world"))}
{step("4-4", "누가 환자인가 — 침대 소속도로 고르기", f"""
<p>추적이 1명이면 그 사람. 2명 이상이면 최근 60초 동안 '침대 영역 안에 있던 비율'(소속도)이 0.25 이상인 사람이 <b>딱 1명</b>일 때만 환자로 확정. 애매하면 추측하지 않고 보류(틀린 데이터보다 없는 게 낫다).</p>
<p><b>보행 구간</b> 조건: 6초 평균 속도 ≥ 0.20 m/s · 순이동 ≥ 0.30 m · 직진성(순이동/이동거리) ≥ 0.5. 이 구간만 기준선·추세에 쓴다.</p>""",
 f"<ul><li>⚠ 침대 영역 {ref(S, r'^BED_X', 'BED_X/BED_Y')}는 2026-07 예전 설치·좌표계 값이다. 레이더를 발치 구석으로 옮기고 좌표 변환을 고친 뒤 다시 재지 않았다 → 재측정 필요(환자 확정·보행 추세 입력에 영향).</li></ul>",
 py(S, "select_patient"))}
""")


def part_gait(f):
    return part("gait", "5. 보행 지표와 평소 대비 판정 — 6초마다 6개 숫자, Z-score", "걸음을 숫자로 바꾸는 식과 '평소와 다름'을 재는 식", f"""
{easy("환자 추적의 최근 6초 위치·속도로 걸음 숫자 6개를 만든다. 그 사람의 평소 평균·흔들림(표준편차)과 비교해 '평소에서 몇 표준편차 벗어났나'(Z-score)를 재고, 6개를 더한 값으로 정상·주의·경고·위험을 정한다. 낙상 확률이 아니라 '평소와 다른 정도'다.")}
{step("5-1", "6개 지표 계산식", table(["지표", "식", "의미"], [
    ["speed", "평균 √(vx²+vy²)", "보행 속도(m/s)"],
    ["speed_cv", "속도 표준편차 ÷ 평균", "속도가 들쭉날쭉한 정도"],
    ["sway", "처음→끝 직선에서 옆으로 벗어난 거리의 표준편차", "좌우 흔들림(m)"],
    ["freeze_ratio", "속도 &lt; 0.15 m/s 인 샘플 비율", "멈칫거림"],
    ["stride_length", "평균 속도 ÷ 걸음 주파수(FFT 0.5~3Hz 최대 피크)", "보폭(m)"],
    ["stride_cv", "걸음 주기마다 자른 보폭의 변동계수", "보폭 불규칙성"]]) +
 "<p>보폭은 직접 재지 못해서, 걸을 때 몸통 속도가 한 걸음마다 오르내리는 주기를 FFT로 찾아 '속도 ÷ 걸음수'로 추정한다. 피크가 주변 평균의 2배가 안 되면 0(추정 실패).</p>",
 "", py(S, "_gait_from_samples") + py(S, "_estimate_stride"))}
{step("5-2", "평소 기준(baseline)과 Z-score", formula(
    "평소 평균 μ = Σx / n,   표준편차 σ = √(Σx²/n − μ²)  (단, σ ≥ 지표별 하한 — 처음엔 0에 가까워 Z가 폭발하므로)<br>"
    "Z<sub>i</sub> = (지금 값 − μ<sub>i</sub>) / σ<sub>i</sub>,   S = Σ|Z<sub>i</sub>|  (6개)<br>"
    "S &lt; 3 정상 · 3~6 주의 · 6~9 경고 · ≥ 9 위험   (지표 4개 시절 2/4/6에서 ×1.5)") +
 "<p>누적 14일·5건 이상이 되기 전엔 정식 알람을 보류한다. 그 전에는 입원 기록·문헌으로 만든 '기대 범위'와 비교한 <b>잠정</b> 판정만(주의·경고까지) 낸다(server/gait_prior.md).</p>",
 "<ul><li>느려져도 빨라져도 |Z|로 똑같이 '다름'으로 센다.</li><li>보행 구간이 아니거나 환자가 확정 안 된 6초는 기준선에 넣지 않는다.</li></ul>",
 py(ML, "update_baseline") + py(ML, "_mu_sigma") + py(ML, "compute_zscore") + py(ML, "classify_alert"))}
""")


def part_fall(f):
    c, rep = f.get("fall_consts", {}), f.get("fall_replay", {})
    if "error" in rep:
        rep_html = f"<p class='muted'>재생 못 함: {esc(rep['error'])}</p>"
    else:
        names = {"slow2m": "천천히 눕기(레이더 약 2.7m)", "slowfar": "천천히 눕기(약 3.3m)", "bedside": "침대에서 일어나 옆 바닥에 천천히",
                 "fast": "빠르게 주저앉아 눕기(매트)", "posR2": "서 있기만(본인 + 테이블에 앉은 사람)"}
        rep_html = table(["실측 기록(2026-10-12)", "판정"], [[names.get(n, n), "; ".join(
            f"{s}{'('+k+')' if k else ''} {t}초 · 몸 중심 {z}m/평소 {rf}m" + (f" · 하강 {d}초" if d is not None else "")
            for t, s, k, d, lo, z, rf in evs) or "판정 없음(정상)"] for n, evs in rep.items()])
    return part("fall", "6. 낙상 감지 — 본인 평소 높이 대비 '얼마나 빨리, 얼마나 낮게'", "실측으로 정한 규칙, 오탐을 고친 기록, 코드", f"""
{easy("레이더는 바닥을 못 보고, 설치 각도가 조금만 틀려도 '바닥에서 몇 m'가 틀어진다. 그래서 바닥 높이 대신 <b>그 사람이 서 있을 때 높이(평소 높이)</b>를 기준으로 비율을 본다. 1초 안에 평소의 25% 높이까지 떨어지면 즉시 낙상, 천천히 내려가도 25% 아래에서 3초 넘게 못 일어나면 낙상. 침대 위(ToF가 재실이라고 볼 때)는 제외한다.")}
{step("6-1", "실측으로 정한 숫자", table(["자세(키 170cm)", "몸 중심 높이(레이더 추적 z)", "평소 대비"], [
    ["서 있음", "1.25~1.60 m (레이더 가까울수록 낮게)", "100%"], ["침대에 걸터앉음", "약 0.6 m", "≈45%"],
    ["의자에 앉음", "약 0.45 m", "≈40%"], ["무릎 꿇음", "0.43 m", "≈29%"], ["바닥에 누움", "−0.2~0.28 m", "0~19%"]]) +
 table(["평소 80% → 25% 하강 시간", "값"], [["빠르게 주저앉기", "0.88초 (80%→40%는 0.38~0.63초)"], ["천천히 내려가기 3회", "1.26~3.07초"],
                                        ["보통 의자에 앉기(라이브)", "80%→40% 0.2~0.4초 — 빠르게 주저앉기와 구분 안 됨"]]),
 "<ul><li>평소 높이(ref) = 그 추적이 <b>걷는 중</b>(초당 0.4m 이상 이동)일 때 몸 중심 높이의 중앙값 → 사람마다 키에 맞게 자동(2026-10-13: 예전 '최근 3분 상위 25%'는 걸을 때 위로 튀는 값 때문에 1.5~1.8m로 잡혀 바닥 기준이 의자 높이까지 올라갔다). 걷는 기록이 아직 없으면 최근 3분 75% 분위수(최소 1.1m), 그것도 적으면 1.3m.</li><li>순간 튐 제거: 최근 0.3초 중앙값.</li></ul>")}
{step("6-2", "판정 규칙(2026-10-13 재설계)", table(["단계", "조건"], [
    ["쓰는 데이터", "추적(1010) + 반사점(1020) + 반사점 소속(1011, 직전 프레임 반사점 기준) + 추적 높이(1012). 추적마다 이번 프레임 반사점 수·머리 쪽 높이(상위 10%)·세로 속도를 붙인다"],
    ["환자 추적", "2026-10-16: ① ToF가 침상 이탈을 잡은 뒤 8초 안 침대 1.5m 안 사람 = 침대에서 내려온 사람 → 고정(다시 누우면 해제) ② 평면도에서 점을 눌러 지정 ③ 환자 추적이 사라지면 3초 안 1m 안 추적이 이어받음 ④ 고정 안 됐을 때만 침대 3m 안 가장 가까운 사람 3초 규칙. 환자가 바뀌기 전에 시작된 하강은 넘긴다. 낙상 판정은 환자 추적만"],
    ["서 있던 기록", "같은 사람(거쳐 온 추적 번호)의 것만 쓴다 — 예전엔 사라진 아무 추적의 기록도 써서, 앉아 있던 관객 옆을 지나간 사람의 기록으로 낙상이 됐다(10-16 발표 기록 20건 중 다수). 이어받은 추적은 이어받은 뒤 반사점이 3프레임 이상 잡혀야 낙상"],
    ["그림자 제거", "다른 추적과 수평 0.7m 안에서 0.8m 이상 낮게 같이 있는 추적(바닥 반사) → 판정 안 함. 최근 5초 걷는 중 높이가 0.5m 미만인 추적 → 환자로 고르지 않음(10-13: 3분 기록으로 판단했더니 바닥 반사를 따라가다 진짜 사람으로 갈아탄 추적을 계속 가짜로 봐 낙상 2건을 놓쳤다)"],
    ["거리 보정", "멀수록 몸 중심이 높게 보인다(10-13 실측 1.2·2.3·3.3·4.2m에서 1.21·1.27·1.38·1.53m, 1m당 약 0.1m) → 모든 높이를 '2m에 섰을 때' 기준으로 비율 보정(높이 × (1.07+0.106×2)/(1.07+0.106×거리))"],
    ["값 고정 제외", "반사점이 0.5초 넘게 없으면 측정 없음. 서 있음→바닥 높이 하강은 반사점 있는 프레임으로 확인돼야 한다(넘어지며 추적 번호가 바뀐 경우, 바닥에 있던 추적이 그 자리에서 넘어진 사람을 이어받은 경우는 허용)"],
    ["낙상", f"평소 {int(c.get('HIGH_FRAC', 0.8)*100)}% 이상 → {int(c.get('LOW_FRAC', 0.25)*100)}% 이하로 내려가 {c.get('CONFIRM_SEC')}초 머묾 + 마지막 1.5초 동안 0.5m 안에 멈춤(넘어질 때의 쏠림·구름은 허용, 계속 걸으면 확정 안 됨)"],
    ["라벨", "1초 이내 하강이고 세로 속도가 초당 1.5m 이상 아래(10-13 실측: 쪼그림 −1.25, 매트 주저앉기 −1.73) → 급격한 하강, 그 외 → 못 일어남(낙상 여부는 같음)"],
    ["주의", "바닥 높이에 1초 이상 있다가 3초 안에 일어남(쪼그림 등) → activity_log fall_dip(주의). 그 사이 0.5m 넘게 움직였으면(걷는 사람) 남기지 않음"],
    ["일어남", f"평소 {int(c.get('RECOVER_FRAC', 0.6)*100)}% 이상으로 1초 이상"],
    ["침대 위 제외", "서버가 ToF 재실이면 suppressed로 기록만, 알림·점수 없음"]]),
 "<ul><li>낙상이면: activity_log fall_detected(위험) + 위험도 +10(10분에 한 번) + 대시보드 팝업·빨간 '낙상' 표시.</li>"
 "<li>라이브 오탐 7건 분석(판정 전후 9초 기록): 바닥 반사 그림자 3, 쪼그렸다 일어남 2, 걷는 중 높이 하락 1, 값 고정 1 → 새 판정기로 다시 돌리면 그림자·걷기 0건, 쪼그림 2건은 주의로 바뀜.</li>"
 "<li>예전에 고친 것: 고정 0.4m 규칙 폐기, 평소 높이 하한 1.1m, 의자 높이(40%) 급하강 규칙 삭제, 평소 높이 = 걷는 중 높이 중앙값.</li></ul>",
 block(FD, r"^REF_DEFAULT", r"^BED_CENTER", "설정값") + py(S, "extract_extras", "extract_extras() — 반사점·소속·추적 높이 읽기") + py(S, "PointLinker", "PointLinker — 반사점을 추적에 연결")
 + py(FD, "FallDetector._choose_patient", "_choose_patient() — 환자 추적 고르기") + py(FD, "FallDetector.update", "update() — 그림자 표시·환자 선택")
 + py(FD, "FallDetector._step", "_step() — 프레임마다 판정") + py(M, "receive_fall", "receive_fall() — 서버: ToF 침대 제외·기록·점수"))}
{step("6-3", "검증 — 실측 기록 재생 결과(생성 시점 자동 계산)", rep_html + "<p>10-13 라벨 녹화(침대 앉기·1~4m 서기·걷기·쪼그림·의자·천천히 눕기·매트 주저앉기, 랩실에 다른 사람 있음) 재생: 천천히 눕기 → 낙상(못 일어남), 매트 주저앉기 → 낙상(급격한 하강, 세로 속도 −1.73m/s), 쪼그림 4초 → 주의, 걷기·의자·짧은 쪼그림 → 없음. 수정 전 실시간 판정은 두 낙상을 모두 놓쳤다.</p><p>자동 테스트: mmWave/tests/test_fall_detect.py (실측 재생 + 10-13 라벨 녹화 + 그림자·환자 선택·다른 사람 낙상·값 고정·걷는 중 높이 하락·쪼그림 주의 등 합성 사례).</p>")}
{warn("검증은 키 170cm 젊은 사람 1명의 동작 재연이다. 어르신의 실제 낙상, 의자에 털썩 앉기 구분, 여러 명이 있는 방(누구든 판정함)은 검증 전이다. 레이더 설치(높이·각도·방향)가 바뀌면 다시 측정해야 한다.")}
""")


def part_trend(f):
    g = f.get("gt", {})
    if "error" in g:
        sim = f"<p class='muted'>합성 시험 계산 못 함: {esc(g['error'])}</p>"
        bt = ""
    else:
        sim = table(["하루 보행 구간 수", "구간 측정 잡음", "평탄할 때 오탐", "완만 감소(7일 −3.5cm/s) 감지", "급감(7일 −7cm/s) 경고", "급감 주의 이상"],
                    [[str(p), f"{n:.2f} m/s", f"{fp}/{k}", f"{mc}/{k}", f"{fw}/{k}", f"{fc}/{k}"] for p, n, k, fp, mc, fw, fc in g["table"]])
        b = g["backtest"]
        bt = f"<p>과거 재현 검증(합성: 40일 동안 하루 0.2cm/s 감소): 7일 앞 예측 <b>평균 오차 {b.get('mae', 0)*100:.1f} cm/s</b>, 80% 범위 적중률 {b.get('coverage80', 0)*100:.0f}% (예측 {b.get('n')}회).</p>"
    return part("trend", "7. 보행 저하 예측 — 칼만 필터로 7·14일 뒤를 예측", "어떤 알고리즘인지, 어떤 논문의 몇 % 결과를 근거로 했는지, 우리 시험에서 얼마나 맞는지", f"""
{easy("매일 '그날 곧게 걸은 구간들의 보행 속도·보폭 중앙값'을 하나씩 만든다. 이 하루 값들은 측정 잡음으로 들쭉날쭉하므로, 칼만 필터로 '진짜 수준'과 '하루에 몇 cm/s씩 변하는지'를 오차 범위와 함께 추정한다. 그 추세를 앞으로 늘려 7·14일 뒤를 예측하고, 7일 동안 얼마나 줄었는지를 논문의 위험 기준과 비교한다.")}
{step("7-1", "근거 논문 — 왜 '7일 동안 얼마나 줄었나'인가", table(["논문", "연구", "결과(원문 확인)"], [
    ["<b>Phillips LJ et al.</b> West J Nurs Res 2017;39(1):78–94 (PMC5272917)", "노인 거주시설 집 안에 Kinect로 24시간 보행 측정, 23명·낙상 69회, 로지스틱 회귀",
     "7일 동안 보행 속도 <b>5.1 cm/s 감소 → 3주 안 낙상 확률 86.3%</b>(변화 없음 19.5%), <b>AUC 0.86</b> · 2.54 cm/s 감소 → 낙상 오즈 4.22배 · 보폭 <b>7.6 cm 감소 → 50.6%</b>(11.4%), <b>AUC 0.88</b> · 하루 추정값은 14일 창으로"],
    ["Verghese J et al. J Gerontol A 2009;64A(8):896–901", "70세 이상 597명, 평균 20개월 추적", "보행 속도 10 cm/s 느릴수록 낙상 위험비 1.069(95% CI 1.001–1.142), 보폭 변동성도 낙상 예측"],
    ["Hausdorff JM et al. Arch Phys Med Rehabil 2001;82:1050–1056", "70세 이상 52명 1년 전향", "보폭 시간 변동성: 낙상자 106±30ms vs 비낙상자 49±4ms, 낙상 예측(p&lt;.05)"],
    ["Dodge HH et al. Neurology 2012;78(24):1946–1952", "집 천장 적외선 센서로 93명 주간 보행 추세", "비기억성 경도인지장애는 '느린 보행 궤적'일 가능성 9배, 속도 변동성도 관련"],
    ["Kuys SS et al. JAMDA 2014 (gait_evidence.json)", "요양시설 노인 메타분석", "평소 보행 속도 0.475 m/s → 0.8 m/s 같은 고정 기준은 대부분을 위험으로 만듦 → '본인 대비 감소'를 쓰는 이유"]]) +
 "<p>그래서 <b>주의</b> = 7일 누적 감소가 2.54 cm/s(보폭 2.54 cm) 이상일 확률 ≥80%, <b>경고</b> = 5.1 cm/s(보폭 7.6 cm) 이상일 확률 ≥80%. 하루 대표값은 Phillips처럼 최근 14일 이상 기록이 있어야 계산한다.</p>",
 "<ul><li>논문의 86.3%는 'Kinect·그 시설·그 사람들'의 결과다. 이 환자의 낙상 확률로 쓰면 안 된다 — 리포트 프롬프트에도 금지 문구를 넣었다.</li></ul>")}
{step("7-2", "1단계: 하루 대표값 만들기", f"""
<p>mmw_readings에서 최근 60일 중 <b>신뢰 가능·보행 구간·환자 확정</b>인 6초 구간만(2026-10-13부터 walking·patient_locked 열 저장, 그 전 행은 속도 ≥0.2·보폭 &gt;0으로 대신 거름). 하루 구간이 {g.get('consts', {}).get('MIN_WINDOWS_PER_DAY', 5)}개 미만이면 그날은 버린다.</p>
{formula("그날 대표값 = 구간 값들의 중앙값,   그날 오차 se = max(MAD×1.4826, 0.10) / √구간 수")}""",
 "<ul><li>중앙값·MAD: 튀는 구간 몇 개에 흔들리지 않게.</li><li>0.10 하한: 구간이 적은 날 오차를 과소평가해 오탐이 나던 것(5구간·잡음 0.15에서 6/40) 방지.</li></ul>",
 py(GT, "_usable") + py(GT, "daily_series"))}
{step("7-3", "2단계: 국소 선형 추세 칼만 필터", formula(
    "상태 x = [수준 L, 하루 기울기 b]<br>"
    "예측(하루 지남): L ← L + b,  b ← b,   P ← F P Fᵀ + Q,   F = [[1,1],[0,1]],   Q = diag(0.005², 0.001²)<br>"
    "갱신(그날 값 y): K = P Hᵀ / (H P Hᵀ + R),   x ← x + K (y − L),   P ← (I − K H) P,   H = [1,0],   R = max(se², 최소오차²)<br>"
    "k일 뒤 예측: 예측 단계를 k번 반복 → 평균 L + k·b,  80% 범위 = 평균 ± 1.2816·√P₀₀<br>"
    "7일 누적 변화 Δ = L<sub>오늘</sub> − L<sub>7일 전</sub>,   σ<sub>Δ</sub> = √(분산<sub>오늘</sub> + 분산<sub>7일 전</sub>),   P(Δ ≤ −기준) = Φ((−기준 − Δ)/σ<sub>Δ</sub>)") +
 "<p>칼만 필터는 '어제까지의 추정 + 오늘 측정'을 각각의 불확실성 비율로 섞는다. 측정이 흔들리면(se 큼) 추정을 덜 바꾸고, 측정이 확실하면 많이 바꾼다. 기록 없는 날은 예측 단계만 거친다. 입원 기록 기반 기대 범위(gait_prior)가 있으면 처음 수준의 출발점으로 쓴다.</p>",
 "<ul><li>처음엔 '7×기울기'로 7일 변화를 계산했는데 기울기가 늦게 반응해 급감(하루 1cm/s)을 경고 0/30만 잡음 → 수준 차이로 바꿔 23/30.</li><li>Q 값은 아래 합성 시험으로 정했다(평탄 오탐 0을 유지하면서 감지율이 가장 높은 값).</li></ul>",
 py(GT, "kalman_trend") + py(GT, "_metric_assessment"))}
{step("7-4", "우리 시험에서 얼마나 맞나", sim + bt + "<p>읽는 법: 하루 걷는 구간이 많을수록(데이터가 많을수록) 잘 잡는다. 완만 감소는 주의 기준(2.54)을 겨우 넘는 경우라 80% 확신 규칙 때문에 절반 정도만 잡는다 — 오탐을 줄인 대가다.</p>",
 "<ul><li>자동 테스트: server/test_gait_trend.py(합성 20회 통계 판정, 보행 아님·환자 미확정 제외, 구간 부족한 날 제외, 과거 재현 검증, 기대 범위 사용).</li></ul>",
 py(GT, "backtest"))}
{step("7-5", "어디에 쓰이나", """
<ol><li><b>AI 리포트</b>: 환자 상태에 '보행_추세_예측'으로 들어간다. 프롬프트가 '향후_위험_가능성'은 이 수치·범위를 그대로 인용하고 숫자를 만들지 말며, 논문 확률을 환자 확률로 쓰지 말라고 지시한다. 기록이 14일 미만이면 '예측하지 않음'.</li>
<li><b>위험도 점수</b>: 경고면 변동 점수 +5(하루 한 번) → 위험군이 오르면 침상 이탈 알림이 한 단계 강해진다(8장).</li>
<li><b>대시보드</b>: 환자 화면 '보행 추세 예측' 카드 — 하루 값(점)·추정 수준(선)·14일 예측과 80% 범위(띠).</li></ol>""", "",
 py(GT, "assess") + py(DA, "_run_gait_trend_signals", "_run_gait_trend_signals() — 매시간 확인, 경고면 +5") + js(DBJ, r"function renderGaitForecast", "renderGaitForecast() — 대시보드 카드"))}
{warn("우리 레이더 데이터로는 아직 검증 전이다(실측 환자 기록 2일). 합성 시험은 알고리즘이 의도대로 동작하는지만 보여준다. 2~3주 매일 보행 기록(평소 걷기 + 이중 과제 걷기로 인위적 저하)을 쌓아 과거 재현 검증 수치를 우리 데이터로 다시 내야 '예측 정확도'를 말할 수 있다.")}
{cite("Kalman RE. A New Approach to Linear Filtering and Prediction Problems. J Basic Eng 1960;82(1):35–45. · Harvey AC. Forecasting, Structural Time Series Models and the Kalman Filter. Cambridge Univ Press; 1989.")}
""")


def part_risk(f):
    return part("risk", "8. 위험도(Morse + 변동 점수)와 침상 알림", "환자마다 다른 위험군 × 거동 범위 → 같은 행동이라도 다른 알림", f"""
{easy("간호사가 정하는 Morse 낙상 척도 점수에, 센서가 본 위험 신호로 오르고 조용한 날마다 내려가는 '변동 점수'를 더한다. 이 점수로 저·중·고위험군을 나누고, 의료진이 정한 거동 범위(자유~절대 안정)와 지금 상황(앉음·걸터앉음·이탈·미복귀)을 조합해 알림 단계를 정한다.")}
{step("8-1", "Morse 낙상 척도(Morse 1989)", table(["항목", "점수"], [
    ["낙상 이력", "아니오 0 · 예 25"], ["2개 이상 진단", "0 · 15"], ["보행 보조", "없음 0 · 지팡이·보행기 15 · 가구 짚기 30"],
    ["정맥 주사", "0 · 20"], ["보행 상태", "정상 0 · 약함 10 · 장애 20"], ["정신 상태", "자기 능력 앎 0 · 한계를 잊음 15"]]) +
 "<p>합계 0~125. <b>0~24 저위험 · 25~44 중위험 · 45 이상 고위험</b>(흔히 쓰는 구분, 기관마다 다름).</p>", "",
 block(FR, r"^MORSE_ITEMS", r"^GROUP_KO", "Morse 항목·위험군"))}
{step("8-2", "변동 점수 — 센서·간호 신호", table(["신호", "점수", "같은 신호 반복 제한"], [
    ["보행 Z-score 경고", "+3", "1시간"], ["낙상 감지(레이더)", "+10", "10분"], ["간호사 낙상 기록", "+15 (Morse 낙상 이력=예)", "없음"],
    ["보행 저하 예측 경고", "+5", "하루"], ["신호 없는 하루", "−2", "—"], ["상한", "40", "—"]]) +
 "<p>대시보드 위험도 카드의 <b>'변동 점수 초기화'</b>로 변동 점수만 0으로 되돌릴 수 있다(Morse·거동 범위 유지, 기록 남음) — 오탐 정리·시험 후.</p>",
 "<ul><li>점수 폭·시간은 근거 없는 설계값이다. 실측·임상 자료로 다시 정해야 한다.</li></ul>",
 block(FR, r"^RULES = \{", r"^DECAY_PER_DAY", "변동 규칙") + py(FR, "add_signal") + py(FR, "_apply_decay") + py(FR, "reset_dynamic"))}
{step("8-3", "침상 알림 결정표", table(["거동 범위 \\ 상황", "일어나 앉음", "걸터앉음", "침상 이탈", "10분 넘게 미복귀"], [
    ["절대 안정", "주의", "경고", "위험", "위험"], ["보조 시에만", "정상", "주의", "경고", "위험"],
    ["화장실 정도", "정상", "정상", "주의", "경고"], ["자유 거동", "정상", "정상", "정상", "주의"]]) +
 "<p>고위험군이면 한 단계, 야간(22~06시)이면 한 단계 더. 상황은 ToF 자세·재실로 1초마다 정한다(tof_server의 침상 알림 작업). 이것이 '이탈 직전 행동(앉음→걸터앉음)을 먼저 감지'하는 부분이다 — 예측 모델이 아니라 전조 감지 규칙.</p>",
 f"<ul><li>⚠ 서버 재시작 직후 환자가 이미 침대 밖이면 이탈 시각을 몰라 이탈 알림이 안 뜬다(다시 들어왔다 나가면 정상).</li></ul>",
 block(FR, r"^ALERT_MATRIX", r"^NIGHT_HOURS", "결정표") + py(FR, "situation_from_tof") + py(FR, "bed_alert") + py(T, "_bed_alert_worker"))}
{cite("Morse JM, Morse RM, Tylko SJ. Development of a scale to identify the fall-prone patient. Can J Aging 1989;8(4):366–377.")}
""")


def part_dashboard(f):
    return part("dashboard", "9. 간호사 대시보드 — 받아오기·시간 맞추기·평면도", "브라우저에서 실제로 일어나는 일", f"""
{easy("화면은 두 개다. <b>간호사 대시보드</b>(/dashboard: 개요·추이·투약·AI 리포트)와 <b>개발·센서 테스트 대시보드</b>(/dev: 서비스·센서 상태, ToF 원시 8×8, 빈 침대 기준·자동 갱신, 레이더 추적·낙상 사건, 3D·포인트클라우드, 수동 캘리브레이션, 개발 도구 링크). 간호사 화면에서 브라우저가 1초마다 자세·판정 결과를, 0.2초마다 레이더 위치를 서버에서 받아 그린다. 노트북 시계와 Pi 시계가 달라도 깜빡이지 않게 Pi 시각으로 맞춰 '연결됨'을 판단하고, 레이더 좌표를 병실 평면도 좌표로 바꿔 사람 점을 움직인다.")}
{step("9-1", "받아오기와 연결 판단", """
<ul><li>1초마다: /tof/latest, /tof/presence, /mmw/latest, /mmw/live (4개 동시). 2026-10-13에 2초→1초(2초에 근거 없음). 판정 자체는 서버가 프레임마다·1초마다·6초마다 따로 계산한다 — 이 값은 '받아오기' 주기다.</li><li>0.2초마다: /mmw/live만(평면도 위치·낙상 상태). 레이더 리더는 0.1초마다 서버에 위치를 올린다.</li>
<li><b>시계 보정</b>: /tof/presence의 server_now로 'Pi 시각 − 노트북 시각'을 매번 계산해 모든 시간 판단을 Pi 기준으로. 예전엔 노트북 시계가 조금만 늦어도 최신 프레임을 '미래 값'으로 버려 연결됨↔끊김이 깜빡였다.</li>
<li>8초 안에 새 값이 있으면 '연결됨'. 끊겨도 카드 칸은 그대로 두고 값만 '—'.</li></ul>""", "",
 js(DJ, r"function parseTimestamp", "parseTimestamp() — 서버 시각 해석") + js(DJ, r"function normalizeTof", "normalizeTof() — ToF 연결·자세·위험 정리") + js(DJ, r"function pollLive", "pollLive() — 0.2초 위치 받기"))}
{step("9-2", "평면도 — 레이더 좌표 → 방 좌표", formula(
    "레이더 위치 (s<sub>x</sub>, s<sub>y</sub>), 방향 a(방 좌표계, 45°)<br>방 x = s<sub>x</sub> + y·cos a − x·sin a,   방 y = s<sub>y</sub> + y·sin a + x·cos a") +
 "<p>방 그림은 현장 사진에 맞춰 시계 방향 90°로 돌려 보여준다(침대가 위쪽 벽, 왼쪽 머리, 오른쪽 구석 레이더). 환자 추적이 확정 안 됐으면 직전 위치·침대에 가까운 추적을 고르고, 가만히 있어 추적이 잠깐 끊기면 10초간 마지막 위치를 유지한다. 낙상 판정이면 빨간 '낙상', ToF 재실이면 침대 위 표시.</p>", "",
 js(SJ, r"function mmwToRoom", "mmwToRoom() — 좌표 변환") + js(DBJ, r"function checkLiveFall", "checkLiveFall() — 낙상 팝업"))}
""")


def part_report(f):
    return part("report", "10. AI 리포트 — 계산은 서버가, 설명은 LLM이", "무엇을 모아 어떻게 검색하고 무엇을 LLM에 맡기나", f"""
{easy("위험 점수와 예측 숫자는 전부 서버가 계산한다. LLM(기본 GPT-4o-mini)은 환자 기록·센서 판정·보행 예측·검색된 논문을 읽고 '왜 그런지, 무엇을 더 볼지'를 문장으로 쓴다. 숫자를 새로 만들지 말라고 지시한다.")}
{step("10-1", "자료 모으기", "<p>환자 정보·위험인자·투약(DB), ToF·mmWave 현재 판정, 최근 24시간 요약·이벤트, 직전 리포트, 낙상 위험도 프로필, <b>보행 추세 예측</b>.</p>", "",
 py(AG, "_attach_fall_risk", "_attach_fall_risk() — 위험도·보행 예측 붙이기"))}
{step("10-2", "논문 검색 — 의미 검색 + BM25 + RRF", formula(
    "의미 검색: 질문·논문 조각을 임베딩(text-embedding-3-small) 벡터로 바꿔 가까운 순<br>BM25: 단어가 얼마나 겹치나(드문 단어일수록 가중)<br>"
    "RRF 점수 = Σ 1 / (60 + 순위)   — 두 순위를 합칠 때 점수 크기가 아니라 순위만 쓴다") +
 "<p>LLM이 환자 상태로 검색어를 만들고 → 검색 → '근거가 충분한가'를 판단해 부족하면 한 번 더.</p>", "", py(RT, "fuse_rankings"))}
{step("10-3", "위험 점수 합치기와 작성 규칙", formula("단계 → 점수: 정상 0.1 · 주의 0.4 · 경고 0.7 · 위험 0.95,   종합 = 확보된 신호 중 최댓값 (낙상 확률 아님)") +
 "<p>프롬프트 규칙: 계산된 점수·예측을 그대로 인용, 의심을 확정으로 바꾸지 않기, 이벤트 0건을 '낙상 없음'의 증거로 쓰지 않기, 보행 예측 논문 확률을 환자 확률로 쓰지 않기 등.</p>", "",
 py(IA, "compute_risk_score"))}
{cite("Robertson S, Zaragoza H. The Probabilistic Relevance Framework: BM25 and Beyond. Found Trends Inf Retr 2009;3(4):333–389. · Cormack GV, Clarke CLA, Büttcher S. Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods. SIGIR 2009.")}
""")


def part_ops(f):
    return part("ops", "11. 운영 — 서버·서비스·저장·보관", "무엇이 켜져 있어야 하고, 데이터는 어디에 얼마나 남나", f"""
{easy("Pi에서 프로그램 4개가 돈다. 레이더 리더는 꺼지면 자동으로 다시 켜지는 서비스이고, 나머지 서버는 직접 켠다. 원시 기록은 3일 지나면 매일 새벽 압축 보관된다.")}
{step("11-1", "프로세스", table(["프로그램", "포트", "켜는 법", "비고"], [
    ["tof_server.py", "5001", "cd server && ../.venv/bin/python tof_server.py", "대시보드·API 포함. torch 스레드 때문에 코드 변경 자동 재시작을 껐다 → 코드 수정 후 직접 재시작"],
    ["mmw_server.py", "5002", "같은 방식", "코드 변경 시 자동 재시작(debug)"],
    ["diagnostic_server.py", "5012", ".venv/bin/python mmWave/diagnostic_server.py", "로우데이터 화면, 메모리만"],
    ["send_mmw.py", "—", "systemd majubom-mmw-reader (자동)", "로그: journalctl -u majubom-mmw-reader -f"]]), "", "")}
{step("11-2", "저장과 보관", """
<ul><li>tof_readings: 재실 중 + 이탈 후 10초만. mmw_readings: 사람이 있을 때만(2026-10-13부터 walking·patient_locked·straightness 열 추가).</li>
<li>매일 03:30 cron: 3일 지난 원시 기록을 server/raw_archive/날짜/*.jsonl.gz로 옮김(zcat·pandas로 읽힘). DB는 WAL 모드.</li>
<li>activity_log(이벤트), fall_risk_profile·fall_risk_events(위험도), ai_analysis(리포트).</li></ul>""", "",
 py("tools/archive_raw.py", "archive") + block(DB, r"2026-10-13: 보행 추세 예측", r'ALTER TABLE mmw_readings ADD COLUMN', "mmw_readings 열 추가"))}
""")


def references():
    return part("refs", "12. 참고문헌과 확인 상태", "숫자를 인용한 논문은 원문·초록에서 직접 확인했다(2026-10-13)", table(["문헌", "쓰인 곳", "확인"], [
        ["Phillips LJ et al. Using Embedded Sensors in Independent Living to Predict Gait Changes and Falls. West J Nurs Res 2017;39(1):78–94. PMC5272917", "보행 저하 예측 기준(5.1·2.54 cm/s, 7.6·2.54 cm, AUC 0.86/0.88)", "PMC 원문"],
        ["Verghese J et al. Quantitative gait markers and incident fall risk in older adults. J Gerontol A 2009;64A(8):896–901", "보행 속도·변동성과 낙상(근거 설명)", "초록·PMC"],
        ["Hausdorff JM et al. Gait variability and fall risk in community-living older adults. Arch Phys Med Rehabil 2001;82(8):1050–1056", "보폭 변동성 근거", "PubMed 초록"],
        ["Dodge HH et al. In-home walking speeds and variability trajectories associated with MCI. Neurology 2012;78(24):1946–1952", "집 안 상시 보행 추세 근거", "PMC"],
        ["Kuys SS et al. Gait speed in ambulant older people in long term care. JAMDA 2014", "고정 0.8 m/s 기준을 쓰지 않는 이유", "server/gait_evidence.json"],
        ["Kalman RE 1960; Harvey AC 1989", "칼만 필터·국소 선형 추세", "표준 교재"],
        ["Breiman L. Random Forests. Mach Learn 2001;45:5–32", "자세 분류", "표준 문헌"],
        ["Hochreiter & Schmidhuber 1997; Malhotra et al. 2016", "LSTM 오토인코더 이상 탐지", "표준 문헌"],
        ["Morse JM et al. Can J Aging 1989;8(4):366–377", "Morse 낙상 척도", "표준 문헌"],
        ["Robertson & Zaragoza 2009; Cormack et al. SIGIR 2009", "BM25·RRF 검색", "표준 문헌"]]))


CSS = """
:root{--bg:#f7f7f5;--bg2:#fff;--fg:#1d2026;--muted:#5d6470;--line:#dfe2e6;--accent:#0f766e;--accent-bg:#e6f4f2;--warn:#9a3412;--warn-bg:#fff4ec;--code-bg:#f2f3f5;--easy-bg:#eef6ff;--easy:#1e4f8a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#15171b;--bg2:#1d2025;--fg:#e7e9ec;--muted:#a3a9b3;--line:#30343b;--accent:#5eead4;--accent-bg:#123a36;--warn:#fdba74;--warn-bg:#3a2414;--code-bg:#111317;--easy-bg:#16263a;--easy:#9cc7ff}}
:root[data-theme="dark"]{--bg:#15171b;--bg2:#1d2025;--fg:#e7e9ec;--muted:#a3a9b3;--line:#30343b;--accent:#5eead4;--accent-bg:#123a36;--warn:#fdba74;--warn-bg:#3a2414;--code-bg:#111317;--easy-bg:#16263a;--easy:#9cc7ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 "Noto Sans KR",system-ui,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 80px}h1{font-size:24px;margin:0 0 4px}h2{font-size:21px;margin:6px 0}
.sub,.muted{color:var(--muted)}a{color:var(--accent)}code{background:var(--code-bg);padding:1px 5px;border-radius:4px;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin:18px 0}
.card{display:block;background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:14px;text-decoration:none;color:var(--fg)}
.card:hover{border-color:var(--accent)}.card b{display:block;font-size:16px;margin-bottom:4px}.card span{color:var(--muted);font-size:13px}
.part-head{border-bottom:1px solid var(--line);margin-bottom:14px;padding-bottom:8px}.back{font-size:13px;text-decoration:none}
.easy{background:var(--easy-bg);color:var(--fg);border-left:4px solid var(--easy);padding:12px 14px;border-radius:6px;margin:12px 0}.easy b{color:var(--easy);margin-right:6px}
.warn{background:var(--warn-bg);border-left:4px solid var(--warn);padding:10px 14px;border-radius:6px;margin:12px 0}.warn b{color:var(--warn);margin-right:6px}
.cite{font-size:12.5px;color:var(--muted);border-top:1px dashed var(--line);margin-top:14px;padding-top:8px}
details.step{background:var(--bg2);border:1px solid var(--line);border-radius:10px;margin:10px 0;padding:0 14px}
details.step>summary{cursor:pointer;padding:12px 0;font-weight:700;font-size:16px;list-style:none}details.step>summary::-webkit-details-marker{display:none}
details.step>summary:before{content:"▸";color:var(--accent);margin-right:8px}details.step[open]>summary:before{content:"▾"}
.num{display:inline-block;background:var(--accent-bg);color:var(--accent);border-radius:6px;padding:0 7px;margin-right:8px;font-size:13px}
.plain{padding-bottom:6px}.detail{background:var(--code-bg);border-radius:8px;padding:8px 14px;margin:8px 0;font-size:14px}.detail-h{font-weight:700;font-size:13px;color:var(--muted)}
.formula{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:13.5px;background:var(--accent-bg);border-radius:8px;padding:10px 14px;margin:10px 0;overflow-x:auto}
details.code{margin:8px 0 12px;border:1px solid var(--line);border-radius:8px;background:var(--code-bg)}
details.code>summary{cursor:pointer;padding:8px 12px;font-size:13.5px}.code-tag{background:var(--accent);color:var(--bg2);border-radius:4px;padding:0 6px;font-size:12px;margin-right:4px}
.code-loc{color:var(--muted);font-size:12px;margin-left:6px}.code-note{padding:0 12px;font-size:13px;color:var(--muted)}
pre{margin:0;padding:10px 12px;overflow-x:auto;font:12.5px/1.5 "JetBrains Mono",ui-monospace,monospace;white-space:pre}.ln{color:var(--muted);user-select:none;margin-right:12px;opacity:.6}
pre.sample{background:var(--code-bg);border-radius:8px;margin:8px 0}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px;display:block;overflow-x:auto}th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--code-bg)}
.flowline{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:8px 0;font-size:13px}.flowline span{background:var(--bg2);border:1px solid var(--line);border-radius:999px;padding:3px 10px}
.ref{font-family:ui-monospace,monospace;font-size:12.5px}.top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start}
#theme,#openall{background:var(--bg2);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:4px 10px;cursor:pointer}
"""

JS = """
const parts=[...document.querySelectorAll('.part')];
function route(){const id=(location.hash||'#home').slice(1);const home=document.getElementById('home');
 const hit=parts.find(p=>p.id===id);home.hidden=!!hit;parts.forEach(p=>p.hidden=p!==hit);window.scrollTo(0,0);}
addEventListener('hashchange',route);route();
document.getElementById('theme').onclick=()=>{const r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';};
document.getElementById('openall').onclick=()=>document.querySelectorAll('.part:not([hidden]) details').forEach(d=>d.open=true);
"""

CARDS = [("flow", "0. 전체 흐름", "센서 → Pi → 판정 → 화면·리포트 한 장"), ("firmware", "1A. ToF 펌웨어(ESP32)", "센서 칩·펌웨어 안의 정제, 전송 규칙, 올리는 법"), ("tof", "1. ToF 침대 센서(서버)", "64칸 거리 수신·재실·3D 변환"),
         ("posture", "2. 자세 분류(Random Forest)", "알고리즘 원리·실제 나무 규칙·정확도 읽는 법"), ("anomaly", "3. 이상 탐지(LSTM-AE)", "4초 움직임을 다시 그려 보기(참고 신호)"),
         ("mmwave", "4. mmWave 레이더", "FMCW 원리·바이트 해석·좌표 보정·환자 고르기"), ("gait", "5. 보행 지표·Z-score", "6개 지표 식과 평소 대비 판정"),
         ("fall", "6. 낙상 감지", "평소 높이 대비 하강·실측 규칙·오탐 수정 기록"), ("trend", "7. 보행 저하 예측", "칼만 필터·Phillips 2017 기준·우리 시험 결과"),
         ("risk", "8. 위험도·침상 알림", "Morse + 변동 점수 × 거동 범위"), ("dashboard", "9. 대시보드", "받아오기·시계 보정·평면도 좌표"),
         ("report", "10. AI 리포트", "자료 모으기·BM25+RRF 검색·작성 규칙"), ("ops", "11. 운영", "서버·서비스·저장·보관"), ("refs", "12. 참고문헌", "확인한 숫자와 출처")]


def main():
    f = facts()
    cards = "".join(f'<a class="card" href="#{i}"><b>{t}</b><span>{s}</span></a>' for i, t, s in CARDS)
    body = "".join(fn(f) for fn in (part_overview, part_firmware, part_tof, part_posture, part_anomaly, part_mmwave, part_gait,
                                    part_fall, part_trend, part_risk, part_dashboard, part_report, part_ops)) + references()
    html_doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>마주봄 코드 가이드</title><style>{CSS}</style></head><body><div class="wrap">
<div class="top"><div><h1>마주봄 실측 경로 코드 가이드</h1><div class="muted">처음 보는 엔지니어·일반인용 · 생성 {date.today().isoformat()} · tools/build_code_guide.py · 코드 조각은 생성 시점 소스에서 직접 잘라 옴</div></div>
<div><button id="openall" type="button">이 파트 전부 펼치기</button> <button id="theme" type="button">밝게/어둡게</button></div></div>
<div id="home"><p>파트를 누르면 그 파트만 펼쳐진다. 각 단계는 <b>쉬운 설명 → 엔지니어 메모·수식 → 실제 코드(줄 번호)</b> 순서다. 단계 제목을 누르면 열리고, '코드' 상자를 누르면 그 함수 전체가 보인다.</p>
<div class="cards">{cards}</div></div>
{body}
</div><script>{JS}</script></body></html>"""
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    print(f"생성: {OUT} ({len(html_doc) // 1024} KB)")
    for k in ("rf", "fall_replay", "gt"):
        if isinstance(f.get(k), dict) and "error" in f[k]:
            print(f"  [주의] {k} 계산 못 함: {f[k]['error']} (.venv 파이썬으로 실행)")


if __name__ == "__main__":
    main()
