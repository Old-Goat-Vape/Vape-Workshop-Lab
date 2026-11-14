// Run: node app/tests/test_builder_flavour_usage.js
const assert = require('node:assert/strict');
const FlavourUsage = require('../static/tvw-flavour-usage.js');
function deferred() { let resolve, reject; const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}; }
function setup(value='') {
  const pct={value,addEventListener(name,fn){this[name]=fn;}};
  const id={value:'101'}, label={textContent:''},button={hidden:true,addEventListener(name,fn){this[name]=fn;}};
  const elements={'.ingredient-pct':pct,'.ingredient-id':id,'.row-usage-text':label,'.row-use-average':button};
  const row={isConnected:true,querySelector:selector=>elements[selector]};
  const requests=[];let changes=0;
  const usage=new FlavourUsage(row,()=>{const d=deferred();requests.push(d);return d.promise;},()=>changes++);
  return {pct,id,label,button,row,requests,usage,changes:()=>changes};
}
(async()=>{
  const guide=(percentage,days,kind='steep_days')=>({percentage,knowledge:{steep_sources:[{kind,high:days}]}});
  assert.deepEqual(FlavourUsage.steepPlan([guide(1,3),guide(2,14),guide(0,30),{percentage:1}]),{days:14,covered:2,total:3});
  assert.deepEqual(FlavourUsage.steepPlan([guide(1,7,'tested_steep')]),{days:null,covered:0,total:1});
  assert.deepEqual(FlavourUsage.steepPlan([guide(1,0)]),{days:0,covered:1,total:1});
  assert.deepEqual(FlavourUsage.steepPlan([guide(0,14)]),{days:null,covered:0,total:0});
  const a=setup();let pending=a.usage.select(101);a.requests[0].resolve({average:2.345,count:148});await pending;
  assert.equal(a.pct.value,'2.35');assert.match(a.label.textContent,/148 recipes/);assert.equal(a.changes(),1);
  a.pct.value='3';a.pct.input();assert.equal(a.pct.value,'3');a.button.click();assert.equal(a.pct.value,'2.35');
  a.id.value='102';pending=a.usage.select(102);assert.equal(a.pct.value,'');a.requests[1].resolve({average:5,count:2});await pending;
  assert.equal(a.pct.value,'5');assert.match(a.label.textContent,/Small sample/);
  const b=setup('7');pending=b.usage.select(101,{autofill:false});b.requests[0].resolve({average:2,count:5});await pending;assert.equal(b.pct.value,'7');
  const c=setup();pending=c.usage.select(101);c.pct.value='9';c.pct.input();c.requests[0].resolve({average:2,count:5});await pending;assert.equal(c.pct.value,'9');
  const d=setup();const old=d.usage.select(101);d.id.value='102';const next=d.usage.select(102);
  d.requests[1].resolve({average:4,count:5});await next;d.requests[0].resolve({average:99,count:50});await old;
  assert.equal(d.pct.value,'4');assert.match(d.label.textContent,/4%/);
  const e=setup();pending=e.usage.select(101);e.row.isConnected=false;e.requests[0].resolve({average:2,count:5});await pending;assert.equal(e.pct.value,'');
  const f=setup();pending=f.usage.select(101);f.requests[0].resolve({average:null,count:0});await pending;assert.equal(f.pct.value,'');assert.equal(f.button.hidden,true);assert.match(f.label.textContent,/No recipe usage/);
  const g=setup('0');pending=g.usage.select(101);g.requests[0].resolve({average:2,count:10});await pending;assert.equal(g.pct.value,'0');
  const h=setup();pending=h.usage.select(101);h.requests[0].reject(new Error('offline'));await pending;assert.equal(h.pct.value,'');assert.match(h.label.textContent,/Enter your own/);
  const i=setup();pending=i.usage.select(101);i.requests[0].resolve({average:0.004,count:1});await pending;assert.equal(i.pct.value,'0.004');
  const j=setup('7');const observations=[];
  j.usage.onKnowledge=(row,k)=>observations.push(k);
  const stale=j.usage.select(101);j.id.value='102';const current=j.usage.select(102);
  j.requests[1].resolve({average:null,count:0,knowledge:{observation_count:3,suggested_steep_days:14}});await current;
  j.requests[0].resolve({average:5,count:10,knowledge:{observation_count:99}});await stale;
  assert.deepEqual(observations,[null,null,{observation_count:3,suggested_steep_days:14}]);
  assert.equal(j.pct.value,'7'); // Review guidance must not replace a saved/manual percentage.
  j.id.value='';await j.usage.select(0);assert.equal(observations.at(-1),null);
  console.log('Usage suggestions passed: editable defaults, saved/manual values, async races, removed rows, no data, failures, exact-ID switching and tiny percentages.');
})().catch(error=>{console.error(error);process.exit(1);});
