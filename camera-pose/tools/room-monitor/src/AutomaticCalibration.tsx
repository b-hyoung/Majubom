import {useEffect,useState} from 'react';
import {RoomStatus} from './types';
import {RelativeRig} from './RelativeRig';
export interface AutoInputs {detector:'SIFT'|'ORB';baseline_m:number|null;cameras:unknown[]|null;room_transform:unknown|null}
export function AutomaticCalibration({data,api,log}:{data:RoomStatus|null;api:(path:string,body?:unknown)=>Promise<any>;log:(s:string)=>void}) {
  const [detector,setDetector]=useState('SIFT'),[baseline,setBaseline]=useState(''),[intrinsics,setIntrinsics]=useState(''),[alignment,setAlignment]=useState('');
  const [loaded,setLoaded]=useState(false),[busy,setBusy]=useState(false);
  useEffect(()=>{if(!loaded&&data?.automatic_inputs){const v=data.automatic_inputs;setDetector(v.detector);setBaseline(v.baseline_m==null?'':String(v.baseline_m));setIntrinsics(v.cameras?JSON.stringify(v.cameras,null,2):'');setAlignment(v.room_transform?JSON.stringify(v.room_transform,null,2):'');setLoaded(true);}},[data?.automatic_inputs,loaded]);
  const run=async(fn:()=>Promise<void>)=>{setBusy(true);try{await fn();}catch(e){log(String(e));}finally{setBusy(false);}};
  const inputs=()=>({detector,baseline_m:baseline?Number(baseline):null,cameras:intrinsics.trim()?JSON.parse(intrinsics):null,room_transform:alignment.trim()?JSON.parse(alignment):null});
  const job=data?.calibration_job,active=['countdown','collecting','running'].includes(job?.state??'');
  const result=job?.result,m=result?.metrics??job?.metrics;
  const metric=(v:unknown,suffix='')=>typeof v==='number'?v.toFixed(3)+suffix:'—';
  const labels:Record<string,string>={idle:'시작 지시 대기',countdown:'사람이 비킬 시간',collecting:'새 배경 프레임 수집',running:'정적 특징점·RANSAC 계산 중',complete:'추정 후보 저장됨',needs_input:'최소 입력 필요',failed:'자동 보정 실패',cancelled:'취소됨'};
  return <div className="automatic-calibration">
    <p>마커 없이 <b>정적 배경</b>으로 추정합니다. 카메라는 고정하고, 가능하면 모두 시야에서 비켜 주세요. 사람 영역과 움직이는 영역을 제외합니다.</p>
    <div className="two-cols"><label className="field">배경 특징점<select disabled={active} value={detector} onChange={e=>setDetector(e.target.value)}><option>SIFT</option><option>ORB</option></select></label><label className="field">렌즈 중심 사이 실측 거리 (m)<input disabled={active} type="number" min=".01" step=".01" placeholder="미입력 시 이동 방향만" value={baseline} onChange={e=>setBaseline(e.target.value)}/></label></div>
    <details><summary>알려진 카메라 내부값 (없으면 입력 없이 진단 가능)</summary><p className="muted">A/B 순서의 K, 왜곡 계수, 원본 해상도입니다. 내부값이 없으면 F·대응 품질만 추정하며 정확한 회전/거리/3D는 계산하지 않습니다. 해상도·자르기·렌즈가 달라지면 다시 보정해야 합니다.</p>
    <button disabled={active} onClick={()=>setIntrinsics(JSON.stringify([0,1].map(()=>({intrinsic_matrix:null,distortion_coefficients:null,image_resolution:[640,480]})),null,2))}>필수 내부값 형식</button>
    <button disabled={active||!data?.calibration} onClick={()=>setIntrinsics(JSON.stringify(data?.calibration?.cameras.map(c=>({intrinsic_matrix:c.intrinsic_matrix,distortion_coefficients:c.distortion_coefficients,image_resolution:c.image_resolution})),null,2))}>적용된 보정에서 내부값 가져오기</button>
    <textarea aria-label="자동 보정 내부값 JSON" disabled={active} rows={7} value={intrinsics} onChange={e=>setIntrinsics(e.target.value)} placeholder="A/B 내부값 배열 · 없으면 비워 두세요"/></details>
    <details><summary>선택: 실측한 카메라 A→방 좌표 변환</summary><p className="muted">렌즈 중심 간 거리만으로 방의 원점/위쪽 방향은 정해지지 않습니다. 미입력 시 상대 카메라 배치만 표시합니다. 회전 3×3, 이동 미터, measured:true인 실제 측정값만 입력하세요.</p><textarea aria-label="자동 보정 방 변환 JSON" disabled={active} rows={4} value={alignment} onChange={e=>setAlignment(e.target.value)} placeholder={'{"rotation":[[...],[...],[...]],"translation":[X,Y,Z],"measured":true}'}/></details>
    <button disabled={busy||active} onClick={()=>run(async()=>{await api('/api/room/automatic-inputs',inputs());log('자동 보정 입력 저장 · 촬영/계산을 시작하지 않았습니다.');})}>입력만 저장</button>
    <button className="primary" disabled={busy||active} onClick={()=>run(async()=>{await api('/api/room/automatic-start',inputs());log('자동 보정 시작 요청 · 10초 후 새 배경 프레임을 수집합니다.');})}>자동 보정 시작 · 10초 후 촬영</button>
    {active&&<button disabled={busy} onClick={()=>run(async()=>{await api('/api/room/automatic-cancel',{});log('자동 보정을 취소했습니다.');})}>보정 취소</button>}
    <button disabled={busy||active||!data?.automatic_saved} onClick={()=>run(async()=>{await api('/api/room/automatic-restore',{});log('저장된 자동 보정 후보 로드 · 실제 설치/해상도가 같은지 확인하세요.');})}>저장된 자동 보정 재사용</button>
    <div className="metrics"><div>상태<span>{labels[job?.state??'idle']??job?.state}</span></div>{job?.state==='countdown'&&<div role="status">촬영까지<span>{job.remaining_seconds??10}초</span></div>}{job?.collected_pairs!=null&&<div>새 프레임 쌍<span>{job.collected_pairs} / 3</span></div>}
    <div>현재 3D 적용<span>{data?.calibration?'기존 보정 유지 · 설치 재확인 필요':'미보정'}</span></div>
    <div>대응점 / 자세 인라이어<span>{m?.matched_features??'—'} / {m?.pose_inliers??m?.fundamental_inliers??'—'}</span></div><div>인라이어 비율<span>{metric(m?.pose_inlier_ratio??m?.fundamental_inlier_ratio)}</span></div><div>재투영 RMS / p95<span>{metric(m?.reprojection_rms_px,' px')} / {metric(m?.reprojection_p95_px,' px')}</span></div><div>시차 중앙값<span>{metric(m?.median_ray_angle_degrees,'°')}</span></div><div>단위 / 좌표계<span>{result?.unit??'미확정'} / {result?.coordinate_system??'미확정'}</span></div><div>삼각측량 사용 가능<span>{result?.triangulation_eligible===true?'품질 검사 통과 후보 · 설치 확인 필요':'차단'}</span></div></div>
    {job?.error&&<p role="alert" className="warning">{job.error}</p>}{result?.reason&&<p className="muted">{result.reason}</p>}
    {result?.cameras?.length===2&&result.coordinate_system==='camera_a'&&<RelativeRig cameras={result.cameras} unit={result.unit}/>}
    <p className="muted">마스킹은 모든 사람의 완전 검출을 보장하지 않습니다. 단일 대상 GPU 모드에서도 별도 HOG/시간적 움직임을 함께 제외합니다. 카메라를 옮겼으면 저장값을 재사용하지 마세요.</p>
    {result&&<details><summary>대응·사람 제외·정적 배경 품질 원본</summary><pre>{JSON.stringify(result,null,2)}</pre></details>}
    {m&&!result&&<details><summary>실패 시 품질 지표</summary><pre>{JSON.stringify(m,null,2)}</pre></details>}
    {job?.state==='complete'&&<button disabled={busy||!result?.triangulation_eligible} onClick={()=>run(async()=>{await api('/api/room/apply-candidate',{});log('품질 통과 후보 적용 · 카메라 설치/동일 대상 확인 후 실제 3D를 켜세요.');})}>검사 통과한 방 보정 적용</button>}
  </div>;
}
