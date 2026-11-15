import * as T from 'three';
import {shots} from './shots.js';
import {mountReviewHeader} from './review-header.js';
import {mountCameraPlacement} from './camera-placement.js';
import {createScenario} from './scenario.js';
import {OrbitControls} from './vendor/OrbitControls.js';
import {RoundedBoxGeometry} from './vendor/RoundedBoxGeometry.js';
const $=s=>document.querySelector(s),canvas=$('#canvas'),view=$('#viewport');
const renderer=new T.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.shadowMap.enabled=true;renderer.shadowMap.type=T.PCFSoftShadowMap;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.2;
const scene=new T.Scene();scene.background=new T.Color('#e7ece4');
const camera=new T.PerspectiveCamera(44,1,.05,80);
const controls=new OrbitControls(camera,canvas);controls.enableDamping=true;controls.minDistance=.6;controls.maxDistance=18;controls.maxPolarAngle=Math.PI*.495;
scene.add(new T.HemisphereLight(0xf4f8ff,0xa29c83,2));
const key=new T.DirectionalLight(0xfff6e4,3);key.position.set(-3,8,5);key.castShadow=true;key.shadow.mapSize.set(2048,2048);Object.assign(key.shadow.camera,{left:-7,right:7,top:7,bottom:-7,near:.1,far:24});key.shadow.normalBias=.025;key.shadow.bias=-.0001;scene.add(key);
const fill=new T.DirectionalLight(0xdceaff,1.2);fill.position.set(4,4,-3);scene.add(fill);
const mat=(color,roughness=.7,metalness=0)=>new T.MeshStandardMaterial({color,roughness,metalness});
const M={white:mat('#e5e5d8'),sheet:mat('#faf9ed'),dark:mat('#272d2b'),metal:mat('#aab3af',.3,.65),oak:mat('#c9ad86'),wall:mat('#e6e8df'),blue:mat('#72858a'),green:mat('#36775e'),black:mat('#182422')};
function box(w,h,d,x,y,z,m=M.white,parent=scene,r=0){const o=new T.Mesh(r?new RoundedBoxGeometry(w,h,d,3,r):new T.BoxGeometry(w,h,d),m);o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;}
function cyl(r,h,x,y,z,m=M.metal,parent=scene){const o=new T.Mesh(new T.CylinderGeometry(r,r,h,18),m);o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;}
function rod(a,b,r=.018,m=M.metal,parent=scene){a=new T.Vector3(...a);b=new T.Vector3(...b);const o=cyl(r,a.distanceTo(b),0,0,0,m,parent);o.position.copy(a.clone().add(b).multiplyScalar(.5));o.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),b.sub(a).normalize());return o;}
function tex(draw,w=1024,h=512){const c=document.createElement('canvas');c.width=w;c.height=h;draw(c.getContext('2d'),w,h);const t=new T.CanvasTexture(c);t.colorSpace=T.SRGBColorSpace;t.anisotropy=8;return t;}
function plane(w,h,x,y,z,texture,parent=scene){const o=new T.Mesh(new T.PlaneGeometry(w,h),new T.MeshBasicMaterial({map:texture,toneMapped:false}));o.position.set(x,y,z);parent.add(o);return o;}
const floorTex=tex((c,w,h)=>{c.fillStyle='#b9b9ac';c.fillRect(0,0,w,h);for(let i=0;i<12;i++){c.fillStyle=i%2?'#c9c9bf':'#c4c5bb';c.fillRect(0,i*h/12,w,h/12-2);for(let j=0;j<80;j++){c.fillStyle=`rgba(80,87,77,${.02+(j%5)*.01})`;c.fillRect((j*83+i*31)%w,i*h/12+(j*7)%(h/12),120,1);}}},1024,1024);floorTex.wrapS=floorTex.wrapT=T.RepeatWrapping;floorTex.repeat.set(2,2);
box(5.2,.15,7.3,0,-.09,-.80,mat('#899581'));const floor=box(5.05,.025,7.15,0,0,-.80,new T.MeshStandardMaterial({map:floorTex,roughness:.9}));
box(80,.1,80,0,-.23,0,mat('#e0e6db'));
// Actual video arrangement: worktops on both sides, mobile monitor left, bed right, glass partition behind.
const sideWalls=new T.Group();scene.add(sideWalls);sideWalls.visible=false;
for(const x of [-2.55,2.55]){box(.1,2.75,7.2,x,1.375,-.80,M.wall,sideWalls);box(.12,.82,7.15,x,.45,-.80,M.oak,sideWalls);box(.14,.04,7.15,x,.87,-.80,M.oak,sideWalls);}
const ceiling=new T.Group();scene.add(ceiling);ceiling.visible=false;box(5.1,.07,7.2,0,2.78,-.80,M.white,ceiling);
for(const x of [-1.5,0,1.5])for(const z of [-1.8,0,1.8]){const lamp=cyl(.12,.025,x,2.73,z,new T.MeshStandardMaterial({color:'#fff8e3',emissive:'#fff6dc',emissiveIntensity:1}),ceiling);}
const glass=new T.MeshStandardMaterial({color:'#acb9b4',transparent:true,opacity:.24,roughness:.2,side:T.DoubleSide,depthWrite:false});
for(const x of [-2.5,-1.65,-.8,.15,1.15,2.5])box(.045,2.65,.055,x,1.325,-3.93,M.dark);
for(const y of [.04,2.2,2.65])box(5.05,.045,.055,0,y,-3.93,M.dark);
for(const [x,w] of [[-2.075,.8],[-1.225,.8],[-.325,.9],[.65,.95],[1.825,1.3]])box(w,2.12,.015,x,1.12,-3.93,glass);
box(.93,1.5,.02,.65,.82,-3.91,new T.MeshStandardMaterial({color:'#aab3af',transparent:true,opacity:.65}));rod([.99,1,-3.87],[.99,1.25,-3.87],.014);box(.17,.2,.008,.65,1.6,-3.89,M.white);
// User-confirmed second glass line: side panels between monitor and head ToF,
// with a passage in the middle; closed glass doors remain farther behind.
const frontGlass=new T.Group();frontGlass.name='front-side-glass-partitions';scene.add(frontGlass);
for(const [center,width] of [[-2.0,1.0],[2.23,.54]]){
 const z=-1.44;
 box(width,2.62,.018,center,1.33,z,glass,frontGlass);
 for(const x of [center-width/2,center+width/2])box(.045,2.68,.06,x,1.34,z,M.dark,frontGlass);
 for(const y of [.04,1.96,2.66])box(width,.045,.06,center,y,z,M.dark,frontGlass);
}
// Raised sill and parallel runners mark the sliding glass doorway.
for(const z of [-1.44,-3.93]){
 box(5.05,.035,.15,0,.027,z,M.metal);
 for(const dz of [-.055,.055])box(5.05,.012,.012,0,.049,z+dz,M.dark);
}
for(const x of [-1.55,1.98])rod([x,1.05,-1.39],[x,1.29,-1.39],.012,M.metal);
// Rear corridor beyond the partition gives the glass depth.
box(5,.04,1,0,-.01,-4.3,M.wall);box(5,2.7,.07,0,1.35,-4.8,mat('#d1d7ce'));
function desk(x,z,w,d,wood=false){box(w,.045,d,x,.75,z,wood?M.oak:M.white,scene,.012);for(const dx of [-w/2+.05,w/2-.05])for(const dz of [-d/2+.04,d/2-.04])box(.04,.72,.04,x+dx,.36,z+dz,M.metal);}
// Right wall: low storage cabinets with laptops on top, not an open-legged desk.
box(.75,.045,3.0,2.12,.75,.7,M.white,scene,.012);
box(.66,.08,2.9,2.14,.055,.7,M.dark);
for(let i=0;i<4;i++){
 const z=-.425+i*.75;
 box(.73,.64,.735,2.12,.41,z,M.white,scene,.008);
 box(.018,.59,.70,1.744,.415,z,mat('#e0e3dc'),scene,.006);
 box(.024,.018,.12,1.725,.60,z+.22,M.metal,scene,.004);
}
// User-confirmed left wall: three separate tables in a straight row, large / small / large, with visible gaps.
// Long dimensions are parallel to the left wall; individual sizes remain approximate.
desk(-2.0,-.20,.9,1.30,true);
desk(-2.0,.90,.9,.55,true);
desk(-2.0,2.0,.9,1.30,true);
function laptop(x,z,angle=0){const g=new T.Group();g.position.set(x,.79,z);g.rotation.y=angle;scene.add(g);box(.35,.018,.25,0,0,0,M.dark,g,.008);const screen=box(.35,.23,.015,0,.12,-.11,M.dark,g,.007);screen.rotation.x=-.18;const screenTex=tex((c,w,h)=>{c.fillStyle='#233b42';c.fillRect(0,0,w,h);c.fillStyle='#619a9d';for(let j=0;j<6;j++)c.fillRect(40,40+j*60,w*(.2+j%3*.2),14);});const s=plane(.32,.20,0,.12,-.095,screenTex,g);s.rotation.x=-.18;for(let i=0;i<5;i++)box(.28,.002,.008,0,.012,-.06+i*.025,M.metal,g);}
laptop(-2,1.9,.45);
// Additional table behind the sill on the Raspberry Pi / right-hand side.
desk(2.10,-2.35,.78,1.35,false);
const piPC=new T.Group();piPC.name='raspberry-connected-pc';piPC.position.set(2.10,.78,-2.35);piPC.rotation.y=-Math.PI/2;scene.add(piPC);
box(.23,.018,.17,0,.01,0,M.dark,piPC,.007);
box(.035,.15,.035,0,.09,-.025,M.metal,piPC);
box(.53,.32,.035,0,.28,-.025,M.dark,piPC,.012);
plane(.50,.29,0,.28,-.005,tex((c,w,h)=>{c.fillStyle='#243b38';c.fillRect(0,0,w,h);c.fillStyle='#abc7ba';c.font='32px sans-serif';c.fillText('Raspberry Pi',32,52);c.fillStyle='#619483';for(let i=0;i<5;i++)c.fillRect(32,95+i*62,w*(.3+(i%3)*.18),18);}),piPC);
box(.34,.016,.12,0,.025,.20,M.dark,piPC,.006);
box(.18,.38,.34,2.12,.22,-2.74,M.dark,scene,.012);
// Visible schematic cable connecting Pi and this PC; connector/protocol unconfirmed.
const piPcRoute=new T.CatmullRomCurve3([
 new T.Vector3(2.15,.81,-.57),new T.Vector3(2.38,.79,-.65),
 new T.Vector3(2.47,.25,-.75),new T.Vector3(2.47,.10,-.94),
 new T.Vector3(2.47,.08,-1.18),new T.Vector3(2.43,.12,-2.72),
 new T.Vector3(2.21,.32,-2.74)
]);
const piPcCable=new T.Mesh(new T.TubeGeometry(piPcRoute,64,.007,8,false),mat('#3d6c84'));
piPcCable.name='pi-to-pc-cable';scene.add(piPcCable);

