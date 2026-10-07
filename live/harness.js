/**
 * harness.js - ruleaza app.js in Node cu un DOM minimal simulat.
 *
 * Nu inlocuieste un browser, dar prinde erorile de runtime care fac pagina
 * alba: referinte la elemente lipsa, metode apelate pe undefined, ordine
 * gresita a initializarilor.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB = path.join(__dirname, 'web');
const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const appjs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');

// ---- id-urile chiar prezente in pagina
const idsInHtml = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
console.log('id-uri in index.html:', idsInHtml.size);

// ---- id-urile cerute de app.js
const idsUsed = new Set([...appjs.matchAll(/getElementById\('([^']+)'\)/g)].map(m => m[1]));
const missing = [...idsUsed].filter(i => !idsInHtml.has(i));
console.log('id-uri cerute de app.js:', idsUsed.size);
console.log(missing.length ? 'LIPSA in HTML: ' + missing.join(', ') : 'toate id-urile cerute exista in HTML');

// ---- DOM simulat
const elements = {};
function makeEl(id) {
  const el = {
    id, textContent: '', innerHTML: '', value: '', checked: id !== 'l-strong',
    title: '', className: '', dataset: {}, style: {}, options: [], selectedIndex: 0,
    clientWidth: 340, clientHeight: 170, width: 340, height: 170,
    addEventListener: () => {},
    appendChild: () => {},
    removeChild: () => {},
    getContext: () => ({
      setTransform() {}, clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {},
      lineTo() {}, stroke() {}, fillText() {}, measureText: () => ({ width: 10 }),
      save() {}, restore() {},
    }),
    querySelectorAll: () => [],
    classList: { add() {}, remove() {}, contains: () => false },
    __proto__: { constructor: { name: 'HTMLDivElement' } },
  };
  return el;
}
idsInHtml.forEach(id => { elements[id] = makeEl(id); });

// select-ul de zona trebuie sa se comporte ca un <select>
['zone', 'range', 'tiles'].forEach(id => {
  if (elements[id]) {
    elements[id].options = [{ textContent: '' }];
    elements[id].selectedIndex = 0;
  }
});

const document = {
  getElementById: id => {
    if (!elements[id]) {
      // comportamentul real: null, iar codul care nu verifica va arunca
      return null;
    }
    return elements[id];
  },
  querySelectorAll: () => [],
  createElement: () => makeEl('tmp'),
  title: '',
  addEventListener: () => {},
  body: makeEl('body'),
  documentElement: makeEl('html'),
};

// ---- Leaflet simulat, cu lanturi de metode
const chain = new Proxy({}, {
  get: (t, k) => {
    if (k === 'getCenter' || k === 'getZoom') return () => ({ lat: 0, lng: 0 });
    if (k === 'addTo' || k === 'clearLayers' || k === 'setView' || k === 'removeLayer' || k === 'addLayer') {
      return () => chain;
    }
    return (...a) => chain;
  },
});
const L = chain;

// ---- window/fetch
let fetchCalls = [];
const window = {
  devicePixelRatio: 1,
  addEventListener: () => {},
  Notification: undefined,
  setTimeout: (f) => setTimeout(f, 0),
  clearTimeout: () => {},
};

const statePath = process.argv[2] || path.join(WEB, 'state', 'state.json');
const realState = JSON.parse(fs.readFileSync(statePath, 'utf8'));
console.log('stare testata:', path.basename(statePath));

const sandbox = {
  document, window, L, console,
  fetch: async (url) => {
    fetchCalls.push(url);
    if (url.includes('state.json')) {
      return { ok: true, json: async () => realState };
    }
    if (url.includes('geojson')) {
      return { ok: true, json: async () => ({ type: 'Polygon', coordinates: [[[0,0],[1,0],[1,1],[0,0]]] }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  },
  Date, Math, JSON, Object, Array, String, Number, Boolean, RegExp, Error, Promise,
  setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
  isNaN, parseInt, parseFloat, undefined,
};
sandbox.globalThis = sandbox;

// ---- rulare
try {
  vm.createContext(sandbox);
  vm.runInContext(appjs, sandbox, { filename: 'app.js' });
  console.log('\nOK: app.js s-a executat fara erori la incarcare');
} catch (e) {
  console.log('\nEROARE la incarcarea app.js:');
  console.log('  ' + e.message);
  console.log(e.stack.split('\n').slice(1, 4).join('\n'));
  process.exit(1);
}

// ---- rulam si ciclul de refresh
(async () => {
  await new Promise(r => setTimeout(r, 200));
  try {
    const st = sandbox.state;
    console.log('\nstare dupa refresh:',
      st ? `zona=${st.name || '?'}, detectii=${Object.keys(st.detections || {}).length}, focare=${(st.fires || []).length}` : 'null');
    console.log('apeluri fetch:', fetchCalls.length);
    console.log('\ncontinutul catorva panouri:');
    for (const id of ['k-24', 'k-total', 'w-temp', 'w-rh', 'b-status', 's-burn', 'w-danger']) {
      const v = (elements[id] || {}).textContent || (elements[id] || {}).innerHTML;
      console.log(`  ${id}: ${String(v).slice(0, 90)}`);
    }
    console.log('  fires (html):', String(elements.fires ? elements.fires.innerHTML : '').slice(0, 80));
  } catch (e) {
    console.log('\nEROARE in ciclul de refresh:');
    console.log('  ' + e.message);
    console.log(e.stack.split('\n').slice(1, 4).join('\n'));
    process.exit(1);
  }
})();
