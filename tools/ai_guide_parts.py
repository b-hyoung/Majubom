"""AI 가이드 본문(파트별). build_ai_guide.py가 불러 쓴다. 코드 조각은 guide_lib가 생성 시점 소스에서 잘라 온다."""
from guide_lib import block, py, ref  # noqa: F401
from build_code_guide import cite, easy, formula, part, step, table, warn

# 파일 경로
TR, PP, TS, ROI = "TOF/ml/train_posture_v2.py", "TOF/ml/predict_posture.py", "server/tof_server.py", "TOF/ml/roi.py"
LA, TA = "TOF/ml/lstm_anomaly.py", "TOF/ml/tof_anomaly.py"
GP, GT = "server/gait_prior.py", "server/gait_trend.py"
AR, AG, PK, RT, EK = "server/ai_report.py", "server/ai_agent.py", "server/paper_kb.py", "server/retrieval.py", "server/emr_kb.py"
IA, RR, RTR, DA = "server/integrated_analysis.py", "server/report_review.py", "server/report_trace.py", "server/dashboard_api.py"
FRP, RC, MA, AK, TT = "server/fall_report.py", "server/report_charts.py", "server/maintenance_ai.py", "server/analysis_kb.py", "server/tof_toss.py"


def p_map(f):
    pk = f.get("papers") or {}
    return part("map", "0. AI 지도 — 무엇이 진짜 '학습'이고 무엇이 계산·규칙인가", "처음 읽는 사람이 헷갈리지 않게 종류별로 나눔", f"""
{easy("마주봄의 'AI'는 한 덩어리가 아니다. ① 데이터를 보고 <b>스스로 배우는 모델</b>(자세 분류·이상 탐지), ② 공식으로 <b>미래를 추정하는 통계</b>(보행 저하 예측·보행 기대 범위), ③ 글을 읽고 <b>문장을 쓰는 LLM</b>(리포트·검색어·기록 판단), ④ 사람이 정한 <b>규칙</b>(낙상 감지·뒤척임·위험 점수)으로 나뉜다. 숫자(점수·확률·예측)는 ①②④가 만들고, LLM은 그 숫자를 <b>설명만</b> 한다 — 이것이 이 프로젝트의 제1원칙이다.")}
{table(["이름", "종류", "입력 → 출력", "코드", "운영에 쓰이나"], [
    ["자세 분류", "학습 모델(Random Forest 200그루)", "ToF 128칸 거리 → 6가지 자세", ref(TR), "✅ 매 프레임"],
    ["움직임 이상 탐지", "학습 모델(LSTM 오토인코더)", "4초 움직임 특징 → 재구성 오차", ref(TA), "참고값만(판정에 안 씀)"],
    ["보행 기대 범위", "통계 + LLM 보조 판단", "입원 기록·나이·키 → 기대 보행속도 범위", ref(GP), "✅ 기록 5일 전 잠정 판정"],
    ["보행 저하 예측", "통계(칼만 필터)", "하루 보행값 → 7·14일 뒤 예측·감소 확률", ref(GT), "✅ 14일 기록부터"],
    ["논문 검색", "임베딩 + BM25 + RRF", f"검색어 → 근거 논문 조각(논문 {pk.get('papers', '?')}편·조각 {pk.get('chunks', '?')}개)", ref(PK), "✅ 리포트 작성 때"],
    ["환자 기록 검색", "어휘 해시 벡터(신경망 아님)", "검색어 → 그 환자 EMR 조각", ref(EK), "✅ 리포트 작성 때"],
    ["AI 종합 리포트", "LLM(gpt-4o-mini)", "계산된 사실·근거 → 15개 항목 문장", ref(IA), "✅ 매주 + 버튼"],
    ["낙상 사건 리포트", "규칙 + LLM", "낙상 순간 사실 → 원인 후보·조치 문장", ref(FRP), "✅ 낙상 감지 직후"],
    ["리포트 자동 검토", "규칙(LLM 없음)", "저장된 리포트 → 점검 결과", ref(RR), "✅ 리포트마다"],
    ["낙상 감지·뒤척임·위험 점수", "규칙(학습 없음)", "레이더 높이·ToF 모양 → 사건", "mmWave/fall_detect.py · " + ref(TT), "✅ (AI 아님 — 코드 가이드 참고)"],
    ["변화 감시(ADWIN)·과거 리포트 검색", "실험", "—", ref(MA) + " · " + ref(AK), "❌ 운영 경로에 연결 안 됨"]])}
{step("0-1", "제1원칙: 숫자는 서버, 문장은 LLM", "<p>LLM은 그럴듯한 숫자를 지어낼 수 있다. 그래서 위험 점수·예측값·확률은 모두 서버 코드가 계산하고, LLM 프롬프트에는 '주어진 수치를 바꾸거나 새로 만들지 말라'를 넣는다. LLM 응답은 형식 검사를 통과해야 저장되고, 저장 뒤에도 규칙 검토(10장)를 받는다.</p>",
      "<p>키가 없거나 호출이 실패하면 가짜 결과를 만들지 않고 <code>{success: False, error_type, message}</code>를 돌려준다('정직한 실패'). 모든 LLM 호출은 " + ref(AR, "def call_openai") + " 하나를 거친다.</p>",
      py(AR, "call_openai", "call_openai() — 모든 LLM 호출의 관문"))}
{step("0-2", "외부로 나가는 것과 안 나가는 것", table(["처리", "어디서"], [
    ["자세 분류·이상 탐지·보행 예측·낙상 감지", "Pi 안에서만(인터넷 불필요)"],
    ["리포트 문장·검색어·기록 판단", "OpenAI API(키: 루트 .env, 저장소 제외)"],
    ["논문·검색어 임베딩", "OpenAI 임베딩 API(text-embedding-3-small)"],
    ["환자 기록(EMR) 벡터", "Pi 안에서만(해시 벡터 — 외부 전송 없음)"]]),
      "<p>2026-10-13부터 실측 낙상이 나면 사건 리포트를 자동 요청한다 → 그 순간의 사건 수치·투약·위험 요인이 OpenAI로 간다. 실제 환자 운영 전에 이 자동 전송을 계속할지 정해야 한다.</p>")}
""")


