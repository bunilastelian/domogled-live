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
let satLayer = null;       // overlay cu imaginea satelitara selectata (S3 termic / S2 foto)
let fullState = null;      // toata starea, cu toate zonele
let state = null;          // subarborele zonei active (.detections, .stats, .fires, .weather...)
let currentZone = null;
let parkGeomFiles = [];
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
      // Pe GitHub Pages nu exista /api/state: serverul intoarce 404 cu HTML.
      // r.json() arunca atunci, dar exceptia nu spune de ce - si mai rau,
      // prima rulare a lui refresh() murea aici si lasa badge-ul pe
      // "date indisponibile" pana la urmatorul ciclu de 60s, desi datele
      // statice erau perfect disponibile.
      if (r.ok) {
        const ct = r.headers.get('content-type') || '';
        if (ct.includes('json')) return await r.json();
      }
    } catch (e) { /* serverul local nu raspunde - trecem pe static */ }
    staticMode = true;      // de aici incolo citim direct fisierul static
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

  // contururile parcurilor vin din zone_list, la syncZones()

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

// Contururile ariilor protejate din regiune. Pot fi mai multe - regiunea
// acopera doua parcuri, iar un focar poate sta in oricare sau in niciunul.
function loadParks(files) {
  const list = (files && files.length ? files : ['domogled.geojson']).filter(Boolean);
  const sig = list.join(',');
  // NU iesi devreme pe baza semnaturii: loadParks e apelat la fiecare refresh
  // (la 60s), iar daca semnatura e deja setata dar layerele nu s-au desenat
  // (fetch esuat, geoJSON aruncat), contururile nu mai apar niciodata.
  // Reincarcarea e ieftina - fisierele vin din cache HTTP.
  parkGeomFiles = list;
  parkLayer.clearLayers();
  list.forEach((f, i) => {
    fetch(f).then(r => r.json()).then(geom => {
      // Fisierele sunt geometrii GeoJSON simple (Polygon/MultiPolygon), nu
      // FeatureCollection. L.geoJSON() accepta ambele, dar o geometrie
      // fara "features" trece neobservata daca o ambalam gresit - asa ca
      // normalizam explicit la Feature.
      const gj = (geom && geom.type === 'FeatureCollection')
        ? geom
        : { type: 'Feature', properties: {}, geometry: geom };
      const cols = ['#3b82f6', '#a855f7', '#f97316', '#14b8a6'];
      L.geoJSON(gj, {
        style: { color: cols[i % cols.length], weight: 2, fill: false, opacity: 0.85, dashArray: '6 4' },
      }).addTo(parkLayer);
    }).catch(e => console.warn('parc nu s-a incarcat:', f, e));
  });
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
  if (!list.length) {
    if (sel) sel.innerHTML = '<option value="">fără zone în date</option>';
    return;
  }

  // Regiunea e una singura: fara selector. Daca totusi vin mai multe zone
  // (format vechi), afisam selectorul, ca sa nu rupem nimic.
  if (sel) {
    if (list.length > 1) {
      sel.style.display = '';
      const sig = list.map(z => z.id).join(',');
      if (sel.dataset.sig !== sig) {
        sel.dataset.sig = sig;
        sel.innerHTML = list.map(z => `<option value="${z.id}">${z.name}</option>`).join('');
        currentZone = currentZone || fullState.default_zone || list[0].id;
      }
    } else {
      sel.style.display = 'none';
      currentZone = currentZone || list[0].id;
    }
  }
  const zones = (fullState && fullState.zones) || {};
  if (!zones[currentZone]) currentZone = fullState.default_zone || list[0].id;
  if (sel && list.length > 1) sel.value = currentZone;

  state = zones[currentZone] || null;
  window.__activeZone = currentZone;      // folosit de timeline (S3/S2 per zona)
  const meta = list.find(z => z.id === currentZone) || {};
  // toate contururile de parc ale regiunii (poate fi mai mult de unul)
  const parks = meta.parks || (meta.park ? [meta.park] : []);
  loadParks(parks.map(p => (p || {}).geojson).filter(Boolean));
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
  // contururile se reincarca singure: syncZones() cheama loadParks cu
  // lista de parcuri a zonei active
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

  // 843 de cerculete desenate simultan se topesc intr-o pata fara forma: nu
  // mai vezi focarele, parcurile, pâraiele. Cauza nu e numarul de detectii,
  // ci faptul ca le desenam pe toate la orice zoom.
  //
  // Doua trepte:
  //   zoom <= DETAIL_ZOOM  -> HEATMAP (densitate; arata unde arde)
  //   zoom >  DETAIL_ZOOM  -> puncte individuale, unde chiar incap
  const DETAIL_ZOOM = 12;
  const detailed = map.getZoom() > DETAIL_ZOOM;

  // punctele care intra in heatmap: doar cele din intervalul selectat
  const heatPts = [];

  for (const d of Object.values((state && state.detections) || {})) {
    if (onlyStrong && (d.frp || 0) < 5) continue;

    // Filtrul temporal: daca timeline-ul e activ, punctele din afara
    // intervalului se deseneaza estompat (context) sau deloc.
    const key = (d.date || '');
    const inRange = !rng || (key >= rng.from && key <= rng.to);
    if (!inRange && !showContext) continue;
    if (inRange) shown++;

    const age = ageHours(d);
    // intensitatea in heatmap creste cu FRP-ul si scade cu vechimea
    const w = Math.max(0.15, Math.min(1, ((d.frp || 0) / 20) + 0.25)) *
              (inRange ? 1 : 0.25) *
              (age <= 6 ? 1 : age <= 24 ? 0.85 : age <= 72 ? 0.6 : 0.35);
    heatPts.push([d.lat, d.lon, w]);

    if (!detailed) continue;      // la zoom mic nu desenam puncte individuale

    const st = tl ? tl.style(d, inRange) : {
      radius: radiusOf(d.frp),
      color: d.src === 'ESA' ? '#22d3ee' : '#ffffff',
      weight: 1.8, opacity: 0.9,
      fillColor: ageColor(age), fillOpacity: 0.55
    };

    L.circleMarker([d.lat, d.lon], st).bindPopup(
      `<b>${d.sensor}</b><br>FRP: <b>${(d.frp || 0).toFixed(2)} MW</b><br>` +
      (d.bt_k ? `temperatură: ${d.bt_k} K<br>` : '') +
      `încredere: ${d.confidence}%<br>${d.lat.toFixed(4)}, ${d.lon.toFixed(4)}<br>` +
      `achiziție: ${d.date} ${d.time}Z (${age.toFixed(1)} h în urmă)<br>` +
      `zi/noapte: ${d.daynight === 'D' ? 'zi' : 'noapte'}` +
      (inRange ? '' : '<br><i style="color:#8b98a5">în afara intervalului selectat</i>')
    ).addTo(d.src === 'ESA' ? esaLayer : nasaLayer);

    if (inRange) {
      (groups[`${d.date} ${d.time}`] = groups[`${d.date} ${d.time}`] || []).push(d);
    }
  }

  drawHeat(heatPts, detailed);

  for (const pts of Object.values(groups)) {
    if (pts.length < 3) continue;
    const s = pts.slice().sort((a, b) => a.lon - b.lon);
    L.polyline(s.map(p => [p.lat, p.lon]),
      { color: '#f97316', weight: 1.4, opacity: 0.55, dashArray: '4 3' }).addTo(trackLayer);
  }

  return shown;
}

