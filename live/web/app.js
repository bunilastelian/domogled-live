/* app.js - dashboard live pentru incendiul Domogled – Valea Cernei
 *
 * Fara dependinte de graficare: graficul e desenat direct pe <canvas>.
 * Mod local: server.py serveste /api/state. Mod static (GitHub Pages): state/state.json.
 */

const API = '/api/state';
const STATIC_STATE = 'state/state.json';
const REFRESH_MS = 60000;

// praguri de prospetime: CI-ul ruleaza la 15 min, deci peste 25 min e intarziere reala
const STALE_WARN_MIN = 25;
const STALE_ERR_MIN = 60;

const COLORS = { h6: '#e5484d', h24: '#f76b15', d3: '#f5d90a', old: '#6b7684' };
const NASA_C = '#f76b15', ESA_C = '#3b82f6';
// etichetele de baza ale selectorului, ca sa nu le concatenam la fiecare randare
const RANGE_LABELS = { '24': 'ultimele 24 h', '72': 'ultimele 3 zile',
                       '168': 'ultimele 7 zile', '0': 'tot istoricul' };

let map, tileLayers = {}, parkLayer, nasaLayer, esaLayer, trackLayer, burnLayer, refLayer;
let state = null;
let staticMode = false;
let lastRenderSig = '';
let lastBurnSig = '';
let lastAlertKey = null;
let lastUpdated = null;
let renderedRange = null;

// ------------------------------------------------------------------ incarcare
async function loadState() {
  if (!staticMode) {
    try {
      const r = await fetch(API + '?_=' + Date.now(), { cache: 'no-store' });
      if (r.ok) return await r.json();
    } catch (e) { /* trecem pe static */ }
    staticMode = true;
  }
  const r = await fetch(STATIC_STATE + '?_=' + Date.now(), { cache: 'no-store' });
  if (!r.ok) throw new Error('nu pot citi starea');
  return await r.json();
}

// ---------------------------------------------------------------------- harta
function initMap() {
  map = L.map('map', { zoomControl: true, preferCanvas: true }).setView([44.8982, 22.4785], 12);

  const attribution = '© OpenStreetMap';
  tileLayers = {
    sat: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 18, attribution: 'Imagery © Esri, Maxar' }),
    osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      { maxZoom: 19, attribution }),
    dark: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, attribution: '© OpenStreetMap, © CARTO' })
  };
  tileLayers.sat.addTo(map);

  parkLayer = L.layerGroup().addTo(map);
  trackLayer = L.layerGroup().addTo(map);
  burnLayer = L.layerGroup().addTo(map);
  nasaLayer = L.layerGroup().addTo(map);
  esaLayer = L.layerGroup().addTo(map);
  refLayer = L.layerGroup().addTo(map);

  fetch('domogled.geojson').then(r => r.json()).then(geom => {
    L.geoJSON(geom, { style: { color: '#3b82f6', weight: 2, fill: false, opacity: 0.85, dashArray: '6 4' } })
      .addTo(parkLayer);
  }).catch(() => {});

  [['Cascada Cociului', 44.91838, 22.46485, '★'],
   ['Colțu Pietrii 1228 m', 44.90550, 22.48217, '▲'],
   ['Domogledul Mare 1105 m', 44.87161, 22.44364, '▲'],
   ['Băile Herculane', 44.8785, 22.4144, '■']].forEach(([name, lat, lon, sym]) => {
    L.marker([lat, lon], {
      icon: L.divIcon({ className: '', html:
        `<div style="color:#fff;text-shadow:0 0 4px #000,0 0 2px #000;font-size:14px">${sym}</div>`,
        iconSize: [18, 18] })
    }).bindTooltip(name, { permanent: true, direction: 'right', className: 'reflabel' }).addTo(refLayer);
  });
}

function ageHours(d) {
  const t = (d.time || '0000').padStart(4, '0');
  const ms = Date.parse(`${d.date}T${t.slice(0, 2)}:${t.slice(2, 4)}:00Z`);
  return isNaN(ms) ? 1e9 : (Date.now() - ms) / 3600000;
}

