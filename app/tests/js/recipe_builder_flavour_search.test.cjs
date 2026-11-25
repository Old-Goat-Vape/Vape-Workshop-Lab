const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../../static/tvw-recipe-builder.js"), "utf8");
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const autocomplete = new Function("document", "fetch", "setTimeout", "clearTimeout", `
  let activeAcWrap = null;
  let searchSeq = 0;
  let searchTimer = null;
  const mkLabel = flavour => flavour.name;
  const registerFlavor = () => {};
  const selected = [];
  const selectFlavor = (wrap, flavour) => {
    selected.push(flavour.id);
    closeFlavorMenu(wrap);
  };
  ${section("  const closeFlavorMenu", "  const updateRowBrand")}
  ${section("  const openFlavorMenu", "  const onFlavorInput")}
  ${section("  const wireFlavorAutocomplete", "  let pgVgSyncing")}
  return {scheduleFlavorSearch, wireFlavorAutocomplete, selected};
`);

const fixture = () => {
  const document = {
    createElement: () => ({dataset: {}, setAttribute() {}, addEventListener(event, listener) { this[event] = listener; }}),
  };
  const pending = [];
  const timers = new Set();
  const builder = autocomplete(document, url => new Promise(resolve => pending.push({url, resolve})),
    callback => { timers.add(callback); return callback; }, callback => timers.delete(callback));
  const row = () => {
    const menu = {
      children: [],
      classList: {
        add() { menu.hidden = true; },
        remove() { menu.hidden = false; },
      },
      set innerHTML(_) { this.children = []; },
      replaceChildren(...children) { this.children = children; },
      appendChild(child) { this.children.push(child); },
    };
    const input = {
      value: "",
      setAttribute(_, value) { this.expanded = value; },
      addEventListener(event, listener) { this[event] = listener; },
    };
    const wrap = {
      isConnected: true,
      querySelector: selector => selector === ".flavor-input" ? input : menu,
      contains: element => element === input || menu.children.includes(element),
    };
    builder.wireFlavorAutocomplete({querySelector: selector => selector === ".flavor-input" ? input : wrap});
    return {input, wrap, menu};
  };
  const settle = async (index, items, ok = true) => {
    pending[index].resolve({ok, json: async () => ({items, message: "Search temporarily unavailable"})});
    await Promise.resolve();
    await Promise.resolve();
  };
  const type = (entry, value) => {
    document.activeElement = entry.input;
    entry.input.value = value;
    builder.scheduleFlavorSearch(entry.wrap, entry.input, 120);
  };
  const flushTimers = () => {
    for (const callback of [...timers]) {
      timers.delete(callback);
      callback();
    }
  };
  return {builder, document, pending, row, settle, type, flushTimers};
};

test("clearing a field and replacing a query invalidate the older search immediately", async () => {
  const {row, type, flushTimers, settle, pending} = fixture();
  const first = row();
  type(first, "Biscuit");
  flushTimers();
  type(first, "");
  await settle(0, [{id: 1, name: "Biscuit"}]);
  assert.equal(first.menu.hidden, true);

  type(first, "Biscuit");
  flushTimers();
  type(first, "Vanilla");
  await settle(1, [{id: 1, name: "Biscuit"}]);
  assert.equal(first.menu.hidden, true);
  flushTimers();
  assert.match(pending[2].url, /q=Vanilla/);
  await settle(2, [{id: 2, name: "Vanilla"}]);
  assert.equal(first.menu.children[0].textContent, "Vanilla");
  assert.equal(first.input.expanded, "true");
});

test("switching rows, dismissing the menu, and removing a row ignore late responses", async () => {
  const {row, type, flushTimers, settle} = fixture();
  const first = row();
  const second = row();
  type(first, "Biscuit");
  flushTimers();
  type(second, "Vanilla");
  flushTimers();
  await settle(0, [{id: 1, name: "Biscuit"}]);
  assert.equal(first.menu.hidden, true);
  second.input.keydown({key: "Escape", preventDefault() {}});
  await settle(1, [], false);
  assert.equal(second.menu.hidden, true);

  type(second, "Vanilla");
  flushTimers();
  second.wrap.isConnected = false;
  await settle(2, [{id: 2, name: "Vanilla"}]);
  assert.equal(second.menu.hidden, true);
});

test("a current failed search is announced and an option can be selected by click", async () => {
  const {builder, row, type, flushTimers, settle} = fixture();
  const first = row();
  type(first, "Biscuit");
  flushTimers();
  await settle(0, [], false);
  assert.equal(first.menu.children[0].textContent, "Search temporarily unavailable");
  assert.equal(first.input.expanded, "true");

  type(first, "Biscuit");
  flushTimers();
  await settle(1, [{id: 1, name: "Biscuit"}]);
  first.menu.children[0].click();
  assert.deepEqual(builder.selected, [1]);
  assert.equal(first.input.expanded, "false");
});