def p_posture(f):
    r = f.get("posture") or {}
    folds = r.get("folds") or []
    fold_rows = [[("S01 시험(S02로 학습)" if x["held_out"] == 0 else "S02 시험(S01로 학습)"), f"{x['accuracy'] * 100:.1f}%", f"{x['macro_f1']:.3f}", x["n"]] for x in folds]
    meta = f.get("rf_meta") or {}
    labels = r.get("confusion_labels") or []
    cm = r.get("confusion") or []
    cm_rows = [[labels[i]] + [str(v) for v in row] for i, row in enumerate(cm)] if cm else []
    return part("posture", "1. 자세 분류 — Random Forest", "ToF 128칸 거리로 누움·옆으로·앉음·걸터앉음·빈 침대 구분", f"""
{easy("침대 머리·발쪽 ToF 두 개가 각각 8×8=64칸 거리를 잰다. 합친 128개 숫자를 보고 '예/아니오 질문 나무' 200그루가 각자 자세를 고르고, 다수결(확률 평균)로 정한다. 나무 하나는 '발쪽 37번 칸이 900mm보다 가까운가?' 같은 질문을 10여 번 잇는다.")}
{step("1-1", "학습 데이터", table(["항목", "값"], [
    ["녹화 도구", ref("TOF/record_posture.py") + " — 30초 녹화 × 회차, 자세 이름이 파일에 같이 저장"],
    ["사람", "S01(10-08, 79회차) · S02(10-13, 102회차, 담요 15회차 포함) · 빈 침대 17회차"],
    ["클래스(프레임 쌍 수)", ", ".join(f"{k} {v}" for k, v in (r.get("class_counts") or {}).items())],
    ["전처리", "두 센서 프레임을 시각 250ms 안으로 짝짓기 · 회차 앞뒤 1초 제외 · 침대 밖 칸은 -1(ROI)"]]),
      "<p>한 프레임 = 한 샘플이라 같은 회차 안 프레임끼리는 거의 같다. 그래서 '회차/사람 단위로 빼고 시험'해야 정직하다(아래 1-3).</p>",
      py(TR, "load", "load() — 녹화 파일 → (128칸, 라벨)") + py(ROI, "mask_offbed", "roi.mask_offbed() — 침대 밖 칸 지우기"))}
{step("1-2", "모델과 학습", formula("RandomForestClassifier(n_estimators=200, class_weight='balanced')<br>나무마다: 데이터를 복원추출로 다시 뽑고(bootstrap), 갈림마다 √128≈11개 칸만 후보로 → 서로 다른 나무 200그루<br>예측 확률 = 200그루가 각 자세에 준 표의 비율 = '확신도'"),
      f"<p>현재 운영 모델: 사람 {meta.get('subjects', '?')}, 학습 {meta.get('trained_at', '?')}, 나무 {meta.get('n_trees', '?')}그루, ROI {meta.get('roi', '?')}. 이전 모델(S01만)은 TOF/ml/archive/에 보관.</p>"
      "<p>class_weight='balanced': 바로 누움이 많고 빈 침대가 적어도 클래스마다 같은 무게로 배우게 한다.</p>",
      py(TR, "make_model") + py(TR, "main", "main() — 평가 후 전체로 다시 학습·저장"))}
{step("1-3", "정확도 — 정직하게 읽기", table(["시험 방식", "정확도", "macro-F1", "샘플"], fold_rows) +
      "<p>혼동행렬(행=정답, 열=예측, 두 시험 합계):</p>" + (table(["정답 \\ 예측"] + labels, cm_rows) if cm_rows else ""),
      "<p><b>같은 사람의 다른 회차로 시험하면 100%</b>가 나온다(같은 날·같은 몸). 이건 성능이 아니다. 기준은 '한 사람으로 배우고 다른 사람으로 시험(LOSO)'이고, 한 사람만 배운 모델은 처음 보는 사람에게 58~73%였다. "
      "주된 혼동: 오른쪽↔왼쪽 누움(화면은 '옆으로 누움' 하나로 표시), 걸터앉음→빈 침대(두 사람이 앉은 위치가 달랐음 — ROI를 꺼도 같아 ROI 탓 아님). "
      "지금 모델은 두 사람을 다 배웠으므로 <b>세 번째 사람 기준 정확도는 아직 모른다</b> — 새 사람 데이터는 먼저 시험용으로 쓴다.</p>")}
{step("1-4", "실시간 추론과 화면용 다듬기", "<p>서버가 새 프레임마다 128칸을 만들어 모델에 넣는다. 사람 칸(빈 침대 기준보다 15cm 이상 가까운 칸)이 하나도 없으면 모델 결과를 무시하고 '빈 침대'로 확정한다(빈 침대를 걸터앉음으로 잘못 보는 것 방지).</p>", "",
      py(PP, "frame_vec", "frame_vec() — 실시간 128칸 만들기") + py(PP, "predict"))}
{warn("이불·체형·설치 각도가 학습 때와 다르면 정확도가 떨어진다. 설치 장소를 옮기면 빈 침대 기준을 다시 잡고(/dev 버튼), 센서 각도(발 50°·머리 30°)를 그대로 맞춘다.")}
{cite("Breiman L. Random Forests. Machine Learning 2001;45:5–32.")}
""")