const ageColor = h => h <= 6 ? COLORS.h6 : h <= 24 ? COLORS.h24 : h <= 72 ? COLORS.d3 : COLORS.old;
const radiusOf = frp => Math.max(3, Math.min(24, 3 + Math.sqrt(frp || 0) * 1.7));

function renderDetections() {
  nasaLayer.clearLayers();
  esaLayer.clearLayers();
  trackLayer.clearLayers();

  const rangeH = +document.getElementById('range').value;   // 0 = tot
  const onlyStrong = document.getElementById('l-strong').checked;
  const groups = {};
  let shown = 0;

  for (const d of Object.values((state && state.detections) || {})) {
    const h = ageHours(d);
    if (rangeH && h > rangeH) continue;
    if (onlyStrong && (d.frp || 0) < 5) continue;
    shown++;

    const esa = d.src === 'ESA';
    L.circleMarker([d.lat, d.lon], {
      radius: radiusOf(d.frp),
      color: esa ? '#22d3ee' : '#ffffff',
      weight: esa ? 1.8 : 1,
      opacity: 0.9,
      fillColor: ageColor(h),
      fillOpacity: 0.55
    }).bindPopup(
      `<b>${d.sensor}</b><br>FRP: <b>${(d.frp || 0).toFixed(2)} MW</b><br>` +
      (d.bt_k ? `temperatură: ${d.bt_k} K<br>` : '') +
      `încredere: ${d.confidence}%<br>${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}<br>` +
      `achiziție: ${d.date} ${d.time}Z (${h.toFixed(1)} h în urmă)<br>` +
      `zi/noapte: ${d.daynight === 'D' ? 'zi' : 'noapte'}`
    ).addTo(esa ? esaLayer : nasaLayer);

    (groups[`${d.date} ${d.time}`] = groups[`${d.date} ${d.time}`] || []).push(d);
  }

  for (const pts of Object.values(groups)) {
    if (pts.length < 3) continue;
    const s = pts.slice().sort((a, b) => a.lon - b.lon);
    L.polyline(s.map(p => [p.lat, p.lon]),
      { color: ageColor(ageHours(s[0])), weight: 1.4, opacity: 0.5, dashArray: '4 3' }).addTo(trackLayer);
  }
  renderedRange = rangeH;

  // punem numarul afisat in eticheta, pornind mereu de la textul de baza
  const sel = document.getElementById('range');
  const base = RANGE_LABELS[String(rangeH)] || sel.options[sel.selectedIndex].textContent;
  sel.options[sel.selectedIndex].textContent = `${base} (${shown})`;
}

function renderBurn() {
  const b = state && state.burn;
  const el = document.getElementById('s-burn');
  if (!b || !b.bounds) { if (el) el.textContent = 'se calculează…'; return; }
  const sig = `${b.post_id}|${b.updated}`;
  if (sig === lastBurnSig) return;          // nu recrea layer-ul daca nu s-a schimbat
  lastBurnSig = sig;
  burnLayer.clearLayers();
  L.imageOverlay('img/' + b.png, b.bounds, { opacity: 0.75, interactive: false }).addTo(burnLayer);
  if (el) {
    el.innerHTML = `${b.ha_moderat_plus} ha <span style="color:var(--dim)">(≥0,27)</span> · ${b.ha_sever} ha sever`;
    el.title = `dNBR corectat: scena ${b.post_date} față de ${b.base_date}. ` +
               `Total ≥0,10: ${b.ha_total} ha. ${b.nota || ''}`;
  }
}

// ------------------------------------------------------- grafic pe canvas
function niceMax(v) {
  if (v <= 10) return 10;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.5, 2, 2.5, 3, 5, 7.5, 10]) if (v <= m * p) return m * p;
  return 10 * p;
}

