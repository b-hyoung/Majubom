"""Self-contained screen target, also downloadable for an offline phone/laptop."""
import base64
from .calibration_methods import pattern_config, pattern_png


def page(query):
    c=pattern_config(dict(kind=query.get('kind',['charuco'])[0],columns=int(query.get('columns',['9'])[0]),
                         rows=int(query.get('rows',['6'])[0]),width_m=None))
    png=base64.b64encode(pattern_png(c)).decode('ascii')
    html='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>화면 보정 패턴 · SCREEN TARGET</title>
<style>*{box-sizing:border-box}body{margin:0;background:#fff;color:#111;font-family:system-ui,sans-serif}header{background:#142336;color:#e0e8f2;padding:15px 22px;font-size:13px}h1{font-size:20px;margin:0 0 8px}button,input,select{padding:8px;margin:4px;border-radius:4px}label{display:inline-block}.target{padding:30px;display:flex;justify-content:center;align-items:center;min-height:60vh}img{display:block;width:auto;height:auto;max-width:calc(100vw - 60px);max-height:78vh;image-rendering:pixelated}p{margin:6px 0}#measurement{color:#ffd587}body:fullscreen header{position:fixed;bottom:0;left:0;right:0;font-size:11px;opacity:.95}body:fullscreen h1,body:fullscreen .help{display:none}body:fullscreen .target{height:100vh}</style>
<header><h1>화면 보정 패턴 · __KIND__</h1><p class="help">인쇄가 필요 없습니다. 모니터·노트북·휴대폰에 표시하고 양쪽 카메라에 같은 면을 보여 주세요. 밝기·반사·모아레·카메라 초점을 확인하고 다양한 거리/각도로 10쌍 이상 촬영합니다.</p><p class="help">측정 범위는 바깥 흰 여백을 제외한 <b>격자 이미지 전체 가로</b>입니다. 아래 mm는 직접 자로 재서 입력하세요. 브라우저 배율·전체 화면·기기 회전 후에는 다시 측정해야 합니다.</p>
<label>전체 격자 가로 실측 <input id="mm" type="number" min="10" step="0.1" placeholder="실제 mm"> mm</label><button id="full">전체 화면</button><button id="download">휴대폰용 HTML 저장</button><p id="measurement">실측값 미입력 · 이 상태로 실제 거리 보정 불가</p><p>설정: __COLUMNS__ × __ROWS__ · ArUco 사전 DICT_4X4_1000 · __DESCRIPTION__</p></header>
<div class="target"><img id="pattern" alt="__KIND__ calibration target" src="data:image/png;base64,__PNG__"></div>
<script>const mm=document.getElementById('mm'),measurement=document.getElementById('measurement');mm.oninput=()=>{const width=Number(mm.value);measurement.textContent=width>0?'전체 가로 '+width+' mm / 보정 UI 실제 패턴 가로에 '+width+' 입력 · 격자 변경 없이 촬영하세요':'실측값 미입력';};document.getElementById('full').onclick=()=>document.fullscreenElement?document.exitFullscreen():document.body.requestFullscreen();document.getElementById('download').onclick=()=>{const html='<!doctype html>'+document.documentElement.outerHTML;const url=URL.createObjectURL(new Blob([html],{type:'text/html'}));const a=document.createElement('a');a.href=url;a.download='screen-calibration-__KIND__.html';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);};window.addEventListener('resize',()=>{mm.value='';measurement.textContent='표시 크기가 바뀌었습니다. 격자 전체 가로를 다시 측정하세요.';});</script></html>'''
    values=dict(KIND=c['kind'],COLUMNS=str(c['columns']),ROWS=str(c['rows']),PNG=png,
        DESCRIPTION='체커/ChArUco는 내부 코너 수, ArUco는 마커 열·행 수')
    for key,value in values.items():html=html.replace('__'+key+'__',value)
    return html