// Independent workstation in the middle room, beyond the sliding-door sill.
desk(-2.0,-2.7,.9,1.9,true);
const pc=new T.Group();pc.position.set(-2.02,.78,-2.7);pc.rotation.y=Math.PI/2;scene.add(pc);
box(.22,.018,.18,0,.01,0,M.dark,pc,.008);box(.035,.15,.035,0,.09,-.025,M.metal,pc);
box(.53,.32,.035,0,.28,-.025,M.dark,pc,.012);
plane(.50,.29,0,.28,-.005,tex((c,w,h)=>{c.fillStyle='#284241';c.fillRect(0,0,w,h);c.fillStyle='#799d93';c.fillRect(35,35,w*.65,22);c.fillStyle='#44665d';for(let i=0;i<5;i++)c.fillRect(35,100+i*55,w*.8,30);}),pc);
box(.35,.015,.12,0,.025,.20,M.dark,pc,.006);
box(.18,.40,.35,-2.0,.23,-3.35,M.dark,scene,.012);
// Ergonomic office chair: shaped mesh back, upholstered seat, armrests and five dual casters.
function chair(x,z,ang){
 const g=new T.Group();g.name='office-chair';g.position.set(x,0,z);g.rotation.y=ang;scene.add(g);
 const frame=mat('#353e3c',.55),fabric=mat('#424e4c',.95),rubber=mat('#242b2a',.95);
 const cushion=box(.49,.085,.46,0,.475,.015,fabric,g,.04);cushion.rotation.x=-.035;
 box(.43,.035,.39,0,.42,0,frame,g,.016);
 box(.17,.06,.19,0,.375,-.015,frame,g,.012);
 cyl(.028,.20,0,.27,0,M.metal,g);cyl(.044,.12,0,.17,0,frame,g);
 // Tilted, curved backrest with a visible molded perimeter.
 const back=new T.Group();back.position.set(0,.57,-.20);back.rotation.x=-.12;g.add(back);
 const positions=[],uvs=[],indices=[],cols=20,rows=24;
 function backPoint(u,v){const width=.205+.025*Math.sin(v*Math.PI);const xx=(u-.5)*2*width;return [xx,v*.45,.025+Math.pow(xx/.23,2)*.035+Math.sin(v*Math.PI)*.018];}
 for(let j=0;j<=rows;j++)for(let i=0;i<=cols;i++){positions.push(...backPoint(i/cols,j/rows));uvs.push(i/cols,j/rows);}
 for(let j=0;j<rows;j++)for(let i=0;i<cols;i++){const k=j*(cols+1)+i;indices.push(k,k+1,k+cols+1,k+1,k+cols+2,k+cols+1);}
 const geo=new T.BufferGeometry();geo.setAttribute('position',new T.Float32BufferAttribute(positions,3));geo.setAttribute('uv',new T.Float32BufferAttribute(uvs,2));geo.setIndex(indices);geo.computeVertexNormals();
 const meshTex=tex((c,w,h)=>{c.fillStyle='#455250';c.fillRect(0,0,w,h);c.fillStyle='#303b39';for(let y=0;y<h;y+=8)for(let x=0;x<w;x+=7)c.fillRect(x,y,3,5);},128,128);
 meshTex.wrapS=meshTex.wrapT=T.RepeatWrapping;meshTex.repeat.set(3,3);
 const mesh=new T.Mesh(geo,new T.MeshStandardMaterial({map:meshTex,roughness:.95,side:T.DoubleSide}));mesh.castShadow=true;mesh.receiveShadow=true;back.add(mesh);
 const border=[];for(let i=0;i<=20;i++)border.push(new T.Vector3(...backPoint(i/20,0)));for(let i=1;i<=24;i++)border.push(new T.Vector3(...backPoint(1,i/24)));for(let i=19;i>=0;i--)border.push(new T.Vector3(...backPoint(i/20,1)));for(let i=23;i>=0;i--)border.push(new T.Vector3(...backPoint(0,i/24)));
 back.add(new T.Mesh(new T.TubeGeometry(new T.CatmullRomCurve3(border,true),100,.016,8,true),frame));
 rod([0,.39,-.12],[0,.72,-.26],.025,frame,g);
 box(.30,.055,.035,0,.67,-.235,frame,g,.015);
 for(const side of [-1,1]){
  rod([side*.19,.41,-.07],[side*.27,.46,-.08],.018,frame,g);
  rod([side*.27,.46,-.08],[side*.27,.66,-.08],.018,frame,g);
  box(.065,.04,.28,side*.27,.68,.01,frame,g,.018);
 }
 rod([.11,.385,0],[.25,.385,.08],.008,M.metal,g);box(.07,.023,.035,.25,.385,.08,frame,g,.01);
 for(let i=0;i<5;i++){
  const a=i*Math.PI*2/5,dx=Math.cos(a)*.31,dz=Math.sin(a)*.31;
  rod([0,.145,0],[dx,.10,dz],.025,frame,g);
  const caster=new T.Group();caster.position.set(dx,.05,dz);caster.rotation.y=-a;g.add(caster);
  cyl(.012,.055,0,.045,0,M.metal,caster);box(.054,.025,.035,0,.023,0,frame,caster,.007);
  for(const sx of [-.025,.025]){const wheel=cyl(.045,.018,sx,0,.012,rubber,caster);wheel.rotation.z=Math.PI/2;const hub=cyl(.018,.019,sx,0,.012,frame,caster);hub.rotation.z=Math.PI/2;}
 }
 return g;
}
chair(-1.46,1.95,-Math.PI/2);chair(-1.46,-2.7,-Math.PI/2);
// Monitor on a wheeled stand, including rear body and phone shelf.
const monitor=new T.Group();monitor.position.set(-.85,0,-1.65);scene.add(monitor);
for(const x of [-.5,.5]){box(.045,1.32,.055,x,.73,0,M.dark,monitor);rod([x,.1,-.33],[x,.1,.4],.025,M.dark,monitor);for(const z of [-.33,.4]){const wheel=cyl(.045,.035,x,.05,z,M.dark,monitor);wheel.rotation.z=Math.PI/2;}}
box(1.2,.05,.06,0,.13,.15,M.dark,monitor);box(1.56,.90,.075,0,1.7,0,M.dark,monitor,.018);
const loader=new T.TextureLoader();const dashboard=loader.load('assets/dashboard.png');dashboard.colorSpace=T.SRGBColorSpace;const monitorScreen=plane(1.49,.835,0,1.7,.041,dashboard,monitor);
box(.52,.025,.3,0,.8,.15,M.dark,monitor);box(.075,.15,.012,0,.89,.16,M.black,monitor,.008);const phoneScreen=plane(.064,.127,0,.894,.168,tex((c,w,h)=>{c.fillStyle='#eff4f0';c.fillRect(0,0,w,h);c.fillStyle='#63a28a';c.fillRect(30,40,w-60,h*.5);}),monitor);
// Small cabinet, PC tower and everyday lab objects.
box(.48,.69,.55,-1.72,.35,-.35,M.white,scene,.012);for(let y=.17;y<.7;y+=.23){box(.44,.008,.01,-1.72,y,-.068,M.metal);box(.12,.018,.02,-1.72,y+.07,-.05,M.metal);}box(.2,.42,.4,-1.72,.92,-.35,M.white,scene,.015);
// Raspberry Pi at the head-end of the cabinet. Power/LAN routing is schematic.
const pi=new T.Group();pi.position.set(2.1,.793,-.57);scene.add(pi);
box(.12,.025,.09,0,0,0,M.dark,pi,.007);
box(.085,.005,.056,0,.016,0,M.green,pi);
box(.023,.018,.02,-.045,.014,0,M.metal,pi);
box(.02,.012,.018,-.045,.012,.025,M.metal,pi);
box(.023,.013,.023,.008,.025,0,M.metal,pi);
for(const [offset,color] of [[0,'#4c8198'],[.03,'#343a37']]){
 const path=new T.CatmullRomCurve3([new T.Vector3(2.05,.81,-.57+offset),new T.Vector3(2.23,.81,-.67+offset),new T.Vector3(2.43,.80,-.70+offset),new T.Vector3(2.48,.38,-.70+offset)]);
 scene.add(new T.Mesh(new T.TubeGeometry(path,24,.004,6,false),mat(color)));
}for(let i=0;i<4;i++)box(.04,.22,.16,-2.0,.9,-.4+i*.16,mat(['#b7bcae','#a7b3b2','#ced2c9','#697b73'][i]));
// Parametric hospital bed. Mattress surface is 0.70 m (recent repository measurement).
const bed=new T.Group();bed.name='hospital-bed';bed.position.set(1.28,0,.45);scene.add(bed);
box(.95,.15,2,0,.625,0,M.sheet,bed,.045);box(1.01,.09,2.08,0,.50,0,M.white,bed,.025);box(.54,.11,1.35,0,.25,0,M.white,bed,.03);
for(const x of [-.39,.39])for(const z of [-.78,.78]){cyl(.035,.28,x,.25,z,M.metal,bed);const wh=cyl(.075,.055,x,.085,z,M.dark,bed);wh.rotation.z=Math.PI/2;}
// Molded boards built around open handholds.
for(const z of [-1.055,1.055]){box(1.04,.26,.07,0,.72,z,M.white,bed,.045);box(.75,.075,.07,0,.98,z,M.white,bed,.025);for(const x of [-.46,0,.46])box(.11,.2,.07,x,.90,z,M.white,bed,.035);for(const x of [-.42,.42])rod([x,.43,z],[x,.74,z],.022,M.metal,bed);}
for(const x of [-.53,.53]){
 const rail=new T.Group();rail.name=x>0?'right-rail-lowered':'left-rail-raised';bed.add(rail);
 if(x>0)rail.position.y=-.38; // Cabinet side rail is lowered, below the mattress.
 rod([x,.6,-.72],[x,.6,.66],.021,M.metal,rail);
 rod([x,.91,-.72],[x,.91,.66],.025,M.metal,rail);
 for(let z=-.72;z<.7;z+=.23)rod([x,.6,z],[x,.91,z],.015,M.metal,rail);
}
box(.65,.12,.37,0,.74,-.70,M.sheet,bed,.06);
const blanket=box(.81,.035,1.23,0,.725,.30,mat('#d6ddd3'),bed,.017);for(let x=-.34;x<.4;x+=.09){const seam=rod([x,.747,-.25],[x,.747,.85],.0025,mat('#bac8bd'),bed);}
// Sensor mounts are visual placeholders based on visible video poles, not calibrated coverage.
const sensorPositions=[];
function stand(x,z,label){const g=new T.Group();g.position.set(x,0,z);scene.add(g);cyl(.022,1.42,0,.74,0,M.dark,g);cyl(.014,.22,0,1.51,0,M.metal,g);for(let i=0;i<3;i++){const a=i*2*Math.PI/3;rod([0,.17,0],[Math.cos(a)*.25,.025,Math.sin(a)*.25],.018,M.dark,g);}box(.11,.07,.09,0,1.63,0,M.dark,g,.014);box(.045,.09,.012,.035,1.51,.052,M.green,g);box(.03,.03,.012,.035,1.53,.064,M.black,g);box(.055,.12,.02,.02,1.28,.03,M.green,g);cyl(.009,.012,.02,1.26,.05,new T.MeshBasicMaterial({color:'#dd7254'}),g);const curve=new T.CatmullRomCurve3([new T.Vector3(.04,1.57,.06),new T.Vector3(.15,1.43,.08),new T.Vector3(.07,.75,.08),new T.Vector3(.12,.07,.12),new T.Vector3(.5,.025,.15)]);const cable=new T.Mesh(new T.TubeGeometry(curve,32,.005,6,false),mat('#527b98'));g.add(cable);sensorPositions.push([label,new T.Vector3(x,1.77,z)]);return g;}
const headTof=stand(1.28,-.575,'ToF 센서 · 머리맡');const footTof=stand(1.28,1.475,'ToF 센서 · 발치');
// User confirms overhead installation near bed center. Latest seungmin service: measured height 2.7m, downward 90 degrees.
const radar=new T.Group();radar.name='ceiling-mmwave';radar.position.set(1.28,2.7,.45);scene.add(radar);
box(.16,.015,.12,0,.07,0,M.metal,radar,.006);
for(const x of [-.055,.055])rod([x,.065,0],[x,.018,0],.006,M.metal,radar);
box(.105,.018,.075,0,.012,0,M.green,radar,.004);
box(.025,.007,.025,0,0,0,M.metal,radar,.002);
box(.025,.014,.018,.054,.015,0,M.metal,radar);
for(const x of [-.04,.04])for(const z of [-.025,.025])box(.007,.004,.008,x,.001,z,M.dark,radar);
rod([.065,.02,0],[.23,.07,0],.004,mat('#526779'),radar);
// User-confirmed mmWave cable routed along ceiling/wall molding to the bedside Pi.
const molding=mat('#dadfd7');
box(1.17,.035,.045,1.895,2.73,.45,molding);
box(.045,.035,1.02,2.48,2.73,-.06,molding);
box(.045,1.92,.035,2.48,1.77,-.57,molding);
let mmwaveCable;
function updateMmwaveCable(){
 if(mmwaveCable){scene.remove(mmwaveCable);mmwaveCable.geometry.dispose();}
 const points=[radar.position.clone().add(new T.Vector3(.065,.025,0)),new T.Vector3(2.46,2.745,.45),new T.Vector3(2.46,2.745,-.57),new T.Vector3(2.46,.81,-.57),new T.Vector3(2.15,.81,-.57)];
 const path=new T.CurvePath();for(let i=1;i<points.length;i++)path.add(new T.LineCurve3(points[i-1],points[i]));
 mmwaveCable=new T.Mesh(new T.TubeGeometry(path,100,.006,8,false),M.dark);mmwaveCable.name='mmwave-to-pi-molding-cable';scene.add(mmwaveCable);
}
sensorPositions.push(['mmWave · 천장 2.7m',new T.Vector3(1.28,2.83,.45)]);
const labels=[['이동식 모니터',new T.Vector3(-.85,2.23,-1.65)],['침상 · 윗면 70cm',new T.Vector3(1.28,1.10,.7)],['Raspberry Pi · 머리맡',new T.Vector3(2.1,.98,-.57)],['라즈베리파이 연결 PC',new T.Vector3(2.1,1.22,-2.35)],...sensorPositions];
for(const [name,pos] of labels){const el=document.createElement('div');el.className='tag';el.textContent=name;$('#labels').append(el);labels[labels.findIndex(a=>a[0]===name)].push(el);}
const presets=[['전체 공간',[7,6.3,8],[0,.65,0]],['원본 방향',[.1,1.8,4.5],[.1,1,-.6]],['침대 측면',[-1.0,2.2,2.7],[1.15,1.0,.45]],['머리맡',[1.9,1.8,-1.9],[1.28,.70,.45]],['위에서',[.1,8,.2],[0,0,0]],['모니터',[-.7,1.8,1.1],[-.85,1.55,-1.15]],['천장 mmWave',[1.60,2.40,.95],[1.31,2.72,.45]],['낙상 전체 동작',[-1.2,3.4,4.3],[.35,.65,.70]],['센서·침대·대시보드',[-1.6,2.5,5.9],[.2,1.35,-.1]]];
let transition=null,currentView=0,currentShot=null;
// A close-up of the same sensor geometry keeps its real room dimensions intact.
const radarDetail=document.createElement('div');radarDetail.id='mmwaveCloseup';
radarDetail.innerHTML='<strong>천장 mmWave</strong><canvas aria-label="mmWave 센서 확대 모습"></canvas><small>확대 보기 · 실제 위치는 빨간색 원</small>';
$('.mmwave-guide').after(radarDetail);
const radarMarker=document.createElement('div');radarMarker.id='mmwaveMarker';radarMarker.setAttribute('aria-label','천장 mmWave 실제 위치');view.append(radarMarker);
const detailRenderer=new T.WebGLRenderer({canvas:radarDetail.querySelector('canvas'),antialias:true,alpha:true});detailRenderer.setPixelRatio(Math.min(devicePixelRatio,2));detailRenderer.setSize(210,125,false);
const detailScene=new T.Scene(),detailCamera=new T.PerspectiveCamera(36,210/125,.001,3);
const radarCopy=radar.clone();radarCopy.position.set(0,0,0);detailScene.add(radarCopy);detailScene.add(new T.HemisphereLight(0xffffff,0x829480,3));
const detailLight=new T.DirectionalLight(0xffffff,3);detailLight.position.set(-1,-1,2);detailScene.add(detailLight);
detailCamera.position.set(.23,-.20,.33);detailCamera.lookAt(.055,.025,0);detailRenderer.render(detailScene,detailCamera);
function updateRadarDetail(){
 const visible=currentView===8;radarDetail.hidden=!visible;
 const p=radar.position.clone().project(camera);radarMarker.hidden=!visible||p.z>1||p.z< -1||Math.abs(p.x)>1||Math.abs(p.y)>1;
 radarMarker.style.left=(p.x*.5+.5)*view.clientWidth+'px';radarMarker.style.top=(-p.y*.5+.5)*view.clientHeight+'px';
}

