(() => {
  const form = document.getElementById('recipeBuilderForm');
  if (!form) return;
  const cfg = window.TVW_RECIPE_BUILDER;
  const byId = id => document.getElementById(id);
  const photo = byId('storyPreviewImage'), originalPhoto = photo.dataset.uploaded === 'true' ? photo.getAttribute('src') : '';
  let photoUrl = null, dirty = false, submitting = false, pending = null, timer;
  const update = () => {
    byId('storyPreviewName').textContent = byId('recipeName').value.trim() || 'Your recipe name';
    byId('storyPreviewDescription').textContent = byId('shortDescription').value.trim() || 'Give your recipe a little personality.';
    byId('storyPreviewProfiles').textContent = Array.from(form.querySelectorAll('[name="profiles"]:checked')).map(x=>x.value).join(' · ');
    const days = byId('steepDays').value;
    byId('storyPreviewSteep').textContent = days === '' ? 'Steep time: Not set' : Number(days) === 0 ? 'Shake & vape' : `Recommended steep: ${days} days`;
    const selected = Array.from(form.querySelectorAll('[name="profiles"]:checked')).map(x=>x.value);
    const main = byId('mainProfile');
    for (const option of main.options) option.disabled = !!option.value && !selected.includes(option.value);
    if (main.value && !selected.includes(main.value)) main.value = '';
    const tagArt = byId('recipeTags').value.split(',').map(t=>cfg.artworkOptions?.[t.trim().toLowerCase()]).find(Boolean);
    const art = cfg.artworkOptions?.[main.value || selected[0]] || tagArt || cfg.defaultArtwork;
    const uploaded = !!photoUrl || (!!originalPhoto && !byId('removeImage')?.checked);
    photo.hidden = false;
    const nextPhoto = photoUrl || (uploaded ? originalPhoto : art.url);
    if (photo.getAttribute('src') !== nextPhoto) photo.src = nextPhoto;
    photo.alt = uploaded ? 'Your recipe photo preview' : art.label;
    byId('storyArtworkCaption').textContent = uploaded ? 'Your recipe photo' : `Using ${art.label} · Upload your own photo`;
  };
  byId('recipeImage').addEventListener('change', e => {
    if (photoUrl) URL.revokeObjectURL(photoUrl);
    photoUrl = null;
    const file = e.target.files[0], message = byId('imageMessage');
    if (file && (file.size > 5*1024*1024 || !['image/jpeg','image/png','image/webp'].includes(file.type))) {
      message.textContent = 'Choose a JPG, PNG or WebP smaller than 5 MB.'; e.target.value = '';
    } else if (file) {
      photoUrl = URL.createObjectURL(file); photo.src = photoUrl;
      message.textContent = 'Landscape centre crop. Select this photo again if you reopen a draft or a save fails.';
    } else message.textContent = '';
    update();
  });
  for (const [id,value] of [['shakeVape','0'],['clearSteep','']]) byId(id).addEventListener('click',()=>{
    byId('steepDays').value = value; byId('steepDays').dispatchEvent(new Event('input',{bubbles:true}));
  });
  const status = byId('draftStatus'), recovery = byId('draftRecovery');
  const names = ['name','short_description','description','private_notes','steep_days','tags','notes','status','visibility','main_profile'];
  const calcIds = ['calcTargetMl','calcTargetNic','calcPgRatio','calcVgRatio','calcNicBase','calcNicCarrier'];
  try { const saved = JSON.parse(localStorage.getItem(cfg.draftKey)); if (saved?.schema === 1 && Array.isArray(saved.rows)) pending = saved; }
  catch (_) { status.textContent = 'Browser draft storage is unavailable. Keep this page open until you save.'; }
  if (pending) { recovery.hidden = false; status.textContent = 'An unfinished draft is available here. Restore or discard it to start saving a fresh draft.'; }
  const save = () => {
    if (!dirty || pending) return;
    const fields = {};
    for (const name of names) {
      const inputs = Array.from(form.querySelectorAll(`[name="${name}"]`));
      const input = inputs.length > 1 ? inputs.find(x=>x.checked) : inputs[0];
      if (input) fields[name] = input.value;
    }
    const rows = Array.from(form.querySelectorAll('.ingredient-row')).map(row=>({ingredient_id:row.querySelector('.ingredient-id').value,percent:row.querySelector('.ingredient-pct').value,name:row.querySelector('.ingredient-name').value || row.querySelector('.flavor-input').value,brand:row.querySelector('.ingredient-brand').value}));
    try {
      localStorage.setItem(cfg.draftKey,JSON.stringify({schema:1,fields,calc:Object.fromEntries(calcIds.map(id=>[id,byId(id).value])),removeImage:!!byId('removeImage')?.checked,profiles:Array.from(form.querySelectorAll('[name="profiles"]:checked')).map(x=>x.value),rows,savedAt:Date.now()}));
      status.textContent = 'Draft saved in this browser · reselect photo files after reopening.';
    } catch (_) { status.textContent = 'Could not save the browser draft. Keep this page open until you save your recipe.'; }
  };
  byId('restoreDraft').addEventListener('click',()=>{
    if (!pending || (dirty && !window.confirm('Replace the changes on this page with the saved draft?'))) return;
    for (const name of names) for (const input of form.querySelectorAll(`[name="${name}"]`)) {
      if (pending.fields?.[name] == null) continue;
      if (input.type === 'radio') input.checked = input.value === pending.fields[name]; else input.value = String(pending.fields[name]);
    }
    for (const id of calcIds) if (pending.calc?.[id] != null) byId(id).value = pending.calc[id];
    for (const input of form.querySelectorAll('[name="profiles"]')) input.checked = (pending.profiles || []).includes(input.value);
    if (byId('removeImage')) byId('removeImage').checked = !!pending.removeImage;
    window.TVWRestoreRecipeRows(pending.rows);
    pending = null; dirty = true; recovery.hidden = true;
    byId('recipeStatus').dispatchEvent(new Event('change',{bubbles:true})); update(); save();
  });
  byId('discardDraft').addEventListener('click',()=>{
    try { localStorage.removeItem(cfg.draftKey); } catch (_) {}
    pending = null; recovery.hidden = true; status.textContent = 'Old draft discarded. Current changes stay on this page.'; save();
  });
  const changed = () => { dirty = true; update(); clearTimeout(timer); timer = setTimeout(save,600); };
  form.addEventListener('input',changed); form.addEventListener('change',changed);
  byId('ingredientRows').addEventListener('click',changed);
  form.addEventListener('submit',e=>{ save(); submitting = !e.defaultPrevented; });
  window.addEventListener('pagehide',save);
  window.addEventListener('beforeunload',e=>{ if (dirty && !submitting) { save(); e.preventDefault(); e.returnValue = ''; } });
  update();
})();