def p_anomaly(f):
    r = f.get("lstm") or {}
    return part("anomaly", "2. 움직임 이상 탐지 — LSTM 오토인코더(참고값)", "평소 움직임을 다시 그려 보고, 못 그리면 '평소와 다름'", f"""
{easy("평소(정상) 4초 움직임만 보여 주며 '그대로 다시 그리기'를 연습시킨 모델이다. 처음 보는 이상한 움직임은 잘 못 그려서 오차가 커진다. 그 오차가 기준보다 크면 '평소와 다른 움직임'. 무엇이 이상한지는 알려 주지 않고, 지금은 판정에 쓰지 않는 참고값이다.")}
{step("2-1", "입력 특징 12개(프레임마다)", "<p>사람 칸 수, 몸 중심 x·y·z, 좌우·앞뒤 퍼짐, x·y·z 범위, 침대 안 여부, 두 센서 사람 칸 수. 36프레임(≈4초)을 한 묶음으로 본다.</p>", "",
      block(LA, r"^FEATURES", r"^DEV", "FEATURES") + py(TA, "frame_feature", "frame_feature() — 서버 안 특징 추출(학습과 같은 식)"))}
{step("2-2", "모델 구조와 판정 기준", formula("인코더 LSTM(12→48) → 마지막 숨은 상태 z(48차원) → 4초 길이로 복제 → 디코더 LSTM(48→48) → 선형(48→12)<br>"
      f"오차 = 평균((입력 − 재구성)²) · 기준 = 검증 정상 데이터 오차의 99백분위 = {r.get('threshold', '?')}"),
      f"<p>학습 {r.get('trained_at', '?')} · 학습 창 {r.get('train_windows', '?')} · 검증 창 {r.get('val_windows', '?')} · 검증에서 기준 넘은 비율 {r.get('val_over_threshold', '?')}. {r.get('note', '')}</p>",
      py(LA, "LSTMAE", "class LSTMAE") + py(TA, "Detector.score", "Detector.score() — 최근 4초 오차"))}
{warn("정상 데이터가 S01 한 사람·하루·정지 자세뿐이라, 다른 사람의 평범한 뒤척임도 '이상'으로 나올 수 있다. 그래서 위험 판정·알림에는 쓰지 않고 응답의 lstm 항목에만 싣는다.")}
{cite("Malhotra P et al. LSTM-based Encoder-Decoder for Multi-sensor Anomaly Detection. ICML Anomaly Detection Workshop 2016 (arXiv:1607.00148).")}
""")


