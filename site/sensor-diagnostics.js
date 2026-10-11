'use strict';
const $ = id => document.getElementById(id);
const host = location.hostname || '192.168.0.4';
const initialContext=new URLSearchParams(location.search);
for(const [key,id] of [['target','target-id']]){if(initialContext.get(key))$(id).value=initialContext.get(key);}
$('nurse-link').href=`http://${host}:5001/dashboard`;
$('architecture-link').href=`http://${host}:5001/system-architecture`;
const flips={tof1:{x:false,y:false},tof2:{x:false,y:false}};
function grid(id, values, sensor, statuses){
  const body=$(id);body.replaceChildren();
  if(!values){body.textContent='수신 대기';return;}
  const side=Math.sqrt(values.length);body.style.gridTemplateColumns=`repeat(${side},1fr)`;
  for(let r=0;r<side;r++)for(let c=0;c<side;c++){
    const sourceRow=flips[sensor].y?side-1-r:r,sourceCol=flips[sensor].x?side-1-c:c,z=sourceRow*side+sourceCol;
    const v=values[z],cell=document.createElement('div');cell.className='tof-cell';cell.textContent=v>0?v:'—';
    cell.style.background=v>0?`hsl(${Math.max(0,Math.min(220,v/4000*220))} 48% 25%)`:'#202938';
    cell.title=`원본 zone ${z} · ${v}mm · status ${statuses?statuses[z]:'없음'}`;body.append(cell);
  }
}
function renderTof(data){for(const sid of ['tof1','tof2']){
  const f=data.tof&&data.tof[sid];if(!f){grid(sid+'-raw',null,sid);grid(sid+'-clean',null,sid);continue;}
  const age=f.age_s;
  $(sid+'-meta').textContent=`${f.resolution} · 마지막 수신 ${age.toFixed(1)}초 전 · ${age>2?'수신 멈춤':'수신 중'} · ${f.raw_distances_mm?'원시값 포함':'유효 거리만 수신(새 펌웨어 필요)'}`;
  grid(sid+'-raw',f.raw_distances_mm,sid,f.target_status);grid(sid+'-clean',f.distances_mm,sid,f.target_status);
  $(sid+'-status').textContent=JSON.stringify({target_status:f.target_status,targets:f.targets,mount_height_m:f.mount_height_m,mount_down_tilt_deg:f.mount_down_tilt_deg},null,2);
}}
for(const button of document.querySelectorAll('[data-flip]'))button.onclick=()=>{const [sid,axis]=button.dataset.flip.split('-');flips[sid][axis]=!flips[sid][axis];button.setAttribute('aria-pressed',flips[sid][axis]);if(latest)renderTof(latest);};
const canvas = $('cloud'), ctx = canvas.getContext('2d');
let latest = null, paused = false, yaw = -.55, pitch = .8, scale = 60, drag = null, previous = null;
const fmt = v => typeof v === 'number' ? v.toFixed(3) : '—';
function table(id, rows){
  const body = $(id); body.replaceChildren();
  for(const row of rows){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td');td.textContent=String(value);tr.append(td);}body.append(tr);}
}
function render(data){
  renderTof(data);
  const f = data.frame;
  $('bytes').textContent=data.bytes.toLocaleString();
  const now=performance.now(); const hz=previous ? Math.max(0,(data.frames-previous.frames)*1000/(now-previous.time)) : 0;
  previous={frames:data.frames,time:now};
  $('frames').textContent=`${data.frames} · ${hz.toFixed(1)} Hz`;
  $('points').textContent=f ? `${f.raw_points.length} / ${f.points.length}` : '—';
  $('tracks').textContent=f ? f.tracks.length : '—';
  $('age').textContent=data.age_s===null ? '수신 전' : `${data.age_s.toFixed(1)}초`;
  $('notice').textContent=data.status==='disconnected' ? 'mmWave 수집기 연결이 끊겼습니다. 마지막 프레임은 이전 데이터입니다.' : data.error ? `센서 오류: ${data.error}` : data.age_s!==null && data.age_s>2 ? '프레임 수신이 멈췄습니다. 마지막 화면은 이전 데이터입니다.' : !f ? `수신 대기 (${data.status})` : f.malformed ? '프레임 구조가 예상 포맷과 다릅니다. 파서/펌웨어를 확인하세요.' : f.tracks.length ? '프레임 수신 중 · 사람 트랙 검출됨' : '프레임 수신 정상 · 사람 트랙 없음 (사람 부재 또는 감지 영역 확인)';
  $('coords').options[0].textContent=`바닥 기준 · 높이 ${data.mount.height_m}m / 하향 ${data.mount.down_tilt_deg}°`;
  if(!f)return;
  $('header').textContent=JSON.stringify({header:f.header,tlvs:f.tlvs,units:f.units,unit_order:['elevation(rad)','azimuth(rad)','doppler(m/s)','range(m)','snr']},null,2);
  $('quality').textContent=JSON.stringify({frame:f.header.frameNum,rejected_points:f.rejected_points,malformed:f.malformed,display_limit:f.display_limit,point_source:'TLV 1020 · 트랙 중심으로 대체하지 않음'},null,2);
  table('raw',f.raw_points.slice(0,100).map((p,i)=>[i,p.elevation,p.azimuth,p.doppler,p.range,p.snr]));
  table('clean',f.points.slice(0,100).map((p,i)=>[i,...p.world.map(fmt),fmt(p.doppler),fmt(p.snr)]));
  $('raw-tracks').textContent=JSON.stringify(f.raw_tracks,null,2);
  $('clean-tracks').textContent=JSON.stringify(f.tracks,null,2);
}
function draw(){
  const box=canvas.getBoundingClientRect(),dpr=devicePixelRatio||1;
  if(canvas.width!==Math.round(box.width*dpr)||canvas.height!==Math.round(box.height*dpr)){canvas.width=Math.round(box.width*dpr);canvas.height=Math.round(box.height*dpr);}
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,box.width,box.height);
  const sensorMode=$('coords').value==='sensor';
  function project(v){const [x,y,z]=v,py=y-2,pz=z-(sensorMode?0:1);const rx=x*Math.cos(yaw)-py*Math.sin(yaw),ry=x*Math.sin(yaw)+py*Math.cos(yaw);return [box.width/2+rx*scale,box.height/2+(ry*Math.cos(pitch)-pz*Math.sin(pitch))*scale];}
  function line(a,b,color){const p=project(a),q=project(b);ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(...q);ctx.stroke();}
  const floor=sensorMode?-2.5:0;
  for(let i=-4;i<=6;i++){line([i,0,floor],[i,6,floor],'#21304a');if(i>=0)line([-4,i,floor],[4,i,floor],'#21304a');}
  const origin=[0,0,0];for(const [end,label,color] of [[[1,0,0],'X','#fb8796'],[[0,1,0],'Y','#75e8b0'],[[0,0,1],'Z','#7aafff']]){line(origin,end,color);ctx.fillStyle=color;ctx.fillText(label,...project(end));}
  const f=latest&&latest.frame;
  if(f){for(const p of f.points){const pos=project(p[sensorMode?'sensor':'world']);ctx.fillStyle=p.doppler>.05?'#fb8796':p.doppler<-.05?'#74b5ff':'#79e5cf';ctx.beginPath();ctx.arc(...pos,3,0,Math.PI*2);ctx.fill();}
    if(!sensorMode)for(const t of f.tracks){const pos=project(t.world);ctx.strokeStyle='#ffc877';ctx.beginPath();ctx.arc(...pos,9,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#ffc877';ctx.fillText(`ID ${t.tid}`,pos[0]+12,pos[1]);}
    if(!f.points.length){ctx.fillStyle='#91a7c5';ctx.fillText('현재 프레임의 거리 점군 없음',20,28);}
  }requestAnimationFrame(draw);
}
canvas.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointermove',e=>{if(drag){yaw+=(e.clientX-drag[0])*.008;pitch=Math.max(.1,Math.min(1.55,pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY];}});
canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('pointercancel',()=>drag=null);
canvas.addEventListener('wheel',e=>{e.preventDefault();scale=Math.max(15,Math.min(180,scale*Math.exp(-e.deltaY*.001)));},{passive:false});
$('reset').onclick=()=>{yaw=-.55;pitch=.8;scale=60;};
$('pause').onclick=()=>{
  paused=!paused;$('pause').textContent=paused?'화면 재개':'화면 일시정지';
  $('pause').setAttribute('aria-pressed',String(paused));
  $('live-state').textContent=paused?'일시정지 · 모든 표시값 고정':'실시간 표시';previous=null;
};
async function poll(){try{if(!paused){const response=await fetch('/api/snapshot',{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);const data=await response.json();if(!paused){latest=data;render(latest);}}}catch(e){$('notice').textContent='진단 서버 연결 끊김: '+e.message;}finally{setTimeout(poll,200);}}


// Read-only operational observability. Never persist to browser storage or send data mutations.
const labels={algorithms:'실행 알고리즘 · 수식',latest:'최신 계산 · 입력과 결과',baseline:'개인 기준 · 평균과 표준편차',combined:'센서 통합 판정',tof_combined:'ToF 기준 · 통합 판정',mmwave_combined:'mmWave 기준 · 통합 판정',tof_events:'ToF 실제 이벤트',mmwave_events:'mmWave 실제 이벤트',storage:'운영 DB · 저장 행과 라벨',events:'실제 이벤트 · 낙상과 이상징후',fusion:'통합 센서 판정',before:'계산 전',after:'계산 후',inputs:'실제 입력',outputs:'실제 결과',gates:'보류 · 유효성 조건',n:'표본 수 n',sum:'누적합 Σx',sumsq:'제곱합 Σx²',mu:'평균 μ',sigma:'표준편차 σ',z:'Z-score',target:'대상 ID',bed_id:'침대 ID',table:'DB 테이블',rows:'조회된 저장 행',label:'저장 라벨',source:'구현 파일 · 출처',formula:'계산식',active:'현재 활성',note:'동작 설명',status:'실행 상태',details:'실행 세부 내역',query:'실제 검색어',queries:'실제 검색어 목록',evidence:'검색된 논문 근거',score:'검색 점수',errors:'연결 · 처리 오류',retrieval:'벡터 · 키워드 검색',prompt:'실제 전달한 질문 · 프롬프트',run_id:'실행 ID',db_path:'DB 경로',updated_at:'갱신 시각',generated_at:'조회 시각',timestamp:'기록 시각'};
const scalar=v=>v===null?'없음':v===undefined?'미제공':typeof v==='boolean'?(v?'예':'아니오'):String(v);
function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function jsonDetails(value,title='전체 필드 · 원본 JSON'){
  const details=el('details',undefined,'json-detail');details.append(el('summary',title),el('pre',JSON.stringify(value,null,2)));return details;
}
function empty(target,text){target.append(el('p',text,'empty'));}
function structured(value){
  const wrap=el('div');
  if(value===null||value===undefined){empty(wrap,'아직 제공된 실제 값이 없습니다.');return wrap;}
  if(typeof value!=='object'){wrap.append(el('p',scalar(value)));return wrap;}
  const facts=el('table',undefined,'facts'),body=el('tbody');facts.append(body);
  let count=0;
  function add(key,v){const tr=el('tr');tr.append(el('th',key),el('td',scalar(v)));body.append(tr);count++;}
  function visit(v,path,depth){
    if(v===null||typeof v!=='object'){add(path,v);return;}
    const entries=Array.isArray(v)?v.map((item,index)=>[String(index),item]):Object.entries(v);
    if(!entries.length){add(path,Array.isArray(v)?'[] · 기록 없음':'{} · 제공 필드 없음');return;}
    if(depth>5||count>200){add(path,`${Array.isArray(v)?'목록':'객체'} · 전체 필드는 아래 JSON에서 확인`);return;}
    if(Array.isArray(v)&&v.every(item=>item===null||typeof item!=='object')){add(path,v.map(scalar).join(', '));return;}
    for(const [key,child] of entries){const name=labels[key]?`${labels[key]} (${key})`:key;visit(child,path?`${path} › ${name}`:name,depth+1);}
  }
  visit(value,'',0);wrap.append(facts,jsonDetails(value));return wrap;
}
function algorithmCards(value){
  const wrap=el('div');if(!Array.isArray(value)){wrap.append(structured(value));return wrap;}
  if(!value.length)empty(wrap,'실행 알고리즘 목록이 제공되지 않았습니다.');
  for(const a of value){if(!a||typeof a!=='object'){wrap.append(structured(a));continue;}
    const card=el('article',undefined,'algorithm'),heading=el('h3',a.name||a.id||'계산 단계');
    if(a.active!==undefined)heading.append(el('span',a.active?'활성':'비활성','pill'));card.append(heading);
    if(a.formula)card.append(el('div',a.formula,'formula'));
    if(a.note)card.append(el('p',a.note,'muted'));
    card.append(structured(Object.fromEntries(Object.entries(a).filter(([k])=>!['name','formula','note','active'].includes(k)))));wrap.append(card);
  }return wrap;
}
function baselineView(value){
  const wrap=el('div');
  if(value&&value.metrics&&typeof value.metrics==='object'){
    wrap.append(el('p','평균은 해당 집계의 누적합 ÷ 표본 수입니다. 기존 n과 유효값 전용 v_n은 구분해서 확인하세요. 아래 값은 서버가 실제 반환한 지표별 통계이며, 기준 갱신 여부와 비교에 사용한 평균·표준편차는 계산 전후 내역에서 확인할 수 있습니다.','muted'));
    const scroll=el('div',undefined,'scroll'),tableNode=el('table');
    const head=el('thead'),row=el('tr');
    for(const title of ['지표','현재 입력 x','표본 n','Σx','Σx²','평균 μ','σ 원본','σ 사용','σ 하한','저장된 z','기준 반영'])row.append(el('th',title));head.append(row);tableNode.append(head);
    const body=el('tbody');
    for(const [name,metric]of Object.entries(value.metrics)){
      if(!metric||typeof metric!=='object')continue;const compare=value.comparison?.[name]||{};const tr=el('tr');
      for(const item of [name,compare.input,metric.n,metric.sum,metric.sumsq,metric.mean,metric.sigma_raw,metric.sigma_used,metric.sigma_floor,compare.z_stored_in_result,compare.included_in_baseline])tr.append(el('td',scalar(item)));body.append(tr);
    }tableNode.append(body);scroll.append(tableNode);wrap.append(scroll);
  }
  wrap.append(structured(value));return wrap;
}
function operationSections(id,value){
  const root=$(id);root.replaceChildren();
  if(value===undefined||value===null){const panel=el('section',undefined,'panel wide');empty(panel,'운영 서버에 연결되지 않았거나 이 대상의 계산 결과가 아직 없습니다.');root.append(panel);return;}
  if(typeof value!=='object'||Array.isArray(value)){const panel=el('section',undefined,'panel wide');panel.append(structured(value));root.append(panel);return;}
  for(const [key,field] of Object.entries(value)){
    const panel=el('section',undefined,`panel ${['algorithms','latest','storage','events','fusion'].includes(key)?'wide':''}`);
    panel.append(el('h2',labels[key]||key),key==='algorithms'?algorithmCards(field):key==='baseline'?baselineView(field):structured(field));root.append(panel);
  }
  if(!Object.keys(value).length)empty(root,'이 대상의 운영 기록이 아직 없습니다.');
}
function reportRuns(ai){
  const root=$('ai-operations');root.replaceChildren();
  const runs=ai&&ai.runs;
  if(!Array.isArray(runs)){root.append(structured(ai));return;}
  const other=Object.fromEntries(Object.entries(ai).filter(([key])=>key!=='runs'));
  if(Object.keys(other).length){const panel=el('section',undefined,'panel');panel.append(structured(other));root.append(panel);}
  if(!runs.length){const panel=el('section',undefined,'panel');empty(panel,'이 침대에서 관찰된 리포트 실행이 없습니다. 리포트 생성이 실행되면 실제 질문·검색·작성 단계가 나타납니다. 과거에 관찰하지 못한 실행을 재현하지 않습니다.');root.append(panel);}
  for(const run of runs){const card=el('section',undefined,'panel run-card');
    const heading=el('h2',`리포트 실행 ${run.id||run.run_id||'ID 미제공'}`);heading.append(el('span',scalar(run.status),'pill'));card.append(heading);
    card.append(el('p',`침대 ${scalar(run.bed_id)} · 시작 ${scalar(run.started_at)} · 종료 ${scalar(run.ended_at)}`,'subtitle'));
    const timeline=el('div',undefined,'timeline');
    for(const event of run.events||[]){const item=el('article',undefined,'trace-event');
      item.append(el('p',`#${scalar(event.seq)} · ${scalar(event.at)} · ${scalar(event.stage)} · ${scalar(event.status)}`,'subtitle'),el('h3',event.title||event.stage||'실행 단계'),structured(event.details));timeline.append(item);
    }if(!(run.events||[]).length)empty(timeline,'아직 실행 단계가 기록되지 않았습니다.');
    card.append(timeline,jsonDetails(run,'이 실행의 전체 기록'));root.append(card);
  }
}
let operationsLatest=null,contextVersion=0;
let operationalContext={bed_id:'bed_01',target:$('target-id').value.trim()};
function renderOperations(data){
  operationSections('tof-operations',data.tof);operationSections('mmwave-operations',data.mmwave);
  const shared={};for(const key of ['fusion','events','integrated','patient','context','errors'])if(data[key]!==undefined)shared[key]=data[key];
  shared.tof_events=data.tof?.events??[];shared.mmwave_events=data.mmwave?.events??[];
  shared.tof_combined=data.tof?.combined??null;shared.mmwave_combined=data.mmwave?.combined??null;
  operationSections('fusion-operations',shared);reportRuns(data.ai);
  $('operations-time').textContent=`운영 조회 ${new Date().toLocaleTimeString('ko-KR')} · 1초 간격`;
  if(data.errors&&Object.values(data.errors).some(Boolean))$('operations-time').textContent+=' · 일부 경로 응답 실패 (융합·이벤트에서 확인)';
}
async function pollOperations(){
  const version=contextVersion;
  try{if(!paused){const query=new URLSearchParams(operationalContext);
    const response=await fetch('/api/operations?'+query,{cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);
    const data=await response.json();if(!paused&&version===contextVersion){operationsLatest=data;renderOperations(data);}
  }}catch(error){if(!paused)$('operations-time').textContent='운영 조회 실패 · 마지막 값은 이전 조회입니다: '+error.message;}
  finally{setTimeout(pollOperations,1000);}
}
const tabs=Array.from(document.querySelectorAll('[data-tab]'));
function selectTab(tab){for(const item of tabs){const selected=item===tab;item.setAttribute('aria-selected',String(selected));item.tabIndex=selected?0:-1;$('view-'+item.dataset.tab).hidden=!selected;}}
for(const [index,tab] of tabs.entries()){
  tab.addEventListener('click',()=>selectTab(tab));
  tab.addEventListener('keydown',event=>{let next;
    if(event.key==='ArrowRight')next=(index+1)%tabs.length;
    if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;
    if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;
    if(next!==undefined){event.preventDefault();selectTab(tabs[next]);tabs[next].focus();}
  });
}
$('apply-context').onclick=()=>{
  if(!$('bed-id').value.trim()||!$('target-id').value.trim()){$('operations-time').textContent='실제 침대 ID와 mmWave 대상 ID를 입력하세요.';return;}
  operationalContext={bed_id:'bed_01',target:$('target-id').value.trim()};
  contextVersion++;operationsLatest=null;
  for(const id of ['tof-operations','mmwave-operations','fusion-operations'])operationSections(id,null);
  reportRuns({runs:[]});$('operations-time').textContent=paused?'대상 변경 · 화면 재개 후 조회':'대상 변경 · 다음 조회 대기';
};
poll();pollOperations();draw();

document.getElementById('emr-link').href='http://'+location.hostname+':5001/emr';