// Heatmap pe canvas, desenat in overlay-ul hartii. Fara plugin extern:
// pentru cateva mii de puncte, o simpla acumulare de gradiente radiale e
// suficienta si nu adauga o dependinta de 40 KB.
let heatCanvas = null;

function drawHeat(points, hidden) {
  if (!heatCanvas) {
    heatCanvas = L.DomUtil.create('canvas', 'heat-layer');
    heatCanvas.style.position = 'absolute';
    heatCanvas.style.pointerEvents = 'none';
    heatCanvas.style.zIndex = 350;
    map.getPanes().overlayPane.appendChild(heatCanvas);
    const sync = () => {
      // eticheta conului de vant se ascunde cand harta e departata, ca sa nu
      // traverseze tot ecranul
      document.body.classList.toggle('zoomed-out', map.getZoom() < 11);
      // Trecerea intre heatmap si puncte individuale se face dupa pragul de
      // zoom. Fara re-randare aici, punctele nu apar niciodata la zoom mare:
      // renderDetections rula doar la refresh-ul de date (60s), deci ramanea
      // pe decizia luata la zoom-ul de atunci.
      const wantDetail = map.getZoom() > DETAIL_ZOOM;
      if (wantDetail !== lastDetail) {
        lastDetail = wantDetail;
        renderDetections();
      }
      drawHeat(lastHeatPts, lastHeatHidden);
    };
    map.on('moveend zoomend resize', sync);
    sync();
  }
  lastHeatPts = points;
  lastHeatHidden = hidden;

  const size = map.getSize();
  const dpr = window.devicePixelRatio || 1;
  heatCanvas.width = size.x * dpr;
  heatCanvas.height = size.y * dpr;
  heatCanvas.style.width = size.x + 'px';
  heatCanvas.style.height = size.y + 'px';

  const ctx = heatCanvas.getContext('2d');
  ctx.clearRect(0, 0, heatCanvas.width, heatCanvas.height);
  if (!points.length || hidden) return;

  const topLeft = map.containerPointToLayerPoint([0, 0]);
  L.DomUtil.setPosition(heatCanvas, topLeft);

  ctx.scale(dpr, dpr);
  ctx.globalCompositeOperation = 'lighter';

  // raza fixa in pixeli: un focar e o pata, nu 400 de puncte
  const R = 18;
  for (const [lat, lon, w] of points) {
    const p = map.latLngToContainerPoint([lat, lon]);
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, R);
    g.addColorStop(0, `rgba(255,140,40,${0.16 * w})`);
    g.addColorStop(0.5, `rgba(255,80,20,${0.08 * w})`);
    g.addColorStop(1, 'rgba(255,60,0,0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(p.x, p.y, R, 0, Math.PI * 2);
    ctx.fill();
  }
}
let lastHeatPts = [];
let lastHeatHidden = false;
let lastDetail = null;      // ultima treapta de zoom randata (heatmap vs puncte)

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