function setView(i,instant=false){currentShot=null;document.body.classList.remove('shot-active');monitor.position.set(-.85,0,-1.65);monitor.rotation.y=0;camera.fov=44;camera.updateProjectionMatrix();currentView=i;controls.maxPolarAngle=i===6?Math.PI*.8:Math.PI*.495;document.body.classList.toggle('capture-view',i===8||i===6);$('#toggleSceneHeader').setAttribute('aria-expanded',String(i!==8&&i!==6));$('#toggleSceneHeader').textContent=(i===8||i===6)?'상단 메뉴 펼치기':'상단 메뉴 접기';const [name,pos,target]=presets[i];$('#viewname').textContent=name;radarDetail.hidden=i!==8;radarMarker.hidden=i!==8;document.querySelectorAll('#views button').forEach((b,j)=>b.setAttribute('aria-pressed',i===j));if(instant){transition=null;camera.position.fromArray(pos);controls.target.fromArray(target);controls.update();}else{transition={start:performance.now(),from:camera.position.clone(),to:new T.Vector3(...pos),a:controls.target.clone(),b:new T.Vector3(...target)};}document.dispatchEvent(new Event('room-shot-change'));}
presets.forEach((p,i)=>{const b=document.createElement('button');b.textContent=i===8?'09  통합 촬영':`0${i+1}  ${p[0]}`;b.title=p[0];b.onclick=()=>setView(i);$('#views').append(b);});setView(0,true);
$('#toggleSceneHeader').onclick=()=>{const folded=document.body.classList.toggle('capture-view');$('#toggleSceneHeader').setAttribute('aria-expanded',String(!folded));$('#toggleSceneHeader').textContent=folded?'상단 메뉴 펼치기':'상단 메뉴 접기';};
$('#showMmwaveView').onclick=()=>{setView(6);$('#labelsToggle').checked=false;$('#labels').hidden=true;};
$('#showIntegratedView').onclick=()=>{setView(8);$('#labelsToggle').checked=true;$('#labels').hidden=false;};
controls.addEventListener('start',()=>{transition=null;if(!window.room?.rig?.isOverview){currentShot=null;document.dispatchEvent(new Event('room-shot-change'));}});
$('#walls').onchange=e=>sideWalls.visible=!e.target.checked;
$('#ceiling').onchange=e=>ceiling.visible=e.target.checked;
$('#labelsToggle').onchange=e=>$('#labels').hidden=!e.target.checked;
function sizeBed(){const w=+$('#bedWidth').value,l=+$('#bedLength').value;bed.scale.set(w/95,1,l/200);bed.position.x=1.725-.53*w/95;headTof.position.x=footTof.position.x=radar.position.x=bed.position.x;labels[1][1].x=bed.position.x;for(const entry of sensorPositions)entry[1].x=bed.position.x;headTof.position.z=.45-l/200-.18;footTof.position.z=.45+l/200+.18;sensorPositions[0][1].z=headTof.position.z;sensorPositions[1][1].z=footTof.position.z;updateMmwaveCable();$('#widthOut').textContent=w+'cm';$('#lengthOut').textContent=l+'cm';}
$('#bedWidth').oninput=sizeBed;$('#bedLength').oninput=sizeBed;$('#reset').onclick=()=>{$('#bedWidth').value=80;$('#bedLength').value=200;sizeBed();};
sizeBed();
$('#referenceBtn').onclick=()=>$('#referenceDialog').showModal();$('#closeDialog').onclick=()=>$('#referenceDialog').close();
$('#capture').onclick=()=>{renderer.render(scene,camera);canvas.toBlob(b=>{if(!b)return;const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='majubom-room.png';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);});};
const resize=()=>{renderer.setSize(view.clientWidth,view.clientHeight,false);camera.aspect=view.clientWidth/view.clientHeight;camera.updateProjectionMatrix();};new ResizeObserver(resize).observe(view);resize();
const scenario=createScenario({scene,bed,monitorScreen,phoneScreen,setView,getView:()=>currentView});setView(8,true);
let frame=0;
function render(now){requestAnimationFrame(render);scenario.update(now);if(transition){const t=Math.min(1,(now-transition.start)/750),s=1-(1-t)**3;camera.position.lerpVectors(transition.from,transition.to,s);controls.target.lerpVectors(transition.a,transition.b,s);if(t===1)transition=null;}if(window.room?.rig?.mode!=='film')controls.update();window.room?.rig?.update(now);renderer.render(scene,camera);updateRadarDetail();updateSensorShotLabels();if(++frame%3===0){const placed=[];for(const [,pos,el] of labels){const v=pos.clone().project(camera);el.hidden=v.z>1||v.z< -1||Math.abs(v.x)>1||Math.abs(v.y)>1;const x=(v.x*.5+.5)*view.clientWidth;let y=(-v.y*.5+.5)*view.clientHeight;for(let n=0;n<8&&placed.some(p=>Math.abs(p.x-x)<145&&Math.abs(p.y-y)<28);n++)y+=28;if(!el.hidden)placed.push({x,y});el.style.left=x+'px';el.style.top=y+'px';}}}
requestAnimationFrame(render);$('#loading').hidden=true;window.room={scene,camera,controls,bed,monitor,monitorScreen,headTof,footTof,radar,presets,setView,scenario,shots,setShot};document.documentElement.dataset.ready='true';

