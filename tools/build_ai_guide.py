"""AI 가이드(docs/AI_GUIDE.html) 생성기 — 2026-10-21.

마주봄에서 'AI'라고 부르는 것 전부(학습 모델·통계 예측·LLM·검색)를 신입 개발자가 코드를 따라 읽듯 정리한다.
화면 조각(카드·단계·코드 상자)과 CSS는 build_code_guide.py와 같고, 코드 조각은 생성 시점 소스에서 줄 번호째 잘라 온다.
본문은 tools/ai_guide_parts.py에 파트별로 나눠 둔다(파일 하나가 너무 길어지지 않게).

실행:  .venv/bin/python tools/build_ai_guide.py
공개:  CF_TOKEN=<토큰> bash deploy/cloudflare/publish_code_guide.sh docs/AI_GUIDE.html majubom-ai-guide
"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_lib import ROOT, read  # noqa: E402
import build_code_guide as B  # noqa: E402  (CSS·JS·화면 조각 재사용)
import ai_guide_parts as P  # noqa: E402

OUT = os.path.join(ROOT, "docs", "AI_GUIDE.html")


def facts():
    f = {"posture": json.loads(read("TOF/ml/posture_model_v2_report.json")),
         "lstm": json.loads(read("TOF/ml/lstm_ae_v2_report.json"))}
    try:
        import lancedb
        db = lancedb.connect(os.path.join(ROOT, "server", "lancedb_store"))
        t = db.open_table("paper_chunks").to_arrow()
        f["papers"] = {"papers": len(set(t.column("paper_id").to_pylist())), "chunks": t.num_rows}
    except Exception as e:
        f["papers"] = {"error": f"{type(e).__name__}"}
    try:
        import joblib
        m = joblib.load(os.path.join(ROOT, "TOF/ml/posture_model.joblib"))
        f["rf_meta"] = {k: (v if isinstance(v, (str, int, float, list)) else str(v)) for k, v in m.items() if k != "model"}
        est = m["model"]
        f["rf_meta"]["n_trees"] = len(est.estimators_)
    except Exception as e:
        f["rf_meta"] = {"error": f"{type(e).__name__}: {e}"}
    return f


def main():
    f = facts()
    cards = "".join(f'<a class="card" href="#{i}"><b>{t}</b><span>{s}</span></a>' for i, t, s in P.CARDS)
    body = "".join(fn(f) for fn in P.PARTS)
    html_doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>마주봄 AI 가이드</title><style>{B.CSS}</style></head><body><div class="wrap">
<div class="top"><div><h1>마주봄 AI 가이드 — 학습 모델·예측·LLM 리포트</h1><div class="muted">신입 개발자용 코드 읽기 안내 · 생성 {date.today().isoformat()} · tools/build_ai_guide.py · 코드 조각은 생성 시점 소스에서 직접 잘라 옴</div></div>
<div><button id="openall" type="button">이 파트 전부 펼치기</button> <button id="theme" type="button">밝게/어둡게</button></div></div>
<div id="home"><p>마주봄에서 'AI'라고 부르는 부분을 <b>무엇을 배우나(학습 모델) · 무엇을 예측하나(통계) · 무엇을 쓰나(LLM)</b>로 나눠 정리했다.
파트를 누르면 그 파트만 펼쳐지고, 단계마다 <b>쉬운 설명 → 엔지니어 메모·수식 → 실제 코드(줄 번호)</b> 순서다. 처음이면 <b>0. AI 지도</b>부터 읽는다.</p>
<div class="cards">{cards}</div></div>
{body}
</div><script>{B.JS}</script></body></html>"""
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    print(f"생성: {OUT} ({len(html_doc) // 1024} KB)")


if __name__ == "__main__":
    main()
