import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {RoomScene} from './scene';
import {EDGES, InputSession, RoomConfig, RoomStatus, Vec3, View} from './types';
import './monitor.css';
import {CalibrationPanel} from './CalibrationPanel';
import {Posture3D} from './Posture3D';

const token = location.hash.slice(1);
async function api(path: string, body?: unknown) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: {'X-BedPose-Token': token, 'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(5000)});
  const data = await response.json(); if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`); return data;
}
const number = (v: number | null | undefined, digits=1) => v == null ? '—' : v.toFixed(digits);
const reasons: Record<string,string> = {
  inputs_unavailable:'카메라 입력 두 대를 선택하세요', waiting_for_pair:'시간이 맞는 프레임 쌍 대기',
  uncalibrated:'미보정 · 보정 파일을 불러오세요', confirm_camera_installation:'카메라 A/B 배치 확인 필요',
  confirm_same_person:'양쪽 영상의 동일 대상 확인 필요', resolution_changed_recalibrate:'해상도가 보정값과 다름 · 재보정 필요',
  stale_pair:'입력이 오래되어 3D 표시 중단', timestamp_skew_exceeded:'프레임 시간 오차 초과',
  calibration_error_exceeded:'보정 재투영 오차 한계 초과 · 촬영 및 보정값 확인 필요',
  no_reconstructable_joints:'양쪽에서 복원 가능한 관절 없음', geometry_error:'3D 기하 계산 오류',
  live_camera_inputs_required:'실시간 복원에는 실제 카메라 입력이 필요합니다'
};

function CameraPanel({session, view, index}: {session?: InputSession; view: View | null; index: number}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const c = canvas.current; if (!c) return;
    const context = c.getContext('2d')!;
    if (!view?.image) {context.clearRect(0,0,c.width,c.height);return;}
    let cancelled = false;
    const image = new Image(); image.onload = () => {
      if (cancelled) return;
      c.width = view.width; c.height = view.height; context.drawImage(image,0,0,c.width,c.height);
      context.strokeStyle = '#7df4b9'; context.fillStyle = '#7df4b9'; context.lineWidth = Math.max(1,c.width/500);
      for (const p of view.people) {
        const usable = (i: number) => {const j=p.joints[i]; return j && j.x_px !== null && j.y_px !== null && Number.isFinite(j.x_px) && Number.isFinite(j.y_px) && (j.visibility == null || j.visibility >= .5);};
        for (const [a,b] of p.edges ?? EDGES) if (usable(a) && usable(b)) {context.beginPath(); context.moveTo(p.joints[a].x_px!,p.joints[a].y_px!); context.lineTo(p.joints[b].x_px!,p.joints[b].y_px!); context.stroke();}
        for (let i=0;i<p.joints.length;i++) if (usable(i)) {context.beginPath(); context.arc(p.joints[i].x_px!,p.joints[i].y_px!,2.5,0,Math.PI*2); context.fill();}
      }
    }; image.src = view.image; return () => {cancelled=true;};
  }, [view]);
  const alive = !!session && !session.closed && !['error','closing','ended'].includes(session.phase);
  return <section className="panel camera" ref={panel}>
    <div className="panel-heading"><span><i className={'dot '+(alive?'green':'')}/> CAMERA {index===0?'A':'B'}</span><button onClick={() => panel.current?.requestFullscreen().catch(()=>{})}>전체 화면</button></div>
    <div className="video">{!view?.image && <div className="empty">{alive?'영상 대기 중':'카메라 미연결'}</div>}<canvas ref={canvas}/></div>
    <div className="camera-meta"><span>{session?.title ?? '입력 미지정'}</span><span>{number(session?.processing_fps)} FPS</span></div>
    <div className="muted">프레임 {view?.frame_id ?? '—'} · 수신 후 {number(view?.age_ms,0)} ms · 검출 {view?.people.length ?? '—'}명</div>
    {alive && session?.pose_recovery?.visible_joints != null && <div className="muted">관절 관측 {session.pose_recovery.visible_joints}/17 · {session.pose_recovery.visible_joints===0?(session.pose_recovery.person_evidence?.confirmed===false?'사람 검증 보류 · 대상 크기/검출 불일치':'사람 미확인 · 자동 재탐색'):session.pose_recovery.healthy?'관측 양호':'부분 관측 · 자동 재탐색'}{session.pose_recovery.crop_bounds?' · 영역 확대':''}{session.pose_recovery.rotation_degrees?` · 방향 보완 ${session.pose_recovery.rotation_degrees}°`:''}{session.pose_recovery.transport_errors?' · 추론 연결 복구 이력 있음':''}</div>}
    {alive && session?.processing_ms && <div className="muted">입력 대기 {number(session.processing_ms.read)} ms · 관절 추론 {number(session.processing_ms.pose)} ms{(session.processing_ms.read??0)>=50 && (session.processing_ms.read??0)>(session.processing_ms.pose??0)?' · 입력 지연: 조명·노출·USB 상태 확인':''}</div>}
    {alive && session?.capture_diagnostics?.low_light && <div className="muted">영상이 어두움 · 조명·렌즈 가림 확인 · 밝기 진단은 미검출 원인을 확정하지 않습니다</div>}
    {alive && session?.capture_diagnostics?.highlight_clipping && <div className="muted">밝은 영역이 많음 · 조명·노출·반사 확인</div>}
    {session?.error && <div className="muted">입력 오류: {session.error}</div>}
  </section>;
}

function App() {
  const host = useRef<HTMLDivElement>(null), scene = useRef<RoomScene | null>(null);
  const [data,setData] = useState<RoomStatus | null>(null), [sessions,setSessions] = useState<InputSession[]>([]);
  const [draft,setDraft] = useState<RoomConfig | null>(null), [views,setViews] = useState<(View|null)[]>([null,null]);
  const [connection,setConnection] = useState(false), [notice,setNotice] = useState(''), [logs,setLogs] = useState<string[]>([]);
  const [ceiling,setCeiling] = useState(false), [axes,setAxes] = useState(true), [webglError,setWebglError] = useState('');
  const [busy,setBusy] = useState(false);
  const [demo,setDemo]=useState(false),[renderStats,setRenderStats]=useState({fps:0,renderer:''});
  const [displayFps,setDisplayFps]=useState([0,0]);
  const log = (message: string) => {setNotice(message); setLogs(old=>[new Date().toLocaleTimeString()+' · '+message,...old].slice(0,20));};
  useEffect(() => {try {scene.current = new RoomScene(host.current!, (fps,renderer)=>setRenderStats({fps,renderer}));} catch {setWebglError('WebGL을 사용할 수 없습니다. 브라우저의 하드웨어 가속을 확인하세요.');} return ()=>scene.current?.dispose();},[]);
  useEffect(()=>{if(scene.current){scene.current.demo=demo;scene.current.poseSource='';if(!demo)scene.current.setPose(data?.experimental_preview?.enabled?data.experimental_preview.skeleton:data?.skeleton??null);}},[demo]);
  useEffect(() => {
    let stopped=false, timer: ReturnType<typeof setTimeout>;
    let bindingKey='',frameIds=[-1,-1],count=[0,0],sampled=performance.now();
    async function tick() {
      try {
        const room=await api('/api/room/live?0='+frameIds[0]+'&1='+frameIds[1]) as RoomStatus & {sessions:InputSession[];views:((View & {unchanged?:boolean})|null)[]};
        if (stopped) return;
        setData(room); setSessions(room.sessions); setDraft(old=>old ?? structuredClone(room.config)); setConnection(true);
        const nextBindingKey=room.config.bindings.join(':');
        if(nextBindingKey!==bindingKey){bindingKey=nextBindingKey;frameIds=[-1,-1];}
        room.views.forEach((v,i)=>{if(v&&!v.unchanged)count[i]++;});
        setViews(old=>room.views.map((v,i)=>{if(v?.unchanged)return old[i];if(v)frameIds[i]=v.frame_id;else frameIds[i]=-1;return v;}));
        const now=performance.now();if(now-sampled>=1000){setDisplayFps(count.map(c=>c*1000/(now-sampled)));count=[0,0];sampled=now;}
      } catch (e) {
        if (!stopped) {setConnection(false);setViews([null,null]);setNotice(String(e));setData(old=>old?{...old,skeleton:null,posture_3d:old.posture_3d?{...old.posture_3d,label:null,label_ko:null,status:'held',reason:'connection_lost'}:undefined,experimental_preview:old.experimental_preview?{...old.experimental_preview,skeleton:null}:undefined}:null);}
      } finally {if (!stopped) timer=setTimeout(tick,25);}
    }
    tick(); return ()=>{stopped=true;clearTimeout(timer);};
  },[]);
  useEffect(()=>{if (data) scene.current?.update(data,ceiling,axes);},[data,ceiling,axes]);
  const preview=data?.experimental_preview;
  const displayedPose=preview?.enabled?preview.skeleton:data?.skeleton;
  const displayReason=preview?.enabled?preview.reason:data?.reason;
  const previewReasons:Record<string,string>={no_reconstructable_joints:'삼각측량 또는 단일 카메라 추정에 필요한 몸통·관절이 부족합니다',one_person_in_both_views_required:'두 카메라에 같은 사람 한 명의 몸이 보여야 합니다',multiple_people:'여러 사람이 감지돼 단일 대상 표시를 보류합니다',bindings_changed:'카메라 연결 변경 · 임시 모드를 다시 켜 주세요',disabled:'임시 표시 꺼짐'};
  const change = (key: keyof RoomConfig, value: unknown) => setDraft(old=>old?{...old,[key]:value}:null);
  async function action(fn: ()=>Promise<void>) {setBusy(true);try {await fn();} catch(e) {log(String(e));}finally {setBusy(false);}}
  const vector = (title: string, value: Vec3, update: (v:Vec3)=>void) => <label className="field">{title}<div className="vector">{value.map((x,i)=><label key={i}><span>{['X','Y','Z'][i]}</span><input aria-label={title+' '+['X','Y','Z'][i]} type="number" step="0.01" value={x} onChange={e=>update(value.map((v,j)=>i===j?Number(e.target.value):v) as Vec3)}/></label>)}</div></label>;
  return <main>
    <header><div><div className="eyebrow">BED POSE SKELETON AI / SPATIAL MONITORING</div><h1>3D 공간 모니터</h1></div><div className="header-actions"><span className="badge"><i className={'dot '+(connection?'green':'')}/>{connection?'서버 연결됨':'서버 연결 대기'}</span><a href={'/#'+token}>측정 작업실 ↗</a></div></header>
    <div className="performance"><label><input type="checkbox" checked={preview?.enabled??false} disabled={busy} onChange={e=>{const enabled=e.target.checked;setDemo(false);action(async()=>{await api('/api/room/experimental-preview',{enabled});log(enabled?'실시간 임시 3D 표시 켜짐 · 정확도 미검증':'임시 표시 꺼짐');});}}/> 실시간 임시 3D · 정확도 미검증</label><label><input type="checkbox" checked={demo} onChange={e=>setDemo(e.target.checked)}/> DEMO 3D · 시뮬레이션 관절</label><span>3D 렌더링 {number(renderStats.fps,0)} FPS</span><span>영상 갱신 A {number(displayFps[0],0)} / B {number(displayFps[1],0)} FPS</span><span>{sessions.map(s=>s.pose_device??'—').join(' / ')}</span><span>3D 입력: {preview?.skeleton?.source==='single_camera_mediapipe_pnp'?`단일 카메라 ${preview.skeleton.source_camera} · 깊이 추정`:preview?.skeleton?'두 카메라 삼각측량':'대기'}</span>{preview?.input_rotations?.some(r=>r!==0)&&<span>검출 방향 보완 A {preview.input_rotations[0]}° / B {preview.input_rotations[1]}°</span>}<span>6자세: {data?.posture_3d?.label_ko??'판정 보류'}</span><button onClick={()=>document.getElementById('posture3d')?.scrollIntoView({behavior:'smooth'})}>침대 등록 · 자세 보기 ↓</button><details><summary>렌더링 GPU</summary>{renderStats.renderer}</details></div>
    {sessions.some(s=>s.pose_max_people===1) && <p className="muted">단일 대상 GPU 모드 · 주변 인물이 있어도 추가 인물을 출력하지 않을 수 있습니다. 실제 복원 시 양쪽에 같은 대상 한 명만 있도록 유지하세요.</p>}
    <div className="stats"><article><span>스테레오 복원</span><strong className={displayedPose?'good':''}>{connection && displayedPose?(preview?.enabled?'실시간 임시 관절':'관절 복원 중'):'대기'}</strong></article><article><span>시간 차이 / 허용 오차</span><strong>{number(data?.synchronization.skew_ms)} <small>/ {data?.config.max_skew_ms ?? '—'} ms</small></strong></article><article><span>동기화 쌍 처리율</span><strong>{number(data?.synchronization.pair_rate_fps)} <small>FPS</small></strong></article><article><span>보정 상태</span><strong>{preview?.enabled?'임시 후보 · 미검증':data?.calibration?'파일 로드됨':'미보정'}</strong></article></div>
    <div className="workspace"><section className="panel scene-panel"><div className="panel-heading"><span>침상 3D 모니터 <span className="muted">{preview?.enabled&&!demo?(preview.alignment?.source==='bed_plane'?'침상 윗면 65cm 표시 · 척도 미검증':'거리·방 위치 미검증'):'미터 · Y 위쪽'}</span></span><span className="badge">{demo?'DEMO · 실제 추적 아님':preview?.enabled?(preview.skeleton?.source==='single_camera_mediapipe_pnp'?'LIVE · 단일 카메라 추정 3D':'LIVE · 실제 카메라 입력 / 임시 3D'):data?.config.dimensions_source==='measured'?'입력한 실측 치수':'DEMO 치수'}</span></div><div className="scene" ref={host}>{webglError && <p>{webglError}</p>}</div><div className="scene-toolbar">{[['reset','초기 시점'],['top','위'],['front','정면'],['side','측면'],['bed','침대 중심']].map(([key,label])=><button key={key} onClick={()=>scene.current?.preset(key)}>{label}</button>)}<label><input type="checkbox" checked={axes} onChange={e=>setAxes(e.target.checked)}/>좌표축</label><label><input type="checkbox" checked={ceiling} onChange={e=>setCeiling(e.target.checked)}/>천장</label></div><div className="legend"><span style={{color:'#37d2d0'}}>● 카메라 A</span><span style={{color:'#f6ad55'}}>● 카메라 B</span><span style={{color:preview?.skeleton?.source==='single_camera_mediapipe_pnp'?'#f6ad55':'#73f0b0'}}>● {demo?'데모 관절':preview?.enabled?'실시간 임시 관절':'복원 관절'}</span><span>떨림 억제 · 적응형 평활화 60~160ms / 전환 300ms · 렌더링 FPS와 추론 FPS는 별개</span></div><p className="status-message">{demo?'SIMULATED · 침대 위 데모 관절입니다. 실제 사람의 자세·위치 측정이 아닙니다.':!connection?'서버 연결을 확인하세요':displayReason?previewReasons[displayReason]??reasons[displayReason]??displayReason:preview?.enabled?'실제 관절을 사용합니다. 양쪽 복원이 부족하면 단일 카메라 MediaPipe 추정 3D로 전환합니다. 깊이·거리·방 정렬은 미검증입니다. 별도 침대 등록 후 실험적 6자세 후보를 표시하며 학습 정답으로 저장하지 않습니다.':'보정된 양쪽 2D 관절을 삼각측량한 추정 좌표입니다'}</p></section>
    <aside>{[0,1].map(i=><CameraPanel key={i} index={i} view={views[i] ?? null} session={sessions.find(s=>s.id===data?.config.bindings[i])}/>)}</aside></div>
    <Posture3D key={data?.config.bindings.join(':')} data={data} views={views} api={api} log={log}/>
    <div className="bottom"><section className="panel settings"><div className="panel-heading">입력 연결 · 공간 설정</div>{draft && <>
      <p className="muted">측정 작업실에서 카메라를 추가한 뒤, 보정 때 사용한 A/B 순서로 연결하세요.</p><div className="two-cols">{[0,1].map(i=><label className="field" key={i}>카메라 {i===0?'A':'B'}<select value={draft.bindings[i]} onChange={e=>{const bindings=[...draft.bindings] as [string,string];bindings[i]=e.target.value;setDraft({...draft,bindings,installation_confirmed:false,same_person_confirmed:false});}}><option value="">입력 선택</option>{sessions.filter(s=>!s.closed).map(s=><option key={s.id} value={s.id}>{s.title} · {s.id.slice(0,6)}</option>)}</select></label>)}</div>
      <label className="check"><input type="checkbox" checked={draft.installation_confirmed} onChange={e=>change('installation_confirmed',e.target.checked)}/>보정 당시와 같은 카메라·렌즈·A/B 순서·고정 위치·해상도입니다</label><label className="check"><input type="checkbox" checked={draft.same_person_confirmed} onChange={e=>change('same_person_confirmed',e.target.checked)}/>양쪽 영상에 같은 사람 한 명만 있습니다</label>
      <div className="two-cols"><label className="field">치수 출처<select value={draft.dimensions_source} onChange={e=>change('dimensions_source',e.target.value)}><option value="demo">데모 치수</option><option value="measured">직접 측정한 치수</option></select></label><label className="field">허용 시간 차이 (ms)<input type="number" min="1" value={draft.max_skew_ms} onChange={e=>change('max_skew_ms',Number(e.target.value))}/></label></div>
      {vector('방 크기 (X 가로 · Y 높이 · Z 세로, m)',draft.room_size,v=>change('room_size',v))}
      <label className="field">입력 만료 시간 (ms)<input type="number" min="1" max="5000" value={draft.max_age_ms} onChange={e=>change('max_age_ms',Number(e.target.value))}/></label>
      {vector('침대 크기 (m)',draft.bed.size,v=>change('bed',{...draft.bed,size:v}))}
      {vector('침대 중심 위치 (m)',draft.bed.position,v=>change('bed',{...draft.bed,position:v}))}
      <label className="field">침대 Y축 회전 (°)<input type="number" value={draft.bed.yaw_degrees} onChange={e=>change('bed',{...draft.bed,yaw_degrees:Number(e.target.value)})}/></label>
      <button className="primary" disabled={busy} onClick={()=>action(async()=>{await api('/api/room/config',draft);log('설정 저장 완료 · 변경한 연결로 동기화를 다시 시작합니다.');})}>설정 저장 및 적용</button><button disabled={busy} onClick={()=>setDraft(data?structuredClone(data.config):null)}>저장값 다시 불러오기</button>
    </>}</section>
    <CalibrationPanel data={data} api={api} log={log}/><section className="panel"><div className="panel-heading">처리 통계 · 로그</div><div className="metrics"><div>매칭 쌍 수<span>{data?.synchronization.pairs ?? '—'}</span></div><div>수신 대비 매칭 비율<span>{number(data?.synchronization.pair_fraction == null ? null : data.synchronization.pair_fraction*100)} %</span></div><div>버퍼 제외 A / B<span>{data?.synchronization.dropped.join(' / ') ?? '—'}</span></div><div>오래된 쪽 프레임 경과<span>{number(data?.synchronization.age_ms,0)} ms</span></div><div>프레임 A / B<span>{data?.synchronization.frame_ids?.join(' / ') ?? '—'}</span></div></div><p className="muted">시간 기준은 PC 수신 단조 시계입니다. 카메라 노출 동기화나 자동 동일인 인증을 의미하지 않습니다. 통계는 설정 적용 시 초기화됩니다.</p>{data?.error && <p role="alert" className="warning">{data.error}</p>}<div className="logs">{logs.length?logs.map((s,i)=><p key={i}>{s}</p>):<p>설정·보정 작업 로그가 여기에 표시됩니다.</p>}</div></section></div>
    {notice && <div role="status" className="notice">{notice}<button onClick={()=>setNotice('')}>닫기</button></div>}
    <footer>3D 추정 좌표 · 기존 자세 판정과 별도 / 실험적 6자세 분류 / ToF 연동·모델 학습은 후속 단계</footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
