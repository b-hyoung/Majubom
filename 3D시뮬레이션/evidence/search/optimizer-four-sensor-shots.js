import * as T from 'three';
// Finite exhaustive geometric search, not a physics or detection-model simulation.
export async function optimize(room, report=()=>{}) {
 const {scene,bed,monitor,scenario}=room;
 const xs=[-1.30,-.90,-.50,-.10,.30,.70,1.10],zs=[-1.15,-.70,-.25,.20,.65,1.10,1.55,2.00,2.55];
 const hs=[1.40,1.65,1.85,2.05],fovs=[44,50,57,64,72,80],tys=[.75,.95,1.15,1.35,1.55];
 const tvs=[-.70,-.35,0].flatMap(x=>[-1.80,-1.15,-.75,2.10].map(z=>[x,0,z]));
 const total=tvs.length*xs.length*zs.length*hs.length*fovs.length*tys.length;
 const keys=['head','shoulder','hip','LWrist','RWrist','LKnee','RKnee','LAnkle','RAnkle'];
 const poses=[];const swept=new T.Box3();
 for(let t=0;t<=30;t+=.5){const p=scenario.samplePose(t);const points=keys.map(k=>p[k].clone().add(bed.position));points.forEach(v=>swept.expandByPoint(v));poses.push({t,points});}
 swept.expandByScalar(.12);
 const corners=b=>[0,1].flatMap(x=>[0,1].flatMap(y=>[0,1].map(z=>new T.Vector3(x?b.max.x:b.min.x,y?b.max.y:b.min.y,z?b.max.z:b.min.z))));
 const sensors=[{name:'머리맡 ToF',object:room.headTof,min:[-.055,1.465,-.045],max:[.06,1.665,.075],center:[.035,1.53,.064]}, {name:'발치 ToF',object:room.footTof,min:[-.055,1.465,-.045],max:[.06,1.665,.075],center:[.035,1.53,.064]}, {name:'천장 mmWave',object:room.radar,min:[-.08,0,-.06],max:[.08,.085,.06],center:[0,.012,0]}];
 scene.updateMatrixWorld(true);
 for(const s of sensors){s.points=corners(new T.Box3(new T.Vector3(...s.min),new T.Vector3(...s.max))).map(p=>s.object.localToWorld(p));s.worldCenter=s.object.localToWorld(new T.Vector3(...s.center));}
 const humanPoints=poses.flatMap(p=>p.points);const poseBoxes=poses.map(p=>new T.Box3().setFromPoints(p.points).expandByScalar(.12));scene.updateMatrixWorld(true);
 const inside=(o,p)=>{for(;o;o=o.parent)if(o===p)return true;return false;};
 const fixed=[],fixedObjects=[];scene.traverseVisible(o=>{if(!o.isMesh||inside(o,monitor)||inside(o,scenario.person)||o.geometry.type==='TubeGeometry')return;const b=new T.Box3().setFromObject(o);if(b.max.y<.055)return;fixed.push(b);fixedObjects.push({box:b,object:o});});
 const monitorBoxes=[];const inv=monitor.matrixWorld.clone().invert();monitor.traverse(o=>{if(!o.isMesh)return;o.geometry.computeBoundingBox();monitorBoxes.push(o.geometry.boundingBox.clone().applyMatrix4(inv.clone().multiply(o.matrixWorld)));});
 const camera=new T.PerspectiveCamera(57,16/9,.05,80);const ray=new T.Ray();const hit=new T.Vector3();
 const stats={total,considered:0,blockedCamera:0,blockedTV:0,patientCut:0,tvCut:0,sensorCut:0,sensorTooSmall:0,sensorOccluded:0,feasible:0};const candidates=[];
 const proj=(v)=>v.clone().project(camera);
 const screenLocal=[[-.745,1.2825,.041],[.745,1.2825,.041],[.745,2.1175,.041],[-.745,2.1175,.041]];
 for(const tv of tvs)for(const x of xs)for(const z of zs){
  const per=hs.length*fovs.length*tys.length;
  const stand=new T.Box3(new T.Vector3(x-.12,.06,z-.12),new T.Vector3(x+.12,2.05,z+.12));
  if(fixed.some(b=>b.intersectsBox(stand))||poseBoxes.some(b=>b.intersectsBox(stand))){stats.blockedCamera+=per;stats.considered+=per;continue;}
  const angle=Math.atan2(x-tv[0],z-tv[2]);const matrix=new T.Matrix4().compose(new T.Vector3(...tv),new T.Quaternion().setFromAxisAngle(new T.Vector3(0,1,0),angle),new T.Vector3(1,1,1));
  const boxes=monitorBoxes.map(b=>b.clone().applyMatrix4(matrix));
  if(boxes.some(b=>fixed.some(f=>f.intersectsBox(b))||poseBoxes.some(p=>b.intersectsBox(p)))){stats.blockedTV+=per;stats.considered+=per;continue;}
  const sc=screenLocal.map(a=>new T.Vector3(...a).applyMatrix4(matrix));
  for(const height of hs)for(const fov of fovs)for(const ty of tys){
   stats.considered++;camera.position.set(x,height,z);camera.fov=fov;camera.updateProjectionMatrix();camera.lookAt(.35,ty,.45);camera.updateMatrixWorld(true);
   const hp=humanPoints.map(proj);if(hp.some(p=>p.z< -1||p.z>1||Math.abs(p.x)>.92||Math.abs(p.y)>.90)){stats.patientCut++;continue;}
   const sp=sc.map(proj);if(sp.some(p=>p.z< -1||p.z>1||Math.abs(p.x)>.95||Math.abs(p.y)>.95)){stats.tvCut++;continue;}
   const sensorProjection=sensors.map(s=>s.points.map(proj));if(sensorProjection.some(ps=>ps.some(p=>p.z< -1||p.z>1||Math.abs(p.x)>.94||Math.abs(p.y)>.94))){stats.sensorCut++;continue;}
   const range=(ps,k)=>Math.max(...ps.map(p=>p[k]))-Math.min(...ps.map(p=>p[k]));
   const sensorPixels=sensorProjection.map((ps,i)=>({name:sensors[i].name,width:Math.round(range(ps,'x')*640),height:Math.round(range(ps,'y')*360)}));if(sensorPixels.some(s=>s.width<12||s.height<6)){stats.sensorTooSmall++;continue;}
   const width=range(sp,'x')*640,screenHeight=range(sp,'y')*360;
   const hpCross=poses[34].points.map(proj);const humanHeight=range(hpCross,'y')*360;
   const screenArea=Math.abs(sp.reduce((a,p,i)=>a+p.x*sp[(i+1)%4].y-p.y*sp[(i+1)%4].x,0))/2*640*360;
   const quality=.55*Math.min(screenArea/(420*236),1)+.45*Math.min(humanHeight/400,1);
   candidates.push({tv,camera:[x,height,z],target:[.35,ty,.45],fov,angle,width:Math.round(width),screenHeight:Math.round(screenHeight),humanHeight:Math.round(humanHeight),quality,sensorPixels});stats.feasible++;
  }
  if(stats.considered%600<per){report({...stats});await new Promise(r=>setTimeout(r,0));}
 }
 candidates.sort((a,b)=>b.quality-a.quality);
 // Second pass: conservative AABB ray visibility, including fixed room and TV geometry.
 const shortlist=candidates.slice(0,1500);
 for(const c of shortlist){
  const matrix=new T.Matrix4().compose(new T.Vector3(...c.tv),new T.Quaternion().setFromAxisAngle(new T.Vector3(0,1,0),c.angle),new T.Vector3(1,1,1));
  const tvBoxes=monitorBoxes.map(b=>b.clone().applyMatrix4(matrix));const origin=new T.Vector3(...c.camera);
  const visible=(target,boxes)=>{const dist=origin.distanceTo(target);ray.set(origin,target.clone().sub(origin).normalize());return !boxes.some(b=>{const p=ray.intersectBox(b,hit);return p&&origin.distanceTo(p)<dist-.09;});};
  c.sensorsVisible=sensors.map(s=>visible(s.worldCenter,[...fixedObjects.filter(f=>!inside(f.object,s.object)).map(f=>f.box),...tvBoxes]));
  // Patient occlusion approximation at every sampled pose: torso/head volumes.
  for(let i=0;i<sensors.length;i++)if(c.sensorsVisible[i])for(const p of poses){const torso=new T.Box3().setFromPoints([p.points[0],p.points[1],p.points[2]]).expandByScalar(.14);if(!visible(sensors[i].worldCenter,[torso])){c.sensorsVisible[i]=false;break;}}
  if(c.sensorsVisible.some(v=>!v)){stats.sensorOccluded++;c.score=-Infinity;continue;}
  let seen=0,n=0,minPhase=1;
  for(const p of poses){let count=0;for(const pt of p.points){if(visible(pt,[...fixed,...tvBoxes])){seen++;count++;}n++;}minPhase=Math.min(minPhase,count/p.points.length);}
  let screenSeen=0;for(const xx of [-.65,0,.65])for(const yy of [1.35,1.7,2.05])if(visible(new T.Vector3(xx,yy,.045).applyMatrix4(matrix),fixed))screenSeen++;
  c.visibility=seen/n;c.worstPoseVisibility=minPhase;c.screenVisibility=screenSeen/9;c.score=100*(.45*c.quality+.35*c.visibility+.20*c.screenVisibility);
  if(screenSeen<9)c.score-=25;if(minPhase<.55)c.score-=20;
 }
 shortlist.sort((a,b)=>b.score-a.score);
 const selected=[];for(const c of shortlist){if(!Number.isFinite(c.score))continue;if(selected.every(s=>Math.hypot(c.tv[0]-s.tv[0],c.tv[2]-s.tv[2])>.3||Math.hypot(c.camera[0]-s.camera[0],c.camera[2]-s.camera[2])>.45||Math.abs(c.camera[1]-s.camera[1])>.3)){selected.push(c);if(selected.length===30)break;}}
 return {method:'Finite grid framing + conservative AABB collision and visibility; not physical simulation or model inference',grid:{xs,zs,hs,fovs,targetHeights:tys,tvs,aspect:'16:9',poseStepSeconds:.5,poseCount:poses.length,cameraFootprintRadius:.12,collisionSubjectMargin:.12,framingMarginNDC:{x:.08,y:.10}},stats,sensorRequirement:{allThreeInstalledPositions:true,minimumWidthPx:12,minimumHeightPx:6,includesPatientTorsoOcclusion:true,interpretation:"Position visibility only; not close-up sensor identification; two ToF units and one mmWave are not three sensor types"},shortlisted:shortlist.length,selected,top:shortlist.slice(0,100),limitations:['AABB checks are conservative; thin/angled geometry may be over-rejected.','Sampled skeleton visibility does not include self-occlusion or actual pixel text legibility.','TV visibility sampled at nine points; person overlapping TV is checked in final renders.','Dimensions and motion are existing scene estimates, not surveyed site measurements.','Camera support approximated as 24cm vertical column, not a full tripod.','Best within this finite grid and scoring function, not a continuous global optimum.']};
}