def p_prior(f):
    return part("prior", "3. 보행 기대 범위 — 입원 기록 + 문헌 + LLM 보조 판단", "센서 기록이 쌓이기 전 '이 환자에게 기대되는 걸음 속도'", f"""
{easy("새로 입원한 환자는 평소 걸음 기록이 없다. 그래서 나이·키로 '같은 나이 건강한 한국 노인'의 기준값을 찾고, 치매 중증도·파킨슨증만큼 줄이고, 요양시설 평균과 섞어 기대 범위를 만든다. LLM은 기록을 읽고 '치매가 어느 정도인지·혼자 걷는지'만 판단하며, 그 판단은 기록 원문을 그대로 인용해야 채택된다.")}
{step("3-1", "숫자는 문헌에서만", "<p>기준값·감소량·시설 평균은 " + ref("server/gait_evidence.json") + "의 출처 수치만 쓴다(AI가 숫자를 만들지 않음). 측정 보행속도·날짜·센서 예측 밖 낙상 요인은 서버 규칙으로만 뽑는다.</p>",
      "", py(GP, "condition_reduction") + py(GP, "mixture", "mixture() — 두 경로(건강 노인−감소 / 시설 평균) 섞기"))}
{step("3-2", "LLM 판단과 검증", formula("LLM 출력: {dementia_severity, parkinsonism, ambulation, community_path_weight} + 각 판단의 quote(기록 원문)<br>채택 조건: ① quote가 기록 줄에 실제로 있음 ② quote 안에 판단 단서(예: '중등도', 'CDR')가 있음 ③ 부정·가족력이 아님"),
      "<p>검증에서 떨어진 판단은 이유와 함께 버리고 규칙 결과만 쓴다. 환자 이름은 보내기 전에 지운다(redact_names).</p>",
      py(GP, "validate_ai_profile", "validate_ai_profile() — 인용 검증") + py(GP, "run_ai"))}
{step("3-3", "어디에 쓰이나", "<p>개인 기록이 5건 모이기 전까지 '잠정' 판정(주의·경고까지, 정식 알람 없음)에 쓰고, 보행 저하 예측(4장) 칼만 필터의 출발점(사전값)으로도 쓴다. 통합 위험 점수에는 넣지 않는다.</p>")}
{warn("문헌 숫자는 다른 집단(지역사회 노인·실험실 측정)에서 나온 값이라 개인에게 그대로 맞지 않는다. 그래서 '범위'로만 쓰고, 개인 기록이 쌓이면 개인 기준이 우선한다.")}
""")


