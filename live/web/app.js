/* app.js - dashboard live pentru incendiul Domogled – Valea Cernei */

// Mod local: server.py serveste /api/state (citit proaspat de pe disc).
// Mod static (GitHub Pages): acelasi folder e servit ca fisiere, deci citim state/state.json.
const API = '/api/state';
const STATIC_STATE = 'state/state.json';
const REFRESH_MS = 60000;

const COLORS = { h6: '#e5484d', h24: '#f76b15', d3: '#f5d90a', old: '#6b7684' };

let map, parkLayer, nasaLayer, esaLayer, trackLayer, burnLayer, chart;
let lastAlertKey = null;
let lastUpdated = null;
let staticMode = false;

async function loadState() {
  if (!staticMode) {
    try {
      const r = await fetch(API + '?_=' + Date.now(), { cache: 'no-store' });
      if (r.ok) return await r.json();
    } catch (e) { /* trecem pe modul static */ }
    staticMode = true;
  }
  const r = await fetch(STATIC_STATE + '?_=' + Date.now(), { cache: 'no-store' });
  if (!r.ok) throw new Error('nu pot citi ' + STATIC_STATE);
  return await r.json();
}

// ---------------------------------------------------------------- harta
function initMap() {
  map = L.map('map', { zoomControl: true, preferCanvas: true }).setView([44.8982, 22.4785], 12);

  const tiles = {
    sat: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 18, attribution: 'Imagery © Esri, Maxar, Earthstar Geographics' }),
    osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      { maxZoom: 19, attribution: '© OpenStreetMap' }),
    dark: L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      { maxZoom: 19, attribution: '© OpenStreetMap, © CARTO' })
  };
  tiles.sat.addTo(map);
  map.tileLayers = tiles;

  parkLayer = L.layerGroup().addTo(map);
  trackLayer = L.layerGroup().addTo(map);
  burnLayer = L.layerGroup().addTo(map);
  nasaLayer = L.layerGroup().addTo(map);
  esaLayer = L.layerGroup().addTo(map);

  fetch('domogled.geojson').then(r => r.json()).then(geom => {
    L.geoJSON(geom, {
      style: { color: '#3b82f6', weight: 2, fill: false, opacity: 0.85, dashArray: '6 4' }
    }).addTo(parkLayer);
  }).catch(() => {});

  // repere fixe
  const marks = [
    ['Cascada Cociului', 44.91838, 22.46485, '★'],
    ['Colțu Pietrii (1228 m)', 44.90550, 22.48217, '▲'],
    ['Domogledul Mare (1105 m)', 44.87161, 22.44364, '▲'],
    ['Băile Herculane', 44.8785, 22.4144, '■'],
    ['Pecinișca', 44.8712, 22.4187, '■']
  ];
  const refLayer = L.layerGroup().addTo(map);
  marks.forEach(([name, lat, lon, sym]) => {
    L.marker([lat, lon], {
      icon: L.divIcon({ className: '', html: `<div style="color:#fff;text-shadow:0 0 4px #000,0 0 2px #000;font-size:14px">${sym}</div>`, iconSize: [18, 18] })
    }).bindTooltip(name, { permanent: true, direction: 'right', className: 'reflabel' }).addTo(refLayer);
  });

  document.getElementById('tiles').addEventListener('change', e => {
    Object.values(map.tileLayers).forEach(l => map.removeLayer(l));
    map.tileLayers[e.target.value].addTo(map);
  });
}

function ageHours(d) {
  // d.date = 'YYYY-MM-DD', d.time = 'HHMM' UTC
  const iso = `${d.date}T${(d.time || '0000').padStart(4, '0').slice(0, 2)}:${(d.time || '0000').padStart(4, '0').slice(2, 4)}:00Z`;
  const t = Date.parse(iso);
  if (isNaN(t)) return 1e9;
  return (Date.now() - t) / 3600000;
}

function ageColor(h) {
  if (h <= 6) return COLORS.h6;
  if (h <= 24) return COLORS.h24;
  if (h <= 72) return COLORS.d3;
  return COLORS.old;
}

function radius(frp) {
  return Math.max(3, Math.min(24, 3 + Math.sqrt(frp || 0) * 1.7));
}

