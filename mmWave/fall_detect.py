"""mmWave 프레임 단위 낙상 감지. 입출력 없음 — send_mmw.py가 매 프레임 호출한다.

바닥은 레이더로 보이지 않으므로(정지 반사 제거) 바닥 높이 대신 '그 사람의 평소 높이(ref)' 대비 비율을 본다.
'높이'는 레이더 추적기가 낸 몸 중심 높이다 — 진짜 무게중심이 아니라 몸 표면(머리·어깨·몸통)에서 튕긴 반사점 묶음의
중심을 추적기가 칼만 필터로 다듬은 값이라 튀고, 반사점이 끊기면 마지막 값에 고정된다.

실측(2026-10-12, 키 170cm, 레이더 2.5m·하향 50°):
  서 있음 1.25~1.60m · 침대에 걸터앉음 0.6m · 무릎 꿇음 0.43m · 바닥에 누움 −0.2~0.28m
  평소 80%→25% 하강: 빠르게 주저앉기 0.88초, 천천히 1.26~3.07초.

판정(2026-10-13 사용자 결정 반영, 라이브 오탐 7건 분석 후 재설계):
  · 환자 추적만 판정 — 한 명만 잡히면 그 사람, 여러 명이면 침대에 가장 가까운 사람이 3초 이상 이어지면 그쪽으로 옮긴다.
  · 그림자 제거 — 다른 추적과 수평 0.7m 안에서 0.8m 넘게 낮게 같이 있는 추적(바닥 반사, 라이브 3건), 걷는데 높이가
    0.5m 아래인 추적(사람은 서서 걷는다)은 사람이 아니다.
  · 값 고정 — 반사점(1011 소속)이 0.5초 넘게 없는 프레임은 '측정 없음'. 하강 자체는 측정이 있는 프레임으로 보여야 한다.
  · 낙상(fallen)  — 서 있다가(평소 80% 이상) 바닥 높이(25% 이하)로 내려가 3초 넘게 머묾. 하강이 1초 이내면 '급격한 하강',
                    아니면 '못 일어남'. 3초 안에 다시 일어나면 낙상이 아니라 주의(caution: 쪼그렸다 일어남 등, 라이브 2건).
  · 걷는 사람 제외 — 낙상 확정은 마지막 1.5초 동안 0.5m 안에 멈춰 있을 때만(넘어질 때의 쏠림·구름은 허용).
    바닥 높이로 잡힌 채 계속 걸으면 확정되지 않고, 0.5m 넘게 이동했다 일어나면 주의도 남기지 않는다(라이브 1건).
  · recovered — 낙상 상태에서 평소 60% 이상으로 1초 이상 돌아옴.
  · 기록용: 하강 중 가장 빠른 아래 방향 속도(추적 velZ, m/s)와 반사점 기반 머리 쪽 높이(top_z).
침대 위(ToF 재실)인지는 서버가 판단해 제외한다(mmw_server).
"""
from collections import deque
import statistics as st

REF_DEFAULT = 1.3        # 개인 기준이 쌓이기 전(실측 서 있음 1.25~1.60m의 아래쪽)
REF_MIN = 1.1            # 앉아 있던 추적의 기준이 앉은 높이가 되지 않게
REF_MAX = 2.0
REF_WINDOW_SEC = 180.0
SMOOTH_SEC = 0.3         # 순간값 튐을 줄이는 중앙값 구간
HIGH_FRAC, LOW_FRAC, RECOVER_FRAC = 0.8, 0.25, 0.6
FAST_SEC = 1.0           # 서 있음 → 바닥 높이가 이 안이면 '급격한 하강'
FAST_VZ = -1.5           # 세로 속도(m/s)가 이보다 빠르게 아래면 급격한 하강. 실측: 천천히 앉기 −0.85(10-13 라이브),
                         # 쪼그려 앉기 −1.2~−1.25, 매트에 빠르게 주저앉기 −1.73(10-13 녹화), 10-12 주저앉기 ≈ −1.6.
                         # 라벨만 바꾸고 낙상 여부는 바꾸지 않는다
CONFIRM_DEADLINE_SEC = 8.0  # 바닥 높이로 내려간 뒤 이 안에 낙상이 확정되지 않으면 이번 구간은 포기(10-20 라이브: 앉아 있던
                            # 추적이 196초 바닥 높이에 있다가 반사점 몇 개가 늦게 잡히자 낙상이 됨. 진짜 낙상은 3초 근처에서 확정)
HANDOVER_FRESH_WINDOW_SEC = 5.0  # 이어받은 뒤 반사점은 이 안에 잡혀야 센다(오래 지나 우연히 잡힌 반사점으로 통과 금지)
CONFIRM_SEC = 3.0        # 사용자 결정(10-13): 바닥 높이에 3초 넘게 머물면 낙상, 그 안에 일어나면 주의
DIP_MIN_SEC = 1.0        # 바닥 높이에 1초 이상 있었어야 '주의'(사용자: 튄 값은 1초를 유지하지 못한다)
LOW_DISP_MAX = 0.5       # 낙상 확정 직전 1.5초 동안 이보다 많이 움직이면 아직 멈추지 않음(걷는 사람이면 끝까지 확정 안 됨)
REST_SEC = 1.5           # 넘어질 때 몸 중심이 0.5~1m 쏠리고 구르는 것은 봐주고(10-12 실측), 확정은 멈춘 뒤에 한다
UPRIGHT_LOOKBACK_SEC = 30.0
NEAR_UPRIGHT_M = 1.2
INHERIT_DIST_M = 0.8
INHERIT_GAP_SEC = 3.0
DROP_TRACK_SEC = 10.0
WALK_SPEED = 0.4         # 이보다 빠르면 걷는 중 → 그때 높이로 평소 높이(키)를 잡는다
WALK_MIN_SAMPLES = 15
SPEED_WINDOW_SEC = 1.0
GHOST_WALK_Z = 0.5       # 걷는 중 높이 중앙값이 이보다 낮으면 사람이 아니다(바닥 반사)
GHOST_WINDOW_SEC = 5.0   # 위 판단은 최근 5초 걷기만 본다(10-13 실측: 추적기가 바닥 반사를 따라가다 진짜 사람으로 갈아타는데,
                         # 3분 기록으로 판단하면 갈아탄 뒤에도 사람을 계속 가짜로 봐 천천히 눕기·주저앉기를 놓쳤다)
