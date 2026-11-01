import {useState} from 'react';
import type {RoomStatus,View} from './types';

const reasons:Record<string,string>={conflicting_pose_evidence:'서로 다른 3D 관측의 자세 근거가 충돌해 보류합니다',skeleton_unavailable:'복원 가능한 사람 관절을 기다립니다',connection_lost:'서버 연결이 끊겨 판정을 비웠습니다',malformed_skeleton:'관절 데이터 형식 오류',duplicate_joint_id:'중복 관절 번호',joint_definition_mismatch:'관절 정의 불일치',
  bed_registration_required:'두 영상의 침대 모서리를 지정해 주세요',coordinate_system_mismatch:'침대와 관절의 좌표계가 다릅니다',
  torso_joints_missing:'양쪽 어깨·골반 관절이 부족합니다',degenerate_torso:'몸통 길이를 안정적으로 계산할 수 없습니다',
  degenerate_body_width:'어깨·골반 너비가 불안정합니다',body_axes_inconsistent:'어깨와 골반의 방향이 일치하지 않습니다',
  body_outside_bed:'침대 밖이거나 침대 평면에서 너무 멉니다',knees_required_for_sitting:'앉음을 확인할 무릎 관절이 필요합니다',
  degenerate_legs:'다리 길이가 불안정합니다',standing_or_transition:'서 있음 또는 자세 전환 중입니다',
  ankles_required_at_edge:'걸터앉음을 확인할 발목 관절이 필요합니다',edge_sitting_ambiguous:'침대 가장자리에서 다리 위치가 불확실합니다',
  knees_required_for_lying:'누움을 확인할 무릎 관절이 필요합니다',lying_or_sitting_ambiguous:'누움·앉음의 다리 형태가 불확실합니다',
  face_required_for_front_back:'바로 누움·엎드림을 구분할 얼굴 관절이 필요합니다',face_and_torso_disagree:'얼굴과 몸통 방향의 근거가 일치하지 않습니다',
  lying_orientation_ambiguous:'누운 방향이 불확실합니다',posture_transition_or_ambiguous:'자세 전환 중이거나 기울기가 애매합니다'};
const order=['머리쪽 벽측','머리쪽 통로측','발쪽 통로측','발쪽 벽측'];
type Point=[number,number];