// ---------------------------------------------------------------- randare
function renderDetections(list) {
  nasaLayer.clearLayers();
  esaLayer.clearLayers();
  trackLayer.clearLayers();

  const onlyStrong = document.getElementById('l-strong').checked;
  const groups = {};   // "date time" -> puncte, pentru trasarea frontului

  list.forEach(d => {
    if (onlyStrong && (d.frp || 0) < 5) return;
    const h = ageHours(d);
    const col = ageColor(h);
    const esa = d.src === 'ESA';
    const m = L.circleMarker([d.lat, d.lon], {
      radius: radius(d.frp),
      color: esa ? '#22d3ee' : '#ffffff',
      weight: esa ? 1.8 : 1,
      opacity: 0.9,
      fillColor: col,
      fillOpacity: 0.55
    });
    const when = `${d.date} ${d.time}Z`;
    m.bindPopup(
      `<b>${d.sensor}</b><br>` +
      `FRP: <b>${(d.frp || 0).toFixed(2)} MW</b><br>` +
      (d.bt_k ? `temperatură: ${d.bt_k} K<br>` : '') +
      `încredere: ${d.confidence}%<br>` +
      `${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}<br>` +
      `achiziție: ${when} (${h.toFixed(1)} h în urmă)<br>` +
      `zi/noapte: ${d.daynight === 'D' ? 'zi' : 'noapte'}`
    );
    m.addTo(esa ? esaLayer : nasaLayer);

    const k = `${d.date} ${d.time}`;
    (groups[k] = groups[k] || []).push(d);
  });

  // linii de front: acelasi moment de achizitie, puncte ordonate pe longitudine
  Object.values(groups).forEach(pts => {
    if (pts.length < 3) return;
    const sorted = pts.slice().sort((a, b) => a.lon - b.lon);
    const h = ageHours(sorted[0]);
    L.polyline(sorted.map(p => [p.lat, p.lon]), {
      color: ageColor(h), weight: 1.4, opacity: 0.5, dashArray: '4 3'
    }).addTo(trackLayer);
  });
}

function renderBurn(burn) {
  burnLayer.clearLayers();
  const el = document.getElementById('s-burn');
  if (!burn || !burn.bounds) {
    if (el) el.textContent = 'se calculează…';
    return;
  }
  L.imageOverlay('img/' + burn.png, burn.bounds, {
    opacity: 0.75, interactive: false, className: 'burnoverlay'
  }).addTo(burnLayer);

  if (el) {
    el.innerHTML = `${burn.ha_moderat_plus} ha <span style="color:var(--dim)">(≥0,27)</span> · ` +
                   `${burn.ha_sever} ha sever`;
    el.title = `dNBR corectat, scena ${burn.post_date} față de ${burn.base_date}. ` +
               `Total ≥0,10: ${burn.ha_total} ha. ${burn.nota || ''}`;
  }
}

function renderStats(st, state) {
  document.getElementById('k-24').textContent = st.last24_count ?? '–';
  document.getElementById('k-frp').textContent = (st.last24_max ?? 0).toFixed(1);
  document.getElementById('k-total').textContent = st.total ?? '–';
  document.getElementById('k-days').textContent = (st.days || []).length;
  document.getElementById('s-src').textContent = `NASA ${st.nasa} · ESA ${st.esa}`;
  document.getElementById('s-cycles').textContent = state.cycles ?? '–';

  const L = st.latest;
  document.getElementById('s-latest').textContent =
    L ? `${L.date} ${L.time}Z · ${(L.frp || 0).toFixed(1)} MW` : '–';

  const badge = document.getElementById('b-updated');
  if (st.updated) {
    const secs = (Date.now() - Date.parse(st.updated)) / 1000;
    const txt = new Date(st.updated).toLocaleTimeString('ro-RO');
    badge.textContent = `colector: ${txt}`;
    const status = document.getElementById('b-status');
    if (secs < 900) { status.className = 'badge ok'; status.innerHTML = '<span class="dot"></span>activ'; }
    else if (secs < 3600) { status.className = 'badge warn'; status.textContent = `întârziat ${Math.round(secs / 60)} min`; }
    else { status.className = 'badge err'; status.textContent = 'colector oprit?'; }
  }
  document.getElementById('b-aoi').textContent = `${state.aoi?.name || 'AOI'} · ${(state.aoi?.bbox || []).join(', ')}`;
}

