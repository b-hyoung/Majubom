import assert from 'node:assert/strict';
import {smoothStep,outlierConfirmed} from './src/poseSmoothing.ts';
const scale=.5,dt=1/60;
let p=[0,0,0],raw=[],filtered=[];
for(let i=0;i<600;i++){
  const target=[Math.sin(i*2.4)*.012,0,0];p=smoothStep(p,target,dt,scale);
  if(i>100){raw.push(target[0]);filtered.push(p[0]);}
}
const rms=a=>Math.sqrt(a.reduce((s,v)=>s+v*v,0)/a.length);
assert.ok(rms(filtered)<rms(raw)*.35,'Stationary jitter must fall substantially');
assert.deepEqual(smoothStep([0,0,0],[.003,0,0],dt,scale),[0,0,0]);
assert.equal(outlierConfirmed([0,0,0],[1,0,0],null,scale),false);
assert.equal(outlierConfirmed([0,0,0],[1,0,0],[1.01,0,0],scale),true);
const normal=smoothStep([0,0,0],[.5,0,0],dt,scale);
const switching=smoothStep([0,0,0],[.5,0,0],dt,scale,true);
assert.ok(switching[0]<normal[0],'Source switch must move more gradually');
p=[0,0,0];for(let i=0;i<30;i++)p=smoothStep(p,[.5,0,0],dt,scale);
assert.ok(p[0]>.46&&p[0]<=.5,'Real movement must still be followed without overshoot');
assert.deepEqual(smoothStep([0,0,0],[1,0,0],0,scale),[0,0,0]);
assert.deepEqual(smoothStep([0,0,0],[NaN,0,0],dt,scale),[0,0,0]);
assert.equal(outlierConfirmed([0,0,0],[Infinity,0,0],null,scale),false);
assert.ok(smoothStep([0,0,0],[.5,0,0],dt,NaN).every(Number.isFinite));
console.log(JSON.stringify({passed:10,jitterRmsRatio:rms(filtered)/rms(raw),positionAfterHalfSecond:p[0]}));