export function Posture3D({data,views,api,log}:{data:RoomStatus|null;views:(View|null)[];api:(path:string,body?:unknown)=>Promise<any>;log:(s:string)=>void}) {
  const [frozen,setFrozen]=useState<(View|null)[]|null>(null),[points,setPoints]=useState<Point[][]>([[],[]]);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const result=data?.posture_3d;
  async function save(){setBusy(true);setError('');try{
    await api('/api/room/preview-bed',{points,bindings:data?.config.bindings,resolutions:frozen!.map(v=>[v!.width,v!.height]),frame_ids:frozen!.map(v=>v!.frame_id)});
    setFrozen(null);log('침대 위치를 임시 3D 좌표에 등록했습니다 · 정확도 미검증');
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function align(reference:string){setBusy(true);setError('');try{
    await api('/api/room/preview-axis',{reference});log(reference==='bed'?'침대 평면을 수평 기준으로 맞췄습니다':reference==='standing'?'서 있음 기준으로 위쪽 축을 맞췄습니다':reference==='lying'?'누움 기준으로 길이 방향을 맞췄습니다':'표시 축을 초기화했습니다');
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  return <section id="posture3d" className="panel" style={{margin:'18px 0',padding:20}}>
    <div className="panel-heading">4단계 · 3D 관절 + 침대 위치 / 6개 자세</div>
    <p><strong style={{fontSize:26,color:result?.label?'#73f0b0':'#f6ad55'}}>{result?.label_ko??'판정 보류'}</strong> <span className="badge">{result?.status==='stable'?`${result?.required_stable_seconds??.6}초 이상 유지`:result?.status==='candidate'?'순간 후보':'관측 대기'} · {result?.evidence_level==='partial'?'일부 관절 가림 · 제한된 후보':'실험적 예측'}</span></p>
    <p>{result?.reason?reasons[result.reason]??result.reason:'동기화된 실제 3D 관절과 침대 평면의 관계로 계산합니다.'}</p>
    <p className="muted">{Object.values(result?.labels??{}).join(' · ')}<br/>관절 품질 점수 {result?.quality_score==null?'—':result.quality_score.toFixed(2)} · 정답 확률이 아닙니다. 정확도 미검증 · ToF/학습 정답으로 저장하지 않습니다.</p>
    {!!result?.warnings?.length&&<p className="warning">{result.warnings.map(w=>({one_knee_occluded:'한쪽 무릎 가림',both_knees_occluded_strong_torso_only:'양쪽 무릎 가림 · 강한 몸통 근거만 사용',nose_occluded_using_eyes:'코 가림 · 눈 위치 참고',monocular_depth_estimate:'단일 카메라 깊이 추정',cross_view_disagreement:'카메라별 기하 근거 불일치'}[w]??w)).join(' · ')}</p>}
    {data?.experimental_preview?.enabled&&<>
      <div className="panel-heading">좌표축 정렬 · 녹화/학습용 라벨 아님</div>
      <p className="muted">기본은 등록된 침대 면을 수평으로 놓고 중심을 맞춥니다. 침상 윗면 65cm는 표시 높이이며 거리 척도는 미검증입니다. 서 있음/누움 기준은 필요할 때 선택할 수 있습니다. 모든 기준은 카메라·관절·침대에 함께 적용하며 학습이 아닙니다.</p>
      <button disabled={busy||!data.experimental_preview.bed} onClick={()=>align('bed')}>침대 기준 수평 정렬</button>
      <p>현재 기준: {data.experimental_preview.alignment?.source==='bed_plane'?'침대 수평 · 중심 정렬':'몸통/카메라 기준'}</p>
      <button disabled={busy} onClick={()=>align('standing')}>지금 서 있음 · 위쪽 축 등록</button>
      <button disabled={busy||!data.experimental_preview.alignment?.up_registered} onClick={()=>align('lying')}>지금 침상 누움 · 길이 축 등록</button>
      <button disabled={busy} onClick={()=>align('reset')}>축 초기화</button>
      <p>축 기준: {data.experimental_preview.alignment?.standing_registered?'서 있음 등록됨':data.experimental_preview.alignment?.reference_info?.source==='manual_camera_pitch'?'하향각 A 37° / B 27° 기준 적용':'서 있음 대기'} / {data.experimental_preview.alignment?.lying_registered?'누움 등록됨':'누움 대기'}</p>
      {error&&!frozen&&<p role="alert" className="warning">{error}</p>}
      <p>침대 등록: {data.experimental_preview.bed?'완료 · 임시 좌표':'미등록'}</p>
      {!frozen&&<button disabled={!views.every(v=>v?.image)} onClick={()=>{setFrozen(structuredClone(views));setPoints([[],[]]);setError('');}}>침대 모서리 지정 · 현재 영상 고정</button>}
      {frozen&&<>
        <p>카메라는 고정한 채, 매트리스 윗면의 <b>같은 실제 모서리</b>를 양쪽에서 같은 순서로 클릭하세요.<br/>1 머리쪽 벽측 → 2 머리쪽 통로측 → 3 발쪽 통로측 → 4 발쪽 벽측. 난간 윗부분은 제외합니다. 가려진 모서리는 추측하지 말고 시야를 확보하세요.</p>
        <div className="two-cols">{frozen.map((v,i)=><div key={i}>
          <p>카메라 {'AB'[i]} · {points[i].length<4?`다음: ${points[i].length+1} ${order[points[i].length]}`:'4점 지정 완료'}</p>
          <div style={{position:'relative'}}><img alt={`침대 모서리 지정 카메라 ${'AB'[i]}`} src={v!.image!} style={{width:'100%',display:'block',cursor:'crosshair'}} onClick={e=>{
            if(points[i].length>=4)return;const r=e.currentTarget.getBoundingClientRect();const p:Point=[(e.clientX-r.left)*v!.width/r.width,(e.clientY-r.top)*v!.height/r.height];
            setPoints(old=>old.map((a,j)=>i===j?[...a,p]:a));
          }}/><svg viewBox={`0 0 ${v!.width} ${v!.height}`} style={{position:'absolute',inset:0,width:'100%',height:'100%',pointerEvents:'none'}}>
            <polyline points={points[i].map(p=>p.join(',')).join(' ')} fill="none" stroke="#ffd36a" strokeWidth="2"/>
            {points[i].map(([x,y],j)=><g key={j}><circle cx={x} cy={y} r="5" fill="#ffd36a"/><text x={x+7} y={y-7} fill="#fff" fontSize="18" stroke="#000" strokeWidth=".5">{j+1}</text></g>)}
          </svg></div>
          <button onClick={()=>setPoints(old=>old.map((a,j)=>i===j?a.slice(0,-1):a))}>마지막 점 취소</button>
        </div>)}</div>
        {error&&<p role="alert" className="warning">{error}</p>}
        <button disabled={busy||!points.every(p=>p.length===4)} onClick={save}>침대 위치 등록</button><button disabled={busy} onClick={()=>setFrozen(null)}>취소</button>
      </>}
    </>}
  </section>;
}
