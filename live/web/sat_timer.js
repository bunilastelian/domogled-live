/* Timer treceri satelit + status date Copernicus.
 *
 * DE CE: Copernicus publica produsul la ~73 min dupa achizitie (medie globala
 * masurata). Pentru zona noastra, la ultimele produse: 2h16m (S3A) si 2h17m (S3B).
 * Orbita e heliosincrona, deci ora trecerii e stabila zi de zi.
 *
 * Afiseaza: countdown pana la urmatoarea trecere, ora cand ar trebui sa apara
 * datele, si cat de vechi sunt datele curente. */
(function () {
  let doc = null;
  let tick = null;

  async function load() {
    try {
      const r = await fetch('state/sat_passes.json?t=' + Date.now());
      doc = r.ok ? await r.json() : null;
    } catch (e) { doc = null; }
    render();
  }

  function fmtLocal(iso) {
    const d = new Date(iso);
    const p = n => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function countdown(min) {
    if (min <= 0) return 'acum';
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60), m = min % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  }

  function render() {
    const box = document.getElementById('sat-timer');
    if (!box) return;
    if (!doc || !doc.urmatoarele_treceri || !doc.urmatoarele_treceri.length) {
      box.innerHTML = '<span style="color:#8b98a5">fără predicție</span>';
      return;
    }

    const now = Date.now();
    // recalculam countdown-ul local (fisierul se actualizeaza la 5 min)
    const rows = doc.urmatoarele_treceri.map(p => {
      const t = new Date(p.ora_estimata_utc).getTime();
      const minsLeft = Math.max(0, Math.round((t - now) / 60000));
      return { ...p, minsLeft };
    }).sort((a, b) => a.minsLeft - b.minsLeft);

    const n = rows[0];
    const avail = new Date(n.date_disponibile_utc).getTime();
    const availMin = Math.max(0, Math.round((avail - now) / 60000));

    const cls = n.minsLeft <= 15 ? 'soon' : '';
    const pole = n.fereastra === 'noapte' ? '🌙' : '☀️';

    let html = `
      <div class="sat-head ${cls}">
        <span class="sat-sat">${n.satelit} ${pole}</span>
        <span class="sat-cd">${countdown(n.minsLeft)}</span>
      </div>
      <div class="sat-sub">
        trece la <b>${fmtLocal(n.ora_estimata_utc)}</b> ·
        date ~<b>${fmtLocal(n.date_disponibile_utc)}</b>
        <span style="color:#6b7785">(±${Math.round(n.variatie_min + 73)}m)</span>
      </div>
    `;

    // celelalte treceri, compact
    const rest = rows.slice(1, 4);
    if (rest.length) {
      html += '<div class="sat-rest">' + rest.map(p =>
        `<span title="date disponibile ~${fmtLocal(p.date_disponibile_utc)}">` +
        `${p.satelit} ${p.fereastra === 'noapte' ? '🌙' : '☀️'} ` +
        `${fmtLocal(p.ora_estimata_utc)}</span>`).join('') + '</div>';
    }

    // starea datelor curente
    const la = doc.ultima_achizitie;
    if (la) {
      const age = la.vechime_min;
      const ageTxt = age < 60 ? `${age} min` :
        `${Math.floor(age / 60)}h ${age % 60}m`;
      // 'in_interval_normal': satelitul nu acopera zona la fiecare orbita -
      // 10h vechime dimineata e normal, nu o intarziere
      const ok = la.in_interval_normal !== false;
      html += `<div class="sat-age ${ok ? 'ok' : 'late'}" title="${la.explicatie || ''}">
        ultima achiziție ${la.satelit} ${fmtLocal(la.achizitie_utc)} · acum ${ageTxt}
        ${ok ? ' ✓' : ' · <b>gol de date</b>'}
      </div>`;
    }

    box.innerHTML = html;
    const el = document.getElementById('s-sat-timer');
    if (el) el.textContent = `${n.satelit} ${countdown(n.minsLeft)}`;
  }

  function init() {
    load();
    if (tick) clearInterval(tick);
    tick = setInterval(render, 30000);      // countdown viu, la 30s
  }

  window.SAT_TIMER = { init, load };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
