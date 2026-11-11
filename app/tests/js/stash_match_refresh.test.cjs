const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '../../templates/tvw_what_can_i_make.html'), 'utf8');
const source = template.match(/<script>([\s\S]*?)<\/script>/)[1];
let click;
let reloads = 0;
const button = {dataset: {flavourId: '4716'}, textContent: 'Add', disabled: false,
  addEventListener: (_, fn) => { click = fn; }};
const context = {document: {querySelectorAll: () => [button]},
  window: {location: {reload: () => reloads++}}, fetch: null};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  context.fetch = async (_, options) => {
    assert.equal(JSON.parse(options.body).quantity_ml, 10);
    return {ok: true, redirected: false, json: async () => ({status: 'success'})};
  };
  await click();
  assert.equal(reloads, 1);
  assert.equal(button.disabled, true);
  assert.match(button.textContent, /updating recipes/);
  context.fetch = async () => ({ok: true, redirected: true});
  await click();
  assert.equal(reloads, 1);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Retry add');
  context.fetch = async () => ({ok: true, json: async () => ({status: 'error'})});
  await click();
  assert.equal(reloads, 1);
  console.log('Stash addition refresh and retry checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
