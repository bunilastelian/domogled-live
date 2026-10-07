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

  /* ---------- construire UI ---------- */

  function build(container, state, onFilter) {
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
        paint(); onFilter(activeList());
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