def p_trend(f):
    return part("trend", "4. 보행 저하 예측 — 칼만 필터(국소 선형 추세)", "매일 걸음값으로 7·14일 뒤를 예측하고 '줄고 있나'를 확률로", f"""
{easy("하루하루 걸음 속도는 들쭉날쭉하다. 칼만 필터는 '진짜 수준'과 '하루에 얼마씩 변하는지(기울기)'를 불확실성(±)과 함께 추정한다. 그 기울기를 앞으로 늘려 7·14일 뒤를 예측하고, '7일 동안 의미 있게 줄었을 확률'이 80%를 넘을 때만 신호를 낸다.")}
{step("4-1", "하루 대표값 만들기", "<p>레이더 6초 구간 중 <b>환자 확정 + 곧게 걸은 구간</b>만 모아 하루 중앙값을 낸다. 하루 5구간 미만인 날은 버린다. 2026-10-20부터 환자 확정은 낙상 판정기가 고른 환자를 그대로 쓴다.</p>", "", py(GT, "daily_series"))}
{step("4-2", "칼만 필터 식", formula("상태 x = [수준 L, 하루 기울기 b] · 하루 지나면 L ← L + b, b ← b (+작은 변화 Q)<br>관측 y = L + 오차(R = max(그날 표준오차², 최소 오차²))<br>예측: x = F x, P = F P Fᵀ + Q,   F = [[1,1],[0,1]]   갱신: K = P Hᵀ/(H P Hᵀ + R), H = [1,0]"),
      "<p>기록 없는 날은 예측 단계만 거친다. 보행 기대 범위(3장)가 있으면 출발 수준으로 쓴다.</p>", py(GT, "kalman_trend"))}
{step("4-3", "신호 기준(논문)", table(["지표", "주의(7일 누적 감소)", "경고"], [["보행 속도", "2.54 cm/s", "5.1 cm/s"], ["보폭", "2.54 cm", "7.6 cm"]]) +
      "<p>감소 확률 = Φ((감소량 − 기준) / 표준편차) ≥ 80%일 때만. 기록일 14일 미만이면 예측하지 않는다.</p>",
      "<p>Phillips 2017: 집 안 상시 측정에서 7일 누적 5.1 cm/s 감소 시 3주 내 낙상 86.3%(변화 없음 19.5%). <b>이 86.3%는 그 연구 집단의 값이지 우리 환자의 낙상 확률이 아니다</b> — 리포트에도 그렇게 쓰게 한다.</p>",
      py(GT, "_metric_assessment"))}
{warn("아직 실제 환자 기록이 14일 이상 쌓인 적이 없어 실데이터 검증은 없다(합성 시험만). 본선 전에 매일 걷는 기록을 쌓으면 실제 예측을 보여 줄 수 있다.")}
{cite("Phillips LJ et al. Using Embedded Sensors in Independent Living to Predict Gait Changes and Falls. West J Nurs Res 2017;39(1):78–94. · Harvey AC. Forecasting, Structural Time Series Models and the Kalman Filter. Cambridge Univ Press 1989.")}
""")


