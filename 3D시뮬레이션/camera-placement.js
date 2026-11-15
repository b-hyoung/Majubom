import * as T from 'three';
export function mountCameraPlacement({scene,camera,controls,view,canvas,getShot,shots,onEdit,bed,headTof,footTof,radar}){
 if(new URLSearchParams(location.search).has('frame'))return null;
 const $=s=>document.querySelector(s);
 let mode='overview',dirty=false,drag=null,lastPreview=0;
 const initial=shots[getShot()??0];const position=new T.Vector3(...initial.camera),target=new T.Vector3(...initial.target);let fov=initial.fov;
 const filming=new T.PerspectiveCamera(fov,16/9,.05,80);
 const overviewPreview=new T.PerspectiveCamera(48,16/9,.05,80);overviewPreview.position.set(-5.8,5.8,6.4);overviewPreview.lookAt(.2,1,.15);overviewPreview.updateMatrixWorld(true);
 const orange=new T.MeshStandardMaterial({color:0xc96732,roughness:.6}),dark=new T.MeshStandardMaterial({color:0x26352e}),metal=new T.MeshStandardMaterial({color:0xb3beb4,metalness:.6,roughness:.3});
 const rig=new T.Group();rig.name='filming-camera-rig';scene.add(rig);
 const body=new T.Group();rig.add(body);
 function mesh(geo,mat,parent=body){const o=new T.Mesh(geo,mat);parent.add(o);return o;}
 mesh(new T.BoxGeometry(.34,.24,.24),orange).position.z=.14;
 const lens=mesh(new T.CylinderGeometry(.085,.105,.18,24),dark);lens.rotation.x=Math.PI/2;lens.position.z=-.04;
 mesh(new T.BoxGeometry(.19,.06,.09),dark).position.set(0,.16,.14);
 const glass=mesh(new T.CircleGeometry(.07,24),new T.MeshBasicMaterial({color:0x74afb1,side:T.DoubleSide}));glass.position.z=-.135;
 const base=new T.Group();scene.add(base);base.name='filming-tripod';
 const pole=mesh(new T.CylinderGeometry(.018,.024,1,10),metal,base);
 const legs=[];for(let i=0;i<3;i++)legs.push(mesh(new T.CylinderGeometry(.013,.018,1,8),dark,base));
 const ring=mesh(new T.RingGeometry(.16,.21,40),new T.MeshBasicMaterial({color:0xd87332,side:T.DoubleSide}),base);ring.rotation.x=-Math.PI/2;ring.position.y=.035;
 const coneCamera=new T.PerspectiveCamera(fov,16/9,.12,2.5),helper=new T.CameraHelper(coneCamera);helper.name='filming-frustum';helper.material.transparent=true;helper.material.opacity=.36;scene.add(helper);
 const guide=document.createElement('div');guide.className='camera-position-label';guide.textContent='촬영 카메라 · 클릭해서 보기';view.append(guide);
 const buttons=document.createElement('div');buttons.className='camera-mode-switch';buttons.innerHTML='<button id="cameraPlacementMode" type="button">카메라 위치 보기</button><button id="cameraFilmMode" type="button">이 카메라로 보기</button>';$('#reviewTools').prepend(buttons);
 const panel=document.createElement('section');panel.id='cameraPlacementPanel';panel.innerHTML=`<h3>촬영 카메라 배치</h3><p class="small">주황색 카메라를 끌어 위치 이동 · 클릭하면 촬영 화면<br>빈 공간 드래그는 공간 회전입니다.</p><div id="filmingPreview"><canvas aria-label="촬영 카메라 실시간 미리보기"></canvas><button id="openCameraPreview" type="button" aria-label="촬영 화면 크게 보기">촬영 화면 크게 보기 ↗</button></div><p id="cameraFrameStatus" aria-live="polite"></p><p id="cameraPlacementStatus" class="small"></p><div class="camera-fields">${[['x','좌우 X',-2.3,2.3,.05],['z','앞뒤 Z',-4.2,2.6,.05],['y','렌즈 높이',.5,2.6,.05],['fov','수직 화각',30,100,1],['aim','시선 높이',.2,2.7,.05]].map(([id,label,min,max,step])=>`<label for="cameraRig-${id}">${label}</label><input id="cameraRig-${id}" type="range" min="${min}" max="${max}" step="${step}"><output id="cameraRigValue-${id}"></output>`).join('')}</div><p id="cameraBedOffset" class="small"></p><div class="camera-panel-actions"><button id="cameraReset" type="button">선택 구도로 복원</button><label><input type="checkbox" id="cameraFrustum" checked> 촬영 범위</label></div><p class="small">이동 후에는 센서 조건·가구 간섭을 다시 확인하세요. 이전 구도의 검토 체크는 변경된 위치에 적용하지 않습니다.</p>`;
 $('aside').prepend(panel);
 const previewCard=document.createElement('section');previewCard.id='cameraPreviewCard';previewCard.innerHTML='<div class="preview-heading"><h3>촬영 카메라 미리보기</h3><span>실시간</span></div>';
 previewCard.append($('#filmingPreview'),$('#cameraFrameStatus'));
 const sidebarControls=document.createElement('div');sidebarControls.id='cameraSidebarControls';while($('aside').firstChild)sidebarControls.append($('aside').firstChild);$('aside').append(previewCard,sidebarControls);

 const overviewPin=document.createElement('div');overviewPin.id='overviewCameraPin';overviewPin.innerHTML='<span>촬영 카메라 위치</span>';overviewPin.hidden=true;$('#filmingPreview').append(overviewPin);
 const previewCanvas=previewCard.querySelector('canvas');const preview=new T.WebGLRenderer({canvas:previewCanvas,antialias:true});preview.setPixelRatio(Math.min(devicePixelRatio,2));preview.setSize(640,360,false);preview.toneMapping=T.ACESFilmicToneMapping;preview.toneMappingExposure=1.2;preview.shadowMap.enabled=false;
 const sensors=[['머리맡 ToF',headTof,new T.Vector3(.035,1.53,.064)],['발치 ToF',footTof,new T.Vector3(.035,1.53,.064)],['mmWave',radar,new T.Vector3(0,.012,0)]];
 let selected=getShot()??0;
 function sync(){filming.position.copy(position);filming.fov=fov;filming.lookAt(target);filming.updateProjectionMatrix();filming.updateMatrixWorld(true);rig.position.copy(position);body.quaternion.copy(filming.quaternion);base.position.set(position.x,0,position.z);pole.position.y=position.y/2;pole.scale.y=position.y;
  const top=new T.Vector3(0,Math.min(.8,position.y*.6),0);for(let i=0;i<3;i++){const end=new T.Vector3(Math.cos(i*Math.PI*2/3)*.28,.03,Math.sin(i*Math.PI*2/3)*.28),d=end.clone().sub(top);legs[i].position.copy(top).lerp(end,.5);legs[i].scale.y=d.length();legs[i].quaternion.setFromUnitVectors(new T.Vector3(0,1,0),d.normalize());}
  coneCamera.position.copy(position);coneCamera.quaternion.copy(filming.quaternion);coneCamera.fov=fov;coneCamera.updateProjectionMatrix();coneCamera.updateMatrixWorld(true);helper.update();
  for(const id of ['x','y','z','fov','aim']){const value=id==='fov'?fov:id==='aim'?target.y:position[id];$('#cameraRig-'+id).value=value;$('#cameraRigValue-'+id).textContent=id==='fov'?value.toFixed(0)+'°':value.toFixed(2)+'m';}
  const dx=position.x-bed.position.x,dz=position.z-bed.position.z;$('#cameraBedOffset').textContent=`침대 중심 기준 ${dx<0?'왼쪽':'오른쪽'} ${Math.abs(dx).toFixed(2)}m · ${dz<0?'머리 방향':'발치 방향'} ${Math.abs(dz).toFixed(2)}m · 렌즈 높이 ${position.y.toFixed(2)}m`;
  $('#cameraPlacementStatus').textContent=dirty?'사용자 이동 · 기존 센서 조건 결과는 미적용':`선택 구도 ${selected+1}의 카메라 위치`;
  if(mode==='film'){camera.position.copy(position);controls.target.copy(target);camera.fov=fov;camera.updateProjectionMatrix();camera.lookAt(target);camera.updateMatrixWorld(true);}lastPreview=0;
 }
 function setMode(next){mode=next;controls.enabled=next==='overview';controls.maxPolarAngle=Math.PI*.495;document.body.classList.toggle('camera-overview',next==='overview');$('#cameraPlacementMode').setAttribute('aria-pressed',String(next==='overview'));$('#cameraFilmMode').setAttribute('aria-pressed',String(next==='film'));
  $('#cameraPreviewCard h3').textContent=next==='film'?'전체 공간 · 카메라 위치':'촬영 카메라 미리보기';$('#openCameraPreview').textContent=next==='film'?'전체 배치 크게 보기 ↗':'촬영 화면 크게 보기 ↗';$('#openCameraPreview').setAttribute('aria-label',next==='film'?'전체 배치 크게 보기':'촬영 화면 크게 보기');previewCanvas.setAttribute('aria-label',next==='film'?'전체 공간과 촬영 카메라 위치':'촬영 카메라 실시간 미리보기');lastPreview=0;
  if(next==='overview'){camera.position.set(-5.8,5.8,6.4);controls.target.set(.2,1,.15);camera.fov=48;camera.updateProjectionMatrix();controls.update();}else sync();
 }
 function edit(){dirty=true;onEdit();sync();}
 for(const id of ['x','y','z','fov','aim'])$('#cameraRig-'+id).oninput=e=>{const value=+e.target.value;if(id==='fov')fov=value;else if(id==='aim')target.y=value;else position[id]=value;edit();};
 $('#cameraPlacementMode').onclick=()=>setMode('overview');$('#cameraFilmMode').onclick=()=>setMode('film');$('#openCameraPreview').onclick=()=>setMode(mode==='film'?'overview':'film');
 $('#cameraReset').onclick=()=>{window.room.setShot(selected);};
 document.addEventListener('room-shot-change',()=>{const i=getShot();if(i===null)return;selected=i;const s=shots[i];position.fromArray(s.camera);target.fromArray(s.target);fov=s.fov;dirty=false;sync();if(mode==='overview')setMode('overview');});
 const raycaster=new T.Raycaster(),pointer=new T.Vector2(),plane=new T.Plane(),intersection=new T.Vector3();
 function ray(e){const r=canvas.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);raycaster.setFromCamera(pointer,camera);}
 canvas.addEventListener('pointerdown',e=>{if(mode!=='overview'||e.button!==0)return;ray(e);scene.updateMatrixWorld(true);const hits=raycaster.intersectObjects([rig,base],true);if(!hits.length)return;e.preventDefault();e.stopImmediatePropagation();controls.enabled=false;plane.setFromNormalAndCoplanarPoint(new T.Vector3(0,1,0),hits[0].point);if(!raycaster.ray.intersectPlane(plane,intersection)){controls.enabled=true;return;}drag={id:e.pointerId,start:intersection.clone(),position:position.clone(),x:e.clientX,y:e.clientY,moved:false};canvas.setPointerCapture(e.pointerId);},true);
 canvas.addEventListener('pointermove',e=>{if(!drag)return;e.preventDefault();e.stopImmediatePropagation();if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<4&&!drag.moved)return;drag.moved=true;ray(e);if(raycaster.ray.intersectPlane(plane,intersection)){position.x=T.MathUtils.clamp(drag.position.x+intersection.x-drag.start.x,-2.3,2.3);position.z=T.MathUtils.clamp(drag.position.z+intersection.z-drag.start.z,-4.2,2.6);edit();}},true);
 function release(e,cancel=false){if(!drag)return;e.preventDefault();e.stopImmediatePropagation();const click=!drag.moved;drag=null;if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);controls.enabled=mode==='overview';if(click&&!cancel)setMode('film');}
 canvas.addEventListener('pointerup',e=>release(e),true);canvas.addEventListener('pointercancel',e=>release(e,true),true);
 sync();setMode('overview');
 return {get isOverview(){return mode==='overview';},get active(){return true;},get mode(){return mode;},get position(){return position.toArray();},get target(){return target.toArray();},filming,rig,setMode,
 update(now){const show=mode==='overview';rig.visible=base.visible=show;helper.visible=show&&$('#cameraFrustum').checked;
  const p=position.clone().project(camera);guide.hidden=!show||Math.abs(p.x)>1||Math.abs(p.y)>1||Math.abs(p.z)>1;guide.style.left=(p.x*.5+.5)*view.clientWidth+'px';guide.style.top=(-p.y*.5+.5)*view.clientHeight+'px';
  if(show){overviewPreview.position.copy(camera.position);overviewPreview.quaternion.copy(camera.quaternion);overviewPreview.fov=camera.fov;overviewPreview.updateProjectionMatrix();overviewPreview.updateMatrixWorld(true);}
  overviewPin.hidden=show;const pin=position.clone().project(overviewPreview);overviewPin.style.left=(pin.x*.5+.5)*100+'%';overviewPin.style.top=(-pin.y*.5+.5)*100+'%';
  if(now-lastPreview>100){rig.visible=base.visible=!show;helper.visible=!show&&$('#cameraFrustum').checked;preview.render(scene,show?filming:overviewPreview);rig.visible=base.visible=show;helper.visible=show&&$('#cameraFrustum').checked;lastPreview=now;
   const inFrame=sensors.map(([name,o,v])=>{const p=o.localToWorld(v.clone()).project(filming);return {name,ok:Math.abs(p.x)<.97&&Math.abs(p.y)<.97&&Math.abs(p.z)<1};});$('#cameraFrameStatus').textContent=`프레임 안 센서 ${inFrame.filter(s=>s.ok).length}/3 · ${inFrame.filter(s=>!s.ok).map(s=>s.name+' 밖').join(', ')||'가림 여부는 영상 확인'}`;
  }
 }};
}