SHADOW_XY = 0.7          # 그림자: 다른 추적과 수평 거리
SHADOW_DZ = 0.8          # 그림자: 다른 추적보다 이만큼 이상 낮음
SHADOW_WINDOW_SEC = 2.0
STALE_SEC = 0.5          # 반사점이 이만큼 없으면 측정 없음(추적기가 값을 붙들고 있음)
FRESH_DESCENT_MIN = 2    # 하강 구간에 측정 있는 프레임이 이만큼은 있어야 한다
PATIENT_SWITCH_SEC = 3.0 # 사용자 결정: 침대에 가장 가까운 사람이 3초 이상 이어지면 환자 추적을 옮긴다(고정 안 됐을 때만)
# 환자 고정(2026-10-16 사용자 결정, 발표 때 관객 4~11명이 잡혀 앉은 관객·지나가는 사람이 환자가 됨):
PATIENT_RADIUS_M = 3.0   # 고정 안 됐을 때 환자 후보는 침대 가운데 3m 안(부스 크기 모름 → 느슨한 기본값). 고정된 환자는 밖으로 나가도 따라감
EXIT_WINDOW_SEC = 8.0    # ToF 침상 이탈 뒤 이 안에 침대 근처(EXIT_RADIUS_M)에서 잡힌 사람 = 침대에서 내려온 사람 → 환자로 고정
EXIT_RADIUS_M = 1.5
EXIT_FROM_BED_M = 1.0    # 내려온 사람 = 최근 EXIT_FROM_BED_SEC 안에 침대 가운데 이 안·누움/앉음 높이였던 사람
EXIT_FROM_BED_SEC = 10.0
HANDOVER_DIST_M = 1.0    # 고정된 환자 추적이 사라지면 마지막 위치 1m 안 다른 추적이 3초 안에 이어받는다(겹쳤다 떨어짐·번호 바뀜)
HANDOVER_SEC = 3.0
# 2026-10-22 사용자 결정: 환자는 '침대에 있던 사람' 하나뿐 — ToF가 연결돼 있으면 가까운 사람 규칙을 쓰지 않는다.
BED_LOCK_RADIUS_M = 1.2  # ToF 재실 중 침대 가운데 이 안의 사람 = 침대에 있는 사람 → 환자로 잡아 둠(내려와도 그대로 따라감)
BED_LOCK_MIN_Z = 0.2     # 매트리스(약 0.4m) 위 몸 — 바닥 반사(−0.3~0.1m)는 아님
BED_LOCK_MAX_Z = 1.0     # 침대 위 사람의 몸 중심 높이 상한(누움·걸터앉음 0.3~0.7m, 서 있음 1.2m 이상 — 10-12·13 실측)
REACQUIRE_SEC = 10.0
CONTINUITY_M = 0.5       # 클릭 모드에서 같은 사람으로 이어받는 거리·시간(사용자 결정 10-22)
CONTINUITY_SEC = 1.0
SAME_PERSON_DZ = 0.5     # 같은 사람 이어받기(빈 껍데기·쌍둥이)는 높이 차이가 이 안일 때만(누운 환자 → 옆에 선 사람 방지, 10-22)
ID_REUSE_JUMP_M = 1.0    # 환자 추적이 한 프레임에 이보다 멀리 뛰면 칩이 번호를 다른 사람에게 재사용한 것
ID_REUSE_SPEED = 1.5     # 프레임이 끊긴 동안 사람이 갈 수 있는 거리(m/s) — 이보다 멀리 다시 나타나면 다른 사람
NEW_TRACK_SLACK_SEC = 0.5  # 클릭 모드 이어받기: 환자를 놓치기(반사점 끊기기) 이 시간 전보다 늦게 생긴 추적만 같은 사람
STILL_M = 0.6            # 이 반경 안에서만 움직이며
STILL_HIDE_SEC = 10.0    # 이만큼 가만히 있는 환자 아닌 사람(앉아 있는 관객·랩실 사람)은 평면도에서 숨김(10-22)
# 빈 껍데기 추적(10-22): 앉으면 칩이 앉은 몸에 새 추적을 붙이고, 서 있던 추적은 반사점 0개로 그 자리에 몇 분 남는다
# (10-13 녹화: 한 사람이 두 개로 잡힌 531프레임 중 의자 363·침대 옆 142, 두 추적 거리 중앙값 0.31m).
FALLEN_SWITCH_STALE_SEC = 1.0  # 환자 추적 반사점이 이만큼 끊기면 옆 바닥 추적으로 넘어갔는지 본다(10-22)
DUP_STALE_SEC = 2.0      # 반사점이 이만큼 없으면 '멈춘 추적'
DUP_DIST_M = 0.6         # 멈춘 추적 이 안에 반사점이 잡히는 추적이 있으면 멈춘 쪽은 빈 껍데기(화면에서 숨김)
DUP_TRANSFER_M = 0.5
MERGE_M = 0.45           # 반사점이 둘 다 잡히는데 몸 중심이 이만큼 붙어 있으면 한 사람이 둘로 쪼개진 것(칩이 반사점을 나눠 줌,
                         # 10-13 녹화 17·5번 0.02~0.3m). 사람 둘이 붙어 서도 몸 중심은 보통 0.5m 넘게 떨어진다.     # 환자가 빈 껍데기가 됐고 이 안에 살아 있는 추적이 있으면 같은 사람으로 이어받음     # 환자 추적을 놓치면 마지막 위치 1m 안에서 이 시간까지 같은 사람으로 다시 잡는다(그 뒤엔 환자 없음)
SPLIT_WAIT_SEC = 1.5
SPLIT_STAY_SEC = 0.5     # 빠른 하강 뒤 환자 추적이 그 자리(0.6m 안)에서 어중간한 높이로 이만큼 머물면 바로 옆 바닥 추적을 이어받음
SPLIT_STAY_M = 0.6     # 빠른 하강 뒤 일어남을 이만큼 지켜봄(10-22 라이브: 쓰러짐이 두 추적으로 갈라짐)
REFLECT_NEAR_M = 0.5     # 바닥 높이 추적 이 안에 서 있는 추적 = 그 사람의 바닥 반사(10-22)
HANDOVER_FRESH_MIN = 3   # 이어받은 추적은 이어받은 뒤 반사점이 이만큼 잡혀야 낙상(10-16 발표 기록: 서 있던 사람이 화면 밖으로 나간 자리에
                         # 남은 바닥 높이 추적이 이어받아 낙상이 됨 — 반사점 0개. 10-13 실측 천천히 눕기는 이어받은 뒤 15~60개)
BED_PARK_M = 1.3         # 클릭한 환자를 침대 가운데 이 안에서 놓치면 '침대 위에 있음'으로 기억(누우면 레이더가 몸을 놓침, 10-22 저녁)
BED_PARK_MAX_SEC = 900.0 # 이만큼 지나도 다시 못 잡으면 기억을 버림(다시 클릭)
BED_PARK_EMPTY_SEC = 15.0  # ToF가 침대 빔이라는데(내려옴 없이) 이만큼 지나면 침대에 없는 것 → 기억을 버림
BED_CENTER = (0.1, 1.2)  # 레이더 바닥 좌표의 침대 가운데(10-13 실측: 침대 가운데 걸터앉음 (+0.09, 1.17); 10-12 (+0.20, 1.16))
# 거리 보정(10-13 실측, 키 170cm 가만히 서기): 레이더 수평거리 1.2·2.3·3.3·4.2m에서 높이 1.21·1.27·1.38·1.53m —
# 직선 맞춤 높이 ≈ 1.07 + 0.106 × 거리(멀수록 1m당 약 0.1m 높게 나온다). 같은 사람의 평소 높이를 가까이·멀리에서 배우고
# 다른 거리에서 비교해도 맞도록, 모든 높이를 '2m에 섰을 때' 기준으로 비율 보정한다. 빼기가 아니라 비율인 이유:
# 한 거리 안의 '평소 대비 몇 %'(앉음 40%·바닥 25%)는 바뀌지 않아야 한다(10-12 실측 4.6m 의자 앉기가 그대로 정상).
# (단순 각도 오차가 아니다: 각도를 바꿔 맞추면 바닥에 누운 높이가 바닥 아래로 간다)
HEIGHT_FIT = (1.07, 0.106)   # 서 있을 때 높이 = a + b × 거리
HEIGHT_REF_DIST = 2.0
HEIGHT_DIST_RANGE = (0.5, 5.0)


def dist_corrected_z(x, y, z):
    d = min(HEIGHT_DIST_RANGE[1], max(HEIGHT_DIST_RANGE[0], (x * x + y * y) ** 0.5))
    a, b = HEIGHT_FIT
    return z * (a + b * HEIGHT_REF_DIST) / (a + b * d)


