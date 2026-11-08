// Display-only causal filtering. Raw geometry/classification is never modified.
export function smoothStep(current:number[], target:number[], dt:number, bodyScale:number, switching=false):number[] {
  if(current.length!==3||target.length!==3||![...current,...target,dt].every(Number.isFinite)||dt<=0)return [...current];
  if(!Number.isFinite(bodyScale)||bodyScale<=0)bodyScale=.5;
  const distance=Math.hypot(...target.map((v,i)=>v-current[i]));
  const deadband=bodyScale*.012;
  if(distance<=deadband)return [...current];
  const moving=Math.min(1,Math.max(0,(distance/bodyScale-.04)/.35));
  const tau=switching?.3:.16-.1*moving;
  const alpha=(1-Math.exp(-Math.max(0,Math.min(dt,.1))/tau))*(1-deadband/distance);
  // Limit visual jumps; units inherit the candidate's unverified scale.
  const step=Math.min(distance*alpha,bodyScale*5*Math.max(0,Math.min(dt,.1)));
  return current.map((v,i)=>v+(target[i]-v)*step/distance);
}

export function outlierConfirmed(current:number[],incoming:number[],pending:number[]|null,bodyScale:number):boolean {
  if(current.length!==3||incoming.length!==3||![...current,...incoming].every(Number.isFinite))return false;
  if(!Number.isFinite(bodyScale)||bodyScale<=0)bodyScale=.5;
  const jump=Math.hypot(...incoming.map((v,i)=>v-current[i]));
  return jump<=bodyScale*.65 || (pending!==null&&Math.hypot(...incoming.map((v,i)=>v-pending[i]))<bodyScale*.18);
}