def p_search(f):
    pk = f.get("papers") or {}
    return part("search", "5. 근거 찾기 — 검색어 생성·하이브리드 검색·충분성 판단", "LLM이 무엇을 찾을지 정하고, 서버가 논문·환자 기록에서 찾는다", f"""
{easy("리포트를 쓰기 전에 '이 환자 상태를 설명할 논문이 뭐지?'를 먼저 찾는다. LLM이 환자 상태를 보고 검색어 2~3개를 만들고, 서버가 논문 조각을 두 방식(뜻이 비슷한 것 + 단어가 겹치는 것)으로 찾아 순위를 합친다. LLM이 '근거가 충분한가'를 판단해 부족하면 한 번 더 찾는다.")}
{step("5-1", "전체 흐름(investigate)", "<p>상태 모으기 → 검색어 생성(LLM) → 하이브리드 검색 + 환자 EMR 검색 → 충분성 판단(LLM) → 부족하면 추가 검색어로 다시(기본 최대 2라운드, 리포트 버튼은 1라운드). 이 함수는 리포트도 점수도 만들지 않고 재료만 모은다.</p>", "",
      py(AG, "gather_patient_state", "gather_patient_state() — 환자 상태 모으기") + py(AG, "generate_queries") + py(AG, "judge_sufficiency") + py(AG, "investigate"))}
{step("5-2", "논문 지식 베이스", f"<p>등록 논문 {pk.get('papers', '?')}편을 문단 단위 조각 {pk.get('chunks', '?')}개로 나눠 OpenAI 임베딩(text-embedding-3-small) 벡터로 바꿔 LanceDB(server/lancedb_store/paper_chunks)에 저장했다.</p>", "",
      py(PK, "embed_texts"))}
{step("5-3", "하이브리드 검색 — 벡터 + BM25 + RRF", formula("벡터: 질문 벡터와 조각 벡터의 코사인 유사도 순위<br>BM25: 질문 단어가 조각에 얼마나 나오나(드문 단어일수록 가중) 순위<br>RRF 점수 = Σ 1/(60 + 순위)   — 점수 크기 대신 순위만 합침(학습 필요 없음)"),
      "<p>RRF 점수는 순위용일 뿐 확률·신뢰도가 아니다. 18개 개발용 질의로 벡터 단독과 비교했다(research/alternatives).</p>",
      py(PK, "search_hybrid") + py(RT, "sparse_scores") + py(RT, "fuse_rankings"))}
{step("5-4", "환자 기록(EMR) 검색 — 신경망이 아닌 해시 벡터", formula("글자 → 단어 + 2·3글자 조각 → SHA-256 해시로 512칸 중 한 칸에 ±1 → 길이 1로 정규화"),
      "<p>환자 기록을 외부로 보내지 않으려고 Pi 안에서 만드는 <b>어휘 유사도</b> 벡터다(뜻 이해 아님). 환자별로 걸러서 다른 환자 기록이 섞이지 않는다. 검색 점수는 확률이 아니다.</p>",
      py(EK, "local_embedding") + py(EK, "search"))}
{cite("Robertson S, Zaragoza H. The Probabilistic Relevance Framework: BM25 and Beyond. 2009. · Cormack GV, Clarke CLA, Büttcher S. Reciprocal Rank Fusion. SIGIR 2009. · Sawarkar K et al. Blended RAG. arXiv:2404.07220, 2024.")}
""")


def p_report(f):
    return part("report", "6. AI 종합 리포트 — 계산은 서버, 문장은 LLM", "15개 항목 리포트가 만들어지는 과정과 안전장치", f"""
{easy("5장에서 모은 재료(환자 기록·센서 판정·보행 예측·논문 근거)를 한 JSON으로 묶어 LLM에 주고 '이 사실만으로 15개 항목을 쓰라'고 한다. 위험 점수는 LLM이 아니라 서버가 미리 계산한다. 응답 형식을 검사하고, 저장 뒤 규칙 검토(7장)를 붙이고, 그래프 숫자도 서버가 넣는다.")}
{step("6-1", "위험 점수(서버 계산)", formula("센서 단계 → 점수: 정상 0.1 · 주의 0.4 · 경고 0.7 · 위험 0.95<br>종합 = 확보된 신호 중 최댓값 · 신호 확보 비율 = 확보 신호 수 / 3   (낙상 확률 아님)"), "",
      py(IA, "compute_risk_score") + py(IA, "compute_data_confidence"))}
{step("6-2", "프롬프트 규칙(요약)", "<ul><li>주어진 수치를 바꾸거나 새로 만들지 않기</li><li>의심 신호를 '낙상 확정'으로 바꾸지 않기</li><li>이벤트 0건을 '낙상 없음'의 증거로 쓰지 않기</li>"
      "<li>논문의 낙상 확률(86.3% 등)을 환자 확률로 쓰지 않기</li><li>시뮬레이션이면 첫 문장에 명시</li><li>투약 조정은 '의사 판단' 문구와 함께 참고로만</li></ul>", "",
      block(IA, r"^INTEGRATED_SYSTEM_PROMPT", r"^def _build_payload", "INTEGRATED_SYSTEM_PROMPT"))}
{step("6-3", "작성·검증·저장", "<p>LLM 응답(JSON)의 15개 항목을 검사한다. 목록·객체는 글로 풀고, 빠진 항목이 3개 이하면 '정보 부족'으로 채우며, 그보다 많거나 숫자만 온 응답은 저장하지 않는다(2026-10-13). "
      "저장 전 report_charts가 계산한 그래프 숫자(24시간 사건 원그래프·7일 보행 속도·위험도 변동)를 붙인다.</p>", "",
      py(IA, "generate_integrated_report") + py(RC, "build"))}
{step("6-4", "언제 만들어지나", "<p>① 매주 자동(스케줄러가 마지막 리포트 7일 뒤) ② 리포트 탭 '지금 리포트 만들기' ③ 예전에는 낙상 때도 이걸 썼지만, 지금은 7장의 낙상 사건 리포트가 따로 만든다. 진행 단계는 report_trace가 메모리에만 기록해 개발 화면에서 볼 수 있다(키·비밀번호는 지움).</p>", "",
      py(DA, "_run_due_weekly_reports") + py(RTR, "emit"))}
{warn("gpt-4o-mini는 지시를 어기고 두루뭉술하게 쓰거나 항목을 빠뜨릴 때가 있다(10-13 첫 실행 실패). 그래서 형식 검사와 규칙 검토가 있고, 리포트는 간호사의 판단을 돕는 참고 자료로만 쓴다.")}
""")