$('#feedbackBtn').onclick=()=>$('#feedbackDialog').showModal();
$('#closeFeedback').onclick=()=>$('#feedbackDialog').close();

function setShot(index){
 const s=shots[index];if(!s)return;
 setView(8,true);currentShot=index;document.body.classList.add('shot-active');
 monitor.position.fromArray(s.tv);monitor.rotation.y=Math.atan2(s.camera[0]-s.tv[0],s.camera[2]-s.tv[2]);
 camera.fov=s.fov;camera.updateProjectionMatrix();camera.position.fromArray(s.camera);controls.target.fromArray(s.target);controls.update();
 $('#labelsToggle').checked=false;$('#labels').hidden=true;$('#viewname').textContent=`후보 ${index+1} · ${s.name}`;
 $('#shotSelect').value=String(index);$('#shotDescription').textContent=s.note;document.dispatchEvent(new Event('room-shot-change'));
}
const shotPanel=document.createElement('section');shotPanel.className='shot-panel';
shotPanel.innerHTML='<h3>센서 전체 포함 · 추천 10안</h3><select id="shotSelect" aria-label="촬영 구도"><option value="">구도 선택</option>'+shots.map((s,i)=>`<option value="${i}">${String(i+1).padStart(2,'0')} ${s.name}</option>`).join('')+'</select><p id="shotDescription" class="small">침대·센서·사람 동작은 고정합니다.</p><a href="shots.html">센서 포함 10개 구도 비교 ↗</a>';
$('.mmwave-guide').after(shotPanel);
$('#shotSelect').onchange=e=>{if(e.target.value!=='')setShot(+e.target.value);};
const shotParam=new URLSearchParams(location.search).get('shot');if(shotParam!==null)setShot(+shotParam);