class _Track:
    def __init__(self, tid):
        self.tid = tid
        self.raw = deque()       # (t, z) 최근 SMOOTH_SEC
        self.hist = deque()      # (t, z_smooth) 평소 높이 후보
        self.walk = deque()      # (t, z_smooth) 걷는 중 높이 = 그 사람의 키에 맞춘 평소 높이(바닥 반사 높이는 넣지 않음)
        self.walk_recent = deque()  # (t, z_smooth) 최근 GHOST_WINDOW_SEC 걷기 전부 — 가짜(바닥 반사) 판단용
        self.posh = deque()      # (t, x, y) 이동 속도
        self.zsh = deque()       # (t, z_smooth, fresh, vz, x, y) 최근 3초 — 하강 구간·멈춤 확인
        self.low_maxdisp = 0.0   # 이번 바닥 구간에서 시작 위치로부터 가장 멀리 간 거리
        self.inherited_at = None # 사라진 추적을 새 번호로 이어 받은 시각(넘어지며 번호가 바뀐 경우)
        self.mid_since = None    # 빠른 하강 뒤 어중간한 높이로 그 자리에 머문 시작 시각(넘어지며 갈라짐 판단)
        self.reflection = False  # 서 있는 사람 바로 옆의 바닥 반사였음 — 스스로 일어서기 전엔 낙상 아님(10-22)
        self.last_on_bed = None  # 마지막으로 침대 위(가운데 1m 안·높이 0.2~1.0m)에 있던 시각
        self.born = None         # 처음 본 시각
        self.pts_avg = 0.0       # 최근 반사점 수(지수 평균) — 쪼개진 두 추적 중 대표를 고를 때
        self.tids = {tid}        # 이 사람이 거쳐 온 추적 번호들 — 서 있던 기록은 이 번호들 것만 쓴다
        self.fresh_after_inherit = 0  # 이어받은 뒤 반사점이 잡힌 프레임 수
        self.low_by_other = False     # 이번 바닥 구간의 '서 있던 기록'이 다른 번호(이어받기 전) 것인가
        self.shadow = deque()    # (t, 그림자 조건 여부)
        self.stage = None        # None | 'fallen'
        self.ref_hint = None
        self.low_since = None
        self.low_pos = None
        self.low_ok = False      # 이번 바닥 구간의 하강이 측정으로 확인됐나
        self.low_fast = None     # 이번 바닥 구간 하강 시간(1초 이내면 급격한 하강)
        self.low_vz = None
        self.high_since = None
        self.stage_at = None
        self.last_t = None
        self.last_fresh = None
        self.still_at = None     # (x, y, 시각) STILL_M 안에 머물기 시작한 곳·때
        self.pos = None

    def speed(self):
        if len(self.posh) < 2 or self.posh[-1][0] - self.posh[0][0] < 0.3:
            return 0.0
        (t0, x0, y0), (t1, x1, y1) = self.posh[0], self.posh[-1]
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / (t1 - t0)

    def walk_median(self):
        return st.median(z for _, z in self.walk) if len(self.walk) >= WALK_MIN_SAMPLES else None

    def ref_source(self):
        return "walking" if len(self.walk) >= WALK_MIN_SAMPLES else "history" if len(self.hist) >= 20 else "default"

    def ref(self):
        wm = self.walk_median()
        if wm is not None:
            return min(REF_MAX, max(REF_MIN, wm))
        zs = sorted(z for _, z in self.hist)
        if len(zs) < 20:
            return self.ref_hint or REF_DEFAULT
        return max(REF_MIN, zs[int(len(zs) * 0.75)])

    def is_ghost(self):
        if len(self.walk_recent) < WALK_MIN_SAMPLES:
            return False
        return st.median(z for _, z in self.walk_recent) < GHOST_WALK_Z

    def is_shadow(self):
        return bool(self.shadow) and sum(v for _, v in self.shadow) >= 0.5 * len(self.shadow)

    def stale(self, now):
        return self.last_fresh is None or now - self.last_fresh > STALE_SEC


