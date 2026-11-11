/* Recipe rescue is a separate persisted workflow; normal step controls stay locked. */
(() => {
  'use strict';
  let session, refresh, plan, busy = false;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const grams = value => Number(value).toFixed(2);
  const bottle = number => number ? `Bottle ${number}` : 'Current mixing bottle';
  const panel = () => {
    let box = document.getElementById('batchRecoveryPanel');
    if (!box) {
      box = document.createElement('section'); box.id = 'batchRecoveryPanel';
      box.style.cssText = 'grid-column:1/-1;max-width:1000px;width:100%;margin:1rem auto;padding:1.5rem;background:#171b1d;border:2px solid #d4ae5a;border-radius:12px;color:#f6f3e9;font-size:1.15rem;line-height:1.6';
      document.getElementById('focusCard').parentElement.appendChild(box);
    }
    return box;
  };
  const button = (action, text, attributes = '') => `<button type="button" class="tvw-ws-commit" style="margin:.5rem .5rem .5rem 0" data-batch-action="${action}" ${attributes}>${escape(text)}</button>`;
  const choices = () => button('preview','Make two same-size bottles','data-mode="two_bottles"') + button('preview','Smallest balanced batch','data-mode="larger_vessel"');
  const hideNormal = () => ['focusCard','usedPanel','todoPanel','completionSummary'].forEach(id => document.getElementById(id)?.classList.add('d-none'));
  const table = p => `<div style="overflow-x:auto"><table style="width:100%;text-align:left"><thead><tr><th>Ingredient</th><th>Already in</th><th>New total</th><th>Add only</th></tr></thead><tbody>${p.ingredients.map(i => `<tr><td>${escape(i.payload.brand)} ${escape(i.payload.name)}</td><td>${grams(i.actual_grams)} g</td><td>${grams(i.total_target_grams)} g</td><td><strong>${grams(i.display_add_grams)} g</strong></td></tr>`).join('')}</tbody></table></div>`;
  async function request(operation, data) {
    if (busy) return null;
    busy = true;
    const box=panel(); box.querySelectorAll('button').forEach(b=>b.disabled=true);
    try {
      const response = await fetch(`/api/mix/guided/session/${session.id}/batch-recovery/${operation}`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
      if (response.redirected || !(response.headers.get('content-type') || '').includes('application/json')) throw new Error('Sign-in may have expired. Reload to resume your saved recovery.');
      const body = await response.json();
      if (!response.ok || body.status !== 'success') throw new Error(body.message || 'Recovery could not be saved.');
      return body;
    } catch (error) {
      let message=box.querySelector('[data-batch-error]');
      if (!message) {message=document.createElement('p');message.dataset.batchError='';message.setAttribute('role','alert');box.prepend(message);}
      message.textContent=error.message; message.style.color='#ffb4a8'; return null;
    } finally {busy=false;box.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  async function preview(mode) {
    // A failed capacity check must not leave the user staring at ordinary pour controls.
    hideNormal(); panel().innerHTML='<h2>Checking the whole recipe…</h2>'+button('back','Back to saved mix');
    const body=await request('preview',{mode}); if (!body) return;
    plan=body.plan;
    panel().innerHTML=`<h2>Keep the recipe balanced</h2><p>Originally ${plan.original_ml} ml. Corrected batch: <strong>${plan.new_ml} ml${plan.bottle_count===2 ? ` — two ${plan.bottle_ml} ml bottles` : ' — one larger vessel'}</strong>.</p>
      <p>Minimum corrected batch: ${plan.minimum_ml} ml. ${plan.bottle_count===2 ? `First we finish the concentrated part in the current bottle (${Number(plan.partial_ml).toFixed(2)} ml), mix it thoroughly and split it by weight. Then we guide PG/VG separately for each bottle.` : 'Confirm a mixing vessel with enough usable capacity, then revisit every ingredient needing a top-up.'}</p>
      ${table(plan)}<p>Target nicotine: ${plan.target_nicotine_mg_ml} mg/ml · planned: ${plan.planned_nicotine_mg_ml} mg/ml. Target PG/VG: ${escape(plan.target_ratio)} · planned: ${escape(plan.planned_ratio)}.</p><p>${escape(plan.caution)}</p>
      <label>Usable mixing-vessel capacity (ml) <input id="recoveryCapacity" type="number" min="0" step="0.1" value="${plan.bottle_count===2 ? plan.bottle_ml : plan.new_ml}" style="width:8rem;color:#111"></label>
      <p><label><input id="recoveryConfirmed" type="checkbox"> I have these bottles, room to mix thoroughly, and enough ingredients for the listed top-ups.</label></p>
      ${button('apply','Apply this recovery plan')}${button('back','Back — change nothing')}${choices()}`;
  }
  function render(sess, callback) {
    session=sess; refresh=callback;
    const state=sess.batch_recovery;
    if (!state || state.status==='complete') {document.getElementById('batchRecoveryPanel')?.remove();return false;}
    hideNormal();
    const role=document.getElementById('roleChip');if(role)role.textContent=state.status==='blocked' ? 'Recovery paused' : 'Recipe recovery';
    const badge=document.getElementById('focusStepBadge');if(badge)badge.textContent=`${state.cursor+1} / ${state.tasks.length}`;
    const batch=document.getElementById('batchReadout');if(batch)batch.textContent=`${state.plan.new_ml} ml total`;
    const progress=document.getElementById('progLabel');if(progress)progress.textContent=`Recovery ${state.cursor+1}/${state.tasks.length}`;
    const measured=document.getElementById('focusMeasured');if(measured)measured.textContent='';
    const p=state.plan, task=state.tasks[state.cursor], box=panel();
    const intro=`<h2>Recipe recovery — ${p.new_ml} ml ${p.bottle_count===2 ? `in two ${p.bottle_ml} ml bottles` : 'in one vessel'}</h2><p>Step ${state.cursor+1} of ${state.tasks.length}. Saved automatically after each confirmation. You can leave and resume here.</p>`;
    if (state.status==='blocked') {
      box.innerHTML=intro+`<p role="alert">${escape(state.message)}</p><p>All recorded weights have been kept. No further pour or completion is allowed.</p>`+(!state.split_done ? choices() : '<p>Keep Bottle 1 and Bottle 2 labelled. This split batch needs a separate correction; do not pour mixed liquid back into an ingredient bottle.</p>')+`<details><summary>I only typed the weight incorrectly</summary><p>Use this only to correct a recorded value, never to pretend an actual overpour was removed.</p><label>Correct scale reading (g) <input id="recoveryWeight" type="number" min="0" step="0.01" style="width:9rem;color:#111"></label><p><label><input id="recoveryReadingError" type="checkbox"> The entry was wrong; this corrected reading matches the liquid actually poured.</label></p>${button('correct','Correct the recorded weight')}</details>`;return true;
    }
    const i=task.key ? p.ingredients.find(i=>i.key===task.key) : null;
    let content='';
    if (task.type==='prepare_vessel') content=`<h3>Prepare and label your bottles</h3><p>${p.bottle_count===2 ? 'Keep the current mixture in Bottle 1. Label a matching empty bottle Bottle 2. We will correct the concentrates before splitting.' : 'Use the confirmed mixing vessel. If the current bottle is too small, transfer the whole mix to it before any top-up. Retain all the liquid; do not discard or return any to stock.'}</p>`+button('advance','Bottles ready; whole mix retained');
    if (task.type==='tare') content=`<h3>${bottle(task.bottle)} — prepare ${escape(i.payload.brand)} ${escape(i.payload.name)}</h3><p>Bring this ingredient back from the used side if needed. Place the selected mixing bottle on the scale and tare once for this additional pour. The next target is only the extra amount needed.</p>${i.payload.category==='nicotine' ? '<p><strong>Gloves on. Verify the original nicotine base strength and carrier. This bottle already contains mixed liquid: never pour it back into the nicotine stock.</strong></p>' : ''}`+button('advance','Correct bottle on scale; scale zeroed');
    if (task.type==='pour') content=`<h3>${bottle(task.bottle)} — add ${escape(i.payload.brand)} ${escape(i.payload.name)}</h3><p style="font-size:1.7rem">Additional target: <strong>${grams(task.target_grams)} g</strong></p><p>Continue from the current reading to this target. <strong>Do not tare again during this pour.</strong> Do not add a full recipe dose again.</p><label>Current scale reading (g) <input id="recoveryWeight" inputmode="decimal" type="number" min="0" step="0.01" value="${task.reading == null ? '' : task.reading}" style="font-size:1.5rem;width:9rem;color:#111"></label><p>Remaining: ${grams(Math.max(0,task.target_grams-(task.reading || 0)))} g. Any excess stops this recovery for a fresh review.</p>`+button('advance','Check weight');
    if (task.type==='move_ingredient') content=`<h3>${escape(i.payload.brand)} ${escape(i.payload.name)} top-up complete</h3><p>Cap the ingredient bottle and move it to the LEFT used side. Keep ${bottle(task.bottle).toLowerCase()} on the bench. We will explicitly ask for an ingredient again if another bottle needs it.</p>`+button('advance','Ingredient bottle moved left');
    if (task.type==='mix') content=`<h3>${bottle(task.bottle)} — mix thoroughly</h3><p>Cap and mix the liquid until uniform before continuing.${!state.split_done && p.bottle_count===2 ? ' Do not split an unmixed concentrate: both bottles must receive the same composition.' : ''}</p>`+button('advance','Mixed thoroughly');
    if (task.type==='transfer') {
      const target=Object.values(state.actuals).reduce((a,b)=>a+Number(b),0)/2;
      content=`<h3>Split the mixed concentrate into two bottles</h3><p>Place the <strong>empty Bottle 2</strong> on the scale and tare it. Transfer the thoroughly mixed liquid from Bottle 1 into Bottle 2 until the scale reads <strong>${grams(target)} g</strong>. Leave the rest in Bottle 1. Do not tare while transferring.</p><p>This weight is liquid only; do not include either bottle's empty weight. Record the amount actually transferred. Transfer only between these two mixing bottles, never back to stock. PG/VG targets will use this measured split.</p><label>Liquid transferred to Bottle 2 (g) <input id="recoveryWeight" type="number" min="0" step="0.01" value="${task.reading ?? ''}" style="width:9rem;color:#111"></label>`+button('advance','Check transferred weight');
    }
    if (task.type==='select_bottle') content=`<h3>Use Bottle ${task.bottle}</h3><p>Set the other bottle aside, labelled. Finish these carrier pours in <strong>Bottle ${task.bottle}</strong> only.</p>`+button('advance',`Bottle ${task.bottle} selected`);
    if (task.type==='finish') content=`<h3>Check and label the recovered batch</h3><p>${p.bottle_count===2 ? 'Both bottles have their own completed PG/VG pours. Check both bottle levels and caps.' : 'Check the final volume and cap.'} Label with recipe, nicotine strength, date and recovery. Weights and the original mistake stay in batch history.</p>`+button('advance',`Save completed ${p.bottle_count===2 ? 'two-bottle batch' : 'batch'}`);
    box.innerHTML=intro+(state.message ? `<p role="alert">${escape(state.message)}</p>`:'')+content;
    return true;
  }
  document.addEventListener('click',async event=>{
    const target=event.target.closest('[data-batch-action]'); if(!target || busy)return;
    const action=target.dataset.batchAction;
    if(action==='preview') return preview(target.dataset.mode);
    if(action==='back') {plan=null;panel().remove();return refresh(session);}
    if(action==='apply') {
      const confirmed=document.getElementById('recoveryConfirmed').checked;
      const body=await request('apply',{mode:plan.mode,new_ml:plan.new_ml,fingerprint:plan.fingerprint,capacity_ml:document.getElementById('recoveryCapacity').value,confirmed});
      if(body)refresh(body.session);
    }
    if(action==='advance' || action==='correct') {
      const state=session.batch_recovery;
      const body=await request('advance',{revision:state.revision,cursor:state.cursor,weight:document.getElementById('recoveryWeight')?.value,recording_error:action==='correct' && document.getElementById('recoveryReadingError')?.checked===true});
      if(body)refresh(body.session);
    }
  });
  window.BatchRecoveryUI={render,bind(sess,callback){session=sess;refresh=callback;},escape};
})();