def p_fall(f):
    return part("fall", "7. 낙상 사건 리포트·자동 검토", "넘어진 순간 '왜?'를 정리하고, 리포트를 규칙으로 다시 점검", f"""
{easy("낙상이 감지되면 4초 뒤 서버가 그 순간의 사실(내려간 속도·바닥에 머문 시간·직전 30분 사건·밤인지·약·위험 요인)을 모은다. 규칙으로 '가능성 있는 원인'을 근거와 함께 뽑고, LLM은 그 후보로만 간호사용 문장을 쓴다. LLM이 실패해도 규칙 요약이 저장된다.")}
{step("7-1", "원인 후보는 규칙으로", table(["사실", "원인 후보"], [
    ["1초 이내 하강·세로 속도 빠름", "미끄러짐·걸림 또는 갑작스러운 힘 빠짐(실신 감별)"], ["천천히 내려가 바닥에 머묾", "다리 힘 빠짐·어지럼"],
    ["낙상 5분 안 침상 이탈", "기립 직후 균형 상실"], ["21~06시", "야간(어두움·졸림·야간뇨)"],
    ["진정·마약성·혈압·어지럼 약", "약물 영향 가능"], ["Morse 보행 약화·자기 한계 잊음", "평소 보행 약화·인지 저하"],
    ["최근 보행 속도 15% 이상 감소", "보행 저하"]]), "", py(FRP, "candidate_causes"))}
{step("7-2", "작성과 저장", "<p>LLM 출력 7항목(요약·양상·추정 원인·직전 상황·지금 확인할 것·재발 방지·데이터 한계)을 저장하고, 낙상 전후 9초 몸 높이 그래프(mmWave/fall_traces)를 붙인다. analysis_type='fall_incident'.</p>", "",
      py(FRP, "collect_facts") + py(FRP, "generate"))}
{step("7-3", "리포트 자동 검토(LLM 없음)", "<p>종합 리포트마다 규칙으로 점검한다: 15개 항목 존재, 작성 당시 입력 보존, 환자 ID 일치, 위험 점수 재계산 일치, 인용 논문 실재, 그리고 과잉 해석 문장(예: '보행 불가능', '낙상이 없었다', 신호 확보 비율을 정확도로 씀, 약 감량·중단 단정)을 찾아 수정 문구를 제안한다. 통과해도 의학적 승인은 아니다.</p>", "",
      py(RR, "build_review"))}
""")


