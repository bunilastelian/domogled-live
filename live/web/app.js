/* app.js - dashboard live pentru incendiul Domogled – Valea Cernei
 *
 * Fara dependinte de graficare: graficul e desenat direct pe <canvas>.
 * Mod local: server.py serveste /api/state. Mod static (GitHub Pages): state/state.json.
 */

const API = '/api/state';
const STATIC_STATE = 'state/state.json';
const REFRESH_MS = 60000;
const BUILD = 'v3.1 · 2026-10-07';

// praguri de prospetime: CI-ul ruleaza la 15 min, deci peste 25 min e intarziere reala
const STALE_WARN_MIN = 25;
const STALE_ERR_MIN = 60;

const COLORS = { h6: '#e5484d', h24: '#f76b15', d3: '#f5d90a', old: '#6b7684' };
const NASA_C = '#f76b15', ESA_C = '#3b82f6';
// etichetele de baza ale selectorului, ca sa nu le concatenam la fiecare randare
const RANGE_LABELS = { '24': 'ultimele 24 h', '72': 'ultimele 3 zile',
                       '168': 'ultimele 7 zile', '0': 'tot istoricul' };

let map, tileLayers = {}, parkLayer, nasaLayer, esaLayer, trackLayer, burnLayer, windLayer, refLayer;
let fullState = null;      // toata starea, cu toate zonele
let state = null;          // subarborele zonei active (.detections, .stats, .fires, .weather...)
let currentZone = null;
let parkGeomFile = null;
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
  windLayer = L.layerGroup().addTo(map);
  refLayer = L.layerGroup().addTo(map);

  loadPark(null);

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

// conturul parcului depinde de zona activa
function loadPark(file) {
  const f = file || 'domogled.geojson';
  if (f === parkGeomFile) return;
  parkGeomFile = f;
  parkLayer.clearLayers();
  fetch(f).then(r => r.json()).then(geom => {
    L.geoJSON(geom, { style: { color: '#3b82f6', weight: 2, fill: false, opacity: 0.85, dashArray: '6 4' } })
      .addTo(parkLayer);
  }).catch(() => {});
}

// ---------------------------------------------------------------- zone
function syncZones() {
  const sel = document.getElementById('zone');
  const ver = document.getElementById('build');
  if (ver) ver.textContent = BUILD;

  // Toleranta la formatul vechi de stare (o singura zona, fara cheia 'zones').
  // Fara asta, un client nou peste o stare veche (sau invers) afiseaza pagina goala
  // in loc sa dea o eroare vizibila.
  if (fullState && !fullState.zones && fullState.detections) {
    const nume = (fullState.aoi || {}).name || 'zonă';
    fullState = {
      zones: { legacy: Object.assign({}, fullState, { id: 'legacy', name: nume, short: nume }) },
      zone_list: [{ id: 'legacy', name: nume, park: {} }],
      default_zone: 'legacy',
      cycles: fullState.cycles,
    };
  }

  const list = (fullState && fullState.zone_list) || [];
  if (!sel || !list.length) {
    if (sel && fullState && !list.length) sel.innerHTML = '<option value="">fără zone în date</option>';
    return;
  }

  const sig = list.map(z => z.id).join(',');
  if (sel.dataset.sig !== sig) {
    sel.dataset.sig = sig;
    sel.innerHTML = list.map(z => `<option value="${z.id}">${z.name}</option>`).join('');
    currentZone = currentZone || fullState.default_zone || list[0].id;
  }
  const zones = (fullState && fullState.zones) || {};
  if (!zones[currentZone]) currentZone = fullState.default_zone || list[0].id;
  sel.value = currentZone;

  state = zones[currentZone] || null;
  const meta = list.find(z => z.id === currentZone) || {};
  loadPark(((meta.park || {}).geojson) || null);
  if (state && state.name) document.title = `${state.name} · incendii live`;
}

