import {useEffect,useRef} from 'react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {CameraCalibration} from './types';

export function RelativeRig({cameras,unit}:{cameras:CameraCalibration[];unit:string}) {
  const host=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!host.current||cameras.length!==2)return;
    let renderer:THREE.WebGLRenderer;
    try{renderer=new THREE.WebGLRenderer({antialias:false,powerPreference:'high-performance'});}catch{return;}
    const node=host.current;renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));node.append(renderer.domElement);
    const scene=new THREE.Scene();scene.background=new THREE.Color('#0a1423');
    const camera=new THREE.PerspectiveCamera(45,1,.001,1000);
    const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
    const roots=new THREE.Group();roots.rotation.x=Math.PI;scene.add(roots);
    const centers=cameras.map(c=>{
      const r=new THREE.Matrix3().set(...c.rotation.flat() as [number,number,number,number,number,number,number,number,number]).transpose();
      return new THREE.Vector3(...c.translation).negate().applyMatrix3(r);
    });
    const length=centers[0].distanceTo(centers[1]);
    roots.add(new THREE.AxesHelper(length*.4));
    cameras.forEach((c,i)=>{
      const r=new THREE.Matrix3().set(...c.rotation.flat() as [number,number,number,number,number,number,number,number,number]).transpose();
      const direction=new THREE.Vector3(0,0,1).applyMatrix3(r);const color=i===0?0x37d2d0:0xf6ad55;
      const marker=new THREE.Mesh(new THREE.SphereGeometry(length*.035,10,8),new THREE.MeshBasicMaterial({color}));marker.position.copy(centers[i]);roots.add(marker);
      roots.add(new THREE.ArrowHelper(direction,centers[i],length*.65,color));
      const k=new THREE.Matrix3().set(...c.intrinsic_matrix.flat() as [number,number,number,number,number,number,number,number,number]).invert();
      const [w,h]=c.image_resolution;
      const corners=[[0,0],[w,0],[w,h],[0,h]].map(([x,y])=>new THREE.Vector3(x,y,1).applyMatrix3(k).multiplyScalar(length*.5).applyMatrix3(r).add(centers[i]));
      const points=corners.flatMap((p,j)=>[centers[i],p,p,corners[(j+1)%4]]);
      roots.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color})));
    });
    roots.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(centers),new THREE.LineBasicMaterial({color:0xd5e5fc})));
    const center=centers[0].clone().add(centers[1]).multiplyScalar(.5);center.applyAxisAngle(new THREE.Vector3(1,0,0),Math.PI);
    controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(length*1.2,length*1.4,length*2.2));controls.update();
    const resize=new ResizeObserver(()=>{const {width,height}=node.getBoundingClientRect();renderer.setSize(width,height);camera.aspect=width/Math.max(1,height);camera.updateProjectionMatrix();});resize.observe(node);
    let frame=0;const draw=()=>{frame=requestAnimationFrame(draw);controls.update();renderer.render(scene,camera);};draw();
    return()=>{cancelAnimationFrame(frame);resize.disconnect();controls.dispose();scene.traverse(obj=>{if(obj instanceof THREE.Mesh||obj instanceof THREE.Line){obj.geometry.dispose();(Array.isArray(obj.material)?obj.material:[obj.material]).forEach(m=>m.dispose());}});renderer.dispose();renderer.domElement.remove();};
  },[cameras]);
  return <div><p className="muted">자동 추정 카메라 배치 · A 청록 / B 주황 · {unit==='m'?'실측 기준선 단위 m':'기준선=1 상대 단위'} · 회전/확대 가능</p><div ref={host} style={{height:230,width:'100%'}}/><p className="muted">카메라 좌표계의 상대 배치입니다. 방/침대 위치는 추정하지 않습니다. 픽셀 시야는 알려진 내부값에서 계산합니다.</p></div>;
}
