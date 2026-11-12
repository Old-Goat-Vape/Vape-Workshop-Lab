// Run with: node app/tests/test_vc_import_ui.js
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../templates/master_admin_console.html'), 'utf8');
const elements = {};
const context = {document: {getElementById: id => elements[id] || null}, setTimeout: () => {}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('async function scraperResponse'), source.indexOf('async function triggerScraper')), context);
(async () => {
  await assert.rejects(context.scraperResponse({status:502,text:async()=>'<html>Bad gateway</html>'}), /HTTP 502/);
  await assert.rejects(context.scraperResponse({status:200,redirected:true,url:'https://site/login'}), /session expired/);
  for (const id of ['vcJobStatus','vcJobPause','vcJobResume','vcJobRetry','vcJobReport','vcJobError']) elements[id] = {classList:{remove:()=>{}}};
  context.renderVcJob({id:1,status:'completed_with_errors',pages_read:236,total_pages:236,total_items:2353,discovery_done:true,
    discovered:2353,imported:2352,pending:0,failed:1,failures:[{recipe_id:42,error:'<script>untrusted</script>'}]});
  assert.match(elements.vcJobStatus.textContent, /2353/);
  assert.match(elements.vcJobStatus.textContent, /completed with errors/);
  assert.equal(elements.vcJobRetry.disabled, false);
  assert.equal(elements.vcJobStatus.innerHTML, undefined);
  assert.equal(elements.vcJobReport.href, '/master-admin/vc-import/1/report');
  console.log('VC import UI checks passed.');
})().catch(error => {console.error(error); process.exit(1);});