function switchZone(id) {
  currentZone = id;
  const zones = (fullState && fullState.zones) || {};
  state = zones[id] || null;
  lastRenderSig = ''; lastBurnSig = ''; lastAlertKey = null;
  for (const el of document.querySelectorAll('#fires, #thumbs, #alerts')) delete el.dataset.sig;
  const aoi = (state && state.aoi) || {};
  if (aoi.center) map.setView(aoi.center, aoi.zoom || 12);
  loadPark(null);                       // forteaza reincarcarea pentru zona noua
  parkGeomFile = null;
  syncZones();
  refresh(true);
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

  const onlyStrong = document.getElementById('l-strong').checked;
  const showContext = document.getElementById('l-context')
    ? document.getElementById('l-context').checked : true;
  const tl = window.TIMELINE;
  const rng = tl ? tl.ranges() : null;   // {from, to} pe zile, sau null
  const groups = {};
  let shown = 0;

  for (const d of Object.values((state && state.detections) || {})) {
    if (onlyStrong && (d.frp || 0) < 5) continue;

    // Filtrul temporal: daca timeline-ul e activ, punctele din afara
    // intervalului se deseneaza estompat (context) sau deloc.
    const key = (d.date || '');
    const inRange = !rng || (key >= rng.from && key <= rng.to);
    if (!inRange && !showContext) continue;
    if (inRange) shown++;

    const st = tl ? tl.style(d, inRange) : {
      radius: radiusOf(d.frp),
      color: d.src === 'ESA' ? '#22d3ee' : '#ffffff',
      weight: 1.8, opacity: 0.9,
      fillColor: ageColor(ageHours(d)), fillOpacity: 0.55
    };

    L.circleMarker([d.lat, d.lon], st).bindPopup(
      `<b>${d.sensor}</b><br>FRP: <b>${(d.frp || 0).toFixed(2)} MW</b><br>` +
      (d.bt_k ? `temperatură: ${d.bt_k} K<br>` : '') +
      `încredere: ${d.confidence}%<br>${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}<br>` +
      `achiziție: ${d.date} ${d.time}Z (${ageHours(d).toFixed(1)} h în urmă)<br>` +
      `zi/noapte: ${d.daynight === 'D' ? 'zi' : 'noapte'}` +
      (inRange ? '' : '<br><i style="color:#8b98a5">în afara intervalului selectat</i>')
    ).addTo(d.src === 'ESA' ? esaLayer : nasaLayer);

    if (inRange) {
      (groups[`${d.date} ${d.time}`] = groups[`${d.date} ${d.time}`] || []).push(d);
    }
  }

  for (const pts of Object.values(groups)) {
    if (pts.length < 3) continue;
    const s = pts.slice().sort((a, b) => a.lon - b.lon);
    L.polyline(s.map(p => [p.lat, p.lon]),
      { color: '#f97316', weight: 1.4, opacity: 0.55, dashArray: '4 3' }).addTo(trackLayer);
  }

  return shown;
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

function renderWeather() {
  const w = state && state.weather;
  const box = document.getElementById('w-danger');
  if (!box) return;
  if (!w) { box.textContent = 'meteo indisponibil (Open-Meteo nu a răspuns)'; return; }

  set('w-temp', w.temp_c ?? '–');
  set('w-rh', w.humidity_pct ?? '–');
  set('w-wind', w.wind_kmh ?? '–');
  set('w-precip', w.precip_next48_mm ?? '–');
  set('w-from', w.wind_from_deg != null ? `${w.wind_from_compass} (${w.wind_from_deg}°)` : '–');
  set('w-to', w.downwind_deg != null ? `${w.downwind_compass} (${w.downwind_deg}°)` : '–');

  const bits = [];
  if (w.gust_kmh != null) bits.push(`rafale ${w.gust_kmh} km/h`);
  if (w.vpd_kpa != null) bits.push(`VPD ${w.vpd_kpa} kPa`);
  if (w.soil_moisture != null) bits.push(`sol ${w.soil_moisture}`);
  set('w-extra', bits.join(' · ') || '–');

  const t = w.terrain || {};
  set('w-terrain', t.elevation_m != null
    ? `${t.elevation_m} m${t.on_summit ? ' · pe vârf/creastă'
        : ` · pantă ${t.slope_pct}% spre ${t.upslope_compass || '?'}`}`
    : '–');

  const d = w.danger || {};
  set('w-src', w.source ? `${w.source} · ${(w.time || '').slice(11, 16)}Z` : '–');
  box.innerHTML = `<span style="color:${d.color || '#6b7684'}">● Pericol ${d.class || '?'}</span>` +
    `<small>${d.name || ''} ${d.index ?? ''}` +
    `${d.next48_min != null ? ` · min. 48 h ${d.next48_min}` : ''}</small>`;

  const note = document.getElementById('w-note');
  if (note) {
    let msg = 'Indicele Angström se calculează din temperatură și umiditate. Mai mic = mai periculos.';
    msg += (w.precip_next48_mm || 0) === 0
      ? ' Nu se anunță precipitații în 48 h.'
      : ` Se anunță ${w.precip_next48_mm} mm în 48 h.`;
    if (t.on_summit) {
      msg += ' Focarul e pe un vârf, deci panta nu determină direcția de propagare; vântul rămâne factorul principal.';
    }
    note.textContent = msg;
  }
}

function renderFires() {
  const box = document.getElementById('fires');
  if (!box) return;
  const fires = (state && state.fires) || [];
  const sig = JSON.stringify(fires.map(f => [f.id, f.points, f.trend, f.frp_last24]));
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;

  if (!fires.length) {
    box.innerHTML = '<div class="empty">Niciun focar cu suficiente detecții.</div>';
    return;
  }
  box.innerHTML = fires.slice(0, 5).map(f => `
    <div class="firecard ${f.rank === 1 ? 'lead' : ''}">
      <b>#${f.rank} ${f.lat.toFixed(4)}, ${f.lon.toFixed(4)}</b>
      <div class="row"><span>${f.points} detecții în ${f.days} zile</span>
        <span class="trend" style="color:${f.trend_color}">${f.trend}</span></div>
      <div class="row"><span>FRP max ${f.frp_max} MW</span><span>24 h: ${f.frp_last24} MW</span></div>
      <div class="row"><span>întindere ${f.extent_km} km</span>
        <span>${f.in_park === true ? 'în parc' : f.in_park === false ? 'în afara parcului' : ''}</span></div>
      <div class="row"><span>cel mai aproape: ${f.nearest_place || '–'}</span><span>${f.nearest_km} km</span></div>
    </div>`).join('');
}

// con de propagare: din focarul principal, pe direcția vântului
function renderWindCone() {
  windLayer.clearLayers();
  const w = state && state.weather;
  const fires = (state && state.fires) || [];
  if (!w || w.downwind_deg == null || !fires.length) return;

  const src = [fires[0].lat, fires[0].lon];
  const bearing = w.downwind_deg * Math.PI / 180;
  const R = 6371.0, LEN = 12, spread = 26 * Math.PI / 180;

  const pt = (brg, km) => {
    const d = km / R;
    const lat1 = src[0] * Math.PI / 180, lon1 = src[1] * Math.PI / 180;
    const lat2 = Math.asin(Math.sin(lat1) * Math.cos(d) +
                           Math.cos(lat1) * Math.sin(d) * Math.cos(brg));
    const lon2 = lon1 + Math.atan2(Math.sin(brg) * Math.sin(d) * Math.cos(lat1),
                                   Math.cos(d) - Math.sin(lat1) * Math.sin(lat2));
    return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
  };

  const poly = [src];
  for (let i = 0; i <= 8; i++) poly.push(pt(bearing - spread / 2 + spread * i / 8, LEN));
  L.polygon(poly, { color: '#22d3ee', weight: 1, opacity: 0.5, fillColor: '#22d3ee',
                    fillOpacity: 0.10, dashArray: '4 4', interactive: false }).addTo(windLayer);
  L.polyline([src, pt(bearing, LEN)], { color: '#22d3ee', weight: 2, opacity: 0.85,
                                        dashArray: '6 4', interactive: false }).addTo(windLayer);
  L.marker(pt(bearing, LEN), {
    icon: L.divIcon({ className: '', iconSize: [0, 0], html:
      `<div style="transform:translate(-50%,-50%);color:#22d3ee;font-size:11px;white-space:nowrap;
                   text-shadow:0 0 4px #000,0 0 2px #000">pană spre ${w.downwind_compass}</div>` })
  }).addTo(windLayer);
}

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
    fullState = st;
    syncZones();
    const sig = `${(st.stats || {}).updated}|${document.getElementById('l-strong').checked}`;
    const changed = force || sig !== lastRenderSig;

    renderStats();          // mereu: badge-ul de prospetime trebuie actualizat
    if (changed) {
      lastRenderSig = sig;
      buildTimeline();      // reconstruim timeline-ul pe zona/noile date
      renderDetections();
      drawChart((st.stats || {}).days || []);
      renderAlerts();
      renderImagery();
      renderWeather();
      renderFires();
      renderWindCone();
    }
    renderBurn();
    lastUpdated = new Date();
  } catch (e) {
    document.getElementById('b-status').className = 'badge err';
    document.getElementById('b-status').textContent = 'date indisponibile';
  }
}

/* Timeline-ul temporal. Se reconstruieste la schimbarea zonei sau cand apar
   detectii noi, dar PAsTRAM selectia curenta daca perioada exista inca -
   altfel filtrul s-ar reseta singur la fiecare refresh de 5 minute. */
function buildTimeline() {
  const box = document.getElementById('timeline');
  if (!box || !window.TIMELINE) return;
  const prev = window.TIMELINE.ranges();
  window.TIMELINE.build(box, state, () => { renderDetections(); renderFires(); });
  if (prev && prev.from) {
    const has = window.TIMELINE.restore && window.TIMELINE.restore(prev.from, prev.to);
    if (has) { renderDetections(); }
  }
}

function bindControls() {
  document.getElementById('zone').addEventListener('change', e => switchZone(e.target.value));
  document.getElementById('l-strong').addEventListener('change', () => refresh(true));
  const ctxBox = document.getElementById('l-context');
  if (ctxBox) ctxBox.addEventListener('change', () => renderDetections());
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
