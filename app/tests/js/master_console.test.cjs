const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const templates = path.join(__dirname, '../../templates');
const source = fs.readFileSync(path.join(templates,'master_admin_console.html'),'utf8');
const functions = source.slice(source.indexOf('async function classifySingleFlavor('),source.indexOf('function recipeAnalyticsBox()'));
(async () => {
  const box = {};
  const context = {document:{getElementById:()=>({value:'626'})},classificationResultBox:()=>box,confirm:()=>true};
  vm.createContext(context);
  vm.runInContext(functions,context);
  context.fetch = async () => ({redirected:false,status:500,headers:{get:()=> 'text/html'}});
  await context.previewSingleFlavorClassification();
  assert.match(box.textContent,/HTTP 500/);
  assert.match(box.className,/text-danger/);
  context.fetch = async () => ({redirected:true});
  await context.previewSingleFlavorClassification();
  assert.match(box.textContent,/sign in again/);
  context.fetch = async () => {throw new Error('Network unavailable');};
  await context.previewSingleFlavorClassification();
  assert.equal(box.textContent,'Network unavailable');
  context.fetch = async () => ({redirected:false,ok:true,headers:{get:()=> 'application/json'},json:async()=>({status:'success',result:{resolved_profile:'Bakery',confidence:0.9}})});
  await context.previewSingleFlavorClassification();
  assert.match(box.textContent,/Bakery/);
  assert.match(box.textContent,/preview/);
  const base = fs.readFileSync(path.join(templates,'base.html'),'utf8');
  const sidebar = base.slice(base.indexOf("    document.querySelectorAll('a.goat-nav[href]')"),base.indexOf('</script>',base.indexOf('// Sidebar active')));
  let active = false;
  const links = [
    {getAttribute:()=> '/master-admin?tab=users',classList:{add:()=>{active=true;}}},
    {getAttribute:()=> 'http://[',classList:{add:()=>{throw new Error('Invalid link highlighted');}}}
  ];
  vm.runInNewContext(sidebar,{URL,document:{baseURI:'https://example.test/',querySelectorAll:(selector)=>{assert.equal(selector,'a.goat-nav[href]');return links;}},window:{location:{pathname:'/master-admin',search:'?tab=users',href:'https://example.test/master-admin?tab=users'}}});
  assert.equal(active,true);
  console.log('Master console UI regressions passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