class FallDetector:
    def __init__(self, manual_only=False, confirm_sec=None, auto_bed=False):
        # manual_only(2026-10-22 사용자 결정, 운영 리더 send_mmw가 켬): 환자는 화면 클릭으로만 정하고 다른 사람으로 자동으로
        # 바뀌지 않는다. 같은 사람이 번호만 바뀌는 경우(그 자리 0.5m 안·1초 안, 넘어지며 갈라짐)만 이어받는다.
        # False는 예전 기록 재생·테스트용(침대·가까운 사람 규칙).
        self.manual_only = manual_only
        # auto_bed(2026-10-29 사용자 결정 '트래킹 자동으로'): manual_only여도 환자가 없으면 ToF 침대 위 사람·침대에서 내려온 사람을
        # 자동으로 잡는다. 한 번 잡으면 manual_only 규칙대로 다른 사람으로 안 바뀐다. 클릭하면 클릭이 우선.
        self.auto_bed = auto_bed
        # 바닥에 머물러야 하는 시간. 기본 3초(10-13 결정), 시연 때 운영 리더는 1초(10-22 사용자 결정: '넘어지는 순간 감지')
        self.confirm_sec = CONFIRM_SEC if confirm_sec is None else confirm_sec
        self.tracks = {}
        self.upright = deque()   # (t, x, y, ref, tid)
        self.alive = set()
        self.patient_tid = None
        self.near_since = {}     # tid → 침대에 가장 가까운 사람이 된 시각
        self.locked = None       # None | 'exit'(침대에서 내려옴) | 'manual'(화면에서 지정) — 고정되면 가까운 사람 규칙으로 안 바꾼다
        self.patient_since = None  # 지금 환자가 환자가 된 시각(같은 사람 이어받기는 유지). 이전에 시작된 하강은 낙상으로 안 본다
        self.patient_lost = None   # (시각, x, y, tids) 고정된 환자 추적이 사라진 때
        self.bed_in = None
        self.exit_at = None
        self.manual_seq = None
        self.manual_reject = None  # 마지막 클릭을 받지 않은 이유(로그용)
        self.bed_park = None       # {'t0': 놓친 시각, 't': 기억 시작, 'tids'} 클릭한 환자를 침대 위에서 놓친 상태
        self.lost_low = None       # 바닥 높이에서 사라진 클릭 환자 추적(10-22 저녁: 사라져도 바닥에 있었으면 낙상)
        self._frame = []
        self.dups = {}             # 빈 껍데기 추적 → (거리, 같은 사람의 살아 있는 추적)
        self.tof_seen = False      # ToF 재실 정보를 한 번이라도 받았나(받았으면 '침대에 있던 사람'만 환자)
        self.split_pending = None  # 빠른 하강 뒤 '일어남'을 1.5초 지켜보는 중(쓰러짐이 둘로 갈라졌는지)

    # ── 서 있던 기록 ──
    def _upright_near(self, x, y, t_from, t_to, tids):
        """같은 사람의 기록만 쓴다 — 이 추적이 거쳐 온 번호들(넘어지며 번호가 바뀐 경우·이어받은 경우).
        10-16: 예전엔 '지금은 사라진 아무 추적'의 기록도 썼더니, 앉아 있던 관객 옆을 지나간 사람의 기록을 빌려 낙상이 됐다."""
        best = None
        for ts, ux, uy, uref, utid in self.upright:
            if utid not in tids:
                continue
            if t_from <= ts <= t_to and ((ux - x) ** 2 + (uy - y) ** 2) ** 0.5 <= NEAR_UPRIGHT_M:
                if best is None or ts > best[0]:
                    best = (ts, uref, utid)
        return best

    def _inherit(self, now, t):
        best = None
        for tid, tr in self.tracks.items():
            if tr.pos is None or tr.last_t is None or now - tr.last_t > INHERIT_GAP_SEC or now - tr.last_t < 0.2:
                continue
            d = ((tr.pos[0] - t["posX"]) ** 2 + (tr.pos[1] - t["posY"]) ** 2) ** 0.5
            if d <= INHERIT_DIST_M and (best is None or d < best[0]):
                best = (d, tid)
        if not best:
            return None
        if self.patient_tid == best[1]:
            old = self.tracks[best[1]]
            if self.manual_only and (best[0] > CONTINUITY_M or now - old.last_t > CONTINUITY_SEC):
                return None                      # 클릭 모드: 그 자리·바로 뒤가 아니면 같은 사람으로 보지 않는다
            self.patient_tid = t["tid"]          # 환자 추적이 번호만 바뀐 것 → 환자 유지
        tr = self.tracks.pop(best[1])
        tr.inherited_at = now
        tr.fresh_after_inherit = 0
        tr.tids.add(t["tid"])
        return tr

    # ── 환자 추적 선택 ──
    def _set_patient(self, now, tid, locked):
        if tid != self.patient_tid:
            self.patient_tid = tid
            self.patient_since = now        # 새 사람 → 키 기준이 다를 수 있으니 이 뒤에 시작된 하강만 본다
        self.locked = locked
        self.near_since = {}
        self.patient_lost = None

    def _handover(self, now, people):
        """고정된 환자 추적이 사라졌을 때: 마지막 위치 1m 안 추적이 3초 안에 이어받는다(같은 사람으로 본다)."""
        t0, lx, ly, tids = self.patient_lost
        limit = CONTINUITY_SEC if self.manual_only else (REACQUIRE_SEC if self.tof_seen else HANDOVER_SEC)
        if now - t0 > limit:
            if self.manual_only and ((lx - BED_CENTER[0]) ** 2 + (ly - BED_CENTER[1]) ** 2) ** 0.5 <= BED_PARK_M:
                self.bed_park = {"t0": t0, "t": now, "tids": set(tids)}   # 침대에 누워 레이더가 놓침 → 그 사람을 기억
            self.locked = None              # 놓침 → 고정 풀고 가까운 사람 규칙으로
            self.patient_lost = None
            self.patient_tid = None
            return
        cands = [(((t["posX"] - lx) ** 2 + (t["posY"] - ly) ** 2) ** 0.5, t["tid"]) for t in people]
        cands = [c for c in cands if c[0] <= (CONTINUITY_M if self.manual_only else HANDOVER_DIST_M)]
        if self.manual_only:
            # 10-22 저녁: 원래부터 옆에 있던 다른 사람 추적은 이어받지 않는다 — 환자를 놓친 뒤 새로 생긴 번호만 같은 사람
            old = self.tracks.get(self.patient_tid)
            ref = min(t0, old.last_fresh) if old is not None and old.last_fresh is not None else t0
            born = {t["tid"]: self.tracks[t["tid"]].born for t in people}
            # 단 바닥 높이 추적은 원래 있던 번호여도 된다 — 넘어질 때 몸이 발밑 바닥 반사 추적으로 넘어간다(17:36 실측:
            # 이 규칙 때문에 넘어진 몸을 못 이어받아 환자 없음 → 낙상 안 뜸). 서 있는 다른 사람은 바닥 높이가 아니다
            pref = old.ref() if old is not None else None
            low = lambda tid: (pref is not None and self.tracks[tid].zsh
                               and self.tracks[tid].zsh[-1][1] <= LOW_FRAC * pref)
            cands = [c for c in cands if (born.get(c[1]) is not None and born[c[1]] >= ref - NEW_TRACK_SLACK_SEC) or low(c[1])]
        if cands:
            tid = min(cands)[1]
            self.tracks[tid].tids |= tids
            self.tracks[tid].reflection = False
            self.tracks[tid].inherited_at = now
            self.tracks[tid].fresh_after_inherit = 0
            self.patient_tid = tid          # 같은 사람 → patient_since 유지
            self.patient_lost = None

    @staticmethod
    def _similar_height(a, b, tol=SAME_PERSON_DZ):
        """같은 사람으로 이어받을 때 두 추적의 최근 높이 차이가 tol 이하인가(누운 사람 ↔ 선 사람 구분)."""
        if not a.zsh or not b.zsh:
            return False
        return abs(a.zsh[-1][1] - b.zsh[-1][1]) <= tol

    def _newer(self, new, old, twin=False):
        """클릭 모드: new가 old에서 갈라져 나온(새로 생긴) 추적인가 — 원래 있던 다른 사람에게 넘기지 않게(10-22).
        빈 껍데기·놓침: old의 반사점이 끊기기 직전 이후에 생긴 추적만. 쌍둥이(둘 다 살아 있음): old보다 늦게 생긴 추적만."""
        if not self.manual_only:
            return True
        if new.born is None:
            return False
        if twin:
            return old.born is not None and new.born > old.born
        ref = old.last_fresh if old.last_fresh is not None else old.born
        return ref is not None and new.born >= ref - NEW_TRACK_SLACK_SEC

    @staticmethod
    def _stale_for(tr, now):
        """반사점이 마지막으로 잡힌 뒤(한 번도 없으면 처음 본 뒤) 지난 시간. 0.0도 올바른 시각으로 다룬다."""
        ref = tr.last_fresh if tr.last_fresh is not None else tr.born
        return 0.0 if ref is None else now - ref

    def _mark_dups(self, now, targets):
        """반사점 없이 멈춘 추적 옆(0.6m)에 반사점 있는 추적이 있으면 멈춘 쪽은 같은 사람의 빈 껍데기."""
        fresh = [t for t in targets if self.tracks[t["tid"]].last_fresh is not None
                 and now - self.tracks[t["tid"]].last_fresh <= 0.5]
        self.dups = {}
        for t in targets:
            tr = self.tracks[t["tid"]]
            stale_for = self._stale_for(tr, now)
            if stale_for < DUP_STALE_SEC:
                continue
            best = None
            for o in fresh:
                if o is t:
                    continue
                d = ((o["posX"] - t["posX"]) ** 2 + (o["posY"] - t["posY"]) ** 2) ** 0.5
                if d <= DUP_DIST_M and (best is None or d < best[0]):
                    best = (d, o["tid"])
            if best:
                self.dups[t["tid"]] = best
        # 둘 다 살아 있는데 0.45m 안에 붙은 추적 → 한 사람이 쪼개진 것: 환자는 그대로 두고, 아니면 반사점이 적은 쪽을 숨긴다
        for i, a in enumerate(fresh):
            for b in fresh[i + 1:]:
                if a["tid"] in self.dups or b["tid"] in self.dups:
                    continue
                d = ((a["posX"] - b["posX"]) ** 2 + (a["posY"] - b["posY"]) ** 2) ** 0.5
                if d > MERGE_M:
                    continue
                ta, tb = self.tracks[a["tid"]], self.tracks[b["tid"]]
                keep, hide = (a, b) if ta.pts_avg >= tb.pts_avg else (b, a)
                if hide["tid"] == self.patient_tid:
                    tk, th = self.tracks[keep["tid"]], self.tracks[hide["tid"]]
                    if (tk.stage is None and tk.pts_avg >= max(5.0, 2 * th.pts_avg) and not tk.reflection
                            and tk.zsh and tk.zsh[-1][1] >= 0.5 and th.low_since is None and th.stage is None
                            and self._similar_height(th, tk) and self._newer(tk, th, twin=True)):   # 서 있는 쪽으로만, 하강 중엔 안 옮김
                        # 10-22: 환자가 쌍둥이 중 약한 쪽(바닥 반사로 미끄러질 추적)이면 강한 쪽으로 옮긴다(같은 사람)
                        tk.tids |= th.tids
                        if len(tk.walk) < len(th.walk):
                            tk.walk, tk.hist = deque(th.walk), deque(th.hist)
                        tk.inherited_at, tk.fresh_after_inherit = now, 0
                        self.patient_tid = keep["tid"]
                    else:
                        keep, hide = hide, keep    # 애매하면 환자는 숨기지 않는다
                self.dups[hide["tid"]] = (d, keep["tid"])
        # 10-22(점심 감시): 둘 다 반사점 없이 멈춘 추적이 0.45m 안에 붙어 있으면(앉아 가만히 있는 한 사람) 하나만 보인다
        stale = [t for t in targets if t["tid"] not in self.dups and t not in fresh
                 and self._stale_for(self.tracks[t["tid"]], now) >= DUP_STALE_SEC]
        for i, a in enumerate(stale):
            for b in stale[i + 1:]:
                if a["tid"] in self.dups or b["tid"] in self.dups:
                    continue
                if ((a["posX"] - b["posX"]) ** 2 + (a["posY"] - b["posY"]) ** 2) ** 0.5 > MERGE_M:
                    continue
                ta, tb = self.tracks[a["tid"]], self.tracks[b["tid"]]
                keep, hide = (a, b) if (a["tid"] == self.patient_tid or
                                        (b["tid"] != self.patient_tid and ta.pts_avg >= tb.pts_avg)) else (b, a)
                self.dups[hide["tid"]] = (((a["posX"] - b["posX"]) ** 2 + (a["posY"] - b["posY"]) ** 2) ** 0.5, keep["tid"])
        # 10-22: 환자 추적의 반사점이 1초 넘게 끊겼는데 마지막 위치 1m 안에 반사점 있는 바닥 높이 추적이 있으면 쓰러진 몸이
        # 그쪽으로 넘어간 것 — 예전 추적이 사라질 때까지 기다리지 않고 바로 이어받는다(11:44 실측: 기다리느라 판정이 늦음)
        p = self.tracks.get(self.patient_tid)
        if (p is not None and self.patient_tid in self.alive and p.stage is None and p.pos
                and self._stale_for(p, now) >= FALLEN_SWITCH_STALE_SEC):
            pref = p.ref()
            best = None
            for o in fresh:
                ot = self.tracks[o["tid"]]
                if ot is p or ot.reflection or ot.stage or not ot.zsh:
                    continue
                d = ((o["posX"] - p.pos[0]) ** 2 + (o["posY"] - p.pos[1]) ** 2) ** 0.5
                if d <= self._cont_m() and ot.zsh[-1][1] <= LOW_FRAC * pref and (best is None or d < best[0]):
                    best = (d, ot)
            if best:
                ot = best[1]
                ot.tids |= p.tids
                if len(ot.walk) < len(p.walk):
                    ot.walk, ot.hist = deque(p.walk), deque(p.hist)
                ot.inherited_at, ot.fresh_after_inherit, ot.reflection = now, 0, False
                self.patient_tid = ot.tid        # 같은 사람(patient_since·고정 유지)
        # 환자가 빈 껍데기가 됐으면 바로 옆 살아 있는 추적이 같은 사람
        if (self.patient_tid in self.dups and self.dups[self.patient_tid][0] <= DUP_TRANSFER_M
                and self.patient_tid not in [o["tid"] for o in fresh]):
            old, new = self.tracks[self.patient_tid], self.tracks[self.dups[self.patient_tid][1]]
            # 10-22 오후: 넘어져 누운(반사점 없는) 환자 옆에 선 사람에게 넘어가던 문제 → 높이가 비슷할 때만 같은 사람
            if new.stage is None and self._similar_height(old, new) and self._newer(new, old):
                new.tids |= old.tids
                if len(new.walk) < len(old.walk):
                    new.walk, new.hist = deque(old.walk), deque(old.hist)
                new.inherited_at, new.fresh_after_inherit = now, 0
                self.patient_tid = new.tid      # 고정 상태·patient_since 그대로(같은 사람)
                self.dups.pop(new.tid, None)

    def _choose_patient(self, now, targets):
        people = [t for t in targets if not self.tracks[t["tid"]].is_shadow() and not self.tracks[t["tid"]].is_ghost()]
        alive_ids = {t["tid"] for t in people}
        dist = lambda t: ((t["posX"] - BED_CENTER[0]) ** 2 + (t["posY"] - BED_CENTER[1]) ** 2) ** 0.5
        if self.patient_tid is not None and self.patient_tid in self.alive:
            self.patient_lost = None        # 다시 보이면 '놓친 위치'를 지운다(10-22: 남아 있다가 다음에 놓칠 때 옛 위치 근처 사람으로 튐)
        # 환자 추적이 사라졌으면(겹침·번호 바뀜) 3초 안에 그 자리 1m 안 추적이 이어받는다 — 고정 여부와 상관없이 같은 사람
        if self.patient_tid is not None and self.patient_tid not in self.alive:
            old = self.tracks.get(self.patient_tid)
            if (old is not None and old.stage is None and old.low_since is not None and old.low_ok
                    and self._rescue_fallen(now, old)):
                return                      # 바닥에 닿은 뒤 사라진 환자 → 바닥 위치의 쓰러진 몸을 이어받음(10-22 저녁)
            if (self.manual_only and old is not None and old.stage is None and old.low_since is not None and old.low_ok
                    and not old.low_by_other and old.zsh and old.zsh[-1][1] <= LOW_FRAC * old.ref()):
                self.lost_low = old         # 바닥 높이에서 사라짐 → 바닥에 닿은 지 confirm_sec이 지나면 낙상(update 끝에서)
            if self.patient_lost is None:
                old = self.tracks.get(self.patient_tid)
                if old is not None and old.pos:
                    self.patient_lost = (now, old.pos[0], old.pos[1], set(old.tids))
            if self.patient_lost:
                was_locked = self.locked
                self._handover(now, people)
                if self.patient_tid in self.alive:
                    self.locked = was_locked
                    return
                if self.locked:
                    return                  # 고정된 환자는 3초 동안 기다린다
        elif self.locked:
            return
        if self.manual_only:
            if self.patient_tid is None and self.bed_park:
                self._bed_reacquire(now, targets, people)
            if self.auto_bed and self.patient_tid is None and self.tof_seen:
                self._bed_pick(now, targets, people, dist)   # 10-29: 클릭 전에는 침대에 있던 사람을 자동으로 잡는다
            return                          # 클릭으로만 정한다(사용자 결정 10-22) — 가까운 사람 규칙 없음

        if self.tof_seen:
            # 침대에 있던 사람만 환자(사용자 결정 10-22) — 가까운 사람 규칙 없음, 다른 사람으로 바뀌지 않는다
            if self.patient_tid is not None and self.patient_tid in self.alive and self.locked:
                return
            if self.patient_lost is not None and self.locked:
                return                      # 놓친 환자를 다시 잡는 중(마지막 위치 1m 안, 10초)
            self._bed_pick(now, targets, people, dist)
            return
        # (ToF 정보가 없을 때만 — 예전 기록 재생·ToF 고장) 침대에서 내려온 사람 → 고정
        if self.exit_at is not None and now - self.exit_at <= EXIT_WINDOW_SEC and self.locked is None:
            near = [t for t in people if dist(t) <= EXIT_RADIUS_M]
            if near:
                self._set_patient(now, min(near, key=dist)["tid"], "exit")
                return
        cand = [t for t in people if dist(t) <= PATIENT_RADIUS_M]
        if not cand:
            return
        if len(cand) == 1:
            if cand[0]["tid"] != self.patient_tid:
                self._set_patient(now, cand[0]["tid"], None)
            return
        nearest = min(cand, key=dist)["tid"]
        if self.patient_tid not in {t["tid"] for t in cand}:
            self._set_patient(now, nearest, None)
            return
        if nearest == self.patient_tid:
            self.near_since = {}
            return
        since = self.near_since.setdefault(nearest, now)
        self.near_since = {nearest: since}
        if now - since >= PATIENT_SWITCH_SEC:
            self._set_patient(now, nearest, None)

    def _bed_pick(self, now, targets, people, dist):
        """ToF 침대 위 사람(누움·앉음 높이) 또는 침대에서 막 내려온 사람을 환자로."""
        if self.bed_in is True:
            # 침대 위 사람은 누웠거나 앉아 있다 — 침대 옆에 서 있는 방문객(1.3m 이상)을 잡지 않게 높이도 본다
            # 그림자 규칙은 쓰지 않는다: 누운 환자 옆에 누가 서 있으면 환자가 '서 있는 사람 아래 바닥 반사'로 보일 수 있다.
            # 대신 높이로 — 바닥 반사(0.1m 아래)는 BED_LOCK_MIN_Z로 거른다
            near = [t for t in targets if dist(t) <= BED_LOCK_RADIUS_M
                    and BED_LOCK_MIN_Z <= dist_corrected_z(t["posX"], t["posY"], t["posZ"]) <= BED_LOCK_MAX_Z]
            if near:
                self._set_patient(now, min(near, key=dist)["tid"], "bed")
        elif self.exit_at is not None and now - self.exit_at <= EXIT_WINDOW_SEC:
            # 최근 10초 안에 실제로 침대 위(1m 안, 누움·앉음 높이)에 있었던 사람만 — 이탈 순간 침대 옆에 서 있던 사람 제외(10-22)
            was_on_bed = lambda t: (self.tracks[t["tid"]].last_on_bed is not None
                                    and now - self.tracks[t["tid"]].last_on_bed <= EXIT_FROM_BED_SEC)
            near = [t for t in people if dist(t) <= EXIT_RADIUS_M and was_on_bed(t)]
            if near:
                self._set_patient(now, min(near, key=dist)["tid"], "exit")

    def _bed_reacquire(self, now, targets, people):
        """클릭한 환자를 침대 위에서 놓쳤을 때(10-22 저녁: 누우면 추적이 끊겨 환자 없음 → 다시 클릭해야 했다) 같은 사람만 다시 잡는다.
        ① 놓친 뒤 새로 생긴 추적이 침대 가운데 1.2m 안·누움/앉음 높이(0.2~1.0m) — 침대 옆에 선 방문객은 높이로 제외
        ② ToF '침대에서 내려옴' 뒤 8초 안 침대 1.5m 안에 놓친 뒤 새로 생긴 추적"""
        bp = self.bed_park
        if (now - bp["t"] > BED_PARK_MAX_SEC
                or (self.bed_in is False and (self.exit_at is None or self.exit_at < bp["t0"] - 1.0)
                    and now - bp["t"] > BED_PARK_EMPTY_SEC)):
            self.bed_park = None
            return
        dist = lambda t: ((t["posX"] - BED_CENTER[0]) ** 2 + (t["posY"] - BED_CENTER[1]) ** 2) ** 0.5
        new = lambda t: (self.tracks[t["tid"]].born is not None
                         and self.tracks[t["tid"]].born >= bp["t0"] - NEW_TRACK_SLACK_SEC)
        cand = [t for t in people if new(t) and dist(t) <= BED_LOCK_RADIUS_M
                and BED_LOCK_MIN_Z <= dist_corrected_z(t["posX"], t["posY"], t["posZ"]) <= BED_LOCK_MAX_Z]
        if not cand and self.exit_at is not None and self.exit_at >= bp["t0"] - 1.0 and now - self.exit_at <= EXIT_WINDOW_SEC:
            cand = [t for t in people if new(t) and dist(t) <= EXIT_RADIUS_M]
        if cand:
            tid = min(cand, key=dist)["tid"]
            self.tracks[tid].tids |= bp["tids"]
            self._set_patient(now, tid, "manual")
            self.bed_park = None

    def _cont_m(self):
        return CONTINUITY_M if self.manual_only else HANDOVER_DIST_M

    @staticmethod
    def _fast(e):
        return (e.get("drop_sec") is not None and e["drop_sec"] <= FAST_SEC) or (e.get("vz_min") is not None and e["vz_min"] <= -1.0)

    def _rescue_fallen(self, now, tr):
        """빠른 하강으로 바닥에 닿은 환자 추적이 그 자리를 벗어나거나 사라졌을 때 바닥 위치의 바닥 높이 추적을 이어받는다(10-22 저녁)."""
        if not (tr.low_fast is not None or (tr.low_vz is not None and tr.low_vz <= -1.0)) or not tr.low_pos:
            return False
        if now - tr.low_since > CONFIRM_DEADLINE_SEC:
            return False
        # 쪼그렸다 일어나 걸어간 사람(10-22 11:39)은 일어난 높이로 움직인다 — 바닥 높이 그대로 밀려나거나 사라질 때만
        if not tr.zsh or tr.zsh[-1][1] > LOW_FRAC * tr.ref():
            return False
        e0 = {"low_start": tr.low_since, "drop_sec": tr.low_fast if tr.low_fast is not None else 0.0,
              "vz_min": tr.low_vz, "x": tr.low_pos[0], "y": tr.low_pos[1]}
        return self._split_on_fall(now, tr, e0, tr.low_pos)

    def _split_on_fall(self, now, tr, e, low_pos=None):
        """2026-10-22 라이브: 빠르게 쓰러지는 순간 레이더가 사람을 둘로 나눔 — 쓰러진 몸은 바로 옆 바닥 높이 추적(원래
        발밑 반사)으로 넘어가고, 환자 추적은 근처 다른 반사로 옮겨 다시 높아져 '쪼그렸다 일어남(주의)'이 됐다.
        빠른 하강(1초 이내 또는 세로 속도 −1m/s 이하) 뒤 '일어남'이면, 바닥 위치 1m 안에서 최근 1초 안 반사점이 잡힌
        바닥 높이 추적을 같은 사람으로 이어받고 하강 시각부터 3초를 계속 센다."""
        if not self._fast(e):
            return False
        lx, ly = low_pos or tr.low_pos or (e["x"], e["y"])
        ref = tr.ref()
        best = None
        for tid in self.alive:
            o = self.tracks.get(tid)
            if o is None or o is tr or o.stage or not o.zsh or o.last_fresh is None or now - o.last_fresh > 1.0:
                continue
            ox, oy = o.pos
            d = ((ox - lx) ** 2 + (oy - ly) ** 2) ** 0.5
            if d <= self._cont_m() and o.zsh[-1][1] <= LOW_FRAC * ref and (best is None or d < best[0]):
                best = (d, o)
        if not best:
            return False
        o = best[1]
        o.reflection = False
        o.tids |= tr.tids
        recent_fresh = sum(1 for r in o.zsh if r[2] and r[0] >= now - 2.0)   # 쓰러진 몸에 직전 2초 잡힌 반사점도 인정
        o.walk = deque(tr.walk)              # 같은 사람 → 평소 높이(키)도 이어받음
        o.hist = deque(tr.hist)
        o.inherited_at, o.fresh_after_inherit = now, recent_fresh
        o.low_since, o.low_pos, o.low_maxdisp = e.get("low_start") or now, o.pos, 0.0
        o.low_ok, o.low_by_other = True, True
        o.low_fast, o.low_vz = e.get("drop_sec"), e.get("vz_min")
        self.patient_tid = o.tid             # patient_since는 그대로(같은 사람)
        if not self.locked:
            self.locked = "split"            # 자동 모드에서도 이어받은 쓰러진 몸을 가까운 사람 규칙으로 바꾸지 않게
        return True

    def set_bed(self, now, in_bed):
        """ToF 침상 재실(True/False/None). True→False 순간을 '침대에서 내려옴'으로 기록한다.
        10-22: 침대에 있던 사람을 계속 따라가므로 다시 누워도 고정을 풀지 않는다(같은 사람)."""
        if in_bed is not None and not self.tof_seen:
            self.tof_seen = True
            if not self.locked:             # ToF가 오기 전 가까운 사람 규칙으로 잡힌 환자는 버린다
                self.patient_tid, self.patient_since, self.patient_lost = None, None, None
        if self.bed_in is True and in_bed is False:
            self.exit_at = now
            if self.locked == "bed":
                self.locked = "exit"
        self.bed_in = in_bed

    def set_manual(self, now, tid, seq):
        """화면에서 점을 눌러 환자 지정(seq가 바뀔 때만 한 번 적용)."""
        if seq is None or seq == self.manual_seq:
            return
        self.manual_seq = seq
        tr = self.tracks.get(tid)
        # 10-22: 바닥 반사(그림자)는 눌러도 환자로 지정하지 않는다(11:24 반사 추적 지정 → 낙상 오탐).
        # 10-22 저녁: 높이 0.5m 기준은 뺐다 — 걷는 사람도 레이더 높이가 순간 0.1~0.7m로 흔들려 클릭이 세 번 무시됐다.
        # 그림자·빈 껍데기는 평면도에 안 그리므로 화면에 보이는 점은 그대로 지정된다
        if tr is None or tid not in self.alive:
            self.manual_reject = "지금 레이더에 없는 번호"
        elif tr.is_shadow():
            self.manual_reject = "바닥 반사(그림자)"
        else:
            self.manual_reject = None
            self.bed_park = None
            self.lost_low = None
            self._set_patient(now, tid, "manual")

    def update(self, now, targets):
        """targets: [{'tid','posX','posY','posZ', 선택: 'velZ','npts','top_z'}] (바닥 기준). 반환: 환자 추적의 사건 목록."""
        self.alive = {t["tid"] for t in targets}
        for t in targets:
            if t["tid"] not in self.tracks:
                tr = self._inherit(now, t) or _Track(t["tid"])
                tr.tid = t["tid"]
                tr.born = tr.born or now
                if len(tr.hist) < 20:
                    near = self._upright_near(t["posX"], t["posY"], now - UPRIGHT_LOOKBACK_SEC, now, tr.tids)
                    tr.ref_hint = near[1] if near else None
                self.tracks[t["tid"]] = tr
        # 그림자: 다른 추적 바로 위(수평 0.7m 안)에 0.8m 이상 높은 추적이 있으면 이 추적은 바닥 반사
        for t in targets:
            under = any(o is not t and ((o["posX"] - t["posX"]) ** 2 + (o["posY"] - t["posY"]) ** 2) ** 0.5 <= SHADOW_XY
                        and o["posZ"] - t["posZ"] >= SHADOW_DZ for o in targets)
            sh = self.tracks[t["tid"]].shadow
            sh.append((now, under))
            while sh and now - sh[0][0] > SHADOW_WINDOW_SEC:
                sh.popleft()
        # 10-22 오후: 레이더 칩이 사라진 환자 번호를 곧바로 다른 곳 사람에게 다시 붙인다(로그에 24번이 다섯 번 새로 등장).
        # 환자 추적이 한 프레임 사이 1m 넘게 순간이동하면 다른 사람 → 따라가지 않고 환자 없음(다시 클릭)
        if self.manual_only and self.patient_tid is not None:
            p = self.tracks.get(self.patient_tid)
            cur = next((t for t in targets if t["tid"] == self.patient_tid), None)
            # 10-22 저녁: 0.5초 넘게 끊겼다 다시 나타난 경우도 본다(끊긴 시간 동안 걸어갈 수 있는 거리보다 멀면 다른 사람)
            if (p is not None and cur is not None and p.pos and p.last_t is not None
                    and ((cur["posX"] - p.pos[0]) ** 2 + (cur["posY"] - p.pos[1]) ** 2) ** 0.5
                    > ID_REUSE_JUMP_M + ID_REUSE_SPEED * max(0.0, now - p.last_t - 0.1)):
                self.tracks.pop(self.patient_tid, None)   # 옛 사람의 기록이 새 사람에게 붙지 않게
                self.patient_tid, self.locked, self.patient_since, self.patient_lost = None, None, None, None
                tr = _Track(cur["tid"]); tr.born = now
                self.tracks[cur["tid"]] = tr
        self._mark_dups(now, targets)
        self._choose_patient(now, targets)
        events = []
        self._frame = targets
        for t in targets:
            tr = self.tracks[t["tid"]]
            ev = self._step(tr, now, t)
            if t["tid"] == self.patient_tid:
                for e in ev:
                    # 환자가 바뀌기 전에 시작된 하강은 넘긴다(새 환자의 키 기준이 아직 없음 — 사용자 결정 10-16)
                    if (e["stage"] in ("fallen", "caution") and self.patient_since is not None
                            and e.get("low_start", now) < self.patient_since):
                        if e["stage"] == "fallen":
                            tr.stage = tr.stage_at = None
                        continue
                    if e["stage"] == "caution" and self._fast(e) and self.split_pending is None:
                        # 빠른 하강 뒤 '일어남' — 1.5초 기다려 원래 키로 돌아오는지 본다(쪼그림이면 돌아옴, 쓰러짐이 둘로
                        # 갈라진 경우면 환자 추적이 평소 80% 아래에 머물고 옆 바닥 추적이 쓰러진 몸)
                        self.split_pending = {"tid": tr.tid, "e": e, "until": now + SPLIT_WAIT_SEC, "low_pos": tr.low_pos}
                        continue
                    events.append(e)
                # 10-22 시연: 빠르게 바닥까지 갔다가 어중간한 높이로만 올라오고 바로 옆에 바닥 추적이 있으면 즉시 이어받는다
                # (예전엔 '일어남(주의)' 뒤 1.5초를 기다려 바닥에 닿고 4초 뒤에야 낙상)
                mid = (tr.stage is None and tr.low_since is not None and tr.low_ok and tr.zsh and tr.low_pos
                       and LOW_FRAC * tr.ref() < tr.zsh[-1][1] < HIGH_FRAC * tr.ref()
                       and ((tr.pos[0] - tr.low_pos[0]) ** 2 + (tr.pos[1] - tr.low_pos[1]) ** 2) ** 0.5 <= SPLIT_STAY_M)
                tr.mid_since = (tr.mid_since or now) if mid else None
                # 0.5초 동안 그 자리에서 어중간한 높이로 머물러야 한다 — 쪼그렸다 일어나 걸어가는 사람(10-22 11:39)은 여기서 빠진다
                if (mid and now - tr.mid_since >= SPLIT_STAY_SEC and self.split_pending is None
                        and (tr.low_fast is not None or (tr.low_vz is not None and tr.low_vz <= -1.0))):
                    e0 = {"low_start": tr.low_since, "drop_sec": tr.low_fast if tr.low_fast is not None else 0.0,
                          "vz_min": tr.low_vz, "x": tr.pos[0], "y": tr.pos[1]}
                    if self._split_on_fall(now, tr, e0, tr.low_pos):
                        tr.low_since = None
                # 10-22 20:41 실측: 1초 만에 바닥까지 떨어진 뒤 레이더 칩이 떨어지던 속도로 계속 예측해 환자 추적이 반사점 없이
                # 바닥 아래로 꺼졌다가 옆으로 2m 날아가 사라졌다(쓰러진 몸은 원래 발밑 바닥 추적). 떨어진 자리에서 0.6m 넘게
                # 밀려나면 그 자리 바닥 추적을 같은 사람으로 이어받는다
                elif (tr.stage is None and tr.low_since is not None and tr.low_ok and tr.low_pos
                        and ((tr.pos[0] - tr.low_pos[0]) ** 2 + (tr.pos[1] - tr.low_pos[1]) ** 2) ** 0.5 > SPLIT_STAY_M):
                    if self._rescue_fallen(now, tr):
                        tr.low_since = None
        if self.split_pending:
            sp = self.split_pending
            p = self.tracks.get(sp["tid"])
            if p is not None and p.zsh and p.zsh[-1][1] >= HIGH_FRAC * p.ref():
                events.append(sp["e"])              # 원래 키로 돌아옴 → 쪼그렸다 일어남(주의)
                self.split_pending = None
            elif now >= sp["until"]:
                self.split_pending = None
                if p is None or not self._split_on_fall(now, p, sp["e"], sp["low_pos"]):
                    events.append(sp["e"])
        ll = self.lost_low
        if ll is not None:
            if ll.stage is not None or ll.low_since is None or now - ll.low_since > CONFIRM_DEADLINE_SEC:
                self.lost_low = None
            elif now - ll.low_since >= self.confirm_sec:
                # 10-22 저녁 사용자 결정: 클릭한 환자가 바닥 높이까지 내려간 뒤 추적이 사라져도 바닥에 닿은 지 confirm_sec이면 낙상
                ll.stage, ll.stage_at = "fallen", now
                fast = ll.low_fast is not None and (ll.low_vz is None or ll.low_vz <= FAST_VZ)
                z_last = ll.zsh[-1][1] if ll.zsh else None
                events.append({"tid": ll.tid, "stage": "fallen", "at": now, "low_start": ll.low_since,
                               "x": ll.low_pos[0] if ll.low_pos else ll.pos[0], "y": ll.low_pos[1] if ll.low_pos else ll.pos[1],
                               "z": round(z_last, 3) if z_last is not None else None, "ref": round(ll.ref(), 3),
                               "ref_source": ll.ref_source(), "npts": 0, "top_z": None, "kind": "fast" if fast else "slow",
                               "drop_sec": ll.low_fast, "low_sec": round(now - ll.low_since, 1), "vz_min": ll.low_vz,
                               "stale": True, "lost": True})
                self.lost_low = None
        for tid in list(self.tracks):
            tr = self.tracks[tid]
            if tid not in self.alive and now - (tr.last_t or now) > DROP_TRACK_SEC:
                del self.tracks[tid]
        while self.upright and now - self.upright[0][0] > UPRIGHT_LOOKBACK_SEC + 5:
            self.upright.popleft()
        return events

    def _step(self, tr, now, t):
        z = dist_corrected_z(t["posX"], t["posY"], t["posZ"])
        tr.last_t = now
        tr.pos = (t["posX"], t["posY"])
        if tr.still_at is None or ((tr.pos[0] - tr.still_at[0]) ** 2 + (tr.pos[1] - tr.still_at[1]) ** 2) ** 0.5 > STILL_M:
            tr.still_at = (tr.pos[0], tr.pos[1], now)
        npts = t.get("npts")
        tr.pts_avg = 0.8 * tr.pts_avg + 0.2 * (npts or 0)
        fresh = npts is None or npts > 0          # 반사점 정보가 없는 입력(예전 기록 재생)은 측정 있음으로 본다
        if fresh:
            tr.last_fresh = now
            if tr.inherited_at is not None and now - tr.inherited_at <= HANDOVER_FRESH_WINDOW_SEC:
                tr.fresh_after_inherit += 1
        tr.raw.append((now, z))
        while tr.raw and now - tr.raw[0][0] > SMOOTH_SEC:
            tr.raw.popleft()
        zs = st.median(v for _, v in tr.raw)
        tr.posh.append((now, t["posX"], t["posY"]))
        while tr.posh and now - tr.posh[0][0] > SPEED_WINDOW_SEC:
            tr.posh.popleft()
        spd = tr.speed()
        if tr.stage is None and spd >= WALK_SPEED and fresh:
            tr.walk_recent.append((now, zs))
            if zs >= GHOST_WALK_Z:
                tr.walk.append((now, zs))
        while tr.walk and now - tr.walk[0][0] > REF_WINDOW_SEC:
            tr.walk.popleft()
        while tr.walk_recent and now - tr.walk_recent[0][0] > GHOST_WINDOW_SEC:
            tr.walk_recent.popleft()
        tr.zsh.append((now, zs, fresh, t.get("velZ"), t["posX"], t["posY"]))
        while tr.zsh and now - tr.zsh[0][0] > 3.0:
            tr.zsh.popleft()
        ref = tr.ref()
        if tr.stage is None and (zs >= RECOVER_FRAC * ref or not tr.hist) and fresh:
            tr.hist.append((now, zs))
        while tr.hist and now - tr.hist[0][0] > REF_WINDOW_SEC:
            tr.hist.popleft()
        x, y = tr.pos
        if (((x - BED_CENTER[0]) ** 2 + (y - BED_CENTER[1]) ** 2) ** 0.5 <= EXIT_FROM_BED_M
                and BED_LOCK_MIN_Z <= zs <= BED_LOCK_MAX_Z):
            tr.last_on_bed = now
        if tr.stage is None and zs >= HIGH_FRAC * ref and fresh and not tr.is_shadow() and not tr.is_ghost():
            self.upright.append((now, x, y, ref, tr.tid))
        events = []
        # 가짜(걷는 바닥 반사)는 환자 선택에서만 뺀다. 판정까지 막으면 바닥 반사를 따라가던 추적이 넘어진 사람을 이어받았을 때
        # (10-13 천천히 눕기 실측: 사람 추적 3이 0.35m까지 내려간 뒤 사라지고 반사 추적 27이 바닥의 사람을 이어받음) 놓친다.
        person = not tr.is_shadow()

        def ev(stage, **extra):
            return {"tid": tr.tid, "stage": stage, "at": now, "low_start": tr.low_since, "x": x, "y": y, "z": round(zs, 3), "ref": round(ref, 3),
                    "ref_source": tr.ref_source(), "npts": npts, "top_z": t.get("top_z"), **extra}

        if zs >= HIGH_FRAC * ref:
            tr.reflection = False
        elif zs <= LOW_FRAC * ref and tr.stage is None:
            # 10-22 오탐 2건: 바닥 높이 추적 0.5m 안에 그 사람이 여전히 서 있으면(평소 80% 이상) 이건 그 사람의 바닥 반사
            for o in self._frame:
                if o["tid"] != tr.tid and ((o["posX"] - x) ** 2 + (o["posY"] - y) ** 2) ** 0.5 <= REFLECT_NEAR_M \
                        and dist_corrected_z(o["posX"], o["posY"], o["posZ"]) >= HIGH_FRAC * ref:
                    tr.reflection = True
                    tr.low_since, tr.low_ok = None, False
                    break
        if zs <= LOW_FRAC * ref and person and not tr.reflection:
            up = None
            if tr.low_since is not None and not tr.low_ok and tr.stage is None:
                # 원래 바닥에 있던 추적(바닥 반사)이 그 뒤 넘어진 사람을 이어받은 경우(10-13 천천히 눕기 실측):
                # 바닥 구간 시작 '이후'에 이 자리에 서 있던 사람이 있으면 바닥 구간을 지금부터 다시 센다
                up = self._upright_near(x, y, tr.low_since, now, tr.tids)
                if up:
                    tr.low_since = None
            if tr.low_since is None:
                tr.low_since, tr.low_pos, tr.low_maxdisp = now, (x, y), 0.0
                up = up or self._upright_near(x, y, now - UPRIGHT_LOOKBACK_SEC, now, tr.tids)
                # 하강 구간(마지막 서 있던 때 ~ 지금)에 측정 있는 프레임이 있어야 진짜 하강(값 고정·점프 제외)
                seg = [r for r in tr.zsh if up and r[0] > up[0]]   # 마지막으로 서 있던 때 '이후' 프레임만
                # 넘어지며 추적을 놓쳤다가 바닥에서 새 번호로 다시 잡은 경우(10-12 실측 9→15)는 하강 프레임이 없어도 인정
                regained = bool(up) and tr.inherited_at is not None and tr.inherited_at > up[0] and fresh
                tr.low_ok = bool(up) and (sum(1 for r in seg if r[2]) >= FRESH_DESCENT_MIN or regained)
                tr.low_by_other = bool(up) and up[2] != tr.tid
                tr.low_fast = round(now - up[0], 2) if up and now - up[0] <= FAST_SEC else None
                vzs = [r[3] for r in seg if isinstance(r[3], (int, float))]
                tr.low_vz = round(min(vzs), 2) if vzs else None
            tr.low_maxdisp = max(tr.low_maxdisp, ((x - tr.low_pos[0]) ** 2 + (y - tr.low_pos[1]) ** 2) ** 0.5)
            back = [r for r in tr.zsh if r[0] >= max(now - REST_SEC, tr.low_since)]   # 바닥에 내려온 뒤 프레임만(쓰러지는 쏠림 제외)
            rest_disp = ((x - back[0][4]) ** 2 + (y - back[0][5]) ** 2) ** 0.5 if back else 0.0
            if tr.stage is None and tr.low_ok and now - tr.low_since > CONFIRM_DEADLINE_SEC:
                tr.low_ok = False               # 늦은 확정 금지 — 이번 바닥 구간은 낙상도 주의도 아님
            handover_ok = not tr.low_by_other or tr.fresh_after_inherit >= HANDOVER_FRESH_MIN
            # 10-22 저녁 사용자 결정: 클릭으로 지정한 환자는 바닥 높이에 있으면 추적이 밀려나도 낙상(레이더 칩이 하강 속도로 계속
            # 예측해 추적이 바닥 아래로 꺼진 채 옆으로 날아가 '멈춤' 조건을 못 채웠다 — 20:30·20:41 놓침)
            still_ok = rest_disp <= LOW_DISP_MAX or (self.manual_only and tr.tid == self.patient_tid)
            if (tr.stage is None and tr.low_ok and handover_ok and now - tr.low_since >= self.confirm_sec
                    and still_ok):     # 바닥 높이 3초(시연 1초) + 마지막 1.5초 동안 멈춤(클릭한 환자는 멈춤 조건 없음)
                tr.stage, tr.stage_at = "fallen", now
                # 라벨: 1초 이내 하강 + (세로 속도를 알면) 초당 1m 이상 아래로 → 급격한 하강, 아니면 못 일어남(판정 자체는 같다)
                fast = tr.low_fast is not None and (tr.low_vz is None or tr.low_vz <= FAST_VZ)
                events.append(ev("fallen", kind="fast" if fast else "slow", drop_sec=tr.low_fast,
                                 low_sec=round(now - tr.low_since, 1), vz_min=tr.low_vz, stale=tr.stale(now)))
        else:
            if (tr.stage is None and tr.low_since is not None and tr.low_ok and zs >= RECOVER_FRAC * ref
                    and now - tr.low_since >= DIP_MIN_SEC and tr.low_maxdisp <= LOW_DISP_MAX):
                # 바닥 높이까지 내려갔다가 3초 안에 일어남 → 낙상이 아니라 주의(쪼그렸다 일어남 등)
                events.append(ev("caution", kind="dip", drop_sec=tr.low_fast, low_sec=round(now - tr.low_since, 1),
                                 vz_min=tr.low_vz))
                tr.low_since = None
            elif zs > LOW_FRAC * ref and tr.stage is None and zs >= RECOVER_FRAC * ref:
                tr.low_since = None

        if zs >= RECOVER_FRAC * ref:
            tr.high_since = tr.high_since or now
            if tr.stage and now - tr.high_since >= 1.0:
                events.append(ev("recovered", was=tr.stage))
                tr.stage = tr.stage_at = None
                tr.low_since = None
        else:
            tr.high_since = None
        return events

    def debug(self, tid):
        """오탐 분석용: 그 추적의 판정 근거 값."""
        tr = self.tracks.get(tid)
        if tr is None:
            return None
        return {"ref": round(tr.ref(), 3), "ref_source": tr.ref_source(), "walk_samples": len(tr.walk),
                "hist_samples": len(tr.hist), "ref_hint": tr.ref_hint, "speed": round(tr.speed(), 3),
                "low_since": tr.low_since, "alive": sorted(self.alive), "patient_tid": self.patient_tid,
                "shadow": tr.is_shadow(), "ghost": tr.is_ghost(),
                "upright_recent": [list(u) for u in self.upright if u[0] >= (tr.low_since or 0) - FAST_SEC][-20:]}

    def patient_info(self):
        return {"tid": self.patient_tid, "locked": self.locked, "since": self.patient_since, "bed_in": self.bed_in,
                "bed_park": self.bed_park is not None and self.patient_tid is None}

    def roles(self):
        """실시간 표시용: 추적 번호 → 'patient'(환자) / 'dup'(빈 껍데기) / 'shadow'(바닥 반사) /
        'still'(10초 넘게 제자리인 환자 아닌 사람 — 평면도에서 숨김) / 'other'(다른 사람)."""
        out = {}
        for tid in self.alive:
            tr = self.tracks.get(tid)
            if tr is None:
                continue
            out[tid] = ("patient" if tid == self.patient_tid else "dup" if tid in self.dups
                        else "shadow" if (tr.is_shadow() or tr.is_ghost())
                        # 10-22 저녁 사용자 결정: 클릭 전에는 아무도 숨기지 않는다(누구든 눌러 지정할 수 있게).
                        # 환자를 지정한 뒤에만 가만히 있는 다른 사람을 숨긴다. 환자 본인은 가만히 있어도 절대 숨기지 않는다
                        else "still" if (self.patient_tid is not None and tr.still_at
                                         and tr.last_t - tr.still_at[2] >= STILL_HIDE_SEC) else "other")
        return out

    def state(self):
        """실시간 표시용: 낙상 단계에 있는 환자 추적."""
        return [{"tid": tr.tid, "stage": tr.stage, "since": tr.stage_at, "x": tr.pos[0], "y": tr.pos[1],
                 "z": round(tr.raw[-1][1], 3) if tr.raw else None, "ref": round(tr.ref(), 3), "ref_source": tr.ref_source()}
                for tr in self.tracks.values() if tr.stage and tr.tid == self.patient_tid]
