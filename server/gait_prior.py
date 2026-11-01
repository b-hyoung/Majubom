"""
gait_prior.py — 입원 기록(EMR) 기반 보행 기대값(사전분포)
==========================================================
센서 기록이 쌓이기 전에도 "이 환자에게 기대되는 보행 범위"를 갖기 위한 모듈이다.

  1) 기록 읽기    : 진단·위험요인·투약·과거 병원 기록에서 치매 중증도, 파킨슨증, 보행 상태,
                    과거에 측정한 보행속도, 센서가 미리 잡기 어려운 낙상 요인을 뽑는다.
                    측정값·날짜·낙상 요인은 서버 규칙으로만 확정한다(AI는 숫자를 다루지 않는다).
  2) 문헌값 계산  : 숫자는 gait_evidence.json의 출처에서만 가져온다(나이·키별 한국 노인 기준값,
                    치매·파킨슨 감소폭, 요양시설 평균, 실험실↔일상 차이, 의미 있는 변화량).
  3) AI 판단      : AI는 치매 중증도·파킨슨증·보행 상태를 기록에서 읽고, "건강 노인 기준 − 진단별 감소"
                    경로와 "요양시설 평균" 경로 사이 비중을 고른다. 인용이 실제 기록 문장에 있고, 인용한
                    부분 안의 단서가 판단을 긍정(또는 부정)으로 뒷받침할 때만 채택한다. 비중은 확인된 보행 상태가
                    허용하는 범위 안에서만 움직인다. AI가 없거나 실패하면 규칙 결과와 비중 0.5를 쓴다.
  4) 저장         : gait_priors 테이블. mmw_server가 대상(target_id)별로 불러 mmw_logic.evaluate에 넘긴다.

이 값은 낙상 확률이 아니다. 개인 기록이 쌓이면 mmw_logic에서 개인 기준 쪽으로 자연스럽게 넘어간다.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE_PATH = os.path.join(HERE, "gait_evidence.json")

PRIOR_VERSION = "gait_prior_v3"
AI_PROMPT_VERSION = "gait_prior_ai_v3"

# 정규분포 백분위 z값. KIM2025가 10·90 백분위를 정상 범위 기준(cutoff)으로 제시하므로 P10/P90을 쓴다.
Z_P10 = 1.2816
Z_P2_5 = 1.9600
Z_P99 = 2.3263

DEFAULT_SETTING = os.environ.get("MAJUBOM_CARE_SETTING", "ltc")   # 배포 환경: 요양병원
SETTINGS = ("ltc", "acute_hospital", "outpatient", "community")
SEVERITIES = ("none", "cognitive_impairment", "mild", "moderate", "severe", "unknown")
# wheelchair_user: 휠체어 사용 기록만 있고 걷는지 모름(기대값은 만든다) · non_ambulatory: 명시적 보행 불가(와상 등)
AMBULATION = ("independent", "assisted", "walker", "wheelchair_user", "non_ambulatory", "unknown")

MEASURED_SPEED_MAX_AGE_DAYS = 365   # 이보다 오래된 보행속도 기록은 쓰지 않는다
RECENT_MEASUREMENT_DAYS = 30        # 이 안의 기록은 '작은 변화' 폭, 그 밖은 '큰 변화' 폭을 불확실성으로 쓴다
PLAUSIBLE_SPEED = (0.05, 2.0)       # m/s. 이 범위 밖 숫자는 기록 오기로 보고 버린다

# AI 비중 허용 범위(설계 정책 — 문헌값이 아님). 확인된 보행 상태가 없으면 비중을 바꾸지 않는다.
WEIGHT_POLICY = {"independent": (0.5, 1.0), "assisted": (0.0, 0.5), "walker": (0.0, 0.5)}
DEFAULT_WEIGHT = 0.5

NOT_AMBULATORY_NOTE = "보행 불가 기록(와상·보행 불가) — 보행 기대값을 만들지 않음. 보행이 감지되면 기록을 확인해야 함"


def load_evidence(path: str = EVIDENCE_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


EVIDENCE = load_evidence()


# ── 1) 문헌 기준값 ───────────────────────────────────────────────────────
def _to_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        if value is None or value == "":
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def healthy_norm(age, height_cm, metric: str) -> dict | None:
    """건강한 한국 노인 기준값(KIM2025). metric: 'velocity' → m/s, 'step_length' → m.

    키가 있으면 표 3의 나이·키 분위회귀식(P10, P90)에서 중앙값과 표준편차를 구한다:
      평균 ≈ (P10+P90)/2, SD ≈ (P90−P10)/(2×1.2816)   ← 정규분포 가정 파생값(derived)
    키가 없으면 표 2의 연령대 평균±SD를 쓴다. 논문은 키 보정 후 속도·보폭의 성별 차이가 없다고 보고해 성별은 쓰지 않는다."""
    age = _to_float(age)
    height_cm = _to_float(height_cm)
    if age is None or age < 50:
        return None
    flags: list[str] = []
    if height_cm is not None:
        reg = EVIDENCE["kim2025_table3_quantile_regression"]
        a_lo, a_hi = reg["applicable_age"]
        h_lo, h_hi = reg["applicable_height_cm"]
        a = min(max(age, a_lo), a_hi)
        h = min(max(height_cm, h_lo), h_hi)
        if a != age:
            flags.append(f"나이 {age:g}세는 회귀식 적용 범위({a_lo}–{a_hi}세) 밖이라 {a:g}세 값을 씀")
        if h != height_cm:
            flags.append(f"키 {height_cm:g}cm는 회귀식 적용 범위({h_lo}–{h_hi}cm) 밖이라 {h:g}cm 값을 씀")
        c10 = reg[metric]["p10"]
        c90 = reg[metric]["p90"]
        p10 = c10[0] + c10[1] * a + c10[2] * h
        p90 = c90[0] + c90[1] * a + c90[2] * h
        mean_cm = (p10 + p90) / 2.0
        sd_cm = abs(p90 - p10) / (2.0 * Z_P10)
        method = "KIM2025 표 3 분위회귀 P10·P90 중간값(나이·키)"
    else:
        table = EVIDENCE["kim2025_table2_by_decade"]
        decade = "50s" if age < 60 else "60s" if age < 70 else "70s" if age < 80 else "80s"
        if age >= 90:
            flags.append(f"{age:g}세는 표 2의 80대 값을 외삽함")
        mean_cm, sd_cm = table[decade][metric]
        method = f"KIM2025 표 2 연령대({decade}) 평균±SD(키 없음)"
    return {"mean": mean_cm / 100.0, "sd": sd_cm / 100.0, "method": method,
            "flags": flags, "source": "KIM2025_KR"}


def _combine_with_parkinsonism(cognition: float, pd_red: float) -> tuple[float, float]:
    """한 가지 치매 범주와 파킨슨증 감소량 결합. 두 효과가 더해지는지 근거가 없어
    '큰 쪽만'과 '둘의 합' 사이 균등분포로 둔다: 평균 = 큰 쪽 + 작은 쪽/2, 분산 = (작은 쪽)²/12."""
    big, small = max(cognition, pd_red), min(cognition, pd_red)
    if small > 0:
        return big + small / 2.0, small ** 2 / 12.0
    return big, 0.0


def condition_reduction(severity: str | None, parkinsonism: bool) -> dict:
    """진단별 평소 보행속도 감소량(PEEL2019 치매, ZANARDI2021 파킨슨증). 반환 {"mean","sd","flags"} (m/s).
    중증도를 모르면 인지저하·경도·중등도 세 범주를 같은 비중으로 두고, 범주마다 파킨슨증을 먼저 결합한 뒤
    섞는다(전체 기댓값·전체 분산 공식)."""
    red = EVIDENCE["cognition_speed_reduction_mps"]
    pd_red = EVIDENCE["parkinsonism_speed_reduction_mps"]["overground"] if parkinsonism else 0.0
    flags: list[str] = []
    if severity in (None, "none"):
        branches = [0.0]
    elif severity == "unknown":
        branches = [red["cognitive_impairment"], red["mild"], red["moderate"]]
        flags.append("치매 기록은 있으나 중증도가 없어 인지저하·경도·중등도를 같은 비중으로 섞음")
    elif severity == "severe":
        branches = [red["moderate"]]
        flags.append("중증 치매는 메타분석 범주에 없어 중등도 감소량을 외삽함")
    else:
        branches = [red[severity]]
    parts = [_combine_with_parkinsonism(b, pd_red) for b in branches]
    k = len(parts)
    mean = sum(m for m, _ in parts) / k
    var = sum(v for _, v in parts) / k + sum(m * m for m, _ in parts) / k - mean * mean
    if pd_red > 0 and any(b > 0 for b in branches):
        flags.append("치매와 파킨슨증이 함께 있어 두 감소량의 합산 여부를 불확실성으로 반영")
    return {"mean": mean, "sd": math.sqrt(max(var, 0.0)), "flags": flags,
            "components": {"cognition_branches": branches, "parkinsonism": pd_red}}


def setting_anchor(setting: str | None) -> dict | None:
    """요양시설·급성기·외래 환경의 평소 보행속도 메타분석 평균(KUYS2014, PEEL2013). 지역사회면 None."""
    s = EVIDENCE["setting_speed_mps"].get(setting or "")
    if not s:
        return None
    lo, hi = s["ci95"]
    return {"mean": s["mean"], "se": (hi - lo) / (2 * Z_P2_5), "source": s["source"]}


def mixture(m1: float, v1: float, m2: float, v2: float, w: float) -> tuple[float, float]:
    """두 경로를 비중 w : (1−w)로 섞은 분포의 평균·분산(혼합분포 공식)."""
    mean = w * m1 + (1 - w) * m2
    var = w * v1 + (1 - w) * v2 + w * (1 - w) * (m1 - m2) ** 2
    return mean, var


_DATE_RE = re.compile(r"(20\d{2}|19\d{2})[-./](\d{1,2})[-./](\d{1,2})")


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    m = _DATE_RE.search(value)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def select_measurement(measurements: list[dict], today: date) -> tuple[dict | None, list[dict]]:
    """과거 보행속도 측정 중 한 건만 고른다: 날짜가 1년 이내인 것 중 가장 최근 것, 날짜가 있는 것이 없으면
    날짜 없는 첫 건. 같은 측정이 여러 번 적히거나 복사돼 불확실성이 부당하게 줄어드는 것을 막기 위해 한 건만 쓴다."""
    candidates, skipped, seen = [], [], set()
    for item in measurements or []:
        value = _to_float(item.get("value_mps"))
        if value is None or not (PLAUSIBLE_SPEED[0] <= value <= PLAUSIBLE_SPEED[1]):
            skipped.append({**item, "why": "값이 없거나 0.05–2.0 m/s 범위를 벗어남"})
            continue
        d = _parse_date(item.get("date"))
        key = (d, round(value, 2))
        if key in seen:
            skipped.append({**item, "why": "같은 날짜·같은 값의 중복 기재"})
            continue
        seen.add(key)
        if d is not None:
            age_days = (today - d).days
            if age_days < 0 or age_days > MEASURED_SPEED_MAX_AGE_DAYS:
                skipped.append({**item, "why": f"측정일이 {MEASURED_SPEED_MAX_AGE_DAYS}일 범위를 벗어남"})
                continue
        candidates.append((d, value, item))
    dated = [c for c in candidates if c[0] is not None]
    if dated:
        chosen = max(dated, key=lambda c: c[0])
    elif candidates:
        chosen = candidates[0]
    else:
        return None, skipped
    for c in candidates:
        if c is not chosen:
            skipped.append({**c[2], "why": "더 최근 측정 1건만 사용"})
    return {**chosen[2], "value_mps": chosen[1], "date": chosen[0].isoformat() if chosen[0] else None}, skipped


def apply_measurement(mean: float, var: float, measurement: dict | None, today: date) -> tuple[float, float, dict | None]:
    """선택된 과거 보행속도 측정 1건으로 사전분포를 갱신(정규-정규 베이즈 갱신).
    측정값 불확실성은 PERERA2006의 변화량을 빌려 쓴다: 30일 이내 0.05, 1년 이내 0.10 m/s(derived)."""
    if not measurement:
        return mean, var, None
    change = EVIDENCE["meaningful_change_mps"]
    d = _parse_date(measurement.get("date"))
    if d is None:
        sd, note = change["substantial"], "측정 날짜를 몰라 큰 변화 폭을 불확실성으로 씀"
    else:
        age_days = (today - d).days
        sd = change["small"] if age_days <= RECENT_MEASUREMENT_DAYS else change["substantial"]
        note = f"{age_days}일 전 측정"
    prec = 1.0 / var + 1.0 / sd ** 2
    post_mean = (mean / var + float(measurement["value_mps"]) / sd ** 2) / prec
    return post_mean, 1.0 / prec, {**measurement, "sd": sd, "note": note}


# ── 2) 문장 분석 도구 ──────────────────────────────────────────────────────
# 부정 판단 범위: 쉼표·마침표·괄호 등으로 나눈 '절' 안에서, 키워드 앞뒤 몇 글자까지.
#  · 키워드 바로 뒤에 붙은 괄호는 같은 범위로 본다: '파킨슨병(없음)', '파킨슨병 (-)'
#  · 괄호 안의 한정어는 괄호 앞 절과 같은 범위로 본다: '알츠하이머병(중등도)'
# '/'는 단위(m/s, cm/s, m/초)가 아닐 때만 절 구분으로 본다.
_CLAUSE_SPLIT = re.compile(r"[,;·()\[\]]|/(?!\s*(?:s(?:ec)?\b|초))|\.(?=\s|$)|\s(?:그리고|하지만|but)\s", re.I)
_SENTENCE_SPLIT = re.compile(r"[;!?]|\.(?=\s|$)")
_NEG_AFTER = re.compile(
    r"(?<!문제)(?<!문제\s)(없|아니|아님|배제|부정적|음성|미사용|미시행)"
    r"|(받|하|보이|관찰되|확인되|의심되|진단되|진단받)지\s*않|지\s*못|\(\s*[-−]\s*\)"
    r"|ruled\s+out|negative|absent|not\s+(present|seen|found|observed)", re.I)
_NEG_BEFORE = re.compile(r"(\bno\b|\bnot\b|\bwithout\b|\bdenies\b|\bnegative\s+for\b)", re.I)
# 진단 키워드 뒤의 부정이 다른 서술에 걸린 경우: '파킨슨병으로 걷지 못함', '치매로 대화하지 못함', '파킨슨병 있고 치매 없음'
# ('알츠하이머병으로 진단되지 않음'처럼 '으로' 뒤가 진단·의심이면 부정이 키워드에 걸린다)
_INTERVENING = re.compile(
    r"(으로(?!\s*(진단|의심|확인|판정|추정|보이))|로\s*인|때문|에\s*의해|하여|해서|되어|돼서|어서|아서|으며|"
    r"있(?!지)|이고|하고|되고|걷|걸|보행|이동|식사|복용|투약|대화|거동|수면|배변|섭취)")
# 독립 보행 단서 뒤의 불가 표현: '독립 보행 불가', '독립 보행은 아직 어려움'
_NEG_AFTER_WALK = re.compile(r"(불가|불능|어려|힘들|힘듦|안\s*됨|못\s*(함|하|해))")
NEG_WINDOW_AFTER = 12
NEG_WINDOW_BEFORE = 14
CONTEXT_WINDOW = 30      # 중증도 단어와 치매 언급 사이 최대 거리(같은 문장 안)


def _bounds(line: str, rx) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for m in rx.finditer(line):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(line)))
    return bounds


def _clause_bounds(line: str) -> list[tuple[int, int]]:
    return _bounds(line, _CLAUSE_SPLIT)


def _containing(bounds: list[tuple[int, int]], pos: int, length: int) -> tuple[int, int]:
    for b0, b1 in bounds:
        if b0 <= pos < b1 or (b0 == b1 == pos):
            return b0, b1
    return 0, length


def _scope(line: str, start: int) -> tuple[int, int]:
    """키워드가 속한 절 + 바로 붙은 괄호(앞뒤)."""
    bounds = _clause_bounds(line)
    cs, ce = _containing(bounds, start, len(line))
    if cs > 0 and line[cs - 1] == "(":                  # 괄호 안: 괄호 앞 절까지
        cs = _containing(bounds, max(cs - 2, 0), len(line))[0]
    if ce < len(line) and line[ce] == "(":              # 뒤에 붙은 괄호: 닫는 괄호까지
        close = line.find(")", ce)
        if close != -1:
            ce = close + 1
    return cs, ce


def _negated(line: str, start: int, end: int, dx: bool = False) -> bool:
    """dx=True(진단·병력 키워드)면 키워드와 부정 사이에 다른 서술이 끼어 있을 때 부정으로 보지 않는다."""
    cs, ce = _scope(line, start)
    after = line[end:min(max(ce, end), end + NEG_WINDOW_AFTER)]
    before = line[max(cs, start - NEG_WINDOW_BEFORE):start]
    m = _NEG_AFTER.search(after)
    if m and dx and any(im.start() < m.start() for im in _INTERVENING.finditer(after)):
        m = None
    return bool(m or _NEG_BEFORE.search(before))


# 가족력: 진단 키워드와 같은 범위(절 + 붙은 괄호)에 친족 표현이 있거나, 같은 문장에서 앞에 '가족력'이 나오면
# 환자 본인의 진단이 아니다('부친은 파킨슨병', '가족력: 파킨슨병, 치매', '치매 가족력 없음').
# 간병 기록에서 환자를 부르는 '어머님·아버님·어머니·아버지·할머니'는 친족 표현으로 보지 않는다.
_KIN = re.compile(r"(부친|모친|친부|친모|형제|자매|자녀|아들|딸(?!꾹)|남편|아내|배우자|"
                  r"\b(father|mother|brother|sister|siblings?|son|daughter|spouse)\b)", re.I)
_FAMILY_HISTORY = re.compile(r"(가족력|가족\s*중|family\s+history|F/?Hx)", re.I)


def _family_context(line: str, start: int) -> bool:
    cs, ce = _scope(line, start)
    if _KIN.search(line[cs:ce]) or _FAMILY_HISTORY.search(line[cs:ce]):
        return True
    # 앞쪽의 '가족력'이 부정('가족력 없음, 알츠하이머 치매')이면 뒤 목록은 환자 본인의 것이다
    ss, _ = _sentence(line, start)
    return any(not _negated(line, fm.start(), fm.end()) for fm in _FAMILY_HISTORY.finditer(line, ss, start))


def _matches(line: str, pattern: str, lo: int = 0, hi: int | None = None, affirmed: bool | None = True,
             self_only: bool = False):
    """line에서 [lo, hi) 안에 완전히 들어오는 패턴 일치. affirmed=True면 긍정만, False면 부정만, None이면 전부.
    self_only면 가족력 문맥의 언급은 뺀다(진단·병력 키워드용)."""
    hi = len(line) if hi is None else hi
    out = []
    for m in re.finditer(pattern, line, re.I):
        if m.start() < lo or m.end() > hi:
            continue
        if self_only and _family_context(line, m.start()):
            continue
        neg = _negated(line, m.start(), m.end(), dx=self_only)
        if affirmed is None or neg != affirmed:
            out.append(m)
    return out


def _affirmed(line: str, pattern: str, self_only: bool = False) -> bool:
    return bool(_matches(line, pattern, affirmed=True, self_only=self_only))


def _line_hits(text: str, pattern: str, self_only: bool = False) -> list[str]:
    """패턴이 부정문이 아닌 형태로 나온 줄만 돌려준다('파킨슨증 소견 없음', 'no syncope'는 제외)."""
    return [line.strip() for line in text.splitlines() if _affirmed(line, pattern, self_only)]


def _sentence(line: str, pos: int) -> tuple[int, int]:
    return _containing(_bounds(line, _SENTENCE_SPLIT), pos, len(line))


# ── 3) 기록 읽기(규칙) ─────────────────────────────────────────────────────
_DEMENTIA = r"(치매|알츠하이머|알쯔하이머|dementia|alzheimer|인지\s*저하|neurocognitive)"
_MCI = r"(경도\s*인지\s*장애|경도인지장애|\bMCI\b|mild cognitive impairment|CDR\s*0\.5|GDS\s*3\b)"
_SEVERE = r"(중증|고도|말기|severe|advanced|CDR\s*3\b|GDS\s*[67]\b)"
_MODERATE = r"(중등도|중등증|moderate|CDR\s*2\b|GDS\s*5\b)"
_MILD = r"(경도|경증|초기|mild|early[-\s]stage|CDR\s*1\b(?!\.)|GDS\s*4\b)"
_SEVERITY_CUES = {"cognitive_impairment": _MCI, "mild": _MILD, "moderate": _MODERATE, "severe": _SEVERE}
_COGNITION_NORMAL = r"(인지\s*(기능\s*)?정상|normal\s+cognition)"
_PARKINSON = r"(파킨슨|parkinson|루이\s*소체|lewy|\bDLB\b|\bPDD\b)"

_NON_AMBULATORY = r"(보행\s*불가|보행하지\s*못|걷지\s*못|와상|bed[-\s]?ridden|bed[-\s]?bound|non[-\s]?ambulatory)"
_WHEELCHAIR = r"(휠체어|wheel\s*chair)"
_WALKER = r"(보행기|보행\s*보조\s*기|\bwalker\b|rollator|워커)"
_ASSISTED = r"(지팡이|\bcane\b|부축|보조\s*필요|보조\s*하에|requires\s+assist|with\s+assist|assist(ance)?\s+required|1인\s*보조)"
_INDEPENDENT = r"(독립\s*보행|(혼자|스스로)(서)?\s*(\S+\s+){0,2}걸|independent(ly)?\s+ambulat|walks\s+(alone|unaided|independently))"
_WALK_ACTION = r"(걸어|걸음|걷는|걷고|걸으|보행\s*(함|하|중|가능|연습|훈련)|이동\s*(함|하|가능)|walks?\b|walking|ambulat)"
_WALKING_CUES = {"walker": _WALKER, "assisted": _ASSISTED, "independent": _INDEPENDENT}
# 조건부 불가: '보행기 없이는 걷지 못함'(보행기), '부축 없이는 보행 불가'·'혼자서는 걷지 못함'·'독립 보행 불가'(보조).
# 보조가 있으면 걷는다는 뜻이라 보행 불가로 보지 않고 그 보조의 보행 단서로 센다.
_UNABLE = r"(걷지\s*못|걸을\s*수\s*없|보행(하지|이)?\s*(못|불가|어려|힘들|힘듦)|이동(하지|이)?\s*(못|불가|어려|힘들|힘듦))"
_CONDITIONAL = re.compile(
    r"(?:(?P<walker>보행기|보행\s*보조\s*기|워커|walker|rollator)|(?P<aid>지팡이|cane|부축|보조|도움))\s*(?:없이는?|없으면)"
    r"[^,;.]{0,6}?" + _UNABLE +
    r"|(?P<alone>혼자(?:서)?|스스로|독립(?:적으로)?)\s*[^,;.]{0,6}?" + _UNABLE, re.I)
_MOBILITY_TERMS = (r"(보행|걷|걸어|걸음|이동|산책|낙상|넘어|ADL|일상\s*생활\s*(동작|수행)|보행기|휠체어|부축|지팡이|"
                   r"독립\s*보행|와상|walk|ambul|mobil|fall|gait|transfer|frail|deconditioning)")

# 센서가 미리 보기 어려운 낙상 유형과 관련된 기록(분류 근거: ROBINOVITCH2013의 원인 분류). 라벨은 서버가 정한다.
_OUT_OF_SCOPE_RULES = (
    ("collapse", "기립성 저혈압", r"(기립성\s*저혈압|orthostatic\s+hypotension|postural\s+hypotension)", "MOL2019"),
    ("collapse", "실신 병력", r"(실신|syncope)", None),
    ("collapse", "부정맥·서맥", r"(부정맥|arrhythmia|심방\s*세동|atrial\s+fibrillation|서맥|bradycardia|방실\s*차단|AV\s*block)", None),
    ("collapse", "뇌전증·경련", r"(뇌전증|간질|epilepsy|경련|seizure)", None),
    ("collapse", "저혈당 위험 약물·병력", r"(인슐린|insulin|설폰요소|sulfonylurea|글리메피리드|glimepiride|글리클라지드|gliclazide|저혈당|hypoglyc)", None),
    ("trip", "시력 저하", r"(시력\s*저하|백내장|녹내장|황반\s*변성|visual\s+impair|cataract|glaucoma|macular)", None),
    ("trip", "편마비·족하수", r"(편마비|hemiparesis|hemiplegia|족하수|foot\s*drop)", None),
    ("trip", "말초신경병증", r"(말초\s*신경\s*병|neuropathy)", None),
)

# 보행속도 측정값: 숫자+단위가 '측정 문맥'과 같은 범위에 있고, 목표·기준·미시행 같은 표현이 없을 때만 인정
_SPEED_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(cm\s*/\s*s(?:ec)?|m\s*/\s*s(?:ec)?|m/초|미터/초)", re.I)
_MEAS_CONTEXT = re.compile(
    r"(보행\s*(검사|속도|평가|측정)|보행속도|걷기\s*(검사|속도)|\d+\s*(m|미터)\s*(보행|걷기)|\d+\s*MWT|"
    r"gait\s+speed|walk(ing)?\s+(test|speed)|timed\s+walk)", re.I)
_MEAS_EXCLUDE = re.compile(
    r"(목표|기준치|기준\s*값|정상\s*(범위|치|값|기준)|참고\s*(범위|치|값)|권장|이상이면|미만이면|예상|추정치|"
    r"cut-?\s*off|target|goal|reference|normal\s+(range|value)|expected|미시행|시행\s*(안|하지|못)|"
    r"측정\s*(불가|못)|불가능|not\s+(done|performed)|unable)", re.I)
_HEADER = re.compile(r"^\s*\[([^\]]*)\]\s*")


def extract_measurements(text: str) -> list[dict]:
    """과거 보행속도 측정값. 측정 날짜는 같은 절 → 같은 줄 본문 → 같은 기록의 앞 줄 → 문서 날짜 순으로 찾는다."""
    found = []
    prev_date: dict[str, date] = {}          # 기록 머리별로 앞 줄 본문에 나온 날짜
    for line in text.splitlines():
        hm = _HEADER.match(line)
        header = hm.group(1) if hm else ""
        body = line[hm.end():] if hm else line
        for sm in _SPEED_RE.finditer(body):
            cs, ce = _scope(body, sm.start())
            scope = body[cs:ce]
            if not _MEAS_CONTEXT.search(scope) or _MEAS_EXCLUDE.search(scope):
                continue
            if _negated(body, sm.start(), sm.end()):
                continue
            value = float(sm.group(1))
            unit = sm.group(2).lower().replace(" ", "")
            value_mps = value / 100.0 if unit.startswith("cm") else value
            d, where = _parse_date(scope), "clause"
            if d is None:
                d, where = _parse_date(body), "body"
            if d is None and header in prev_date:
                d, where = prev_date[header], "previous_line"
            if d is None:
                d, where = _parse_date(header), "record"
            if d is None:
                where = None
            found.append({"value_mps": round(value_mps, 3), "date": d.isoformat() if d else None,
                          "date_from": where, "quote": line.strip(), "source": "rule"})
        body_dates = [_parse_date(m.group(0)) for m in _DATE_RE.finditer(body)]
        body_dates = [d for d in body_dates if d]
        if body_dates:
            prev_date[header] = body_dates[-1]
    # 같은 줄에서 같은 측정이 두 번(예: 괄호 안 환산값) 잡히면 첫 번째만 쓴다
    unique, seen = [], set()
    for item in found:
        key = (item["quote"], item["date"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _severity_hits(line: str, cue: str, lo: int = 0, hi: int | None = None) -> bool:
    """치매 중증도 단서: 긍정이고, 인지저하(MCI) 표현의 일부가 아니며, CDR·GDS 같은 척도이거나
    같은 문장에서 30자 안에 긍정인 치매 언급이 있어야 한다('고혈압 경도' 같은 오인을 막음)."""
    mci_spans = [m.span() for m in re.finditer(_MCI, line, re.I)] if cue != _MCI else []
    for m in _matches(line, cue, lo, hi, affirmed=True, self_only=True):
        if any(s <= m.start() < e for s, e in mci_spans):
            continue
        if cue == _MCI or re.match(r"(CDR|GDS)", m.group(0), re.I):
            return True
        ss, se = _sentence(line, m.start())
        if _matches(line, _DEMENTIA, max(ss, m.start() - CONTEXT_WINDOW), min(se, m.end() + CONTEXT_WINDOW),
                    self_only=True):
            return True
    return False


def _conditional_spans(line: str) -> list[tuple[str, int, int]]:
    """조건부 불가 구문 위치: [(보조 종류 'walker'|'assisted', 시작, 끝)]."""
    return [("walker" if m.group("walker") else "assisted", m.start(), m.end()) for m in _CONDITIONAL.finditer(line)]


def _non_ambulatory(line: str, lo: int = 0, hi: int | None = None) -> bool:
    """명시적 보행 불가. 조건부 불가('보행기 없이는 걷지 못함', '독립 보행 불가')의 일부는 제외한다."""
    cond = _conditional_spans(line)
    return any(not any(s <= m.start() < e for _, s, e in cond)
               for m in _matches(line, _NON_AMBULATORY, lo, hi, affirmed=True))


def _walking_evidence(line: str, cue: str, strong_only: bool, lo: int = 0, hi: int | None = None) -> bool:
    """보행 단서. strong_only면 보조기 언급만으로는 부족하고, 같은 문장에 걷는 행동이 긍정으로 있어야 한다
    ('보행기 보관'은 걷는다는 근거가 아님). 독립 보행 단서는 그 자체로 걷는다는 뜻이지만 뒤에 불가 표현이 붙으면
    인정하지 않는다. 조건부 불가 구문은 그 보조로 걷는다는 직접 근거다."""
    kind = next((k for k, c in _WALKING_CUES.items() if c == cue), None)
    top = len(line) if hi is None else hi
    cond = _conditional_spans(line)
    if any(k == kind and s >= lo and e <= top for k, s, e in cond):
        return True
    for m in _matches(line, cue, lo, hi, affirmed=True):
        if any(s <= m.start() < e for _, s, e in cond):
            continue
        if cue == _INDEPENDENT:
            _, ce = _scope(line, m.start())
            if _NEG_AFTER_WALK.search(line[m.end():min(max(ce, m.end()), m.end() + NEG_WINDOW_AFTER)]):
                continue
            return True
        if not strong_only:
            return True
        ss, se = _sentence(line, m.start())
        if _matches(line, _WALK_ACTION, ss, se, affirmed=True):
            return True
    return False


def _resolve_ambulation(text: str) -> tuple[str, str | None, list[str]]:
    """보행 상태. 명시적 보행 불가(와상·보행 불가)가 있으면 걷는 행동이 함께 적힌 보행 단서만 인정한다.
    휠체어 사용만으로는 보행 불가로 보지 않는다(wheelchair_user: 걷는지 모름)."""
    flags: list[str] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    non_amb = [ln for ln in lines if _non_ambulatory(ln)]
    wheel = [ln for ln in lines if _affirmed(ln, _WHEELCHAIR)]
    for value in ("walker", "assisted", "independent"):
        hits = [ln for ln in lines if _walking_evidence(ln, _WALKING_CUES[value], strong_only=bool(non_amb))]
        if hits:
            if non_amb or wheel:
                flags.append("휠체어·보행 불가 언급과 걷는다는 기록이 함께 있어 보행 기록을 우선함")
            return value, hits[0].strip(), flags
    if non_amb:
        return "non_ambulatory", non_amb[0].strip(), flags
    if wheel:
        flags.append("휠체어 사용 기록만 있고 걷는지 기록이 없음 — 기대값은 만들되 기본 비중을 씀")
        return "wheelchair_user", wheel[0].strip(), flags
    return "unknown", None, flags


def extract_profile_rules(text: str) -> dict:
    """키워드 규칙으로 임상 정보를 뽑는다. 부정 표현은 같은 범위 안에서만 판단한다. AI가 검증되면 AI가 우선."""
    profile: dict = {}
    lines = [ln for ln in text.splitlines() if ln.strip()]
    severity, quote = "none", None
    for sev in ("severe", "moderate", "mild"):
        hits = [ln for ln in lines if _severity_hits(ln, _SEVERITY_CUES[sev])]
        if hits:
            severity, quote = sev, hits[0].strip()
            break
    if severity == "none":
        mci = [ln for ln in lines if _severity_hits(ln, _MCI)]
        dem = [ln for ln in lines if _affirmed(ln, _DEMENTIA, self_only=True)]
        if mci:
            severity, quote = "cognitive_impairment", mci[0].strip()
        elif dem:
            severity, quote = "unknown", dem[0].strip()
    profile["dementia_severity"] = {"value": severity, "quote": quote, "source": "rule"}

    pk = _line_hits(text, _PARKINSON, self_only=True)
    profile["parkinsonism"] = {"value": bool(pk), "quote": pk[0] if pk else None, "source": "rule"}

    amb, amb_quote, amb_flags = _resolve_ambulation(text)
    profile["ambulation"] = {"value": amb, "quote": amb_quote, "source": "rule", "flags": amb_flags}

    profile["measured_gait_speeds"] = extract_measurements(text)

    factors = []
    for category, factor, pat, evidence in _OUT_OF_SCOPE_RULES:
        hits = _line_hits(text, pat, self_only=True)
        if hits:
            factors.append({"category": category, "factor": factor, "quote": hits[0],
                            "evidence": evidence, "source": "rule"})
    profile["out_of_scope_factors"] = factors
    return profile


# ── 4) AI 판단(인용 위치·극성 검증) ──────────────────────────────────────────
AI_SYSTEM_PROMPT = (
    "당신은 요양병원 환자 기록을 읽고 보행 기대값 계산에 필요한 판단만 하는 보조자입니다. "
    "보행속도·보폭 같은 숫자를 새로 만들거나 추정하지 마세요. 숫자 계산과 과거 측정값·낙상 요인 추출은 서버가 합니다. "
    "모든 판단에는 기록에 실제로 있는 문장을 한 글자도 바꾸지 않고 quote에 그대로 옮기세요. "
    "quote에는 판단의 단서가 되는 단어가 직접 들어 있어야 합니다. "
    "기록에 근거가 없으면 value를 'unknown' 또는 null로 두고 quote도 null로 두세요. "
    "부정 표현(예: '휠체어 사용하지 않음', '파킨슨증 소견 없음')을 주의해서 읽으세요. "
    "가족력(예: '부친은 파킨슨병')은 환자의 진단이 아닙니다. "
    "dementia_severity의 quote에는 중증도 단서(경도·중등도·중증, CDR, GDS 등)가 직접 들어 있는 문장을 옮기세요. "
    "MMSE 점수만으로 중증도를 정하지 말고, 단서가 없으면 'unknown'으로 두세요. "
    "ambulation 값의 뜻: independent=혼자 걸음, assisted=지팡이·부축, walker=보행기로 걸음, "
    "wheelchair_user=휠체어를 쓰지만 걷는지 기록 없음, non_ambulatory=와상·보행 불가가 명시됨. "
    "보행기를 보관만 하는 것은 걷는다는 근거가 아닙니다. "
    "community_path_weight는 0~1 사이 값입니다. 1에 가까울수록 '같은 나이·키 건강 노인 기준에서 진단별 감소만큼 뺀 값'을, "
    "0에 가까울수록 '요양시설 보행 가능 입소자 평균'을 따른다는 뜻입니다. 독립 보행·활동적이라는 기록이 있으면 1 쪽, "
    "보행 보조·잦은 넘어짐·최근 입원 후 기능 저하 기록이 있으면 0 쪽으로 두고, 판단 근거가 된 보행·이동 관련 문장을 "
    "weight_quotes에 그대로 옮기세요. "
    "반드시 다음 JSON 스키마로만 답하세요: "
    '{"dementia_severity": {"value": "none|cognitive_impairment|mild|moderate|severe|unknown", "quote": null}, '
    '"parkinsonism": {"value": true, "quote": null}, '
    '"ambulation": {"value": "independent|assisted|walker|wheelchair_user|non_ambulatory|unknown", "quote": null}, '
    '"community_path_weight": 0.5, "weight_quotes": [""], "weight_rationale": ""}'
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _find_spans(quote: str, lines: list[str]) -> list[tuple[str, int, int]]:
    q = _norm(quote)
    if len(q) < 2:
        return []
    spans = []
    for line in lines:
        idx = [i for i, ch in enumerate(line) if not ch.isspace()]
        compact = "".join(line[i] for i in idx).lower()
        pos = compact.find(q)
        while pos != -1:
            spans.append((line, idx[pos], idx[pos + len(q) - 1] + 1))
            pos = compact.find(q, pos + 1)
    return spans


def _quote_spans(quote, lines: list[str]) -> list[tuple[str, int, int]]:
    """인용이 원래 기록 줄의 어디에 있는지(공백 차이만 허용). 반환 [(줄, 시작, 끝)].
    AI가 줄 머리에 문장 일부만 붙여 인용하면('[기록 … #1] 파킨슨병 없음.') 같은 머리를 가진 줄의 본문에서
    그 일부를 글자 그대로 찾는다."""
    if not isinstance(quote, str):
        return []
    spans = _find_spans(quote, lines)
    hm = _HEADER.match(quote)
    if spans or not hm:
        return spans
    head = _norm(hm.group(0))
    out = []
    for line in lines:
        lm = _HEADER.match(line)
        if lm and _norm(lm.group(0)) == head:
            out += [(ln, lo, hi) for ln, lo, hi in _find_spans(quote[hm.end():], [line]) if lo >= lm.end()]
    return out


def _severity_supported(value: str, spans) -> bool:
    for line, lo, hi in spans:
        if value == "none":
            if (_matches(line, _COGNITION_NORMAL, lo, hi, affirmed=True, self_only=True)
                    or _matches(line, _DEMENTIA, lo, hi, affirmed=False, self_only=True)):
                return True
        elif _severity_hits(line, _SEVERITY_CUES[value], lo, hi):
            return True
    return False


def validate_ai_profile(ai, records_text: str) -> tuple[dict, list[str]]:
    """AI 판단 중 인용이 기록 줄에 있고, 인용한 부분 안의 단서가 판단을 긍정(또는 부정)으로 뒷받침하는 것만 남긴다.
    긍정·부정은 그 단서가 있는 원래 문장 범위로 판단한다. 반환 (검증된 항목, 버린 이유 목록)."""
    kept: dict = {}
    dropped: list[str] = []
    if not isinstance(ai, dict):
        return kept, ["AI 응답이 JSON 객체가 아님"]
    lines = [ln for ln in records_text.splitlines() if ln.strip()]
    non_amb_in_records = any(_non_ambulatory(ln) for ln in lines)

    item = ai.get("dementia_severity")
    if isinstance(item, dict) and item.get("value") not in (None, "unknown"):
        value, spans = item.get("value"), _quote_spans(item.get("quote"), lines)
        if value not in SEVERITIES:
            dropped.append(f"dementia_severity: 허용되지 않은 값 {value!r}")
        elif not spans:
            dropped.append("dementia_severity: 인용이 기록에 없음")
        elif not _severity_supported(value, spans):
            dropped.append(f"dementia_severity: 인용한 부분에 '{value}'를 뒷받침하는 단서가 없음")
        else:
            kept["dementia_severity"] = {"value": value, "quote": item.get("quote"), "source": "ai"}

    item = ai.get("parkinsonism")
    if isinstance(item, dict) and isinstance(item.get("value"), bool):
        spans = _quote_spans(item.get("quote"), lines)
        if item["value"] is False and item.get("quote") in (None, ""):
            kept["parkinsonism"] = {"value": False, "quote": None, "source": "ai"}
        elif not spans:
            dropped.append("parkinsonism: 인용이 기록에 없음")
        elif any(_matches(ln, _PARKINSON, lo, hi, affirmed=item["value"], self_only=True) for ln, lo, hi in spans):
            kept["parkinsonism"] = {"value": item["value"], "quote": item["quote"], "source": "ai"}
        else:
            dropped.append("parkinsonism: 인용한 부분의 긍정·부정이 판단과 맞지 않음")

    item = ai.get("ambulation")
    if isinstance(item, dict) and item.get("value") not in (None, "unknown"):
        value, spans = item.get("value"), _quote_spans(item.get("quote"), lines)
        if value not in AMBULATION:
            dropped.append(f"ambulation: 허용되지 않은 값 {value!r}")
        elif not spans:
            dropped.append("ambulation: 인용이 기록에 없음")
        elif value == "non_ambulatory":
            if not any(_non_ambulatory(ln, lo, hi) for ln, lo, hi in spans):
                dropped.append("ambulation: 인용한 부분에 보행 불가 단서가 없음")
            elif any(_walking_evidence(ln, pat, strong_only=True) for ln in lines for pat in _WALKING_CUES.values()):
                dropped.append("ambulation: 기록에 걷는다는 단서가 있어 보행 불가와 충돌")
            else:
                kept["ambulation"] = {"value": value, "quote": item["quote"], "source": "ai"}
        elif value == "wheelchair_user":
            if any(_matches(ln, _WHEELCHAIR, lo, hi) for ln, lo, hi in spans):
                kept["ambulation"] = {"value": value, "quote": item["quote"], "source": "ai"}
            else:
                dropped.append("ambulation: 인용한 부분에 휠체어 단서가 없음")
        elif any(_walking_evidence(ln, _WALKING_CUES[value], strong_only=non_amb_in_records, lo=lo, hi=hi)
                 for ln, lo, hi in spans):
            kept["ambulation"] = {"value": value, "quote": item["quote"], "source": "ai"}
        else:
            dropped.append(f"ambulation: 인용한 부분이 '{value}'를 뒷받침하지 않음")

    w = _to_float(ai.get("community_path_weight"))
    quotes = ai.get("weight_quotes")
    quotes = [q for q in quotes if isinstance(q, str)] if isinstance(quotes, list) else []
    evidence = [q for q in quotes
                if any(_matches(ln, _MOBILITY_TERMS, lo, hi, affirmed=None) for ln, lo, hi in _quote_spans(q, lines))]
    if w is not None and 0.0 <= w <= 1.0 and evidence:
        kept["community_path_weight"] = {"value": w, "quotes": evidence,
                                         "rationale": str(ai.get("weight_rationale") or "")[:600]}
    elif w is not None:
        dropped.append("community_path_weight: 0~1 범위가 아니거나 인용에 보행·이동 관련 단어가 없어 기본 비중을 씀")
    return kept, dropped


def merge_profiles(rule_profile: dict, ai_kept: dict) -> dict:
    merged = dict(rule_profile)
    for key in ("dementia_severity", "parkinsonism", "ambulation"):
        if key not in ai_kept:
            continue
        # AI가 인용 없이 '없음'이라고 하면 규칙이 기록에서 찾은 값을 지우지 않는다(부정은 인용이 있어야 인정).
        if (key == "parkinsonism" and ai_kept[key]["value"] is False and not ai_kept[key].get("quote")
                and rule_profile.get(key, {}).get("value")):
            continue
        merged[key] = ai_kept[key]
    return merged


def policy_weight(ai_weight: dict | None, ambulation: str) -> tuple[float, str]:
    """확인된 보행 상태가 허용하는 범위 안에서만 AI 비중을 쓴다(WEIGHT_POLICY, 설계 정책)."""
    rng = WEIGHT_POLICY.get(ambulation)
    if ai_weight is None:
        return DEFAULT_WEIGHT, "default"
    if rng is None:
        return DEFAULT_WEIGHT, "보행 상태가 확인되지 않아 AI 비중을 쓰지 않음"
    lo, hi = rng
    w = float(ai_weight["value"])
    if w < lo or w > hi:
        return min(max(w, lo), hi), f"AI 비중 {w:g}를 보행 상태({ambulation}) 허용 범위 {lo}–{hi}로 제한"
    return w, "ai"


def redact_names(text: str, names) -> str:
    """등록된 환자 이름을 '환자'로 가린다(뒤에 붙은 '환자·님·씨'까지). 기록 원문의 다른 식별 정보(보호자 이름 등)는
    가리지 못한다."""
    for name in names:
        if isinstance(name, str) and len(name.strip()) >= 2:
            text = re.sub(re.escape(name.strip()) + r"(\s*(환자|님|씨))?", "환자", text, flags=re.I)
    return text


def run_ai(records_text: str, rule_profile: dict, paths_preview: dict, caller=None) -> dict:
    """AI 호출. 실패하면 가짜 결과 없이 {"success": False, ...}를 그대로 돌려준다."""
    if caller is None:
        import ai_report
        caller = ai_report.call_openai
    brief = {k: rule_profile[k] for k in ("dementia_severity", "parkinsonism", "ambulation") if k in rule_profile}
    user_content = (
        "아래는 한 환자의 기록입니다. 등록된 환자 이름은 '환자'로 가렸습니다.\n\n[기록]\n" + records_text +
        "\n\n[규칙으로 먼저 뽑은 값 — 틀렸으면 바로잡으세요]\n" + json.dumps(brief, ensure_ascii=False) +
        "\n\n[서버가 문헌값으로 계산한 두 경로 — 숫자는 바꾸지 말고 비중만 고르세요]\n" +
        json.dumps(paths_preview, ensure_ascii=False)
    )
    try:
        result = caller(system_prompt=AI_SYSTEM_PROMPT, user_content=user_content, json_mode=True)
    except Exception as e:   # 호출 계층이 예외를 던져도 규칙 결과로 계속한다
        return {"success": False, "error_type": "call_error", "message": type(e).__name__}
    if not isinstance(result, dict) or not result.get("success"):
        result = result if isinstance(result, dict) else {}
        return {"success": False, "error_type": result.get("error_type"), "message": result.get("message")}
    try:
        parsed = json.loads(result["text"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"success": False, "error_type": "parse_error", "message": "AI 응답을 JSON으로 읽지 못했습니다."}
    if not isinstance(parsed, dict):
        return {"success": False, "error_type": "invalid_response", "message": "AI 응답이 JSON 객체가 아닙니다."}
    return {"success": True, "parsed": parsed, "model": result.get("model")}


# ── 5) 사전분포 계산 ───────────────────────────────────────────────────────
def build_prior(patient: dict, records_text: str, care_setting: str | None = None,
                use_ai: bool = False, ai_caller=None, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    setting = care_setting if care_setting in SETTINGS else DEFAULT_SETTING
    age = _to_float(patient.get("age"))
    height = _to_float(patient.get("height_cm"))
    records_text = redact_names(records_text, [patient.get(k) for k in ("name", "name_en", "patient_name")])
    rule_profile = extract_profile_rules(records_text)
    trace: list[dict] = []
    out = {
        "version": PRIOR_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"age": age, "sex": patient.get("sex"), "height_cm": height, "care_setting": setting},
        "metrics": {}, "not_applicable": None, "trace": trace,
        "ai": {"requested": bool(use_ai), "used": False},
        "unsupported_metrics": {
            m: "레이더 고유 정의(6초 구간 기준)라 대응되는 문헌 기준값이 없어 개인 기록만으로 판단"
            for m in ("speed_cv", "sway", "freeze_ratio", "stride_cv")},
    }

    speed_norm = healthy_norm(age, height, "velocity")
    step_norm = healthy_norm(age, height, "step_length")
    anchor = setting_anchor(setting)

    def paths(profile, w):
        red = condition_reduction(profile["dementia_severity"]["value"], bool(profile["parkinsonism"]["value"]))
        m1 = speed_norm["mean"] - red["mean"]
        v1 = speed_norm["sd"] ** 2 + red["sd"] ** 2
        if anchor is not None:
            v2 = speed_norm["sd"] ** 2 + anchor["se"] ** 2   # 개인 간 흩어짐은 KIM2025 SD를 빌려 씀(derived)
            mean, var = mixture(m1, v1, anchor["mean"], v2, w)
        else:
            mean, var = m1, v1
        return red, m1, v1, mean, var

    profile = rule_profile
    ai_kept: dict = {}
    if use_ai and speed_norm is not None:
        _, m1, v1, _, _ = paths(rule_profile, DEFAULT_WEIGHT)
        preview = {
            "community_path": {"mean_mps": round(m1, 3), "sd_mps": round(math.sqrt(v1), 3),
                               "basis": "KIM2025 건강 노인 기준 − PEEL2019/ZANARDI2021 감소량"},
            "setting_path": ({"mean_mps": anchor["mean"], "basis": anchor["source"] + " 요양시설 보행 가능 입소자 평균"}
                             if anchor else None),
        }
        ai_result = run_ai(records_text, rule_profile, preview, caller=ai_caller)
        if ai_result["success"]:
            try:
                ai_kept, dropped = validate_ai_profile(ai_result["parsed"], records_text)
            except Exception as e:   # 형식이 틀린 응답은 AI 실패로 기록하고 규칙 결과로 계속
                ai_kept, dropped = {}, [f"응답 검증 중 오류({type(e).__name__})"]
                out["ai"].update(error_type="invalid_response")
            out["ai"].update(used=bool(ai_kept), model=ai_result.get("model"), prompt_version=AI_PROMPT_VERSION,
                             accepted=sorted(ai_kept.keys()), dropped=dropped)
            profile = merge_profiles(rule_profile, ai_kept)
        else:
            out["ai"].update(used=False, error_type=ai_result.get("error_type"), message=ai_result.get("message"))

    out["profile"] = profile
    out["out_of_scope"] = out_of_scope_summary(profile)
    if speed_norm is None:
        out["not_applicable"] = "나이 정보가 없거나 50세 미만이라 한국 노인 기준값을 쓸 수 없음"
        return out
    if profile["ambulation"]["value"] == "non_ambulatory":
        out["not_applicable"] = NOT_AMBULATORY_NOTE
        return out

    if anchor is None:
        w, weight_source = 1.0, "지역사회 환경 — 요양시설 평균과 섞지 않음"
    else:
        w, weight_source = policy_weight(ai_kept.get("community_path_weight"), profile["ambulation"]["value"])
    if "community_path_weight" in ai_kept:
        out["ai"]["weight"] = {**ai_kept["community_path_weight"], "applied": w, "policy_note": weight_source}
    red, m1, v1, mean_pop, var_pop = paths(profile, w)

    trace.append({"step": "건강 노인 기준", "speed_mps": round(speed_norm["mean"], 3), "sd": round(speed_norm["sd"], 3),
                  "method": speed_norm["method"], "flags": speed_norm["flags"], "source": "KIM2025_KR"})
    trace.append({"step": "진단별 감소", "minus_mps": round(red["mean"], 3), "sd": round(red["sd"], 3),
                  "components": red["components"], "flags": red["flags"], "source": ["PEEL2019", "ZANARDI2021"]})
    if anchor is not None:
        trace.append({"step": "환경 평균과 섞기", "community_path_mps": round(m1, 3), "setting_path_mps": anchor["mean"],
                      "weight_community": round(w, 3), "weight_source": weight_source,
                      "ambulation_flags": profile["ambulation"].get("flags", []),
                      "source": anchor["source"], "note": "환경 경로의 개인 간 흩어짐은 KIM2025 SD를 빌려 씀(derived)"})

    chosen, skipped = select_measurement(profile.get("measured_gait_speeds"), today)
    mean_sup, var_sup, used = apply_measurement(mean_pop, var_pop, chosen, today)
    if used or skipped:
        trace.append({"step": "과거 측정 보행속도 반영", "used": used, "skipped": skipped, "source": "PERERA2006",
                      "note": "가장 최근 측정 1건만 사용. 측정값 불확실성: 30일 이내 0.05, 1년 이내 0.10 m/s"
                              "(변화량을 빌려 씀, derived)"})

    ctx = EVIDENCE["lab_to_real_world_difference_mps"]
    tau = math.sqrt(var_sup + ctx["mean"] ** 2)
    trace.append({"step": "병실 레이더 측정 환경 불확실성", "plus_sd_mps": ctx["mean"], "source": "THIMYLIEN2026",
                  "note": "실험실보다 일상 보행이 평균 0.22 m/s 느리다는 메타분석 값을 평균 이동이 아닌 불확실성으로만 반영. "
                          "설치 병실에서 기준 보행검사로 보정하면 줄일 수 있음"})
    within = EVIDENCE["radar_within_person_window_sd"]
    out["metrics"]["speed"] = {
        "mean": round(mean_sup, 4), "tau": round(tau, 4),
        "p10": round(max(0.0, mean_sup - Z_P10 * tau), 3), "p90": round(mean_sup + Z_P10 * tau, 3),
        "sigma_within_prior": within["speed"], "unit": "m/s", "use_for_provisional": True,
        "components_sd": {"population_and_diagnosis": round(math.sqrt(var_pop), 4),
                          "after_measurement": round(math.sqrt(var_sup), 4), "radar_context": ctx["mean"]},
    }

    # 보폭: 진단·환경 효과를 수치로 준 문헌이 없어 평균은 건강 노인 기준을 두고,
    # "속도 감소가 모두 보폭에서 온다면"의 최대 변화폭을 불확실성으로만 넓힌다(derived).
    deficit = max(0.0, speed_norm["mean"] - mean_sup)
    extra = step_norm["mean"] * deficit / speed_norm["mean"] if speed_norm["mean"] > 0 else 0.0
    step_tau = math.sqrt(step_norm["sd"] ** 2 + extra ** 2)
    out["metrics"]["stride_length"] = {
        "mean": round(step_norm["mean"], 4), "tau": round(step_tau, 4),
        "p10": round(max(0.0, step_norm["mean"] - Z_P10 * step_tau), 3),
        "p90": round(step_norm["mean"] + Z_P10 * step_tau, 3),
        "sigma_within_prior": within["stride_length"], "unit": "m", "use_for_provisional": False,
        "note": "코드의 stride_length는 속도÷걸음 주파수라 한 걸음 길이(step length)에 해당. 레이더 보폭의 정확도는 검증되지 않음",
    }
    trace.append({"step": "보폭 기준", "step_length_m": round(step_norm["mean"], 3), "sd": round(step_tau, 3),
                  "method": step_norm["method"], "source": "KIM2025_KR",
                  "note": "진단별 보폭 감소 근거가 없어 평균은 두고 불확실성만 넓힘(derived)"})
    return out


def out_of_scope_summary(profile: dict) -> dict:
    mech = EVIDENCE["fall_mechanisms_ltc"]["causes"]
    return {
        "patient_factors": profile.get("out_of_scope_factors", []),
        "mechanism_share_ltc": {
            "partly_observable": {"incorrect_weight_shifting": mech["incorrect_weight_shifting"]["share"]},
            "not_predictable_by_sensors": {k: mech[k]["share"] for k in
                                           ("trip_or_stumble", "hit_or_bump", "loss_of_support", "collapse", "slip")},
        },
        "source": "ROBINOVITCH2013",
        "note": ("요양시설 영상 낙상 227건 기준 걸림·부딪힘·지지물 상실·주저앉음·미끄러짐이 합쳐 약 57%. "
                 "이런 낙상은 센서가 미리 알 수 없고, 넘어진 뒤 높이 급강하 규칙으로만 감지를 시도한다(실제 낙상 검증 없음). "
                 "원 연구의 카메라는 공용 공간에만 있어 침실 낙상 비율은 다를 수 있다."),
    }


def summarize(prior: dict | None) -> dict | None:
    """대시보드·리포트용 요약."""
    if not prior:
        return None
    speed = (prior.get("metrics") or {}).get("speed")
    return {
        "id": prior.get("id"),
        "created_at": prior.get("created_at"),
        "not_applicable": prior.get("not_applicable"),
        "speed_expected_mps": ({"mean": round(speed["mean"], 2), "p10": speed["p10"], "p90": speed["p90"]}
                               if speed else None),
        "dementia_severity": ((prior.get("profile") or {}).get("dementia_severity") or {}).get("value"),
        "ambulation": ((prior.get("profile") or {}).get("ambulation") or {}).get("value"),
        "ai_used": (prior.get("ai") or {}).get("used", False),
        "out_of_scope_factors": [f["factor"] for f in (prior.get("out_of_scope") or {}).get("patient_factors", [])],
        "note": "문헌 기준 참고 범위이며 낙상 확률이 아님. 개인 보행 기록이 쌓이면 개인 기준이 우선함",
    }


# ── 6) 저장 · 조회 ─────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS emr_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bed_id TEXT NOT NULL,
    record_date TEXT,
    source TEXT,
    record_type TEXT,
    text TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS gait_priors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bed_id TEXT NOT NULL,
    target_id TEXT,
    method TEXT,
    prior_json TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def init_schema(conn) -> None:
    conn.executescript(SCHEMA)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(patients)")}
    for name, kind in (("height_cm", "REAL"), ("weight_kg", "REAL"), ("diagnosis_json", "TEXT")):
        if columns and name not in columns:
            conn.execute(f"ALTER TABLE patients ADD COLUMN {name} {kind}")


def add_emr_records(conn, bed_id: str, records: list[dict]) -> list[int]:
    ids = []
    for rec in records:
        cur = conn.execute("""INSERT INTO emr_records (bed_id, record_date, source, record_type, text)
                              VALUES (?, ?, ?, ?, ?)""",
                           (bed_id, rec.get("record_date"), rec.get("source"), rec.get("record_type"), rec["text"]))
        ids.append(cur.lastrowid)
    return ids


def _rows(conn, sql, params=()):
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


def collect_records_text(conn, bed_id: str) -> tuple[dict, str]:
    """환자 행 + 위험요인 + 투약 + EMR 기록 + 간호 기록을 한 텍스트로 모은다. 이름 칸은 넣지 않는다.
    기록 줄 앞의 [기록 날짜 · 출처 · #id]는 문서 정보이며, 측정 날짜는 본문의 날짜를 먼저 쓴다(extract_measurements)."""
    patient = (_rows(conn, "SELECT * FROM patients WHERE bed_id = ?", (bed_id,)) or [{}])[0]
    lines: list[str] = []
    for key, label in (("diagnosis_primary_ko", "주진단"), ("diagnosis", "진단"), ("notes", "메모")):
        if patient.get(key):
            lines.append(f"[{label}] {patient[key]}")
    if patient.get("diagnosis_json"):
        try:
            dx = json.loads(patient["diagnosis_json"])
            for d in dx if isinstance(dx, list) else [dx]:
                lines.append(f"[진단] {d}")
        except (TypeError, json.JSONDecodeError):
            lines.append(f"[진단] {patient['diagnosis_json']}")
    for r in _rows(conn, "SELECT * FROM risk_factors WHERE bed_id = ? AND active = 1", (bed_id,)):
        text = r.get("label") or r.get("factor")
        if text:
            lines.append(f"[위험요인] {text}")
    for r in _rows(conn, "SELECT * FROM medications WHERE bed_id = ? AND active = 1", (bed_id,)):
        parts = [r.get("name"), r.get("dose"), r.get("frequency"), r.get("note") or r.get("notes")]
        lines.append("[투약] " + " · ".join(str(p) for p in parts if p))
    for r in _rows(conn, "SELECT * FROM emr_records WHERE bed_id = ? ORDER BY id", (bed_id,)):
        head = " · ".join(x for x in (r.get("record_date"), r.get("source"), r.get("record_type")) if x)
        head = f"{head} · #{r['id']}" if head else f"#{r['id']}"     # 기록마다 머리를 구분(앞 줄 날짜 이어받기용)
        for line in str(r["text"]).splitlines():
            if line.strip():
                lines.append(f"[기록 {head}] {line.strip()}")
    for r in _rows(conn, "SELECT id, report_date, title, body FROM nurse_reports WHERE bed_id = ? ORDER BY id DESC LIMIT 20",
                   (bed_id,)):
        head = " · ".join(x for x in (r.get("report_date"), f"#n{r['id']}") if x)
        for line in str(r.get("body") or "").splitlines():
            if line.strip():
                lines.append(f"[간호기록 {head}] {line.strip()}")
    return patient, "\n".join(lines)


def save_prior(conn, bed_id: str, target_id: str | None, prior: dict) -> int:
    conn.execute("UPDATE gait_priors SET active = 0 WHERE bed_id = ?", (bed_id,))
    method = "ai_checked" if (prior.get("ai") or {}).get("used") else "rules_only"
    cur = conn.execute("INSERT INTO gait_priors (bed_id, target_id, method, prior_json) VALUES (?, ?, ?, ?)",
                       (bed_id, target_id, method, json.dumps(prior, ensure_ascii=False)))
    return cur.lastrowid


def _decode_prior(row: dict | None) -> dict | None:
    if not row:
        return None
    try:
        prior = json.loads(row["prior_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    prior["id"] = row["id"]
    prior["bed_id"] = row["bed_id"]
    prior["target_id"] = row.get("target_id")
    return prior


def get_active_prior(conn, bed_id: str) -> dict | None:
    rows = _rows(conn, "SELECT * FROM gait_priors WHERE bed_id = ? AND active = 1 ORDER BY id DESC LIMIT 1", (bed_id,))
    return _decode_prior(rows[0] if rows else None)


def get_active_prior_for_target(conn, target_id: str) -> dict | None:
    rows = _rows(conn, "SELECT * FROM gait_priors WHERE target_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
                 (target_id,))
    return _decode_prior(rows[0] if rows else None)


def build_and_store(conn, bed_id: str, use_ai: bool = False, care_setting: str | None = None,
                    ai_caller=None, today: date | None = None) -> dict:
    patient, text = collect_records_text(conn, bed_id)
    if not patient:
        return {"success": False, "error_type": "not_found", "message": "환자를 찾을 수 없습니다."}
    prior = build_prior(patient, text, care_setting=care_setting, use_ai=use_ai, ai_caller=ai_caller, today=today)
    prior_id = save_prior(conn, bed_id, patient.get("mmw_target_id"), prior)
    prior["id"] = prior_id
    prior["bed_id"] = bed_id
    prior["target_id"] = patient.get("mmw_target_id")
    return {"success": True, "prior": prior, "summary": summarize(prior)}
