/* ============================================================================
   timeline.js — filtru temporal pe harta de incendii Domogled
   ============================================================================

   CE FACE
   Selector de evolutie pe zile + bara de interval cu doua manere, care
   filtreaza punctele de detectie de pe harta pe orice perioada, de la
   izbucnirea incendiului pana acum.

   DE CE ASA
   Inainte, harta arata toate cele ~780 de detectii simultan, amestecate -
   nu se putea citi evolutia in timp. Acum vezi ce s-a intamplat cand.

   CUM SE COLOREAZA (cerinta: culoare = FRP, opacitate = varsta)
     - FILL  = FRP (puterea reala a focului):  galben -> portocaliu -> rosu -> violet
     - OPACITY = varsta: punctele din intervalul selectat sunt saturate,
       cele din afara intervalului rămân estompate (context vizual)
     - CONTUR = sursa (alb = NASA, cyan = ESA)

   CAPCANA REZOLVATA
   Detectiile nu au timestamp complet, ci `date` (YYYY-MM-DD) + `time` (HHMM
   UTC). Le combinam intr-un Date real. Unele au time lipsa -> tratam ca
   00:00 si marcam, ca sa nu le aruncam din grafic.
   ========================================================================== */

(function () {
  'use strict';

  let detections = [];     // toate, cu timestamp precomputat
  let days = [];           // zilele disponibile, sortate
  let selFrom = 0;         // index inceput in `days`
  let selTo = 0;           // index sfarsit
  let playing = false;
  let playTimer = null;

  // Culori dupa FRP (MW). Pragurile sunt alese pe datele reale:
  // majoritatea detectiilor au 0.5-15 MW, varfurile ajung la 97 MW.
  const FRP_STOPS = [
    [0,   [254, 240, 138]],   // #fef08a  slab
    [2,   [250, 204, 21]],    // #facc15
    [5,   [249, 115, 22]],    // #f97316  moderat
    [15,  [220, 38, 38]],     // #dc2626  puternic
    [40,  [157, 23, 77]],     // #9d174d  intens
    [100, [88, 28, 135]],     // #581c87  extrem
  ];

  function frpColor(frp) {
    const v = Math.max(0, frp || 0);
    for (let i = FRP_STOPS.length - 1; i >= 0; i--) {
      if (v >= FRP_STOPS[i][0]) {
        if (i === FRP_STOPS.length - 1) return FRP_STOPS[i][1];
        const [t0, c0] = FRP_STOPS[i];
        const [t1, c1] = FRP_STOPS[i + 1];
        const k = (v - t0) / (t1 - t0);
        return [0, 1, 2].map(j => Math.round(c0[j] + (c1[j] - c0[j]) * k));
      }
    }
    return FRP_STOPS[0][1];
  }

  const rgb = c => `rgb(${c[0]},${c[1]},${c[2]})`;

  function stamp(d) {
    const t = (d.time || '0000').padStart(4, '0');
    const ms = Date.parse(`${d.date}T${t.slice(0, 2)}:${t.slice(2, 4)}:00Z`);
    return isNaN(ms) ? Date.parse(`${d.date}T00:00:00Z`) : ms;
  }

  function dayKey(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }

  function fmtDay(k) {
    const [, m, d] = k.split('-');
    return `${d}.${m}`;
  }

  function fmtDT(ms) {
    const dt = new Date(ms);
    const p = n => String(n).padStart(2, '0');
    return `${p(dt.getUTCDate())}.${p(dt.getUTCMonth() + 1)} ${p(dt.getUTCHours())}:${p(dt.getUTCMinutes())}Z`;
  }

  /* ---------- imagini termice Sentinel-3 pentru intervalul selectat --------
     S3 SLSTR trece de 2-4 ori pe zi (S2 doar la 5 zile) si prinde 75% din
     detectii noaptea. Fiecare trecere are o harta termica generata din
     produsul SL_2_FRP - arata FRONTUL ACTIV, nu cicatricea. */
  let s3Index = null;

  async function loadS3() {
    if (s3Index) return s3Index;
    try {
      const r = await fetch('state/s3_thermal.json');
      s3Index = r.ok ? await r.json() : {};
    } catch (e) { s3Index = {}; }
    return s3Index;
  }

  function s3ForZone(list) {
    if (!s3Index) return [];
    return s3Index[window.__activeZone] || [];
  }

  /* ---------- construire UI ---------- */

  async function build(container, state, onFilter) {
    // incarcam indexul S3 (o singura data) si expunem zonele S2 pentru modul foto
    await loadS3();
    window.__s2Scenes = window.__s2Scenes || {};
    const zid0 = window.__activeZone;
    if (state && state.imagery && zid0) {
      window.__s2Scenes[zid0] = Object.values(state.imagery)
        .map(v => ({ file: v.file, datetime: v.datetime, cloud: v.cloud }))
        .sort((a, b) => (a.datetime || '').localeCompare(b.datetime || ''));
    }

    detections = Object.values((state && state.detections) || {})
      .map(d => ({ ...d, _ms: stamp(d) }))
      .sort((a, b) => a._ms - b._ms);

    if (!detections.length) { container.innerHTML = '<div style="padding:10px;color:#8b98a5">fără detecții</div>'; return; }

    const byDay = {};
    for (const d of detections) (byDay[dayKey(d._ms)] = byDay[dayKey(d._ms)] || []).push(d);
    days = Object.keys(byDay).sort();

    // suma FRP pe zi -> inaltimea barelor din timeline
    const dayFrp = {};
    for (const k of days) dayFrp[k] = byDay[k].reduce((s, d) => s + (d.frp || 0), 0);
    const maxFrp = Math.max(...Object.values(dayFrp), 1);

    // implicit: ultimele 3 ore; daca nu sunt detectii atat de recente,
    // cadem pe ultima zi cu date (altfel harta apare goala si pare stricata)
    const now = Date.now();
    const recent = detections.filter(d => now - d._ms <= 3 * 3600000);
    if (recent.length >= 3) {
      setSel(dayKey(recent[0]._ms), dayKey(recent[recent.length - 1]._ms));
    } else {
      setSel(days[Math.max(0, days.length - 2)], days[days.length - 1]);
    }

    container.innerHTML = `
      <div class="tl-wrap">
        <div class="tl-presets">
          <button data-preset="3h" class="tl-btn">ultimele 3h</button>
          <button data-preset="24h" class="tl-btn">24h</button>
          <button data-preset="48h" class="tl-btn">48h</button>
          <button data-preset="all" class="tl-btn">tot</button>
          <button data-preset="play" class="tl-btn tl-play" title="derulează zilele">▶</button>
          <span class="tl-sep"></span>
          <button data-imgs="s3" class="tl-btn on" title="hărți termice SLSTR, 2-4 treceri/zi">S3 termic</button>
          <button data-imgs="s2" class="tl-btn" title="true color, la ~5 zile">S2 foto</button>
        </div>

        <div class="tl-days" id="tl-days"></div>

        <div class="tl-bar" id="tl-bar">
          <div class="tl-sel" id="tl-sel"></div>
          <div class="tl-h tl-h0" id="tl-h0"></div>
          <div class="tl-h tl-h1" id="tl-h1"></div>
        </div>

        <div class="tl-info">
          <span id="tl-label"></span>
          <span id="tl-count" style="color:#8b98a5"></span>
        </div>

        <div class="tl-passes" id="tl-passes"></div>
      </div>`;

    drawDays(byDay, dayFrp, maxFrp);
    wire(container, onFilter);
    paint();
    onFilter(activeList());
  }

  function setSel(fromKey, toKey) {
    selFrom = Math.max(0, days.indexOf(fromKey));
    selTo = Math.max(selFrom, days.indexOf(toKey));
    if (selFrom < 0) selFrom = 0;
    if (selTo < 0) selTo = days.length - 1;
  }

  function drawDays(byDay, dayFrp, maxFrp) {
    const el = document.getElementById('tl-days');
    el.innerHTML = days.map((k, i) => {
      const h = Math.max(6, (dayFrp[k] / maxFrp) * 100);
      const n = byDay[k].length;
      return `<div class="tl-day" data-i="${i}" title="${k}: ${n} detecții, ${dayFrp[k].toFixed(0)} MW">
                <div class="tl-daybar" style="height:${h}%"></div>
                <div class="tl-daylbl">${fmtDay(k)}</div>
              </div>`;
    }).join('');
  }

  function wire(container, onFilter) {
    // presets
    container.querySelectorAll('[data-preset]').forEach(b => {
      b.addEventListener('click', () => {
        const p = b.dataset.preset;
        if (p === 'play') { togglePlay(container, onFilter); return; }
        const last = detections[detections.length - 1]._ms;
        if (p === 'all') setSel(days[0], days[days.length - 1]);
        else {
          const h = p === '3h' ? 3 : p === '24h' ? 24 : 48;
          const from = last - h * 3600000;
          const inRange = detections.filter(d => d._ms >= from);
          if (inRange.length) setSel(dayKey(inRange[0]._ms), dayKey(last));
          else setSel(days[days.length - 1], days[days.length - 1]);
        }
        container.querySelectorAll('[data-preset]').forEach(x => x.classList.remove('on'));
        b.classList.add('on');
        paint(); onFilter(activeList());
      });
    });

    // comutator S3 termic / S2 foto
    container.querySelectorAll('[data-imgs]').forEach(b => {
      b.addEventListener('click', () => {
        imgMode = b.dataset.imgs;
        container.querySelectorAll('[data-imgs]').forEach(x => x.classList.remove('on'));
        b.classList.add('on');
        renderPasses();
      });
    });

    // click pe o zi = selecteaza doar ziua aia
    document.getElementById('tl-days').addEventListener('click', e => {
      const d = e.target.closest('.tl-day');
      if (!d) return;
      const i = +d.dataset.i;
      selFrom = selTo = i;
      paint(); onFilter(activeList());
    });

    // tragere manere
    const bar = document.getElementById('tl-bar');
    for (const [id, which] of [['tl-h0', 0], ['tl-h1', 1]]) {
      const h = document.getElementById(id);
      h.addEventListener('pointerdown', ev => {
        ev.preventDefault();
        h.setPointerCapture(ev.pointerId);
        const move = mv => {
          const r = bar.getBoundingClientRect();
          const k = Math.min(1, Math.max(0, (mv.clientX - r.left) / r.width));
          const idx = Math.round(k * (days.length - 1));
          if (which === 0) selFrom = Math.min(idx, selTo);
          else selTo = Math.max(idx, selFrom);
          paint(); onFilter(activeList());
        };
        const up = () => {
          h.removeEventListener('pointermove', move);
          h.removeEventListener('pointerup', up);
        };
        h.addEventListener('pointermove', move);
        h.addEventListener('pointerup', up);
      });
    }
  }

  function togglePlay(container, onFilter) {
    if (playing) {
      playing = false; clearInterval(playTimer);
      container.querySelector('.tl-play').textContent = '▶';
      return;
    }
    playing = true;
    container.querySelector('.tl-play').textContent = '⏸';
    const step = () => {
      if (!playing) return;
      selFrom = selTo = (selTo + 1) % days.length;
      paint(); onFilter(activeList());
    };
    playTimer = setInterval(step, 900);
    step();
  }

  /* ---------- stare vizuala ---------- */

  function activeList() {
    const k0 = days[selFrom], k1 = days[selTo];
    return detections.filter(d => {
      const k = dayKey(d._ms);
      return k >= k0 && k <= k1;
    });
  }

  function paint() {
    const bar = document.getElementById('tl-bar');
    if (!bar || !days.length) return;
    const n = days.length;
    const p0 = n === 1 ? 0 : (selFrom / (n - 1)) * 100;
    const p1 = n === 1 ? 100 : (selTo / (n - 1)) * 100;

    const sel = document.getElementById('tl-sel');
    sel.style.left = p0 + '%';
    sel.style.width = Math.max(3, p1 - p0) + '%';
    document.getElementById('tl-h0').style.left = `calc(${p0}% - 7px)`;
    document.getElementById('tl-h1').style.left = `calc(${p1}% - 7px)`;

    document.querySelectorAll('.tl-day').forEach((el, i) => {
      el.classList.toggle('in-range', i >= selFrom && i <= selTo);
    });

    const a = activeList();
    const frp = a.reduce((s, d) => s + (d.frp || 0), 0);
    document.getElementById('tl-label').innerHTML =
      `<b>${fmtDay(days[selFrom])}${selFrom !== selTo ? ' → ' + fmtDay(days[selTo]) : ''}</b>`;
    document.getElementById('tl-count').textContent =
      ` · ${a.length} detecții · ${frp.toFixed(0)} MW`;

    renderPasses();
  }

  /* Banda cu trecerile satelitare din intervalul selectat. Fiecare buton
     incarca o imagine pe harta (termica S3 sau true-color S2). */
  let imgMode = 's3';

  function renderPasses() {
    const box = document.getElementById('tl-passes');
    if (!box) return;
    const z = window.__activeZone;
    const list = (s3Index && s3Index[z]) || [];
    if (!list.length) { box.innerHTML = ''; return; }

    const k0 = days[selFrom], k1 = days[selTo];
    const inRange = list.filter(e => {
      const d = (e.datetime || '').slice(0, 10);
      return d >= k0 && d <= k1;
    }).sort((a, b) => (a.datetime || '').localeCompare(b.datetime || ''));

    if (imgMode === 's2') {
      // modul S2: imaginile true-color, pe zile distincte
      const scenes = (window.__s2Scenes && window.__s2Scenes[z]) || [];
      const sel = scenes.filter(e => {
        const d = (e.datetime || '').slice(0, 10);
        return d >= k0 && d <= k1;
      });
      if (!sel.length) {
        box.innerHTML = '<div style="color:#8b98a5;font-size:11px;padding:6px 0">' +
          'nicio scenă Sentinel-2 în interval</div>';
        return;
      }
      box.innerHTML = '<div class="tl-ptitle">Sentinel-2 (true color, 60 m) — ' +
        sel.length + ' scene</div><div class="tl-prow">' +
        sel.map((e, i) => {
          const d = (e.datetime || '').slice(0, 10);
          const file = e.file || '';
          return `<button class="tl-pass" data-img="${file}" data-label="S2 ${d}">` +
                 `<span class="tl-pdot" style="background:#3b82f6"></span>` +
                 `<b>${fmtDay(d)}</b><br><span>${e.cloud ?? '?'}% nori</span></button>`;
        }).join('') + '</div>';
      wirePassButtons();
      return;
    }

    if (!inRange.length) {
      box.innerHTML = '<div style="color:#8b98a5;font-size:11px;padding:6px 0">' +
        'nicio trecere Sentinel-3 în interval</div>';
      return;
    }

    box.innerHTML = '<div class="tl-ptitle">Sentinel-3 SLSTR (termic) — ' +
      inRange.length + ' treceri</div><div class="tl-prow">' +
      inRange.map(e => {
        const d = (e.datetime || '').slice(0, 10);
        const t = (e.datetime || '').slice(11, 16);
        const night = e.daynight === 'N';
        const hot = e.bt_max > 330;
        return `<button class="tl-pass${hot ? ' hot' : ''}" data-img="${e.file}" ` +
               `data-label="S3 ${d} ${t}Z">` +
               `<span class="tl-pdot" style="background:${night ? '#6366f1' : '#facc15'}"></span>` +
               `<b>${fmtDay(d)} ${t}</b><br>` +
               `<span>${e.points}px · ${e.frp_sum.toFixed(0)}MW` +
               (e.bt_max > 200 ? ` · ${e.bt_max.toFixed(0)}K` : '') + '</span></button>';
      }).join('') + '</div>';

    wirePassButtons();
  }

  function wirePassButtons() {
    const mapImg = window.__showS3Image;
    document.querySelectorAll('#tl-passes .tl-pass').forEach(b => {
      b.addEventListener('click', () => {
        document.querySelectorAll('#tl-passes .tl-pass').forEach(x => x.classList.remove('on'));
        b.classList.add('on');
        if (mapImg) mapImg(b.dataset.img, b.dataset.label);
      });
    });
  }

  /* ---------- API pentru renderDetections ---------- */

  // Folosit de app.js: punctele din interval sunt saturate, restul estompate.
  function ranges() {
    return { from: days[selFrom], to: days[selTo] };
  }

  /* Reselecteaza o perioada dupa o reconstruire (refresh la 5 min).
     Fara asta, filtrul s-ar reseta la fiecare actualizare. */
  function restore(from, to) {
    const i = days.indexOf(from);
    const j = days.indexOf(to);
    if (i < 0 || j < 0) return false;
    selFrom = i; selTo = j;
    paint();
    return true;
  }

  function style(d, inRange) {
    const c = frpColor(d.frp);
    const esa = d.src === 'ESA';
    const ageH = (Date.now() - d._ms) / 3600000;
    // in interval: opacitate dupa vechime (proaspat = plin, vechi = mai stins)
    let op;
    if (inRange) {
      op = ageH <= 6 ? 0.92 : ageH <= 24 ? 0.8 : ageH <= 72 ? 0.68 : 0.55;
    } else {
      op = 0.13;   // context: se vede ca e ceva acolo, dar nu distrage
    }
    return {
      radius: Math.max(3, Math.min(22, 3 + Math.sqrt(d.frp || 0) * 1.6)),
      color: esa ? '#22d3ee' : '#ffffff',
      weight: inRange ? (esa ? 1.8 : 1.1) : 0.6,
      opacity: inRange ? 0.9 : 0.2,
      fillColor: rgb(c),
      fillOpacity: op,
    };
  }

  window.TIMELINE = { build, ranges, restore, style, activeList, frpColor: c => rgb(frpColor(c)) };
})();
