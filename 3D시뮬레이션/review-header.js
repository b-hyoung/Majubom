import {shots} from './shots.js';
import {shots as olderShots} from './shots-without-sensor-constraint.js';
export function mountReviewHeader({setShot,getShot,scenario}){
 if(new URLSearchParams(location.search).has('frame'))return;
 const approvedCount=shots.length;shots.push(...olderShots.map(s=>({...s,name:'이전 비교안 · '+s.name,legacy:true})));
 const $=s=>document.querySelector(s),header=$('body>header');document.body.classList.add('review-ui');
 const checks=[['sensor','센서 근접·명칭·설치 위치'],['large','사람·대시보드 크기'],['readable','대시보드 밝기·결과 가독성'],['story','정상 → 위험 → 알림 자막·내레이션'],['clothes','모자·마스크 없는 자연스러운 복장'],['timing','데모 30초·전체 3분'],['voice','스피커 위험 알림 음성'],['guardian','보호자 알림 결과']];
 let saved={};try{saved=JSON.parse(localStorage.getItem('majubom-shot-review-v1')||'{}');if(!saved||typeof saved!=='object'||Array.isArray(saved))saved={};}catch{}
 const key=i=>JSON.stringify([shots[i]?.tv,shots[i]?.camera,shots[i]?.target,shots[i]?.fov]);
 const state=i=>saved[key(i)]||{};
 function persist(){try{localStorage.setItem('majubom-shot-review-v1',JSON.stringify(saved));$('#reviewSave').textContent='이 브라우저에 저장됨';}catch{$('#reviewSave').textContent='저장 불가 · 현재 화면에서만 유지';}}
 const toggle=document.createElement('button');toggle.id='reviewToggle';toggle.type='button';toggle.textContent='구도 필터 접기';toggle.setAttribute('aria-expanded','true');toggle.setAttribute('aria-controls','reviewTools');header.insertBefore(toggle,$('#feedbackBtn'));
 const tools=document.createElement('div');tools.id='reviewTools';tools.innerHTML=`
 <div class="review-row"><label class="review-select-label">구도 <select id="reviewShot" aria-label="필터링된 촬영 구도"></select></label><strong id="reviewCount" aria-live="polite"></strong><a href="shots.html" target="_blank" rel="noopener">캡처 비교 ↗</a><div class="review-phases" aria-label="검토할 동작"><button data-review-time="0">정상</button><button data-review-time="16.8">난간 넘기</button><button data-review-time="18.1">추락 중</button><button data-review-time="23">바닥</button></div></div>
 <div class="review-row review-filters"><span>조건 필터</span><label><input id="filterSensors" type="checkbox" checked> 센서 3곳 포함</label><label><input id="filterTV" type="checkbox"> TV 폭 350px 이상</label><label><input id="filterHuman" type="checkbox"> 사람 높이 220px 이상</label><label>검토 상태 <select id="filterReviewed"><option value="all">전체</option><option value="pending">미검토만</option><option value="done">검토 완료만</option></select></label><button id="resetReviewFilters" type="button">필터 초기화</button></div>
 <p id="reviewNotice" class="review-notice" aria-live="polite"></p>
 <details id="reviewChecklist"><summary>현재 구도 피드백 체크 <span id="reviewProgress"></span></summary><div class="review-check-grid">${checks.map(([id,text])=>`<label><input type="checkbox" data-review-check="${id}">${text}</label>`).join('')}</div><div class="review-row"><label><input id="markReviewed" type="checkbox"> 이 구도 검토 완료</label><span id="reviewSave">체크는 이 브라우저에 저장됩니다.</span></div><p class="review-notice">직접 확인한 항목만 체크하세요. 촬영·편집 완료 여부는 알고리즘이 자동 판정하지 않습니다.</p></details>`;
 header.append(tools);
 const quick=document.createElement('div');quick.id='quickShotChoices';quick.setAttribute('aria-label','센서 전체 포함 추천 구도 10개');tools.prepend(quick);
 quick.addEventListener('click',e=>{const b=e.target.closest('[data-quick-shot]');if(!b)return;setShot(+b.dataset.quickShot);window.room?.rig?.setMode('film');});
 const stage=document.createElement('div');stage.id='reviewStage';const view=$('#viewport');view.before(stage);stage.append(view);
 const fit=()=>{if(document.body.classList.contains('shot-capture'))return;const w=Math.min(stage.clientWidth,stage.clientHeight*16/9);view.style.width=w+'px';view.style.height=w*9/16+'px';};new ResizeObserver(fit).observe(stage);
 $('#shotSelect').innerHTML='<option value="">구도 선택</option>'+shots.map((s,i)=>`<option value="${i}">${i<approvedCount?'통과 '+(i+1):'이전 '+(i-approvedCount+1)} · ${s.name}</option>`).join('');
 function matching(){return shots.map((s,i)=>({s,i})).filter(({s,i})=>(!$('#filterSensors').checked||s.sensorsVisible?.every(Boolean))&&(!$('#filterTV').checked||s.width>=350)&&(!$('#filterHuman').checked||s.humanHeight>=220)&&($('#filterReviewed').value==='all'||($('#filterReviewed').value==='done')===!!state(i).done));}
 function render(autoSelect=false){const rows=matching();$('#reviewCount').textContent=`${rows.length} / ${shots.length}개`;
  const quickRows=rows.filter(r=>r.i<approvedCount);quick.innerHTML='<span class="quick-shot-heading">추천 구도 · 누르면 촬영 화면으로 전환</span><div class="quick-shot-buttons">'+quickRows.map(({s,i})=>`<button type="button" data-quick-shot="${i}" aria-pressed="${getShot()===i}" title="카메라 ${s.camera.join(', ')}m / TV ${s.tv.join(', ')}m / 화각 ${s.fov}°"><b>${String(i+1).padStart(2,'0')}</b> ${s.name}</button>`).join('')+'</div>'+(quickRows.length?'':'<small>필터에 맞는 추천 구도가 없습니다.</small>');
  $('#reviewShot').innerHTML=rows.length?rows.map(({s,i})=>`<option value="${i}">${s.legacy?'이전 '+(i-approvedCount+1):'통과 '+(i+1)} · TV ${s.width}px · 카메라 ${s.camera[1].toFixed(2)}m${state(i).done?' · 검토 완료':''}</option>`).join(''):'<option value="">조건에 맞는 구도 없음</option>';
  $('#reviewShot').disabled=!rows.length;let i=getShot();if(autoSelect&&rows.length&&!rows.some(r=>r.i===i)){setShot(rows[0].i);return;}$('#reviewShot').value=rows.some(r=>r.i===i)?String(i):'';
  const s=shots[i],st=state(i);$('#reviewNotice').textContent=!rows.length?'일치하는 구도가 없습니다. 필터를 해제하면 다시 표시됩니다. 현재 장면은 유지합니다.':s?`${s.legacy?'이전 비교안: 센서 전체 노출 조건을 충족한 추천안이 아닙니다.':'센서 3곳 = 머리맡 ToF·발치 ToF·천장 mmWave. 센서 3종 확인과는 다릅니다.'} ${s.tradeoff?s.tradeoff+'. ':''}크기는 1280×720 기준이며 글자 판독 보장이 아닙니다.${rows.some(r=>r.i===i)?'':' 현재 장면은 필터 결과 밖에 있습니다.'}`:'구도를 선택하면 해당 구도의 피드백을 체크할 수 있습니다.';
  tools.querySelectorAll('[data-review-check]').forEach(el=>{el.checked=!!st[el.dataset.reviewCheck];el.disabled=!s;});$('#markReviewed').checked=!!st.done;$('#markReviewed').disabled=!s;$('#reviewProgress').textContent=s?`${checks.filter(([k])=>st[k]).length}/${checks.length}`:'';fit();
 }
 for(const id of ['filterSensors','filterTV','filterHuman','filterReviewed'])$('#'+id).onchange=()=>render(true);
 $('#resetReviewFilters').onclick=()=>{$('#filterSensors').checked=true;$('#filterTV').checked=false;$('#filterHuman').checked=false;$('#filterReviewed').value='all';render(true);};
 $('#reviewShot').onchange=e=>{if(e.target.value!=='')setShot(+e.target.value);};
 tools.querySelectorAll('[data-review-time]').forEach(b=>b.onclick=()=>{scenario.seek(+b.dataset.reviewTime);tools.querySelectorAll('[data-review-time]').forEach(x=>x.setAttribute('aria-pressed',x===b));});
 tools.querySelectorAll('[data-review-check]').forEach(el=>el.onchange=()=>{const i=getShot();if(i===null)return;saved[key(i)]={...state(i),[el.dataset.reviewCheck]:el.checked};persist();render();});
 $('#markReviewed').onchange=e=>{const i=getShot();if(i===null)return;saved[key(i)]={...state(i),done:e.target.checked};persist();render();};
 const toggleTools=()=>{tools.hidden=!tools.hidden;toggle.textContent=tools.hidden?'구도 필터 펼치기':'구도 필터 접기';toggle.setAttribute('aria-expanded',String(!tools.hidden));fit();};toggle.onclick=toggleTools;$('#toggleSceneHeader').onclick=toggleTools;$('#toggleSceneHeader').textContent='구도 필터 접기 / 펼치기';
 document.addEventListener('room-shot-change',()=>render());
 setShot(getShot()??0);render();
}