/* --------- imaginea satelitara selectata din timeline (S3 termic / S2 foto) ----
   Punem imaginea ca overlay pe AOI. S3 e harta termica (bbox din index),
   S2 e decupajul true-color (bounds din state.burn sau AOI). */
function showSatImage(file, label) {
  if (!satLayer) satLayer = L.layerGroup().addTo(map);
  satLayer.clearLayers();
  if (!file) return;

  let bounds = null;
  const zid = window.__activeZone;
  const s3 = (window.__s3Idx && window.__s3Idx[zid]) || [];
  const hit = s3.find(e => e.file === file);
  if (hit && hit.bbox) {
    const [w, s, e, n] = hit.bbox;
    bounds = [[s, w], [n, e]];
  } else if (state && state.burn && state.burn.bounds) {
    bounds = state.burn.bounds;
  } else if (state && state.aoi && state.aoi.bounds) {
    bounds = state.aoi.bounds;
  }
  if (!bounds) return;

  // opacitate: termic peste harta = 0.85; foto true-color = 0.75
  const op = file.startsWith('s3_') ? 0.85 : 0.75;
  L.imageOverlay('img/' + file, bounds,
    { opacity: op, interactive: false, className: 'sat-overlay' }).addTo(satLayer);

  const el = document.getElementById('s-sat');
  if (el) {
    el.innerHTML = `<b>${label || file}</b> <span style="color:var(--dim)">` +
      `(click din nou pe buton ca să scoți)</span>`;
  }
}