function drawChart(days) {
  const cv = document.getElementById('chart');
  if (!cv || !days || !days.length) return;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || 340, h = cv.clientHeight || 170;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);

  const L = 42, R = 4, T = 6, B = 24;
  const pw = w - L - R, ph = h - T - B;
  const peak = Math.max(...days.map(d => (d.frp_nasa || 0) + (d.frp_esa || 0)), 1);
  const ymax = niceMax(peak);

  // grila + etichete pe Y
  g.font = '10px system-ui, sans-serif';
  g.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const y = T + ph - (ph * i / 4);
    g.strokeStyle = i === 0 ? '#3a4553' : '#212a35';
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(L, Math.round(y) + .5); g.lineTo(L + pw, Math.round(y) + .5); g.stroke();
    g.fillStyle = '#8b98a5'; g.textAlign = 'right';
    g.fillText(String(Math.round(ymax * i / 4)), L - 6, y);
  }

  const step = pw / days.length;
  const bw = Math.max(3, Math.min(30, step * 0.62));
  const every = days.length > 16 ? 2 : 1;

  days.forEach((d, i) => {
    const x = L + step * i + (step - bw) / 2;
    let yb = T + ph;
    for (const [val, col] of [[d.frp_nasa || 0, NASA_C], [d.frp_esa || 0, ESA_C]]) {
      if (val <= 0) continue;
      const bh = (val / ymax) * ph;
      yb -= bh;
      g.fillStyle = col;
      g.fillRect(x, yb, bw, Math.max(1, bh));
    }
    if (i % every === 0 || i === days.length - 1) {
      g.fillStyle = '#8b98a5'; g.textAlign = 'center'; g.textBaseline = 'top';
      g.fillText(d.date.slice(5), L + step * i + step / 2, T + ph + 6);
    }
  });

  const top = days.reduce((a, b) => ((b.frp_nasa + b.frp_esa) > (a.frp_nasa + a.frp_esa) ? b : a));
  document.getElementById('chart-note').textContent =
    `vârf: ${top.date.slice(8)}.${top.date.slice(5, 7)} cu ${(top.frp_nasa + top.frp_esa).toFixed(0)} MW ` +
    `(${top.count} detecții) · total ${days.reduce((s, d) => s + d.frp_nasa + d.frp_esa, 0).toFixed(0)} MW în ${days.length} zile`;
}

// ------------------------------------------------------------------ restul
function set(id, txt) { const e = document.getElementById(id); if (e) e.textContent = txt; }

function renderStats() {
  const st = state && state.stats;
  if (!st) return;
  set('k-24', st.last24_count ?? '–');
  set('k-frp', (st.last24_max ?? 0).toFixed(1));
  set('k-total', st.total ?? '–');
  set('k-days', (st.days || []).length);
  set('s-src', `NASA ${st.nasa} · ESA ${st.esa}`);
  set('s-cycles', state.cycles ?? '–');
  set('s-latest', st.latest ? `${st.latest.date} ${st.latest.time}Z · ${(st.latest.frp || 0).toFixed(1)} MW` : '–');
  const aoi = document.getElementById('b-aoi');
  if (aoi && state.aoi) {
    aoi.textContent = state.aoi.name;
    aoi.title = `zona analizată: ${(state.aoi.bbox || []).join(', ')} (W, S, E, N)`;
  }

  const badge = document.getElementById('b-updated');
  const status = document.getElementById('b-status');
  if (!st.updated) return;
  const mins = (Date.now() - Date.parse(st.updated)) / 60000;
  badge.textContent = 'colector: ' + new Date(st.updated).toLocaleString('ro-RO',
    { hour: '2-digit', minute: '2-digit' });
  badge.title = `ultima actualizare: ${new Date(st.updated).toLocaleString('ro-RO')} (${mins.toFixed(0)} min în urmă)`;
  if (mins < STALE_WARN_MIN) {
    status.className = 'badge ok'; status.innerHTML = '<span class="dot"></span>activ';
  } else if (mins < STALE_ERR_MIN) {
    status.className = 'badge warn'; status.textContent = `întârziat ${mins.toFixed(0)} min`;
  } else {
    status.className = 'badge err'; status.textContent = `oprit de ${(mins / 60).toFixed(0)} h`;
  }
}