if(new URLSearchParams(location.search).has('frame')){
 document.body.classList.add('shot-capture');
 const bar=document.createElement('nav');bar.className='shot-playbar';bar.innerHTML='<a href="shots.html">← 센서 포함 구도 비교</a><button id="shotPlay">▶ 재생 / 일시정지</button><button id="shotReset">처음으로</button>';document.body.append(bar);
 $('#shotPlay').onclick=()=>$('#playScenario').click();$('#shotReset').onclick=()=>$('#restartScenario').click();
}

const sensorShotLabels=[['머리맡 ToF',headTof,new T.Vector3(.035,1.53,.064)],['발치 ToF',footTof,new T.Vector3(.035,1.53,.064)],['천장 mmWave',radar,new T.Vector3(0,.012,0)]].map(([name,object,offset])=>{const el=document.createElement('div');el.className='sensor-shot-label';el.dataset.label=name;el.hidden=true;view.append(el);return {object,offset,el};});
function updateSensorShotLabels(){for(const s of sensorShotLabels){const p=s.object.localToWorld(s.offset.clone()).project(camera);s.el.hidden=(!(window.room?.rig?.active)&&(currentShot===null||!shots[currentShot]?.sensorPixels))||Math.abs(p.x)>1||Math.abs(p.y)>1||Math.abs(p.z)>1;s.el.style.left=(p.x*.5+.5)*view.clientWidth+'px';s.el.style.top=(-p.y*.5+.5)*view.clientHeight+'px';}}

mountReviewHeader({setShot,getShot:()=>currentShot,scenario});

window.room.rig=mountCameraPlacement({scene,camera,controls,view,canvas,getShot:()=>currentShot,shots,scenario,bed,headTof,footTof,radar,onEdit:()=>{currentShot=null;document.dispatchEvent(new Event('room-shot-change'));}});