function renderChart(days) {
  const labels = days.map(d => d.date.slice(5));
  const data = {
    labels,
    datasets: [
      { label: 'NASA (MW)', data: days.map(d => +(d.frp_nasa || 0).toFixed(1)), backgroundColor: '#f76b15' },
      { label: 'ESA (MW)', data: days.map(d => +(d.frp_esa || 0).toFixed(1)), backgroundColor: '#3b82f6' }
    ]
  };
  const ctx = document.getElementById('chart');
  if (chart) { chart.data = data; chart.update(); return; }
  chart = new Chart(ctx, {
    type: 'bar',
    data,
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#8b98a5', boxWidth: 10, font: { size: 10 } } },
        tooltip: { callbacks: { afterBody: i => `${days[i[0].dataIndex].count} detecții` } }
      },
      scales: {
        x: { stacked: true, ticks: { color: '#8b98a5', font: { size: 10 } }, grid: { display: false } },
        y: { stacked: true, ticks: { color: '#8b98a5', font: { size: 10 } }, grid: { color: '#26303d' } }
      }
    }
  });
}

function renderAlerts(alerts) {
  const box = document.getElementById('alerts');
  if (!alerts || !alerts.length) return;
  box.innerHTML = alerts.slice(0, 12).map((a, i) =>
    `<div class="alert ${i === 0 ? 'new' : ''}">
       ${a.label}<br>
       <time>${new Date(a.at).toLocaleString('ro-RO')} · ${a.source}${a.telegram ? ' · Telegram trimis' : ''}</time>
     </div>`).join('');

  const newest = alerts[0].at;
  if (lastAlertKey && newest !== lastAlertKey && document.getElementById('notif').checked
      && 'Notification' in window && Notification.permission === 'granted') {
    new Notification('🔥 Detecție nouă — Domogled', { body: alerts[0].label });
  }
  lastAlertKey = newest;
}

function renderImagery(imagery) {
  const box = document.getElementById('thumbs');
  const items = Object.values(imagery || {}).sort((a, b) => (b.datetime || '').localeCompare(a.datetime || ''));
  if (!items.length) { box.innerHTML = '<div class="empty">Se așteaptă o scenă Sentinel-2 nouă peste zonă…</div>'; return; }
  box.innerHTML = items.slice(0, 6).map(it => {
    const dt = (it.datetime || '').slice(0, 16).replace('T', ' ');
    return `<div class="thumb" onclick="window.open('img/${it.file}','_blank')">
              <img src="img/${it.file}" alt="${it.scene}" loading="lazy">
              <span>${dt}Z · nori ${it.cloud ?? '?'}%</span>
            </div>`;
  }).join('');
}

// ---------------------------------------------------------------- bucla
async function refresh() {
  try {
    const st = await loadState();
    if (st.error) {
      document.getElementById('b-status').className = 'badge warn';
      document.getElementById('b-status').textContent = st.error;
      return;
    }
    const list = Object.values(st.detections || {});
    renderDetections(list);
    if (st.stats) renderStats(st.stats, st);
    if (st.stats && st.stats.days) renderChart(st.stats.days);
    renderBurn(st.burn);
    renderAlerts(st.alerts);
    renderImagery(st.imagery);
    lastUpdated = new Date();
  } catch (e) {
    document.getElementById('b-status').className = 'badge err';
    document.getElementById('b-status').textContent = 'date indisponibile';
  }
}

function bindControls() {
  ['l-nasa', 'l-esa', 'l-park', 'l-tracks', 'l-burn', 'l-strong'].forEach(id => {
    document.getElementById(id).addEventListener('change', () => {
      const on = document.getElementById(id).checked;
      if (id === 'l-nasa') on ? map.addLayer(nasaLayer) : map.removeLayer(nasaLayer);
      if (id === 'l-esa') on ? map.addLayer(esaLayer) : map.removeLayer(esaLayer);
      if (id === 'l-park') on ? map.addLayer(parkLayer) : map.removeLayer(parkLayer);
      if (id === 'l-tracks') on ? map.addLayer(trackLayer) : map.removeLayer(trackLayer);
      if (id === 'l-burn') on ? map.addLayer(burnLayer) : map.removeLayer(burnLayer);
      if (id === 'l-strong') refresh();
    });
  });
  document.getElementById('notif').addEventListener('change', e => {
    if (e.target.checked && 'Notification' in window && Notification.permission !== 'granted') {
      Notification.requestPermission();
    }
  });
  // numaratoare inversa pana la reimprospatare
  setInterval(() => {
    if (!lastUpdated) return;
    const s = Math.round((Date.now() - lastUpdated) / 1000);
    const left = Math.max(0, Math.round(REFRESH_MS / 1000) - s);
    document.getElementById('b-next').textContent = `reîmprospătare în ${left}s`;
  }, 1000);
}

initMap();
bindControls();
refresh();
setInterval(refresh, REFRESH_MS);
