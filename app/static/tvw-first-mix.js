(() => {
  const root=document.querySelector('.first-mix');
  if(!root)return;
  const panels=Array.from(root.querySelectorAll('.guide-panel'));
  const links=Array.from(root.querySelectorAll('.guide-steps a'));
  const controls=root.querySelector('.guide-controls');
  const previous=document.getElementById('guidePrevious'),next=document.getElementById('guideNext');
  const complete=document.getElementById('guideComplete'),status=document.getElementById('guideProgress');
  const key=`tvw-first-mix:v1:${root.dataset.guideUser}`;
  let index=0,done=false,storage=true;
  try{const saved=JSON.parse(localStorage.getItem(key));if(saved&&Number.isInteger(saved.step)&&saved.step>=0&&saved.step<panels.length){index=saved.step;done=saved.done===true;}}catch(_){storage=false;}
  const save=()=>{try{localStorage.setItem(key,JSON.stringify({step:index,done}));}catch(_){storage=false;}};
  const show=(target,focus=true)=>{
    index=Math.max(0,Math.min(panels.length-1,target));
    panels.forEach((panel,n)=>{panel.hidden=n!==index;});
    links.forEach((link,n)=>{if(n===index)link.setAttribute('aria-current','step');else link.removeAttribute('aria-current');});
    previous.disabled=index===0;next.hidden=index===panels.length-1;complete.hidden=index!==panels.length-1;
    save();
    status.textContent=done?'Walkthrough complete. Revisit any step whenever you need it.':`Step ${index+1} of ${panels.length}. ${storage?'Your place is remembered in this browser.':'Browser storage is unavailable; your place is kept only while this page stays open.'}`;
    if(focus){history.replaceState(null,'',`#guide-${index+1}`);panels[index].focus();}
  };
  links.forEach((link,n)=>link.addEventListener('click',event=>{event.preventDefault();show(n);}));
  previous.addEventListener('click',()=>show(index-1));next.addEventListener('click',()=>show(index+1));
  complete.addEventListener('click',()=>{done=true;show(index,false);});
  document.getElementById('guideRestart').addEventListener('click',()=>{done=false;show(0);});
  controls.hidden=false;
  const hash=location.hash.match(/^#guide-([1-5])$/);if(hash)index=Number(hash[1])-1;
  show(index,false);
})();
