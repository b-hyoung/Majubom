"""Export saved reports and their reviews to an offline HTML viewer. No AI calls.

python server/export_report_reviews.py --output docs/report-review/index.html
"""
import argparse
import json
from pathlib import Path

import db
import report_review


def export(output):
    root = Path(__file__).resolve().parent.parent
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM ai_analysis ORDER BY created_at DESC, id DESC')]
    records = []
    for row in rows:
        review = report_review.saved_review(row)
        if not review:
            continue
        records.append({'id': row['id'], 'bed_id': row['patient_id'], 'created_at': row['created_at'],
                        'model': row['model_version'], 'prompt': row['prompt_version'],
                        'report': report_review.decode(row['report_json'], {}),
                        'review': review, 'input_snapshot': report_review.decode(row.get('input_snapshot_json'))})
    data = json.dumps(records, ensure_ascii=False).replace('<', '\\u003c')
    renderer = (root / 'site/dashboard-assets/report-review.js').read_text(encoding='utf-8')
    styles = (root / 'site/dashboard-assets/dashboard.css').read_text(encoding='utf-8')
    styles = styles[styles.index('.report-review {'):styles.index('/* ── Theme switch')]
    html = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>마주봄 AI 리포트 검토 결과</title><style>
:root{--line:#dbe2eb;--r-md:12px;--bg-inset:#f4f7fb;--bg-elev-1:#fff;--fg-muted:#526078;--fg:#18263e;--accent:#235cb8}
*{box-sizing:border-box}body{margin:0;background:#edf2f7;color:var(--fg);font:15px/1.65 "Malgun Gothic",sans-serif}
main{max-width:1040px;margin:auto;padding:32px 18px}h1{font-size:29px;margin:0 0 8px}h2{font-size:19px}
.intro{color:var(--fg-muted);max-width:900px}.card{background:white;border:1px solid var(--line);padding:22px;border-radius:14px;margin:18px 0}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.pill{display:inline-block;font-size:12px;padding:3px 10px;border-radius:30px;background:#e8effc;color:#24477b}
.warning{background:#fff1d9;color:#8a5100}.muted,.faint{color:var(--fg-muted)}.faint{font-size:11px}
select,button{font:inherit;padding:9px 12px;border:1px solid #bfcbdc;border-radius:8px;background:white;max-width:100%}
label{display:grid;gap:5px;margin:8px 0}#choose{width:100%}.report-field{border-top:1px solid var(--line);padding:12px 0;white-space:pre-wrap;overflow-wrap:anywhere}
.report-field strong{display:block;color:#34506f}a{color:var(--accent)}@media(max-width:600px){main{padding:18px 10px}.card{padding:15px}h1{font-size:24px}}
''' + styles + '''</style><main>
<h1>마주봄 AI 리포트 검토 결과</h1>
<p class="intro">실제로 생성된 답변을 작성 입력·계산 결과·연결 문헌과 대조했습니다. 원문과 수정 의견을 함께 볼 수 있습니다. 자동 점검 통과는 모든 문장이 맞다는 뜻이 아니며, 직접 검토도 의료진의 임상 승인을 뜻하지 않습니다.</p>
<div class="row" id="stats"></div>
<div class="card"><div class="row"><label>환자<select id="patient"><option value="">전체 환자</option></select></label><label>보기<select id="filter"><option value="direct">직접 대조한 리포트</option><option value="all">전체 리포트</option><option value="latest">최종 검증 예시 (v6)</option><option value="issues">수정·확인 필요</option></select></label></div>
<label>리포트 선택<select id="choose"></select></label><div id="meta" class="muted"></div></div>
<div id="review"></div><section class="card"><h2>AI가 작성한 원문</h2><p class="muted">검토 의견과 구분하기 위해 생성된 원문을 그대로 보존했습니다.</p><div id="report"></div></section>
<p class="muted">이 파일은 내보낸 시점의 로컬 사본입니다. 이후 생성된 리포트와 검토는 대시보드에서 확인하세요.</p></main>
<script>''' + renderer + '''</script><script>
const records = ''' + data + ''';
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names={bed_01:'센서 연결 대상',bed_sim_calm:'안정 시나리오',bed_sim_bedexit:'침상 이탈',bed_sim_gaitcaution:'뒤척임·보행 주의',bed_sim_risk:'불안정 보행',bed_sim_fall:'낙상 의심'};
$('#stats').innerHTML=[`리포트 ${records.length}건`,`직접 대조 ${records.filter(r=>r.review.direct_review).length}건`,`작성 입력 보존 ${records.filter(r=>r.input_snapshot).length}건`].map(t=>'<span class="pill">'+esc(t)+'</span>').join('');
for(const id of [...new Set(records.map(r=>r.bed_id))]){$('#patient').add(new Option((names[id]||id)+' · '+id,id));}
function list(){const filter=$('#filter').value,patient=$('#patient').value;const rows=records.filter(r=>(!patient||r.bed_id===patient)&&(filter==='all'||filter==='direct'&&r.review.direct_review||filter==='latest'&&r.prompt==='integrated_v6'||filter==='issues'&&r.review.status==='needs_review'));
$('#choose').replaceChildren(...rows.map(r=>new Option('#'+r.id+' · '+(names[r.bed_id]||r.bed_id)+' · '+r.created_at+' · '+r.review.label,r.id)));show();}
function show(){const r=records.find(r=>r.id===Number($('#choose').value));if(!r){$('#review').textContent='해당 조건의 리포트가 없습니다.';$('#report').textContent='';$('#meta').textContent='';return;}
$('#meta').textContent='리포트 #'+r.id+' · '+r.model+' · '+r.prompt;$('#review').innerHTML=MajubomReportReview.render(r);
$('#report').innerHTML=Object.entries(r.report).map(([k,v])=>'<div class="report-field"><strong>'+esc(k.replaceAll('_',' '))+'</strong>'+esc(v)+'</div>').join('');}
$('#patient').onchange=list;$('#filter').onchange=list;$('#choose').onchange=show;list();
</script></html>'''
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding='utf-8')
    return {'reports': len(records), 'direct_reviews': sum(bool(r['review']['direct_review']) for r in records), 'path': str(output.resolve())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='docs/report-review/index.html')
    print(json.dumps(export(parser.parse_args().output), ensure_ascii=False))
