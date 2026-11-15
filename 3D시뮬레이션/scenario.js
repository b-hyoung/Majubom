import * as T from 'three';
import {loadPatient} from './patient.js';
import {createFallScenario} from './fall-scenario.js';
const $=s=>document.querySelector(s),v=a=>new T.Vector3(...a);
export function createScenario({scene,bed,monitorScreen,phoneScreen,setView,getView=()=>-1}){
 const person=new T.Group();person.name='patient-scenario';scene.add(person);
 const material=c=>new T.MeshStandardMaterial({color:c,roughness:.85});
 const skin=material('#bd967b'),shirt=material('#8baca7'),pants=material('#607a78'),hair=material('#424440'),sole=material('#d8d9cf');
 function ellipsoid(scale,m,parent=person){const o=new T.Mesh(new T.SphereGeometry(1,24,16),m);o.scale.set(...scale);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;}
 const pelvis=ellipsoid([.155,.12,.105],pants),chest=ellipsoid([.205,.26,.115],shirt);
 const neck=ellipsoid([.055,.065,.055],skin),head=new T.Group();person.add(head);
 ellipsoid([.09,.115,.087],skin,head);
 const haircap=new T.Mesh(new T.SphereGeometry(.093,24,16,0,Math.PI*2,0,Math.PI*.48),hair);haircap.scale.set(1,1.25,1);haircap.position.y=.018;head.add(haircap);
 const nose=ellipsoid([.014,.025,.022],skin,head);nose.position.set(0,-.01,.085);
 for(const x of [-.035,.035]){const eye=ellipsoid([.009,.005,.004],material('#343b36'),head);eye.position.set(x,.02,.081);const ear=ellipsoid([.016,.027,.015],skin,head);ear.position.set(Math.sign(x)*.092,0,0);}
 const limbs=[];
 function limb(a,b,r,m){const mesh=new T.Mesh(new T.CylinderGeometry(r*.85,r,1,16),m);mesh.castShadow=true;mesh.receiveShadow=true;person.add(mesh);limbs.push({a,b,mesh});}
 for(const side of ['L','R']){limb(side+'Shoulder',side+'Elbow',.055,shirt);limb(side+'Elbow',side+'Wrist',.043,skin);limb(side+'Hip',side+'Knee',.075,pants);limb(side+'Knee',side+'Ankle',.052,pants);}
 const joints={};for(const side of ['L','R']){joints[side+'Shoulder']=ellipsoid([.065,.065,.065],shirt);joints[side+'Elbow']=ellipsoid([.046,.046,.046],skin);joints[side+'Knee']=ellipsoid([.065,.065,.065],pants);joints[side+'Wrist']=ellipsoid([.035,.055,.025],skin);}
 const feet={L:ellipsoid([.053,.04,.115],sole),R:ellipsoid([.053,.04,.115],sole)};
 // Coordinates are meters relative to bed center; hand/leg waypoints explicitly clear the raised left rail.
 const rest={hip:[0,.85,.10],shoulder:[0,.89,-.31],head:[0,.91,-.57],LShoulder:[-.18,.88,-.31],RShoulder:[.18,.88,-.31],LElbow:[-.24,.84,-.08],RElbow:[.24,.84,-.08],LWrist:[-.16,.94,.10],RWrist:[.16,.94,.10],LHip:[-.09,.85,.10],RHip:[.09,.85,.10],LKnee:[-.10,.83,.47],RKnee:[.10,.83,.47],LAnkle:[-.10,.82,.83],RAnkle:[.10,.82,.83]};
 const sit={hip:[0,.85,.10],shoulder:[0,1.29,-.03],head:[0,1.54,-.08],LShoulder:[-.18,1.29,-.03],RShoulder:[.18,1.29,-.03],LElbow:[-.24,1.02,-.08],RElbow:[.24,1.02,-.08],LWrist:[-.25,.79,-.04],RWrist:[.25,.79,-.04],LHip:[-.09,.85,.10],RHip:[.09,.85,.10],LKnee:[-.1,.84,.47],RKnee:[.1,.84,.47],LAnkle:[-.1,.82,.83],RAnkle:[.1,.82,.83]};
 const grip={hip:[-.10,.85,.08],shoulder:[-.18,1.30,.08],head:[-.24,1.54,.08],LShoulder:[-.18,1.30,-.10],RShoulder:[-.18,1.30,.26],LElbow:[-.38,1.13,-.15],RElbow:[-.38,1.13,.28],LWrist:[-.446,.94,-.20],RWrist:[-.446,.94,.32],LHip:[-.10,.87,-.01],RHip:[-.10,.87,.17],LKnee:[-.27,.89,-.23],RKnee:[-.25,.89,.43],LAnkle:[.18,.80,.28],RAnkle:[.06,.82,.60]};
 const lean={...grip,hip:[-.10,.85,.08],LHip:[-.10,.85,-.01],RHip:[-.10,.85,.17],shoulder:[-.27,1.28,.08],head:[-.34,1.52,.08],LShoulder:[-.27,1.28,-.10],RShoulder:[-.27,1.28,.26],LElbow:[-.50,1.10,-.14],RElbow:[-.50,1.10,.28]};
 const lift={...lean,RKnee:[-.28,1.25,.22],RAnkle:[.06,1.02,.50]};
 const cross={...lift,RKnee:[-.48,1.24,.22],RAnkle:[-.64,1.02,.50]};
 const over={...cross,hip:[-.10,.85,.08],LHip:[-.10,.85,-.01],RHip:[-.10,.85,.17],RKnee:[-.57,1.18,.22],RAnkle:[-.66,.76,.50]};
 // Reference: nurse camera originals, sampled at 1-second intervals around transitions.
 // Preparation starts while lying; rail grip precedes sitting, rather than following it.
 const prepare={...rest,RKnee:[.14,1.13,.36],RAnkle:[.14,.81,.55],LWrist:[-.446,.94,-.20],LElbow:[-.30,.85,-.18]};
 const rise={...prepare,hip:[-.025,.85,.10],shoulder:[-.06,1.10,-.25],head:[-.12,1.30,-.42],LShoulder:[-.23,1.04,-.20],RShoulder:[.11,1.16,-.30],LElbow:[-.36,.99,-.23],RElbow:[.25,.99,-.04],RWrist:[.25,.79,.03],LKnee:[-.22,.85,.43],LAnkle:[-.03,.81,.64]};
 const folded={...sit,LKnee:[-.24,.85,.39],RKnee:[.24,.87,.39],LAnkle:[.12,.81,.37],RAnkle:[-.12,.84,.28],LWrist:[-.446,.94,-.20],LElbow:[-.33,1.08,-.15],RWrist:[.25,.79,.05],RElbow:[.25,1.03,.02]};
 const returnFolded={...folded,LWrist:[-.21,.93,.34],RWrist:[.21,.93,.34],LElbow:[-.26,1.09,.17],RElbow:[.26,1.09,.17]};
 const exitPrepare={...prepare,LWrist:rest.LWrist,LElbow:rest.LElbow};
 const exitRise={...rise,LWrist:[-.25,.79,.03],LElbow:[-.26,.99,-.04]};
 const exitFolded={...returnFolded};
 const exitTurn={...grip,LWrist:[-.18,.78,-.27],RWrist:[.05,.78,.31],LElbow:[-.18,1.04,-.22],RElbow:[.0,1.04,.31]};
 const exitSwing={...exitTurn,hip:[-.22,.86,.08],LHip:[-.22,.86,-.01],RHip:[-.22,.86,.17],LKnee:[-.54,.83,-.01],RKnee:[-.54,.83,.17],LAnkle:[-.66,.68,-.01],RAnkle:[-.66,.68,.17]};
 const edge={...exitSwing,shoulder:[-.28,1.29,.08],head:[-.30,1.54,.08],LShoulder:[-.28,1.29,-.10],RShoulder:[-.28,1.29,.26],LAnkle:[-.67,.38,-.01],RAnkle:[-.67,.38,.17],LWrist:[-.20,.78,-.27],RWrist:[-.20,.78,.40],LElbow:[-.26,1.04,-.24],RElbow:[-.26,1.04,.36]};
 // Scoot to the rail before lifting; support foot stays tucked behind the pelvis.
 for(const pose of [grip,lean,lift,cross,over])Object.assign(pose,{
  hip:[-.24,.85,.08],LHip:[-.183,.85,-.002],RHip:[-.297,.85,.162],
  shoulder:[-.29,1.30,.02],head:[-.32,1.54,0],
  LShoulder:[-.187,1.30,-.127],RShoulder:[-.393,1.30,.167],
  LAnkle:[-.05,.79,.36],LWrist:[-.446,1.00,-.20],RWrist:[-.446,1.00,.32]
 });
 // Knee bend directions are authored with the whole seated pose, not forced upwards.
 for(const pose of [rest,sit,prepare,rise,exitPrepare,exitRise]){
  pose.LKneePole=[0,1,0];pose.RKneePole=[0,1,0];pose.ankleFlex=[0,0,0];
 }
 for(const pose of [folded,returnFolded,exitFolded]){
  pose.LKneePole=[-1,.12,.3];pose.RKneePole=[1,.12,.3];pose.ankleFlex=[25,25,0];
 }
 for(const pose of [grip,lean,lift,cross,over]){
  pose.LKneePole=[1,-.15,.2];pose.RKneePole=[0,1,.1];pose.ankleFlex=[25,0,0];
 }
 for(const pose of [exitTurn,exitSwing,edge]){
  pose.LKneePole=[-1,.8,0];pose.RKneePole=[-1,.8,0];pose.ankleFlex=[0,0,0];
 }
 const tuckSupport={...folded,hip:[-.12,.85,.09],
  shoulder:[-.14,1.30,.05],head:[-.15,1.54,.03],
  LShoulder:[-.28,1.30,-.06],RShoulder:[0,1.30,.16],
  LAnkle:[-.02,.94,.40],LKneePole:[0,1,0],ankleFlex:[15,25,0]};
 const phase=(t,title,caption,risk='normal',label='정상')=>({t,title,caption,risk,label});
 const configs={rail:{
  name:'안전바 넘기 시도',alertAt:14,alertText:'어르신이 안전바를 넘으려 합니다.',shortAlert:'안전바 넘기 시도',
  keys:[{t:0,p:rest},{t:4,p:rest},{t:6,p:prepare},{t:8,p:rise},{t:10,p:folded},{t:11,p:folded},{t:12,p:tuckSupport},{t:13,p:grip},{t:13.6,p:lift},{t:14.5,p:cross},{t:15.5,p:over},{t:18,p:over},{t:19,p:cross},{t:20,p:lift},{t:21,p:grip},{t:22,p:tuckSupport},{t:23,p:returnFolded},{t:30,p:returnFolded}],
  phases:[phase(0,'정상 · 바로 누워 휴식','바로 누워 가슴 위에 손을 올립니다.'),phase(4,'무릎 굽히기 · 한 손으로 난간 잡기','누운 채 무릎을 세우고 난간을 잡습니다.'),phase(6,'몸 비틀어 일으키기','난간을 잡아 지지하며 몸을 비틀어 일어납니다.'),phase(10,'주의 · 다리를 접고 앉음','다리를 접고 앉아 난간을 잡은 자세를 유지합니다.','warning','주의'),phase(11,'주의 · 몸통과 골반 회전','난간 쪽으로 몸과 골반을 돌려 다리를 들어 올립니다.','warning','주의'),phase(14,'위험 · 한쪽 다리 넘기 시도','한쪽 다리가 난간 위를 넘어갑니다. 반대 다리는 침대 위에 있습니다.','danger','위험'),phase(15.5,'위험 · 난간에 걸친 자세 유지','한쪽 다리는 밖으로, 반대쪽 무릎은 침대 위에서 몸을 지지합니다.','danger','위험'),phase(18,'다시 침대 안으로 복귀','넘긴 다리를 다시 들어 침대 안으로 가져옵니다.','danger','위험'),phase(23,'복귀 · 침대 안에 앉음','다리를 접고 침대 안에 앉습니다. 연출용 알림은 유지합니다.','danger','위험'),phase(30,'난간 시나리오 종료','난간 넘기 시도와 복귀 동작을 확인했습니다.','danger','위험')]
 },exit:{
  name:'침상 가장자리로 이동',alertAt:16,alertText:'어르신이 침상 가장자리로 이동했습니다. 확인해 주세요.',shortAlert:'침상 가장자리 이동',
  keys:[{t:0,p:rest},{t:5,p:rest},{t:7,p:exitPrepare},{t:8.5,p:exitRise},{t:10,p:exitFolded},{t:12,p:exitFolded},{t:14,p:exitTurn},{t:15,p:exitSwing},{t:17,p:edge},{t:23,p:edge},{t:24.5,p:exitSwing},{t:26,p:exitTurn},{t:28,p:exitFolded},{t:30,p:exitFolded}],
  phases:[phase(0,'정상 · 바로 누워 휴식','침대에 바로 누워 쉬고 있습니다.'),phase(5,'무릎 굽히기 · 상체 일으키기','무릎을 굽히고 손으로 매트리스를 짚어 일어납니다.'),phase(10,'주의 · 다리를 접고 앉음','침대 안에 다리를 접고 앉습니다.','warning','주의'),phase(12,'주의 · 옆으로 돌며 다리 내리기','손으로 몸을 지지하고 골반을 돌려 두 다리를 내립니다.','warning','주의'),phase(16,'위험 · 침상 가장자리 이동','가장자리에 걸터앉습니다. 일어서거나 걷는 동작은 없습니다.','danger','위험'),phase(23,'다시 침대 안으로 복귀','두 다리를 침대 위로 가져오며 몸을 다시 돌립니다.','danger','위험'),phase(28,'복귀 · 다리를 접고 앉음','침대 안에 돌아와 앉습니다. 연출용 알림은 유지합니다.','danger','위험'),phase(30,'이탈 시나리오 종료','가장자리 이동과 복귀 동작을 확인했습니다.','danger','위험')]
 }};
 const railOnly={...rest,LWrist:prepare.LWrist,LElbow:prepare.LElbow};
 const inward={...exitFolded,hip:[.04,.85,.10],shoulder:[.14,1.29,.08],head:[.16,1.54,.08],LShoulder:[.14,1.29,.26],RShoulder:[.14,1.29,-.10],LWrist:[.12,.82,.35],RWrist:[.12,.82,-.18],LElbow:[.20,1.05,.30],RElbow:[.20,1.05,-.15],LHip:[.04,.85,.19],RHip:[.04,.85,.01]};
 configs.sit_only={name:'비교 · 앉기만',alertAt:Infinity,keys:[{t:0,p:rest},{t:4,p:rest},{t:7,p:exitPrepare},{t:9,p:exitRise},{t:12,p:exitFolded},{t:20,p:exitFolded},{t:24,p:exitRise},{t:28,p:rest},{t:30,p:rest}],phases:[phase(0,'비교 실험 · 누움','실험 대조 동작: 침상 안에서 일어나 앉습니다.'),phase(4,'비교 실험 · 일어나기','앉는 움직임만으로 이탈 시도로 분류하는지 비교합니다.'),phase(12,'비교 실험 · 앉기만 유지','안전바를 잡거나 다리를 밖으로 넘기지 않습니다.'),phase(20,'비교 실험 · 다시 눕기','동작 라벨과 센서 출력은 별도로 기록합니다.')]};
 configs.rail_only={name:'비교 · 안전바만 잡기',alertAt:Infinity,keys:[{t:0,p:rest},{t:4,p:rest},{t:8,p:railOnly},{t:20,p:railOnly},{t:24,p:rest},{t:30,p:rest}],phases:[phase(0,'비교 실험 · 누움','누운 자세에서 팔만 안전바로 뻗습니다.'),phase(4,'비교 실험 · 안전바만 잡기','팔 뻗는 순간과 잡은 상태를 따로 관찰합니다.'),phase(20,'비교 실험 · 팔 내리기','안전바 접촉만으로 이탈 경보가 발생하는지 비교합니다.')]};
 configs.turn_in={name:'비교 · 안쪽으로 돌기',alertAt:Infinity,keys:[{t:0,p:rest},{t:4,p:rest},{t:7,p:exitPrepare},{t:9,p:exitRise},{t:12,p:exitFolded},{t:16,p:inward},{t:20,p:inward},{t:24,p:exitFolded},{t:30,p:exitFolded}],phases:[phase(0,'비교 실험 · 누움','일어나 앉은 뒤 침대 안쪽으로 몸을 돌립니다.'),phase(4,'비교 실험 · 일어나기','상체 상승과 침상 이탈을 구분하기 위한 동작입니다.'),phase(12,'비교 실험 · 안쪽으로 돌기','몸 중심이 안전바 바깥쪽으로 움직이는 경우와 비교합니다.'),phase(24,'비교 실험 · 중앙 복귀','다리는 침상 안에 유지합니다.')]};
 configs.fall=createFallScenario({intro:configs.rail.keys,over,phase});
 let mode='fall';
 const active=()=>configs[mode];
 let time=0,playing=false,last=null,lastPhase=-1,alertSpoken=false;
 const makeScreen=(w,h)=>{const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;const texture=new T.CanvasTexture(canvas);texture.colorSpace=T.SRGBColorSpace;return {canvas,c:canvas.getContext('2d'),texture};};
 const phone=makeScreen(256,512);phoneScreen.material.map=phone.texture;
 function drawScreens(phase){const color=phase.risk==='danger'?'#a23d30':phase.risk==='warning'?'#8d681b':'#326e55';
 const p=phone.c;p.fillStyle='#f2f5ee';p.fillRect(0,0,256,512);p.fillStyle='#354d3b';p.font='bold 25px sans-serif';p.fillText('간호사 알림',20,48);p.font='17px sans-serif';p.fillText('촬영 리허설',20,78);p.fillStyle=color;p.fillRect(14,110,228,190);p.fillStyle='white';p.font='bold 42px sans-serif';p.fillText(phase.label,25,174);p.font='20px sans-serif';p.fillText('침상 01',25,218);p.fillText(phase.risk==='danger'?(phase.shortAlert||active().shortAlert):'상태 관찰 중',25,260);p.fillStyle='#48604c';p.font='18px sans-serif';p.fillText('시나리오 알림',20,350);p.fillText('실제 판정 아님',20,383);phone.texture.needsUpdate=true;}
 function poseAt(t){const keys=active().keys;let i=0;while(i<keys.length-2&&keys[i+1].t<t)i++;const a=keys[i],b=keys[i+1];let u=T.MathUtils.clamp((t-a.t)/(b.t-a.t),0,1);if(!(mode==='fall'&&t>=17.6&&t<19.2))u=u*u*(3-2*u);const result={};for(const k of Object.keys(rest))result[k]=v(a.p[k]).lerp(v(b.p[k]),u);// Keep hip socket spacing constant while the pelvis turns.
 const hipAxis=result.RHip.clone().sub(result.LHip).normalize();
 result.LHip.copy(result.hip).addScaledVector(hipAxis,-.09);
 result.RHip.copy(result.hip).addScaledVector(hipAxis,.09);
 if(mode==='rail'){const liftWeight=T.MathUtils.smoothstep(t,13,13.6)*(1-T.MathUtils.smoothstep(t,20,21));result.RKnee.lerp(result.RHip.clone().add(new T.Vector3(0,1,0)),liftWeight);}
 for(const side of ['L','R'])solveLeg(result,side);
 return result;}
 // Two-bone IK: knee hints determine the bend plane; bone lengths never interpolate.
 function solveLeg(p,side){
  const h=p[side+'Hip'],k=p[side+'Knee'],a=p[side+'Ankle'];
  const thigh=.371,shin=.361,axis=a.clone().sub(h);
  const distance=T.MathUtils.clamp(axis.length(),.025,thigh+shin-.0001);
  axis.normalize();a.copy(h).addScaledVector(axis,distance);
  const pole=k.clone().sub(h);pole.addScaledVector(axis,-pole.dot(axis));
  if(pole.lengthSq()<1e-8){pole.set(0,1,0);pole.addScaledVector(axis,-pole.dot(axis));}
  pole.normalize();
  const along=(thigh*thigh-shin*shin+distance*distance)/(2*distance);
  k.copy(h).addScaledVector(axis,along).addScaledVector(pole,Math.sqrt(Math.max(0,thigh*thigh-along*along)));
 }
 const patient=loadPatient(person,()=>refresh());
 const upAxis=new T.Vector3(0,1,0);
 function applyPose(){const p=poseAt(time),offset=bed.position.clone();offset.y=0;person.position.copy(offset);
 // Width edits keep the hands aligned with the left rail; the human body retains its proportions.
 const railX=-.53*bed.scale.x,delta=railX+.446;const reach=T.MathUtils.smoothstep(time,4,6);for(const key of Object.keys(p))if(!key.endsWith('Pole')&&key!=='ankleFlex')p[key].x+=delta*reach;
 const right=p.RShoulder.clone().sub(p.LShoulder).normalize(),up=p.head.clone().sub(p.shoulder).normalize(),forward=new T.Vector3().crossVectors(right,up).normalize();right.crossVectors(up,forward).normalize();const q=new T.Quaternion().setFromRotationMatrix(new T.Matrix4().makeBasis(right,up,forward));
 if(patient.ready){patient.update(p,q,time,mode);return;}
 pelvis.position.copy(p.hip);pelvis.quaternion.copy(q);chest.position.copy(p.hip).lerp(p.shoulder,.53);chest.quaternion.copy(q);neck.position.copy(p.shoulder).lerp(p.head,.42);neck.quaternion.copy(q);head.position.copy(p.head);head.quaternion.copy(q);
 for(const {a,b,mesh} of limbs){const dir=p[b].clone().sub(p[a]);mesh.position.copy(p[a]).lerp(p[b],.5);mesh.scale.y=dir.length();mesh.quaternion.setFromUnitVectors(upAxis,dir.normalize());}
 for(const [name,o] of Object.entries(joints)){o.position.copy(p[name]);o.quaternion.copy(q);}
 // Continuous foot orientation, including the return, without the old abrupt 11s switch.
 const turnIn=mode==='rail'?T.MathUtils.smoothstep(time,6,10):T.MathUtils.smoothstep(time,7,10);
 for(const side of ['L','R']){feet[side].position.copy(p[side+'Ankle']);feet[side].rotation.set(-Math.PI/2*(1-turnIn),-Math.PI/2*turnIn,0);feet[side].position.y+=.055*(1-turnIn);feet[side].position.x-=.055*turnIn;}

 }
 function speak(message=active().alertText){if(!$('#voiceScenario').checked||!('speechSynthesis'in window))return;const u=new SpeechSynthesisUtterance('위험 알림입니다. '+message);u.lang='ko-KR';u.rate=.95;speechSynthesis.speak(u);}
 function refresh(){$('#scenarioCaption small').textContent=mode==='fall'?'가상 사고 시나리오 · 동작과 센서 알림은 연출입니다':'촬영 리허설 · 동작과 알림은 정해진 시나리오입니다';applyPose();const phases=active().phases;let idx=0;for(let i=0;i<phases.length;i++)if(time>=phases[i].t)idx=i;const phase=phases[idx];if(idx!==lastPhase){$('#phaseText').textContent=phase.title;$('#captionText').textContent=phase.caption;$('#riskBadge').textContent=phase.label;$('#scenarioCaption').dataset.risk=phase.risk;drawScreens(phase);lastPhase=idx;}$('#timeline').value=time;$('#timecode').textContent=`${time.toFixed(1).padStart(4,'0')} / 30.0초`;$('#playScenario').textContent=playing?'Ⅱ 일시정지':'▶ 재생';}
 function cancelVoice(){if('speechSynthesis'in window)speechSynthesis.cancel();}
 $('#playScenario').onclick=()=>{if(time>=30)time=0;playing=!playing;last=null;if(playing&&time===0){if(getView()!==8){setView(mode==='fall'?7:2);$('#labelsToggle').checked=false;$('#labels').hidden=true;}alertSpoken=false;}if(!playing)cancelVoice();refresh();};
 $('#restartScenario').onclick=()=>{playing=false;time=0;last=null;alertSpoken=false;cancelVoice();refresh();};
 $('#timeline').oninput=e=>{playing=false;time=+e.target.value;last=null;alertSpoken=time>=active().alertAt;cancelVoice();refresh();};
 $('#voiceScenario').onchange=()=>{if(!$('#voiceScenario').checked)cancelVoice();};
 $('#scenarioType').onchange=e=>{mode=e.target.value;playing=false;time=0;last=null;lastPhase=-1;alertSpoken=false;cancelVoice();const rail=bed.getObjectByName('left-rail-raised');rail.position.y=mode==='exit'?-.38:0;if(mode==='fall'&&getView()!==8)setView(7);refresh();};
 $('#showPerson').onchange=e=>person.visible=e.target.checked;
 document.addEventListener('visibilitychange',()=>{if(document.hidden){playing=false;last=null;cancelVoice();refresh();}});
 refresh();
 return {person,patient,samplePose:poseAt,update(now){if(playing){if(last!==null){const prev=time;time=Math.min(30,time+Math.min((now-last)/1000,.1)*+$('#simSpeed').value);if(prev<active().alertAt&&time>=active().alertAt&&!alertSpoken){alertSpoken=true;speak();}if(active().fallAlertAt&&prev<active().fallAlertAt&&time>=active().fallAlertAt)speak(active().fallAlertText);}last=now;if(time>=30)playing=false;refresh();}},seek(t){playing=false;time=T.MathUtils.clamp(t,0,30);last=null;cancelVoice();refresh();},get mode(){return mode;},get time(){return time;},get playing(){return playing;}};
}
