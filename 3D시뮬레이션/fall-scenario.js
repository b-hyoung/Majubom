import * as T from 'three';
// Authored accident storyboard, not a reconstruction of the nurse camera recording.
export function createFallScenario({intro,over,phase}){
 const vec=a=>new T.Vector3(...a);
 function pose(hip,up,right,feet,hands,poles){
  const h=vec(hip),u=vec(up).normalize(),r=vec(right);r.addScaledVector(u,-r.dot(u)).normalize();
  const f=new T.Vector3().crossVectors(r,u).normalize(),s=h.clone().addScaledVector(u,.45);
  const p={...over,hip:[...hip],shoulder:s.toArray(),head:h.clone().addScaledVector(u,.70).toArray(),ankleFlex:[5,5,0]};
  for(const [side,sign,i] of [['L',-1,0],['R',1,1]]){
   p[side+'Hip']=h.clone().addScaledVector(r,.09*sign).toArray();
   const shoulder=s.clone().addScaledVector(r,.18*sign);
   p[side+'Shoulder']=shoulder.toArray();p[side+'Wrist']=hands[i];
   p[side+'Elbow']=shoulder.clone().lerp(vec(hands[i]),.5).addScaledVector(f,.12).toArray();
   p[side+'Ankle']=feet[i];p[side+'KneePole']=poles[i];
   p[side+'Knee']=h.clone().addScaledVector(vec(poles[i]).normalize(),.3).toArray();
  }
  return p;
 }
 const straddle=pose([-.43,1.16,.12],[-.14,1,-.06],[-.5,0,.86],
  [[-.10,1.08,.25],[-.88,.56,.50]], [[-.446,1.00,-.20],[-.446,1.00,.32]], [[-.3,1,-1],[-1,.5,.4]]);
 const trailing=pose([-.68,1.08,.16],[-.28,1,-.06],[-.4,0,.92],
  [[-.68,1.15,.24],[-1.02,.45,.54]], [[-.446,1.00,-.20],[-.64,.98,.40]], [[-.3,1,-1],[-1,.5,.4]]);
 const release=pose([-.73,1.04,.18],[-.32,1,-.06],[-.4,0,.92],
  [[-.72,1.05,.24],[-1.02,.40,.54]], [[-.78,1.00,-.14],[-.89,.93,.40]], [[-.3,1,-1],[-1,.5,.4]]);
 const descend=pose([-.94,.79,.24],[-.48,1,-.08],[-.3,.1,.95],
  [[-.78,.70,.20],[-1.06,.12,.56]], [[-1.22,.95,-.05],[-1.25,.85,.60]], [[0,1,1],[-1,.2,.3]]);
 const loseBalance=pose([-1.12,.55,.28],[-.85,.8,-.08],[0,.25,1],
  [[-.88,.42,.27],[-1.07,.18,.58]], [[-1.60,.53,.10],[-1.52,.55,.76]], [[0,.4,1],[0,.5,1]]);
 const contact=pose([-1.36,.25,.32],[-1,.25,-.08],[0,.72,.7],
  [[-.91,.18,.50],[-.97,.17,.71]], [[-1.95,.10,.45],[-1.76,.25,.86]], [[0,.4,1],[0,.3,1]]);
 const floor=pose([-1.43,.23,.34],[-1,.025,-.04],[0,.95,.25],
  [[-.91,.13,.54],[-.99,.18,.77]], [[-1.94,.10,.52],[-1.73,.15,.85]], [[0,.35,1],[0,.3,1]]);
 // Keep the lower elbow in front of the torso, above the floor plane.
 floor.LElbow=[-1.70,.18,.72];
 return {
  name:'가상 사고 · 난간 넘다 낙상',alertAt:14,alertText:'침상 이탈 위험이 있습니다. 확인해 주세요.',shortAlert:'침상 이탈 위험',
  fallAlertAt:22.2,fallAlertText:'낙상 알림입니다. 환자가 바닥에 있습니다. 확인해 주세요.',
  keys:[...intro.filter(k=>k.t<=15.5),{t:16.8,p:straddle},{t:17.6,p:trailing},{t:17.7,p:release},{t:17.95,p:descend},{t:18.1,p:loseBalance},{t:18.25,p:contact},{t:18.55,p:floor},{t:30,p:floor}],
  phases:[
   phase(0,'가상 사고 · 침상에서 휴식','환자가 침상에 누워 있습니다.'),
   phase(4,'침상에서 벗어나려는 행동','난간을 잡고 몸을 일으킵니다.'),
   phase(10,'앉아서 난간 쪽으로 이동','몸을 돌리고 난간 가까이 앉습니다.','warning','주의'),
   phase(13,'한쪽 다리 들어 올리기','반대 다리와 손으로 몸을 지지하며 다리를 듭니다.','warning','주의'),
   phase(14,'사전 위험 알림 장면 · 연출','첫 번째 다리가 난간을 넘습니다. 이탈 위험 알림을 연출합니다.','danger','위험'),
   phase(15.5,'난간을 넘어 내려가려는 행동','몸을 들어 바깥쪽으로 옮기고, 지지하던 다리도 넘깁니다.','danger','위험'),
   phase(17.6,'지지 상실 · 균형 무너짐','남아 있던 손의 지지가 풀리며 몸이 침대 바깥으로 기웁니다.','danger','위험'),
   phase(18.1,'낙상 · 바닥으로 넘어짐','발로 몸을 지탱하지 못해 옆으로 넘어집니다.','danger','위험'),
   phase(18.55,'낙상 이후 · 바닥에 머묾','환자가 바닥에 쓰러진 상태로 머뭅니다.','danger','위험'),
   {...phase(22.2,'낙상 후 알림 장면 · 연출','보호자에게 낙상 알림이 전달되는 장면입니다.','danger','위험'),shortAlert:'낙상 · 확인 필요'},
   {...phase(30,'가상 사고 시나리오 종료','이탈 전 행동부터 낙상 이후까지 관찰합니다.','danger','위험'),shortAlert:'낙상 · 확인 필요'}
  ]
 };
}
