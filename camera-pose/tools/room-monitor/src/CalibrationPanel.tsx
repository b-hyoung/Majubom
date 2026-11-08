import {useEffect,useState} from 'react';
import {RoomStatus,Vec3} from './types';
import {AutomaticCalibration} from './AutomaticCalibration';
type Api=(path:string,body?:unknown)=>Promise<any>;
export interface CalibrationSetup {method:string;pattern:{kind:string;columns:number;rows:number;width_m:number|null};manual_cameras:{position:Vec3;look_at:Vec3}[]}
const methods=[['automatic_background','자동 · 정적 배경 특징점 (기본)'],['screen_charuco','선택: 화면 ChArUco'],['screen_checkerboard','선택: 화면 체커보드'],['screen_aruco','선택: 화면 ArUco'],['measured_points','선택: 방 실측 기준점'],['natural_features','선택: 단일 쌍 상대 자세'],['manual','선택: 수동 시각화']];

export function CalibrationPanel({data,api,log}:{data:RoomStatus|null;api:Api;log:(s:string)=>void}) {
  const [setup,setSetup]=useState<CalibrationSetup|null>(null),[busy,setBusy]=useState(false);
  const [anchor,setAnchor]=useState(''),[payload,setPayload]=useState(''),[transform,setTransform]=useState('');
  useEffect(()=>{if(data?.setup)setSetup(old=>old??structuredClone(data.setup!));},[data?.setup]);
  const run=async(fn:()=>Promise<void>)=>{setBusy(true);try{await fn();}catch(e){log(String(e));}finally{setBusy(false);}};
  if(!setup)return <section className="panel">보정 설정 로드 중…</section>;
  const screen=setup.method.startsWith('screen_'),job=data?.calibration_job;
  const save=async()=>{await api('/api/room/setup',setup);};
  const vectors=(title:string,value:Vec3,change:(v:Vec3)=>void)=><label className="field">{title}<div className="vector">{value.map((x,i)=><input key={i} aria-label={title+' '+['X','Y','Z'][i]} type="number" step=".01" value={x} onChange={e=>change(value.map((v,j)=>j===i?Number(e.target.value):v) as Vec3)}/>)}</div></label>;
  const template=()=>setup.method==='natural_features'?{cameras:[0,1].map(()=>({image_resolution:[640,480],intrinsic_matrix:null,distortion_coefficients:null}))}:{cameras:[0,1].map(()=>({image_resolution:[640,480],intrinsic_matrix:null,distortion_coefficients:null,world_points_m:[],image_points_px:[],measured_camera_position_m:null})),max_camera_position_error_m:.2};
  return <section className="panel calibration"><div className="panel-heading">카메라 보정 · 자동 정적 배경 우선</div>
    <label className="field">보정 방식<select value={setup.method} onChange={e=>setSetup({...setup,method:e.target.value,pattern:{...setup.pattern,kind:e.target.value.startsWith('screen_')?e.target.value.slice(7):setup.pattern.kind}})}>{methods.map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    {setup.method==='automatic_background'?<AutomaticCalibration data={data} api={api} log={log}/>:<>
    {screen && <><p className="muted">모니터·노트북·휴대폰에 패턴을 표시하세요. 양쪽 카메라가 같은 면을 보게 하고 정지시킨 채, 여러 위치·기울기로 10쌍 이상 촬영합니다. 반사·모아레·초점을 확인하세요.</p>
      <div className="two-cols"><label className="field">가로 {setup.pattern.kind==='aruco'?'마커':'내부 코너'} 수<input type="number" min="3" max="16" value={setup.pattern.columns} onChange={e=>setSetup({...setup,pattern:{...setup.pattern,columns:Number(e.target.value)}})}/></label><label className="field">세로 수<input type="number" min="3" max="16" value={setup.pattern.rows} onChange={e=>setSetup({...setup,pattern:{...setup.pattern,rows:Number(e.target.value)}})}/></label></div>
      <label className="field">화면에 보이는 전체 격자 가로 실측 (mm)<input type="number" min="10" step=".1" placeholder="자로 잰 실제 크기" value={setup.pattern.width_m===null?'':setup.pattern.width_m*1000} onChange={e=>setSetup({...setup,pattern:{...setup.pattern,width_m:e.target.value?Number(e.target.value)/1000:null}})}/></label>
      <p className="muted">흰 여백을 제외한 전체 격자 폭입니다. 화면 사양·CSS mm로 추측하지 않습니다. 전체 화면/배율/회전 변경 후 다시 측정하세요.</p>
      <a className="upload" href={'/screen-pattern?kind='+setup.pattern.kind+'&columns='+setup.pattern.columns+'&rows='+setup.pattern.rows} target="_blank" rel="noreferrer">화면 패턴 열기 ↗</a>
      <button disabled={busy} onClick={()=>run(async()=>{await save();log('보정 방식·패턴 실측값 저장 완료');})}>패턴 설정 저장</button>
      <button disabled={busy || setup.pattern.width_m===null} onClick={()=>run(async()=>{await save();const c=await api('/api/room/capture',{});setAnchor(old=>old||c.pair_id);log('보정 쌍 저장: '+c.pair_id);})}>공통 패턴 촬영</button>
      <label className="field">기준 촬영 쌍<select value={anchor} onChange={e=>setAnchor(e.target.value)}><option value="">기준 촬영 선택</option>{data?.captures?.map(id=><option key={id}>{id}</option>)}</select></label>
      <label className="field">실측한 패턴→방 변환 JSON (선택)<textarea rows={4} placeholder={'미입력: 패턴 좌표계 보정만 계산\n{"rotation":[[...],[...],[...]],"translation":[X,Y,Z],"measured":true}'} value={transform} onChange={e=>setTransform(e.target.value)}/></label>
      <p className="muted">화면은 바닥이 아닙니다. 이 변환은 기준 촬영의 실제 위치/방향을 측정한 경우만 입력하세요. 미입력 결과는 방 좌표 복원에 적용할 수 없습니다.</p>
    </>}
    {setup.method==='manual' && <><p className="warning">수동 배치는 검증되지 않은 시각화 전용입니다. 이 값으로 실제 3D 관절을 복원하지 않습니다.</p>
      {setup.manual_cameras.length===0?<button onClick={()=>setSetup({...setup,manual_cameras:[{position:[0,1.6,0],look_at:[2,.5,2.5]},{position:[4,1.6,5],look_at:[2,.5,2.5]}]})}>예시 카메라 배치 입력</button>:setup.manual_cameras.map((c,i)=><div key={i}>{vectors('카메라 '+(i===0?'A':'B')+' 위치 XYZ (m)',c.position,v=>setSetup({...setup,manual_cameras:setup.manual_cameras.map((c,j)=>j===i?{...c,position:v}:c)}))}{vectors('카메라 '+(i===0?'A':'B')+' 바라보는 점 XYZ (m)',c.look_at,v=>setSetup({...setup,manual_cameras:setup.manual_cameras.map((c,j)=>j===i?{...c,look_at:v}:c)}))}</div>)}
      <button disabled={busy} onClick={()=>run(async()=>{await save();log('수동 배치 표시 · 보정값으로 사용하지 않습니다.');})}>시각화용 배치 저장</button></>}
    {!screen && setup.method!=='manual' && <><p className="muted">{setup.method==='natural_features'?'동일 해상도에서 얻은 내부 행렬/왜곡 계수와 정적인 공통 장면이 필요합니다. 상대 이동 방향만 구하며 실제 척도·방 정렬 없이 적용하지 않습니다.':'각 카메라의 알려진 내부 행렬/왜곡 계수, 실측 방 XYZ 6개 이상, 대응 원본 픽셀이 필요합니다. 카메라 실측 위치를 입력하면 추정 위치와 비교합니다.'}</p><button onClick={()=>setPayload(JSON.stringify(template(),null,2))}>입력 형식 보기</button><textarea aria-label="보정 입력 JSON" rows={8} value={payload} onChange={e=>setPayload(e.target.value)} placeholder="실제 측정 자료 JSON을 입력하세요. null은 실제 값으로 바꿔야 합니다."/></>}
    {setup.method!=='manual' && <button className="primary" disabled={busy || job?.state==='running'} onClick={()=>run(async()=>{await save();await api('/api/room/solve',screen?{anchor_pair:anchor,anchor_world:transform.trim()?JSON.parse(transform):null}:JSON.parse(payload));log('보정 계산 시작 · 상태/오차를 확인하세요.');})}>보정 계산</button>}
    <div className="metrics"><div>계산 상태<span>{job?.state??'idle'}</span></div><div>현재 적용된 보정<span>{data?.calibration?'보정 파일 적용됨':'미보정'}</span></div><div>스테레오 RMS<span>{data?.calibration?.stereo_reprojection_error?.toFixed(3)??'—'} px</span></div>{job?.result && <><div>후보 좌표계<span>{job.result.coordinate_system}</span></div><div>후보 RMS<span>{job.result.stereo_reprojection_error?.toFixed(3)??'실제 거리 척도 없음'}</span></div></>}</div>
    {job?.error && <p role="alert" className="warning">{job.error}</p>}
    {job?.result && <details><summary>보정 후보 오차·원본 결과</summary><pre>{JSON.stringify(job.result,null,2)}</pre></details>}
    {job?.state==='complete' && <><p className="muted">{job.result?.reason??(job.result?.coordinate_system==='target_world'?'패턴 기준 보정 완료 · 실측한 방 정렬이 없어 실제 방 3D에는 적용하지 않습니다.':'실측 입력 기반 후보입니다. 별도 기준점 검증 후 적용하세요.')}</p><button disabled={busy || job.result?.coordinate_system!=='room_world'} onClick={()=>run(async()=>{await api('/api/room/apply-candidate',{});log('보정 후보 적용 · 카메라 설치와 동일 대상을 확인해 주세요.');})}>방 좌표 보정 적용</button></>}
    <label className="upload">기존 보정 JSON 불러오기<input type="file" accept=".json" onChange={e=>{const file=e.target.files?.[0];if(file)run(async()=>{await api('/api/room/calibration',JSON.parse(await file.text()));log('보정 파일 로드 완료 · 설치 확인 후 설정을 적용하세요.');});e.target.value='';}}/></label>
    </>}
  </section>;
}
