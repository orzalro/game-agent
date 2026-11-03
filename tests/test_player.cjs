// Logic integration test; no real browser or UI automation is involved.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const base = path.join(__dirname, '../examples/menu_demo');
const scenario = JSON.parse(fs.readFileSync(path.join(base, 'scenario.json')));
const elements = new Map();
let drawn;
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    listeners: {}, textContent: '', hidden: true,
    addEventListener(event, fn) { this.listeners[event] = fn; },
    getContext() { return {drawImage(image) {drawn = image.src;}}; },
    getBoundingClientRect() { return {left: 10, top: 20, width: 480, height: 270}; },
    replaceChildren() {}
  });
  return elements.get(id);
}
const context = vm.createContext({
  document: {querySelector: element, createElement: () => ({})},
  Image: class {set src(value) {this._src=value;this.onload();} get src(){return this._src;}},
  fetch: async()=>({ok:true,json:async()=>scenario}),
  performance:{now:()=>1}, URLSearchParams, location:{search:''}, console,
});
const source = fs.readFileSync(path.join(base,'index.html'),'utf8').split('<script>')[1].split('</script>')[0];
vm.runInContext(source, context);
setImmediate(()=>{
  assert.equal(drawn, 'frames/menu.svg');
  const click = (x,y)=>element('#game').listeners.click({clientX:10+x/2,clientY:20+y/2});
  click(1,1);assert.equal(drawn,'frames/menu.svg');
  click(480,395);assert.equal(drawn,'frames/missions.svg');
  click(480,395);assert.equal(drawn,'frames/reward.svg');
  click(480,395);assert.equal(drawn,'frames/done.svg');
  assert.match(element('#status').textContent,/완료/);
  click(480,395);assert.equal(drawn,'frames/done.svg');
  element('#reset').onclick();assert.equal(drawn,'frames/menu.svg');
  console.log('Player logic: wrong click, scaled coordinates, 3 transitions, terminal state, reset PASS');
});
