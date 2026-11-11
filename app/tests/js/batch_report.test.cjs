const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../templates/tvw_guided_mix_session.html'),'utf8');
const output={innerHTML:'',classList:{add(){},remove(){}}};
const context={document:{getElementById:()=>output},escHtml:s=>String(s),sessionMeta:{}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('  const renderMixSummary ='),source.indexOf('  const syncMixerNotesUi ='))+'\nthis.render=renderMixSummary;',context);
context.render({mix_summary:{recipe_name:'QA simulation',batch_size_ml:15,target_nic_mg_ml:0,
  target_ratio_label:'30/70',estimated_final_ratio_label:'29/71',total_flavor_percent:5,
  estimated_final_flavor_percent:3.217,estimated_final_nicotine_mg_ml:0,estimated_final_volume_ml:14.70}});
assert.match(output.innerHTML,/Recipe flavour/);
assert.match(output.innerHTML,/Estimated final flavour/);
assert.match(output.innerHTML,/3.22%/);
assert.match(output.innerHTML,/Estimated final nicotine/);
assert.match(output.innerHTML,/0.00 mg\/ml/);
context.render({mix_summary:{batch_size_ml:60,target_nic_mg_ml:3,
  target_ratio_label:'30/70',estimated_final_ratio_label:'30/70',adjustments_superseded_by_recovery:true,
  accepted_deviations:[{label:'Earlier Biscuit overpour'}],
  batch_recovery:{status:'complete',original_ml:30,plan:{new_ml:60,bottle_count:2},estimated_volume_ml:59.99,
    estimated_nicotine_mg_ml:2.99,estimated_ratio:'30/70',
    bottles:[{number:1,estimated_volume_ml:30,estimated_nicotine_mg_ml:2.99},
             {number:2,estimated_volume_ml:29.99,estimated_nicotine_mg_ml:2.99}]}}});
assert.match(output.innerHTML,/Bottle 1: estimated 30.00 ml/);
assert.match(output.innerHTML,/Bottle 2: estimated 29.99 ml/);
assert.match(output.innerHTML,/Earlier deviations — rebalanced/);
const scaleContext={};vm.createContext(scaleContext);
vm.runInContext('let nicotineRecoveryUi=null;'+source.slice(source.indexOf('  const activePourScaleGrams ='),source.indexOf('  const syncActivePourReadout ='))+'\nthis.seed=activePourScaleGrams;',scaleContext);
assert.equal(scaleContext.seed({},null,false,1),null);
assert.equal(scaleContext.seed({under_decision_pending:true,measured_grams:0},null,false,1),0);
assert.equal(scaleContext.seed({nicotine_recovery_mode:true,recovery_current_scale_grams:0},null,false,1),0);
console.log('Actual batch composition, bottle report and fresh blank scale checks passed');

const ackContext={stepTypeFor:s=>s.payload.step_type,ingredientLabel:p=>p.name};
vm.createContext(ackContext);
vm.runInContext(source.slice(source.indexOf('  const stepAckMessage ='),source.indexOf('\n  const ',source.indexOf('  const stepAckMessage =')+10))+'\nthis.ack=stepAckMessage;',ackContext);
assert.match(ackContext.ack({steps:[{step_no:1,payload:{step_type:'add_ingredient',name:'Biscuit',measured_grams:0}}]},1),/Zero dose recorded/);
