const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class El {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.hidden=false;this.textContent='';this.className='';this.parentElement=null;this.animations=0;}
 append(...els){for(const el of els){el.remove();el.parentElement=this;this.children.push(el);}}
 replaceChildren(...els){for(const c of [...this.children])c.remove();this.append(...els);}
 remove(){if(this.parentElement){const a=this.parentElement.children;a.splice(a.indexOf(this),1);this.parentElement=null;}}
 setAttribute(k,v){this[k]=v;}
 querySelector(sel){const cls=sel.slice(1);for(const el of this.children){if(el.className.split(' ').includes(cls))return el;const nested=el.querySelector(sel);if(nested)return nested;}return null;}
 getBoundingClientRect(){return {x:this.parentElement?.x||0,y:0,width:this.parentElement?100:0,height:100};}
 animate(){this.animations++;}
}
const ids=Object.fromEntries(['usedPanel','todoPanel','benchCurrentBottle','benchCurrentEmpty','benchLiquid','benchFillCaption'].map(k=>[k,new El()]));
let reduce=false;const context={document:{createElement:t=>new El(t),getElementById:id=>ids[id]},window:{matchMedia:()=>({matches:reduce})}};
vm.runInNewContext(fs.readFileSync('app/static/tvw-bench-visuals.js','utf8'),context);
const render=context.window.TVWBenchVisuals.render;
const first={key:1,shortName:'<img onerror=bad>',brand:'Brand A',category:'nicotine'},second={key:2,shortName:'Custard',brand:'Brand B'};
render({ready:[first,second],fill:{pct:0,doneMl:0,targetMl:30}});
const right=ids.todoPanel.querySelector('.bench-bottle-tray'),left=ids.usedPanel.querySelector('.bench-bottle-tray');right.x=300;left.x=-300;ids.benchCurrentBottle.x=0;
const same=right.children[0];assert.equal(same.querySelector('.bench-ingredient-name').textContent,first.shortName);assert.equal(right.children.length,2);
render({ready:[second],current:first,fill:{pct:0,doneMl:0,targetMl:30}});
assert.equal(ids.benchCurrentBottle.children[0],same);assert.equal(right.children.length,1);assert.equal(same.animations,1);assert.equal(ids.benchLiquid.style.height,'0%');
render({done:[first],ready:[second],fill:{pct:10,doneMl:3,targetMl:30}});
assert.equal(left.children[0],same);assert.equal(ids.benchCurrentBottle.children.length,0);assert.equal(same.dataset.position,'done');assert.equal(ids.benchCurrentEmpty.hidden,false);assert.match(ids.benchFillCaption.textContent,/3.00 \/ 30 ml/);
reduce=true;const animations=same.animations;render({ready:[first,second],fill:{pct:999,doneMl:31,targetMl:30}});assert.equal(same.animations,animations);assert.equal(ids.benchLiquid.style.height,'100%');assert.equal(left.children.length,0);
render({ready:[{...second,key:3,brand:'Different brand'},second],fill:{pct:-10}});assert.equal(right.children.length,2);assert.equal(ids.benchLiquid.style.height,'0%');
// Exercise the existing template's state adapter and fill calculator, independent of controls.
const template=fs.readFileSync('app/templates/tvw_guided_mix_session.html','utf8');
const section=(start,end)=>template.slice(template.indexOf(start),template.indexOf(end,template.indexOf(start)));
const state={stepTypeFor:s=>s.step_type,cleanDisplayName:s=>s,ingredientLabel:p=>p.name,window:{TVWBenchVisuals:{render:x=>state.last=x}},sessionBatchMl:()=>30,goatLine:{}};
vm.createContext(state);
vm.runInContext(section('  const computeBottleFill =','  const pourVolumeFor =')+section('  const isBottlePhysicallyLeft =','  const sessionBatchMl =')+section('  const renderBenchPanels =','  const updateCenterBatchMeta =')+'this.paint=renderBenchPanels;',state);
const pour=(n,name,extra={})=>({step_no:n,step_type:'add_ingredient',payload:{name,brand:'Test',ml:3,target_grams:3,...extra}});
const a=pour(2,'Nicotine'),b=pour(5,'Custard');
state.paint({steps:[a,b]}, {step_type:'instruction'}, 'instruction');assert.equal(state.last.ready.length,2);assert.equal(state.last.current,null);
state.paint({steps:[a,b]}, a, 'add_ingredient');assert.equal(state.last.current.key,2);assert.equal(state.last.ready.length,1);assert.equal(state.last.fill.doneMl,0);
a.completed_at='today';a.payload.measured_grams=3;
state.paint({steps:[a,b]}, {step_no:3,step_type:'transition'}, 'transition');assert.equal(state.last.current.key,2);assert.equal(state.last.done.length,0);assert.equal(state.last.fill.doneMl,3);
a.payload.bottle_moved=true;state.paint({steps:[a,b]},b,'add_ingredient');assert.equal(state.last.done[0].key,2);assert.equal(state.last.current.key,5);
b.completed_at='today';b.payload.overpour_recovery_pending=true;state.paint({steps:[a,b]},{step_no:6,step_type:'recovery'},'recovery');assert.equal(state.last.current.key,5);assert.equal(state.last.fill.doneMl,3);
console.log('Bench visuals passed: stable right/centre/left movement, safe labels, undo, reduced motion, fill bounds, preparation, transition and pending recovery.');