function renderAlerts() {
  const a = (state && state.alerts) || [];
  const box = document.getElementById('alerts');
  if (!a.length) return;
  box.innerHTML = a.slice(0, 12).map((x, i) =>
    `<div class="alert ${i === 0 ? 'new' : ''}">${x.label}<br>
      <time>${new Date(x.at).toLocaleString('ro-RO')} · ${x.source}${x.telegram ? ' · Telegram' : ''}</time>
     </div>`).join('');

  if (lastAlertKey && a[0].at !== lastAlertKey && document.getElementById('notif').checked &&
      'Notification' in window && Notification.permission === 'granted') {
    new Notification('🔥 Detecție nouă — Domogled', { body: a[0].label });
  }
  lastAlertKey = a[0].at;
}

function renderImagery() {
  const box = document.getElementById('thumbs');
  const items = Object.values((state && state.imagery) || {})
    .sort((a, b) => (b.datetime || '').localeCompare(a.datetime || ''));
  if (!items.length) { box.innerHTML = '<div class="empty">Se așteaptă o scenă Sentinel-2 nouă…</div>'; return; }
  const sig = items.map(i => i.file).join(',');
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = items.slice(0, 6).map(it =>
    `<button class="thumb" onclick="window.open('img/${it.file}','_blank')" title="deschide imaginea mare">
       <img src="img/${it.file}" alt="Sentinel-2 ${it.datetime}" loading="lazy">
       <span>${(it.datetime || '').slice(0, 10)} · nori ${it.cloud ?? '?'}%</span>
     </button>`).join('');
}

// ------------------------------------------------------------------- bucla
async function refresh(force) {
  try {
    const st = await loadState();
    if (st.error) {
      document.getElementById('b-status').className = 'badge warn';
      document.getElementById('b-status').textContent = st.error;
      return;
    }
    state = st;
    const rangeH = +document.getElementById('range').value;
    const sig = `${(st.stats || {}).updated}|${rangeH}|${document.getElementById('l-strong').checked}`;
    const changed = force || sig !== lastRenderSig;

    renderStats();          // mereu: badge-ul de prospetime trebuie actualizat
    if (changed) {
      lastRenderSig = sig;
      renderDetections();
      drawChart((st.stats || {}).days || []);
      renderAlerts();
      renderImagery();
    }
    renderBurn();
    lastUpdated = new Date();
  } catch (e) {
    document.getElementById('b-status').className = 'badge err';
    document.getElementById('b-status').textContent = 'date indisponibile';
  }
}

function bindControls() {
  document.getElementById('range').addEventListener('change', () => refresh(true));
  document.getElementById('l-strong').addEventListener('change', () => refresh(true));
  ['l-nasa', 'l-esa', 'l-park', 'l-tracks', 'l-burn'].forEach(id => {
    document.getElementById(id).addEventListener('change', e => {
      const on = e.target.checked;
      const layer = { 'l-nasa': nasaLayer, 'l-esa': esaLayer, 'l-park': parkLayer,
                      'l-tracks': trackLayer, 'l-burn': burnLayer }[id];
      on ? map.addLayer(layer) : map.removeLayer(layer);
    });
  });
  document.getElementById('tiles').addEventListener('change', e => {
    Object.values(tileLayers).forEach(l => map.removeLayer(l));
    tileLayers[e.target.value].addTo(map);
  });
  document.getElementById('notif').addEventListener('change', e => {
    if (e.target.checked && 'Notification' in window && Notification.permission !== 'granted') {
      Notification.requestPermission();
    }
  });

  let t = 0;
  setInterval(() => {
    if (++t % 5) return;                     // o data la 5 s e destul pentru un contor
    if (!lastUpdated) return;
    const left = Math.max(0, Math.round(REFRESH_MS / 1000) - Math.round((Date.now() - lastUpdated) / 1000));
    set('b-next', `reîmprospătare în ${left}s`);
  }, 1000);

  let rt;
  window.addEventListener('resize', () => {
    clearTimeout(rt);
    rt = setTimeout(() => drawChart(((state || {}).stats || {}).days || []), 180);
  });
}

initMap();
bindControls();
refresh(true);
setInterval(refresh, REFRESH_MS);
