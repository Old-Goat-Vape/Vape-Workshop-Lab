/* Presentation only. No requests, measurements, step completion or recovery decisions. */
(() => {
  const nodes = new Map();
  const make=(tag,cls,text)=>{const el=document.createElement(tag);el.className=cls;if(text!=null)el.textContent=text;return el;};
  const tray=(id,title,kicker)=>{
    const panel=document.getElementById(id);if(!panel)return null;
    let list=panel.querySelector('.bench-bottle-tray');
    if(!list){panel.replaceChildren(make('p','bench-rack-kicker',kicker),make('h2','bench-rack-title',title));list=make('div','bench-bottle-tray');panel.append(list,make('p','bench-rack-empty'));}
    return list;
  };
  const bottle=item=>{
    const key=String(item.key);let el=nodes.get(key);
    if(!el){el=make('div','bench-ingredient');el.dataset.benchKey=key;
      const art=make('div','bench-mini-bottle');art.setAttribute('aria-hidden','true');art.append(make('span','bench-bottle-cap'),make('span','bench-bottle-label','TVW'));
      el.append(art,make('strong','bench-ingredient-name'),make('span','bench-ingredient-brand'),make('span','bench-ingredient-state'));nodes.set(key,el);}
    el.querySelector('.bench-ingredient-name').textContent=item.shortName||item.label||'Ingredient';
    el.querySelector('.bench-ingredient-brand').textContent=item.brand||'';el.dataset.kind=String(item.category||'').toLowerCase();return el;
  };
  window.TVWBenchVisuals={render({done=[],ready=[],current=null,fill={}}){
    const left=tray('usedPanel','Already added','LEFT · PUT AWAY'),right=tray('todoPanel','Ready to pour','RIGHT · POUR ORDER'),hand=document.getElementById('benchCurrentBottle');
    if(!left||!right||!hand)return;
    const positions=new Map(Array.from(nodes,([k,el])=>[k,el.getBoundingClientRect()])),active=new Set();
    const place=(items,target,state)=>items.forEach((item,i)=>{const el=bottle(item);active.add(String(item.key));el.dataset.position=state;
      el.querySelector('.bench-ingredient-state').textContent=state==='done'?'✓ Put away':state==='current'?'Current ingredient':i===0?'Up next':`In line · ${i+1}`;target.append(el);});
    place(done,left,'done');place(ready,right,'ready');place(current?[current]:[],hand,'current');
    for(const [key,el] of nodes)if(!active.has(key)){el.remove();nodes.delete(key);}
    for(const [list,count,message] of [[left,done.length,'Finished ingredient bottles move here.'],[right,ready.length,'No bottles waiting.']]){const empty=list.parentElement.querySelector('.bench-rack-empty');empty.textContent=count?'':message;empty.hidden=!!count;}
    document.getElementById('benchCurrentEmpty').hidden=!!current;
    document.getElementById('benchLiquid').style.height=`${Math.max(0,Math.min(100,Number(fill.pct)||0))}%`;
    document.getElementById('benchFillCaption').textContent=`Estimated fill · ${Math.max(0,Number(fill.doneMl)||0).toFixed(2)} / ${Number(fill.targetMl)||0} ml`;
    if(!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)for(const [key,el]of nodes){const old=positions.get(key),now=el.getBoundingClientRect();
      if(old?.width&&now.width&&(Math.abs(old.x-now.x)>2||Math.abs(old.y-now.y)>2))el.animate?.([{transform:`translate(${old.x-now.x}px,${old.y-now.y}px)`,opacity:.6},{transform:'translate(0,0)',opacity:1}],{duration:400,easing:'ease-out'});}
  }};
})();