def p_lab(f):
    return part("lab", "8. 실험 코드와 아직 안 쓰는 것", "운영 경로에 연결 안 된 AI — 헷갈리지 않게", f"""
{table(["이름", "무엇", "상태"], [
    [ref(MA), "ADWIN 방식 평균 변화 감시(값의 분포가 바뀌었나)", "테스트용 — 운영에서 import 안 함. 자동 재학습 없음"],
    [ref(AK), "과거 AI 리포트를 벡터화해 비슷한 사례 찾기", "인덱스는 있으나(7조각) 리포트 작성 흐름에 연결 안 됨"],
    [ref("TOF/ml/compare_rf_tabpfn.py"), "자세 분류 Random Forest vs TabPFN 비교", "연구용"],
    [ref("research/alternatives/README.md"), "후보 알고리즘·검색 평가", "연구용, 운영과 분리"],
    ["mmWave/exit_seq/", "침상 이탈 시퀀스 학습 실험(LSTM 등)", "예전 설치 기준 실험"]])}
{warn("실험 코드의 수치를 운영 성능으로 인용하지 않는다. 운영에 넣으려면 실측 시험과 테스트를 먼저 만든다.")}
""")


def p_ops(f):
    return part("ops", "9. 실행·시험·바꿀 때 체크리스트", "신입이 처음 손댈 때 보는 곳", f"""
{step("9-1", "다시 학습·다시 만들기", table(["무엇", "명령"], [
    ["자세 모델 평가만", "cd TOF/ml && ../../.venv/bin/python train_posture_v2.py --group subject --no-save"],
    ["자세 모델 학습·교체", "기존 모델을 TOF/ml/archive/에 복사 → train_posture_v2.py --group subject → tof_server 재시작"],
    ["논문 인덱스 다시 만들기", ".venv/bin/python -c 'import sys;sys.path.insert(0,\"server\");import paper_kb;print(paper_kb.build_index(force=True))'"],
    ["이 가이드 다시 만들기", ".venv/bin/python tools/build_ai_guide.py"]]))}
{step("9-2", "자동 테스트", table(["파일", "무엇을 지키나"], [
    ["server/test_patient_report_context.py", "리포트 입력·형식 검사·환자 분리"], ["server/test_report_review.py", "자동 검토 규칙"],
    ["server/test_fall_report.py", "원인 후보 규칙·LLM 실패 시 대체 저장"], ["server/test_gait_prior.py", "기대 범위·인용 검증"],
    ["server/test_gait_trend.py", "칼만 추세·신호 기준"], ["server/test_emr_kb.py", "환자별 EMR 벡터 분리"],
    ["mmWave/tests/test_fall_detect.py", "낙상 규칙(AI 아님)·실측 재생"]]))}
{step("9-3", "바꿀 때 지킬 것", "<ul><li>LLM에게 숫자를 만들게 하지 않는다 — 새 수치는 서버 함수로 계산해 입력에 넣는다.</li><li>프롬프트를 바꾸면 PROMPT_VERSION을 올린다(저장된 리포트와 구분).</li>"
      "<li>모델을 바꾸면 이전 모델을 archive/에 남기고, 사람 단위(LOSO) 성능을 같이 적는다.</li><li>키·환자 이름을 로그·문서에 남기지 않는다(.env는 저장소 제외).</li><li>시뮬레이션 환자와 실측 환자(bed_01)를 섞지 않는다.</li></ul>")}
""")


PARTS = (p_map, p_posture, p_anomaly, p_prior, p_trend, p_search, p_report, p_fall, p_lab, p_ops)
CARDS = [("map", "0. AI 지도", "학습 모델·통계·LLM·규칙 구분, 제1원칙"),
         ("posture", "1. 자세 분류", "Random Forest · 정직한 정확도(LOSO)"),
         ("anomaly", "2. 움직임 이상 탐지", "LSTM 오토인코더(참고값)"),
         ("prior", "3. 보행 기대 범위", "문헌 + LLM 인용 검증"),
         ("trend", "4. 보행 저하 예측", "칼만 필터 · Phillips 2017 기준"),
         ("search", "5. 근거 찾기", "검색어 생성 · 벡터+BM25+RRF · EMR"),
         ("report", "6. AI 종합 리포트", "서버 계산 · 프롬프트 규칙 · 검증"),
         ("fall", "7. 낙상 사건 리포트·검토", "규칙 원인 후보 · 자동 검토"),
         ("lab", "8. 실험 코드", "아직 운영에 안 쓰는 것"),
         ("ops", "9. 실행·시험", "다시 학습·테스트·체크리스트")]
