import * as T from 'three';
import {GLTFLoader} from './vendor/GLTFLoader.js';
const V=()=>new T.Vector3(),Q=()=>new T.Quaternion();
export function loadPatient(parent,onReady){
 const state={ready:false,update(){}};
 new GLTFLoader().load('assets/patient/human-0423.glb',gltf=>{
  const model=gltf.scene;model.name='patient-skinned-model';model.scale.setScalar(.95);
  const fallback=[...parent.children];parent.add(model);model.updateWorldMatrix(true,true);
  const bones={};model.traverse(o=>{if(o.isBone)bones[o.name]=o;if(o.isMesh){o.castShadow=true;o.receiveShadow=true;}});
  const bind={};for(const [name,b] of Object.entries(bones))bind[name]={q:b.getWorldQuaternion(Q()),position:b.position.clone(),rotation:b.quaternion.clone()};
  const pairs={};for(const suffix of ['l','r'])for(const [a,b] of [['thigh','calf'],['calf','foot'],['upperarm','lowerarm'],['lowerarm','hand'],['foot','ball']]){
   const name=a+'_'+suffix,child=b+'_'+suffix;const delta=bones[child].getWorldPosition(V()).sub(bones[name].getWorldPosition(V()));pairs[name]={direction:delta.clone().normalize(),length:delta.length()};
  }
  // A complete bend-plane frame removes the free axial twist left by direction-only aiming.
  const bendNormals={};for(const suffix of ['l','r'])bendNormals[suffix]=new T.Vector3().crossVectors(pairs['thigh_'+suffix].direction,pairs['calf_'+suffix].direction).normalize();
  function frame(direction,normal){const y=direction.clone().normalize(),x=normal.clone().addScaledVector(y,-normal.dot(y)).normalize(),z=new T.Vector3().crossVectors(x,y).normalize();return Q().setFromRotationMatrix(new T.Matrix4().makeBasis(x,y,z));}
  function aimLeg(name,target,normal,suffix){const b=bones[name],direction=target.clone().sub(b.getWorldPosition(V())).normalize();const delta=frame(direction,normal).multiply(frame(pairs[name].direction,bendNormals[suffix]).invert());worldRotation(b,delta.multiply(bind[name].q));}
  function worldRotation(b,q){b.quaternion.copy(b.parent.getWorldQuaternion(Q()).invert().multiply(q));b.updateWorldMatrix(false,true);}
  function aim(name,target,body){const b=bones[name],dir=target.clone().sub(b.getWorldPosition(V())).normalize();const base=pairs[name].direction.clone().applyQuaternion(body);const q=Q().setFromUnitVectors(base,dir).multiply(body).multiply(bind[name].q);worldRotation(b,q);}
  function ik(h,end,hint,a,b){const axis=end.clone().sub(h);const minReach=Math.sqrt(a*a+b*b+2*a*b*Math.cos(150*Math.PI/180));const d=T.MathUtils.clamp(axis.length(),minReach,a+b-.0001);axis.normalize();const target=h.clone().addScaledVector(axis,d);const pole=hint.clone().sub(h);pole.addScaledVector(axis,-pole.dot(axis));if(pole.lengthSq()<1e-8)pole.set(0,1,0);pole.normalize();const along=(a*a-b*b+d*d)/(2*d);return {joint:h.clone().addScaledVector(axis,along).addScaledVector(pole,Math.sqrt(Math.max(0,a*a-along*along))),target};}
  state.update=(p,body,time,mode)=>{
   // Reset each frame so timeline seeking and backwards playback are deterministic.
   for(const [name,b] of Object.entries(bones)){b.position.copy(bind[name].position);b.quaternion.copy(bind[name].rotation);}
   model.updateWorldMatrix(true,true);
   const origin=parent.getWorldPosition(V()),hip=p.hip.clone().add(origin),pelvis=bones.pelvis;
   const yawAxis=p.RShoulder.clone().sub(p.LShoulder);yawAxis.y=0;yawAxis.normalize();
   const up=new T.Vector3(0,1,0),forward=new T.Vector3().crossVectors(yawAxis,up).normalize();
   const yaw=Q().setFromRotationMatrix(new T.Matrix4().makeBasis(yawAxis,up,forward));
   const seated=T.MathUtils.smoothstep(p.shoulder.y-p.hip.y,.20,.38)*(mode==='fall'?1-T.MathUtils.smoothstep(time,16,17.6):1);
   const pelvisFrame=body.clone().slerp(yaw,seated);
   pelvis.position.copy(pelvis.parent.worldToLocal(hip.clone()));worldRotation(pelvis,pelvisFrame.multiply(bind.pelvis.q));
   // Lean through the spine while the pelvis remains seated on the mattress.
   worldRotation(bones.spine_01,body.clone().multiply(bind.spine_01.q));
   // Scenario L/R are bed-space sides; the authored +Z-facing rig uses opposite anatomical names.
   for(const [side,suffix] of [['L','r'],['R','l']]){
    const h=bones['thigh_'+suffix].getWorldPosition(V());
    const pole=p[side+'KneePole'].clone().normalize();
    const leg=ik(h,p[side+'Ankle'].clone().add(origin),h.clone().add(pole),pairs['thigh_'+suffix].length,pairs['calf_'+suffix].length);
    const thighDirection=leg.joint.clone().sub(h).normalize(),shinDirection=leg.target.clone().sub(leg.joint).normalize();
    const kneeNormal=new T.Vector3().crossVectors(thighDirection,shinDirection).normalize();
    aimLeg('thigh_'+suffix,leg.joint,kneeNormal,suffix);aimLeg('calf_'+suffix,leg.target,kneeNormal,suffix);
    // Ankle flexion is authored in the bind-local frame; toes retain their bind pose.
    // The shoe follows the shin, never a world-space time-dependent heading.
    const foot=bones['foot_'+suffix];
    const pitchAxis=new T.Vector3(1,0,0).applyQuaternion(bind['foot_'+suffix].q.clone().invert());
    const pitch=(side==='L'?p.ankleFlex.x:p.ankleFlex.y)*Math.PI/180;
    foot.quaternion.copy(bind['foot_'+suffix].rotation).multiply(Q().setFromAxisAngle(pitchAxis,pitch));
    bones['ball_'+suffix].quaternion.copy(bind['ball_'+suffix].rotation);
    const shoulder=bones['upperarm_'+suffix].getWorldPosition(V());
    const arm=ik(shoulder,p[side+'Wrist'].clone().add(origin),p[side+'Elbow'].clone().add(origin),pairs['upperarm_'+suffix].length,pairs['lowerarm_'+suffix].length);
    aim('upperarm_'+suffix,arm.joint,body);aim('lowerarm_'+suffix,arm.target,body);
   }
   model.updateWorldMatrix(true,true);
  };
  fallback.forEach(o=>o.visible=false);state.ready=true;state.model=model;state.bones=bones;state.bind=bind;onReady();
 },undefined,error=>{console.error('Patient model could not load',error);});
 return state;
}
