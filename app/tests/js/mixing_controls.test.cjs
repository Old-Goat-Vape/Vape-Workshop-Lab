const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../templates/tvw_guided_mix_session.html'),'utf8');
const scale=require('../../static/guided_scale_input.js');
const context={window:{GuidedScaleInput:scale,__guidedSessionLast:{steps:[]}},activeStepTargetGrams:()=>.31,isPartialScaleEntry:scale.isPartialScaleEntry,parseScaleInputGrams:scale.parseScaleGrams};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('  const resolveMeasuredGramsForStep ='),source.indexOf('  const renderMixSummary ='))+'\nthis.resolve=resolveMeasuredGramsForStep;',context);
assert.equal(context.resolve('',4).ok,false);
assert.equal(context.resolve('   ',4).ok,false);
assert.equal(context.resolve('0',4).grams,0);
assert.equal(context.resolve('0,08',4).grams,.08);
const under=source.slice(source.indexOf('  const syncUnderPourUiFromReading ='),source.indexOf('  const showAlarm ='));
vm.runInContext('let underPourUi={stepNo:4,measured:.2,partialGrams:.1};'+under+'\nthis.sync=syncUnderPourUiFromReading;this.under=()=>underPourUi;',context);
context.sync(4,{reading:{measured_grams:0,partial_grams:0},session:{steps:[]}});
assert.equal(context.under().measured,0);
assert.equal(context.under().partialGrams,0);
const alarm=source.slice(source.indexOf('  const showAlarm ='),source.indexOf('  document.getElementById("guidedRoot").addEventListener'));
context.stepTypeFor=()=> 'add_ingredient';
vm.runInContext('underPourUi=null;let nicotineRecoveryUi={mode:"pour_back"};'+alarm+'\nthis.alarm=showAlarm;',context);
// No generic warning renderer is supplied: the no-tare/pour-back card must remain intact.
context.alarm({warnings:['Over target']},'Over target',{});
console.log('Mixing control regressions passed');

// Exercise the real delegated click handler: setup/tare/movement need no weight,
// while a pour must still reject a missing or blank field and preserve explicit zero.
const clickContext={window:{},resolveMeasuredGramsForStep:context.resolve,showAlarm(){}};
vm.createContext(clickContext);
let handler;
let input=null;
const posts=[];
const toasts=[];
clickContext.document={getElementById:id=>id==='guidedRoot'?{addEventListener:(_,fn)=>handler=fn}:input};
clickContext.stepTypeFor=step=>step.payload.step_type;
clickContext.completeGuidedStep=async(no,payload)=>{posts.push({no,payload});return {session:{steps:[]},policy:{}};};
clickContext.applySession=()=>{};
clickContext.showToast=message=>toasts.push(message);
clickContext.stepAckMessage=()=>'';
vm.runInContext('let stepSaveInFlight=false,underPourUi=null,nicotineRecoveryUi=null;'+source.slice(source.indexOf('  document.getElementById("guidedRoot").addEventListener'),source.indexOf('  window.__guidedSessionLast = JSON.parse')),clickContext);
const clickDone=()=>handler({target:{closest:selector=>selector==='.js-done-step'?{dataset:{stepNo:1}}:null}});
(async()=>{
  for(const type of ['instruction','tare_scale','move_bottle','recovery']){
    clickContext.window.__guidedSessionLast={steps:[{step_no:1,payload:{step_type:type}}]};
    await clickDone();
    assert.equal(posts.length,1,type+' must advance without a weight');posts.length=0;
  }
  clickContext.window.__guidedSessionLast={steps:[{step_no:1,payload:{step_type:'add_ingredient'}}]};
  await clickDone();assert.equal(posts.length,0);
  input={value:'',focus(){}};await clickDone();assert.equal(posts.length,0);
  input.value='0';await clickDone();assert.equal(posts[0].payload.measured_grams,0);
  console.log('Non-pour progression and pour-only weight validation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