/* curata overlay-ul cand se schimba zona sau intervalul */
function clearSatImage() {
  if (satLayer) satLayer.clearLayers();
  const el = document.getElementById('s-sat');
  if (el) el.textContent = '—';
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
        <span>${(f.parks && f.parks.length)
                 ? f.parks.map(p => p.replace(/^Parcul (Național|Natural) /, '')).join(' · ')
                 : (f.in_park === false ? 'în afara parcurilor' : '')}</span></div>
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
    // Eticheta era font 11px fara limite: la zoom mic se intindea peste jumatate
    // de ecran si nu se putea citi. Acum e mica, pe un singur rand, cu fundal,
    // si dispare cand nu incape (zoom mic) - conul ramane vizibil oricum.
    icon: L.divIcon({ className: '', iconSize: [0, 0], html:
      `<div class="windlabel">pană spre ${w.downwind_compass}</div>` })
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
  // Fiecare alerta devine clicabila si duce harta la locul focarului.
  // Alertele au lat/lon (centrul de masa al detectiilor noi din acel lot),
  // deci nu ducem la focarul principal, ci exact unde au aparut detectiile.
  box.innerHTML = a.slice(0, 12).map((x, i) => {
    const areLoc = typeof x.lat === 'number' && typeof x.lon === 'number';
    const cls = `alert ${i === 0 ? 'new' : ''}${areLoc ? ' clickable' : ''}`;
    const atr = areLoc
      ? ` role="button" tabindex="0" data-lat="${x.lat}" data-lon="${x.lon}"`
        + ` data-label="${esc(x.label)}" data-at="${x.at}"`
      : '';
    return `<div class="${cls}"${atr}>
       ${areLoc ? '<span class="alert-pin">📍</span>' : ''}${esc(x.label)}<br>
      <time>${new Date(x.at).toLocaleString('ro-RO')} · ${esc(x.source)}${x.telegram ? ' · Telegram' : ''}${areLoc ? ' · <b class="see-map">vezi pe hartă</b>' : ''}</time>
     </div>`;
  }).join('');

  if (lastAlertKey && a[0].at !== lastAlertKey && document.getElementById('notif').checked &&
      'Notification' in window && Notification.permission === 'granted') {
    new Notification('🔥 Detecție nouă — Domogled', { body: a[0].label });
  }
  lastAlertKey = a[0].at;
}

// mic helper anti-XSS pentru textul care intra in HTML
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Duce harta la alerta selectata, cu un marker temporar care arata unde e focul.
// Markerul se sterge singur cand selectezi alta alerta (unul singur odata).
let alertMarker = null;
function goToAlert(lat, lon, label) {
  if (typeof lat !== 'number' || typeof lon !== 'number') return;
  if (!map) return;
  map.flyTo([lat, lon], 14, { duration: 0.9 });

  if (alertMarker) { map.removeLayer(alertMarker); alertMarker = null; }

  alertMarker = L.circleMarker([lat, lon], {
    radius: 11, color: '#fbbf24', weight: 3, opacity: 1,
    fillColor: '#f59e0b', fillOpacity: 0.35, className: 'alert-marker',
  }).addTo(map);

  // pulsul care atrage atentia (se opreste dupa 6s ca sa nu oboseasca)
  const ring = L.circleMarker([lat, lon], {
    radius: 11, color: '#fbbf24', weight: 2, opacity: 0.9, fill: false,
  }).addTo(map);
  let r = 11;
  const anim = setInterval(() => {
    r += 2.2;
    ring.setRadius(r);
    ring.setStyle({ opacity: Math.max(0, 0.9 - (r - 11) / 26) });
    if (r > 38) { clearInterval(anim); map.removeLayer(ring); }
  }, 60);

  alertMarker.bindTooltip(String(label || '').slice(0, 90), {
    permanent: false, direction: 'top', offset: [0, -12],
  }).openTooltip();

  const st = document.getElementById('s-alert');
  if (st) st.textContent = '📍 alertă: ' + String(label || '').slice(0, 60);
  setTimeout(() => { if (st) st.textContent = ''; }, 6000);
}

