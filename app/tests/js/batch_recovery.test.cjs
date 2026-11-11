const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const nodes=new Map();
class Node {
  constructor(){this.dataset={};this.style={};this.value='';this.checked=false;this.classList={add(){},remove(){}};this.parentElement={appendChild:n=>nodes.set(n.id,n)};}
  set innerHTML(html){this.html=html;for(const match of html.matchAll(/id="([^"]+)"([^>]*)/g)){const n=new Node();n.value=(match[2].match(/value="([^"]*)"/)||[])[1]||'';nodes.set(match[1],n);}}
  get innerHTML(){return this.html||'';}
  querySelectorAll(){return [];}
  querySelector(){return null;}
  prepend(n){this.error=n;}
  setAttribute(){}
  remove(){nodes.delete(this.id);}
}
for(const id of ['focusCard','usedPanel','todoPanel','completionSummary','roleChip','focusStepBadge','batchReadout'])nodes.set(id,new Node());
let click;
const context={window:{},document:{getElementById:id=>nodes.get(id),createElement:()=>new Node(),addEventListener:(event,fn)=>{click=fn;}},fetch:null};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/batch_recovery.js'),'utf8'),context);
const api=context.window.BatchRecoveryUI;
const ingredient={key:'nic',payload:{brand:'QA <test>',name:'Nicotine base',category:'nicotine'},actual_grams:.93,total_target_grams:1.86,add_grams:.93,display_add_grams:.93};
const plan={original_ml:30,new_ml:60,minimum_ml:45,bottle_count:2,bottle_ml:30,partial_ml:7.8,mode:'two_bottles',fingerprint:'server-token',ingredients:[ingredient],caution:'Use recorded amounts'};
const initial={id:42};
let latest=initial;
const refresh=s=>{latest=s;api.render(s,refresh);};
api.bind(initial,refresh);
const action=async(batchAction,mode)=>click({target:{closest:()=>({dataset:{batchAction,mode}})}});
(async()=>{
  context.fetch=async(url,opts)=>{
    assert.equal(JSON.parse(opts.body).mode,'two_bottles');
    return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({status:'success',plan})};
  };
  await action('preview','two_bottles');
  let box=nodes.get('batchRecoveryPanel');
  assert.match(box.innerHTML,/two 30 ml bottles/);
  assert.match(box.innerHTML,/Already in/);
  assert.match(box.innerHTML,/QA &lt;test&gt;/);
  assert(!box.innerHTML.includes('QA <test>'));
  const tasks=[{type:'prepare_vessel'},{type:'tare',key:'nic',bottle:0},{type:'pour',key:'nic',bottle:0,target_grams:.93},{type:'move_ingredient',key:'nic',bottle:0},{type:'mix',bottle:0},{type:'transfer'},{type:'select_bottle',bottle:1},{type:'finish'}];
  const state={status:'active',revision:1,cursor:0,plan,tasks,actuals:{nic:1.86},split_done:false};
  nodes.get('recoveryConfirmed').checked=true;
  context.fetch=async(url,opts)=>{
    const data=JSON.parse(opts.body);assert.equal(data.fingerprint,'server-token');assert.equal(data.confirmed,true);
    return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({status:'success',session:{id:42,batch_recovery:state}})};
  };
  await action('apply');
  assert.match(box.innerHTML,/Prepare and label/);
  for(let cursor=1;cursor<tasks.length;cursor++){
    api.render({id:42,batch_recovery:{...state,cursor}},refresh);
    const html=box.innerHTML;
    if(tasks[cursor].type==='tare')assert.match(html,/never pour it back into the nicotine stock/);
    if(tasks[cursor].type==='pour'){assert.match(html,/Additional target/);assert.match(html,/Do not tare again/);assert.equal(nodes.get('recoveryWeight').value,'');}
    if(tasks[cursor].type==='move_ingredient')assert.match(html,/LEFT used side/);
    if(tasks[cursor].type==='transfer'){assert.match(html,/empty Bottle 2/);assert.match(html,/0.93 g/);assert.match(html,/mixing bottles/);}
    if(tasks[cursor].type==='select_bottle')assert.match(html,/Bottle 1/);
  }
  api.render({id:42,batch_recovery:{...state,status:'blocked',split_done:true,message:'Carrier over target'}},refresh);
  assert.match(box.innerHTML,/Carrier over target/);
  assert(!box.innerHTML.includes('data-mode="two_bottles"'));
  api.render({id:42,batch_recovery:{...state,status:'complete'}},refresh);
  assert(!nodes.has('batchRecoveryPanel'));
  // HTML/sign-in errors are visible and never leave an unhandled JSON rejection.
  api.bind(initial,refresh);
  context.fetch=async()=>({redirected:true,headers:{get:()=> 'text/html'}});
  await action('preview','two_bottles');
  assert.match(nodes.get('batchRecoveryPanel').error.textContent,/expired/);
  console.log('Two-bottle recovery UI regressions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
