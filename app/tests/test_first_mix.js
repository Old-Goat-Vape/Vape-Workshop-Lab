const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function setup(saved,blocked=false){
 const make=()=>({hidden:false,disabled:false,textContent:'',events:{},attributes:{},addEventListener(name,fn){this.events[name]=fn;},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},getAttribute(k){return this.attributes[k];},focus(){this.focused=true;}});
 const panels=Array.from({length:5},make),links=Array.from({length:5},(_,i)=>{let link=make();link.attributes.href=`#guide-${i+1}`;return link;});
 const controls=make(),ids=Object.fromEntries(['guidePrevious','guideNext','guideComplete','guideProgress','guideRestart'].map(id=>[id,make()]));
 const root={dataset:{guideUser:'42'},querySelectorAll(s){return s==='.guide-panel'?panels:links;},querySelector(){return controls;}};
 let writes=[],hashes=[];
 vm.runInNewContext(fs.readFileSync(__dirname+'/../static/tvw-first-mix.js','utf8'),{document:{querySelector(){return root;},getElementById(id){return ids[id];}},localStorage:{getItem(){if(blocked)throw Error();return saved;},setItem(k,v){if(blocked)throw Error();writes.push([k,JSON.parse(v)]);}},history:{replaceState(a,b,value){hashes.push(value);}},location:{hash:''}});
 return {panels,links,ids,writes,hashes};
}
let app=setup(null);
assert.equal(app.panels.filter(p=>!p.hidden).length,1);
app.ids.guideNext.events.click();assert.equal(app.panels[1].hidden,false);assert.equal(app.hashes.at(-1),'#guide-2');
app.links[4].events.click({preventDefault(){}});assert.equal(app.panels[4].hidden,false);
app.ids.guideComplete.events.click();assert.equal(app.writes.at(-1)[1].done,true);
assert.equal(app.writes.at(-1)[0],'tvw-first-mix:v1:42');
app.ids.guideRestart.events.click();assert.equal(app.panels[0].hidden,false);assert.equal(app.writes.at(-1)[1].done,false);
app=setup(JSON.stringify({step:3,done:false}));assert.equal(app.panels[3].hidden,false);
app=setup(JSON.stringify({step:999,done:false}));assert.equal(app.panels[0].hidden,false);
app=setup(null,true);assert.equal(app.panels[0].hidden,false);assert.match(app.ids.guideProgress.textContent,/storage is unavailable/);
console.log('First-mix navigation, restore, reset, isolation and storage fallback passed.');