// click (si Enter/Space pentru tastatura) pe orice alerta care are coordonate
document.addEventListener('click', (ev) => {
  const el = ev.target.closest('#alerts .alert.clickable');
  if (!el) return;
  goToAlert(parseFloat(el.dataset.lat), parseFloat(el.dataset.lon), el.dataset.label);
});
document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const el = document.activeElement;
  if (!el || !el.classList || !el.classList.contains('alert')) return;
  if (!el.dataset.lat) return;
  ev.preventDefault();
  goToAlert(parseFloat(el.dataset.lat), parseFloat(el.dataset.lon), el.dataset.label);
});

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
    syncZones();            // seteaza `state` pe zona activa
    // Statisticile stau sub zones[<zona>].stats, NU la radacina state.json.
    // Citirea din radacina (st.stats) dadea undefined: semnatura de randare
    // devenea "undefined|false", iar drawChart primea un array gol - graficul
    // se desena din date vechi sau deloc, desi state.stats era plin.
    const zst = (state && state.stats) || {};
    const sig = `${zst.updated}|${document.getElementById('l-strong').checked}`;
    const changed = force || sig !== lastRenderSig;

    renderStats();          // mereu: badge-ul de prospetime trebuie actualizat
    if (changed) {
      lastRenderSig = sig;
      buildTimeline();      // reconstruim timeline-ul pe zona/noile date
      renderDetections();
      drawChart(zst.days || []);
      renderAlerts();
      renderImagery();
      renderWeather();
      renderFires();
      renderWindCone();
    }
    renderBurn();
    lastUpdated = new Date();
  } catch (e) {
    // Nu inghitim eroarea: daca pagina arata "date indisponibile", vrem sa
    // stim de ce. O expunem si pe window, ca sa poata fi citita din afara
    // (consola browserului nu e disponibila cand diagnosticam remote).
    console.error('refresh a esuat:', e);
    window.__lastRefreshError = {
      msg: String(e && e.message || e),
      stack: String(e && e.stack || '').split('\n').slice(0, 5),
    };
    document.getElementById('b-status').className = 'badge err';
    document.getElementById('b-status').textContent = 'date indisponibile';
    const u = document.getElementById('b-updated');
    if (u && u.textContent === '—') u.title = String(e && e.message || e);
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
  // Pe mobil, cardurile sunt lungi: pagina ajungea la 4400px. Le facem pliabile,
  // cu titlul ca buton. Doar pe ecrane inguste - pe desktop panoul are scroll
  // propriu si plierea ar ascunde informatia fara motiv.
  if (window.matchMedia('(max-width: 900px)').matches) {
    const cards = Array.from(document.querySelectorAll('aside .card'));
    cards.forEach(card => {
      const h = card.querySelector('h2');
      if (!h) return;
      // pornim cu toate deschise; utilizatorul pliaza ce nu-l intereseaza
      card.style.cursor = '';
      h.setAttribute('role', 'button');
      h.setAttribute('tabindex', '0');
      h.setAttribute('aria-expanded', 'true');
      const toggle = () => {
        const nowCollapsed = !card.classList.contains('collapsed');
        card.classList.toggle('collapsed', nowCollapsed);
        h.setAttribute('aria-expanded', String(!nowCollapsed));
      };
      h.addEventListener('click', toggle);
      h.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
      });
    });
  }

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
window.__showS3Image = showSatImage;   // timeline.js apeleaza asta la click pe o trecere
fetch('state/s3_thermal.json')
  .then(r => r.ok ? r.json() : {})
  .then(d => { window.__s3Idx = d; })
  .catch(() => { window.__s3Idx = {}; });
refresh(true);
setInterval(refresh, REFRESH_MS);
