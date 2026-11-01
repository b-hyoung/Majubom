import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {EDGES, RoomStatus, Vec3, Skeleton3D} from './types';
import {smoothStep,outlierConfirmed} from './poseSmoothing';

export class RoomScene {
  renderer: THREE.WebGLRenderer;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, 1, .01, 200);
  controls: OrbitControls;
  room = new THREE.Group();
  skeleton = new THREE.Group();
  lastRoom = '';
  config: RoomStatus['config'] | null = null;
  frame = 0;
  demo = true;
  targets = new Float32Array(51);
  positions = new Float32Array(51);
  valid = new Uint8Array(17);
  dots = new THREE.InstancedMesh(new THREE.SphereGeometry(.026,10,8),new THREE.MeshBasicMaterial({color:0x73f0b0}),17);
  bonePositions = new Float32Array(EDGES.length*6);
  bones: THREE.LineSegments;
  transform = new THREE.Object3D();
  poseTime = 0;
  poseSource = '';
  preview = false;
  previewFramed = false;
  alignmentKey = '';
  bodyScale = .5;
  sourceMode = '';
  switchUntil = 0;
  lastPoseKey = '';
  previewBed: {center?:Vec3;corners:Vec3[];width?:number;length?:number}|null = null;
  jointSeen = new Float64Array(17);
  pending: (number[]|null)[] = Array(17).fill(null);
  resize: ResizeObserver;
  constructor(public host: HTMLElement, onStats?: (fps:number,renderer:string)=>void) {
    this.renderer = new THREE.WebGLRenderer({antialias: false, powerPreference:'high-performance'});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    host.append(this.renderer.domElement);
    this.scene.background = new THREE.Color('#0b1322');
    this.scene.add(new THREE.HemisphereLight(0xe2edff, 0x334155, 2.8));
    const sun = new THREE.DirectionalLight(0xffffff, 2); sun.position.set(2, 7, 4); this.scene.add(sun);
    this.scene.add(this.room, this.skeleton);
    const geometry=new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.BufferAttribute(this.bonePositions,3).setUsage(THREE.DynamicDrawUsage));
    this.bones=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:0x73f0b0}));
    this.dots.instanceMatrix.setUsage(THREE.DynamicDrawUsage);this.dots.frustumCulled=false;this.bones.frustumCulled=false;
    this.skeleton.add(this.dots,this.bones);
    const gl=this.renderer.getContext(), ext=gl.getExtension('WEBGL_debug_renderer_info');
    const renderer=ext?String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)):'WebGL renderer unavailable';
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; this.controls.maxPolarAngle = Math.PI * .95;
    this.preset('reset');
    this.resize = new ResizeObserver(() => {
      const {width, height} = host.getBoundingClientRect();
      this.renderer.setSize(width, height); this.camera.aspect = width/Math.max(1, height); this.camera.updateProjectionMatrix();
    });
    this.resize.observe(host);
    let previous=performance.now(), sampled=previous, frames=0;
    const draw = () => {
      this.frame = requestAnimationFrame(draw);
      const now=performance.now(),dt=Math.min(.1,(now-previous)/1000);previous=now;
      if(this.demo)this.demoPose(now/1000);
      const fresh=this.demo || now-this.poseTime<1500;
      for(let i=0;i<17;i++) {
        const valid=!!this.valid[i] && fresh;
        if(valid){
          const p=smoothStep(Array.from(this.positions.slice(i*3,i*3+3)),Array.from(this.targets.slice(i*3,i*3+3)),dt,this.bodyScale,now<this.switchUntil);
          this.positions.set(p,i*3);
        }
        this.transform.position.fromArray(this.positions,i*3);this.transform.scale.setScalar(valid?1:0);this.transform.updateMatrix();
        this.dots.setMatrixAt(i,this.transform.matrix);
      }
      this.dots.instanceMatrix.needsUpdate=true;
      let offset=0;
      for(const [a,b] of EDGES)for(const i of [a,b])for(let j=0;j<3;j++)this.bonePositions[offset++]=this.valid[a]&&this.valid[b]&&fresh?this.positions[i*3+j]:0;
      this.bones.geometry.attributes.position.needsUpdate=true;
      this.controls.update();this.renderer.render(this.scene,this.camera);
      frames++;if(now-sampled>=1000){onStats?.(frames*1000/(now-sampled),renderer);frames=0;sampled=now;}
    }; draw();
  }
  setPose(pose:Skeleton3D|null) {
    if(this.demo)return;
    if(!pose){this.valid.fill(0);this.lastPoseKey='';return;}
    const key=(pose.observation_id??String(pose.timestampMs))+pose.coordinateSystem;
    if(this.lastPoseKey===key&&this.poseSource===pose.personId)return;
    this.lastPoseKey=key;
    this.valid.fill(0);
    const color=pose.source==='single_camera_mediapipe_pnp'?0xf6ad55:0x73f0b0;
    (this.dots.material as THREE.MeshBasicMaterial).color.setHex(color);
    (this.bones.material as THREE.LineBasicMaterial).color.setHex(color);
    const now=performance.now();
    const reset=this.poseSource!==pose.personId||now-this.poseTime>750;this.poseSource=pose.personId;this.poseTime=now;
    const mode=pose.source+':'+(pose.source_camera??'');
    if(mode!==this.sourceMode){this.sourceMode=mode;this.switchUntil=now+500;this.pending.fill(null);}
    const torso=[5,6,11,12].map(i=>pose.joints.find(j=>j.id===i&&j.valid));
    if(torso.every(j=>j&&j.x!==null&&j.y!==null&&j.z!==null)){
      const [a,b,c,d]=torso!;const size=Math.hypot((a!.x!+b!.x!-c!.x!-d!.x!)/2,(a!.y!+b!.y!-c!.y!-d!.y!)/2,(a!.z!+b!.z!-c!.z!-d!.z!)/2);
      if(size>.1&&size<2)this.bodyScale+=.1*(size-this.bodyScale);
    }
    for(const j of pose.joints)if(j.valid && j.x!==null && j.y!==null && j.z!==null && [j.x,j.y,j.z].every(Number.isFinite) && j.id>=0 && j.id<17){
      const incoming=[j.x,j.y,j.z],current=Array.from(this.targets.slice(j.id*3,j.id*3+3));
      const reappeared=now-this.jointSeen[j.id]>750;
      if(!reset&&!reappeared&&now>=this.switchUntil&&!outlierConfirmed(current,incoming,this.pending[j.id],this.bodyScale)){
        this.pending[j.id]=incoming;this.valid[j.id]=now-this.jointSeen[j.id]<=250?1:0;continue;
      }
      this.pending[j.id]=null;this.jointSeen[j.id]=now;
      this.targets.set(incoming,j.id*3);this.valid[j.id]=1;
      if(reset||reappeared)this.positions.set(incoming,j.id*3);
    }
    if(this.preview&&!this.previewBed&&!this.previewFramed&&pose.joints.filter(j=>j.valid).length>=5){
      const center=new THREE.Vector3();let n=0;
      for(const j of pose.joints)if(j.valid){center.add(new THREE.Vector3(j.x!,j.y!,j.z!));n++;}
      center.divideScalar(n);this.controls.target.copy(center);this.camera.position.copy(center).add(new THREE.Vector3(2,1.3,3));this.controls.update();this.previewFramed=true;
    }
  }
  demoPose(t:number) {
    const bed=this.config?.bed;if(!bed)return;
    const breath=Math.sin(t*1.6)*.012,arm=Math.sin(t*1.3)*.05;
    // Simulated supine COCO17; never derived from live 2D pixels or exported as measured data.
    const points=[[0,.17,-.72],[-.035,.18,-.74],[.035,.18,-.74],[-.075,.15,-.7],[.075,.15,-.7],[-.2,.12,-.43],[.2,.12,-.43],[-.28,.1,-.12],[.28,.15+arm,-.1],[-.3,.08,.14],[.25,.2+arm,.13],[-.13,.1,.17],[.13,.1,.17],[-.13,.07,.54],[.13,.07,.54],[-.15,.05,.87],[.15,.05,.87]];
    const a=THREE.MathUtils.degToRad(bed.yaw_degrees),ca=Math.cos(a),sa=Math.sin(a);
    points.forEach(([x,y,z],i)=>{this.targets.set([bed.position[0]+x*ca+z*sa,bed.position[1]+bed.size[1]/2+y+breath,bed.position[2]-x*sa+z*ca],i*3);});
    if(this.poseSource!=='demo'){this.positions.set(this.targets);this.poseSource='demo';}this.valid.fill(1);
  }
  clear(group: THREE.Group) {
    group.traverse(o => {if (o instanceof THREE.Mesh || o instanceof THREE.Line) {o.geometry.dispose(); const ms = Array.isArray(o.material) ? o.material : [o.material]; ms.forEach(m => m.dispose());}});
    group.clear();
  }
  box(size: Vec3, position: Vec3, color: number, opacity=1, parent=this.room) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), new THREE.MeshStandardMaterial({color, transparent: opacity<1, opacity, side: THREE.DoubleSide}));
    mesh.position.set(...position); parent.add(mesh); return mesh;
  }
  lines(points: THREE.Vector3[], color: number, parent=this.room) {
    parent.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({color})));
  }
  preset(name: string) {
    if(this.preview){
      const center=this.previewBed?.center?new THREE.Vector3(...this.previewBed.center):this.previewFramed?this.controls.target.clone():new THREE.Vector3(0,-.5,-2);
      const length=Math.max(2.5,(this.previewBed?.length??2)*1.8);
      const offset=name==='top'?new THREE.Vector3(0,length,.001):name==='side'?new THREE.Vector3(length,.5,0):name==='front'?new THREE.Vector3(0,.6,length):new THREE.Vector3(length*.8,length*.65,length*.9);
      this.controls.target.copy(center);this.camera.position.copy(center).add(offset);this.controls.update();return;
    }
    const [w,h,d] = this.config?.room_size ?? [4,2.7,5];
    const center = name === 'bed' ? this.config?.bed.position ?? [w/2,.5,d/2] : [w/2, h/4, d/2];
    this.controls.target.set(...center as Vec3);
    const length = name==='bed' ? Math.max(...(this.config?.bed.size??[1,.5,2]))*1.8 : Math.max(w,d,h)*1.5;
    const offset: Vec3 = name === 'top' ? [0,length,.001] : name === 'front' ? [0,1,length] : name === 'side' ? [length,1,0] : [length*.8,length*.65,length*.9];
    this.camera.position.copy(this.controls.target).add(new THREE.Vector3(...offset)); this.controls.update();
  }
  update(data: RoomStatus, ceiling: boolean, axes: boolean) {
    const first=this.config===null;
    this.config = data.config;
    const preview=!!data.experimental_preview?.enabled&&!this.demo;
    if(this.preview!==preview){this.preview=preview;this.previewFramed=false;this.poseSource='';}
    this.previewBed=preview?data.experimental_preview?.bed??null:null;
    const alignmentKey=JSON.stringify([data.experimental_preview?.alignment?.rotation,data.experimental_preview?.alignment?.translation,data.experimental_preview?.bed?.center]);
    if(alignmentKey!==this.alignmentKey){this.alignmentKey=alignmentKey;this.previewFramed=false;this.poseSource='';this.lastPoseKey='';if(this.previewBed){this.preset('bed');this.previewFramed=true;}}
    const cameras=preview?data.experimental_preview!.cameras:data.calibration?.cameras??[];
    const key = JSON.stringify([data.experimental_preview?.bed,preview,data.config.room_size, data.config.bed, cameras, data.setup?.manual_cameras, ceiling, axes]);
    if (key !== this.lastRoom) {
      this.lastRoom = key; this.clear(this.room);
      if(!preview){
      const [w,h,d] = data.config.room_size;
      this.box([w,.04,d], [w/2,-.02,d/2], 0x18263b);
      this.box([w,h,.025], [w/2,h/2,0], 0x33445e, .2);
      this.box([.025,h,d], [0,h/2,d/2], 0x33445e, .2);
      if (ceiling) this.box([w,.025,d], [w/2,h,d/2], 0x53627c, .12);
      const grid = new THREE.GridHelper(Math.max(w,d), Math.ceil(Math.max(w,d)*2), 0x3b526e, 0x23354b); grid.position.set(w/2,.003,d/2); this.room.add(grid);
      if (axes) this.room.add(new THREE.AxesHelper(1));
      const bed = new THREE.Group(); bed.position.set(...data.config.bed.position); bed.rotation.y = THREE.MathUtils.degToRad(data.config.bed.yaw_degrees); this.room.add(bed);
      const size = data.config.bed.size;
      const mattress = Math.min(.1, size[1]/3);
      this.box([size[0],size[1]-mattress,size[2]],[0,-mattress/2,0],0x36566a,1,bed);
      this.box([size[0]*.98,mattress,size[2]*.98],[0,(size[1]-mattress)/2,0],0xb6cbd4,1,bed);
      const edges = new THREE.EdgesGeometry(new THREE.BoxGeometry(...size)); bed.add(new THREE.LineSegments(edges, new THREE.LineBasicMaterial({color:0x7dd3fc})));
      }else{
        const grid=new THREE.GridHelper(10,20,0x3b526e,0x23354b);grid.position.set(0,data.experimental_preview?.alignment?.source==='bed_plane'?0:-2,data.experimental_preview?.alignment?.source==='bed_plane'?0:-3);this.room.add(grid);
        if(axes)this.room.add(new THREE.AxesHelper(.5));
        const corners=data.experimental_preview?.bed?.corners;
        if(corners){
          if(data.experimental_preview?.alignment?.source==='bed_plane'){
            const b=data.experimental_preview.bed!,height=data.experimental_preview.alignment.bed_surface_height_m??.65;
            this.box([b.width??1,.1,b.length??2],[0,height-.05,0],0x486375,.65);
            for(const x of [-1,1])for(const z of [-1,1])this.box([.04,Math.max(.05,height-.1),.04],[x*(b.width??1)*.4,(height-.1)/2,z*(b.length??2)*.42],0x5c7486);
          }
          const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(corners.flat(),3));geometry.setIndex([0,1,2,0,2,3]);geometry.computeVertexNormals();
          this.room.add(new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color:0x7dd3fc,transparent:true,opacity:.2,side:THREE.DoubleSide})));
          this.lines(corners.flatMap((p,i)=>[new THREE.Vector3(...p),new THREE.Vector3(...corners[(i+1)%4])]),0x7dd3fc);
        }
      }
      // Frustum rays from calibrated K, in the room frame. No assumed camera poses/FOV.
      for (const [index,c] of cameras.entries()) {
        const r = new THREE.Matrix3().set(...c.rotation.flat() as [number,number,number,number,number,number,number,number,number]).transpose();
        const center = new THREE.Vector3(...c.translation).negate().applyMatrix3(r);
        const color = index === 0 ? 0x37d2d0 : 0xf6ad55;
        const marker = new THREE.Mesh(new THREE.SphereGeometry(.07,12,8), new THREE.MeshBasicMaterial({color})); marker.position.copy(center); this.room.add(marker);
        const k = new THREE.Matrix3().set(...c.intrinsic_matrix.flat() as [number,number,number,number,number,number,number,number,number]).invert();
        const [cw,ch] = c.image_resolution;
        const corners = [[0,0],[cw,0],[cw,ch],[0,ch]].map(([x,y]) => new THREE.Vector3(x,y,1).applyMatrix3(k).multiplyScalar(.7).applyMatrix3(r).add(center));
        this.lines(corners.flatMap((p,i) => [center,p,p,corners[(i+1)%4]]),color);
      }
      if(!preview&&!data.calibration)for(const [i,c] of (data.setup?.manual_cameras??[]).entries()) {
        const origin=new THREE.Vector3(...c.position),direction=new THREE.Vector3(...c.look_at).sub(origin).normalize();
        this.room.add(new THREE.ArrowHelper(direction,origin,.7,i===0?0x37d2d0:0xf6ad55));
      }
    }
    this.setPose(preview?data.experimental_preview!.skeleton:data.skeleton);
    if(first)this.preset('bed');
  }
  dispose() {cancelAnimationFrame(this.frame); this.resize.disconnect(); this.controls.dispose(); this.clear(this.room); this.clear(this.skeleton); this.renderer.dispose(); this.renderer.domElement.remove();}
}
