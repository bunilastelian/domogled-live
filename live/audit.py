#!/usr/bin/env python
"""
audit.py - audit masurat al dashboard-ului.

Compara ce se transfera pe fir (nu decomprimat), verifica structura incarcaturii,
tagurile din <head>, contrastul WCAG, comportamentul pe mobil, costul
reimprospatarii si dependentele externe.

Rulare:
    python audit.py                                  # site-ul public
    python audit.py http://127.0.0.1:8777/           # build-ul local

Atentie la masurare: GitHub Pages si CDN-urile servesc gzip. Daca masori dupa
decomprimare, supraestimezi transferul de cateva ori — prima versiune a acestui
audit a raportat 1,78 GB/luna in loc de 0,15 GB/luna din exact aceasta cauza.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BASE = "https://bunilastelian.github.io/domogled-live/"
UA = {"User-Agent": "audit/1.0", "Accept-Encoding": "gzip"}

ASSETS = ["", "app.js", "state/state.json", "domogled.geojson", "portiledefier.geojson"]


def zone_assets(state: dict) -> list[str]:
    """Fisierele per zona, luate din stare — nu hardcodate.

    Dupa trecerea la multi-zona, numele imaginilor contin prefixul zonei
    (`domogled_arsura.png`), deci o lista fixa ajunge sa raporteze 404.
    """
    out = []
    for _zid, z in (state.get("zones") or {}).items():
        if not isinstance(z, dict):
            continue
        b = (z.get("burn") or {}).get("png")
        if b:
            out.append(f"img/{b}")
        for it in list((z.get("imagery") or {}).values())[:1]:
            if it.get("file"):
                out.append(f"img/{it['file']}")
    return out

ASSETS = ["", "app.js", "state/state.json", "domogled.geojson", "portiledefier.geojson"]


def fetch(url: str) -> tuple[int, int, bytes, dict]:
    """Intoarce (bytes pe fir, bytes decomprimat, continut, headere)."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        hdrs = dict(r.headers)
    wire = len(raw)
    if hdrs.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return wire, len(raw), raw, hdrs


def external_urls(html: str) -> list[str]:
    """Resursele externe chiar INCARCATE de pagina, nu o lista hardcodata.

    Se uita doar la <script src> si <link rel=stylesheet href> — altfel linkul
    canonical si og:url (care sunt pagini, nu resurse) ajung numarate ca transfer.
    """
    out, seen = [], set()
    for tag in re.findall(r"<script\b[^>]*>|<link\b[^>]*>", html, re.I):
        if tag.lower().startswith("<link") and "stylesheet" not in tag.lower():
            continue
        m = re.search(r'(?:src|href)="(https?://[^"]+)"', tag)
        if not m:
            continue
        base = m.group(1).split("?")[0]
        if base in seen:
            continue
        seen.add(base)
        out.append(base)
    return out


def contrast(fg: str, bg: str) -> float:
    def lum(hexcol: str) -> float:
        h = hexcol.lstrip("#")
        parts = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        parts = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    if not base.endswith("/"):
        base += "/"
    print(f"Audit pentru: {base}\n")

    # ---------------------------------------------------------------- 1. marimi
    print("=" * 74)
    print("1. MARIMI SI COMPRESIE")
    print("=" * 74)
    wire_total = dec_total = 0
    html = ""
    state_raw = b""
    state_wire = 0
    try:
        _w0, _d0, _raw0, _h0 = fetch(base + "state/state.json")
        _state0 = json.loads(_raw0)
    except Exception:
        _state0 = {}
    assets = ASSETS + [a for a in zone_assets(_state0) if a not in ASSETS]
    print(f"  fisiere verificate: {len(assets)}")
    for a in assets:
        try:
            wire, dec, raw, h = fetch(base + a)
            enc = h.get("Content-Encoding", "fara")
            cc = h.get("Cache-Control", "-")[:20]
            wire_total += wire
            dec_total += dec
            print(f"  {('/' + a) if a else '/':<26}{wire/1024:>7.1f} KB pe fir {dec/1024:>8.1f} KB brut  "
                  f"{enc:<5} {cc}")
            if a == "":
                html = raw.decode("utf-8", "replace")
            if a == "state/state.json":
                state_raw, state_wire = raw, wire
        except Exception as e:  # noqa: BLE001
            print(f"  /{a:<25} EROARE: {e}")

    print(f"\n  {'TOTAL pagina':<26}{wire_total/1024:>7.1f} KB pe fir {dec_total/1024:>8.1f} KB brut")

    ext = external_urls(html)
    cdn_wire = 0
    print(f"\n  Resurse externe chiar incarcate ({len(ext)}):")
    for u in ext:
        try:
            wire, dec, _r, _h = fetch(u)
            cdn_wire += wire
            print(f"    {u.split('/')[-1][:24]:<24}{wire/1024:>7.1f} KB pe fir {dec/1024:>8.1f} KB brut")
        except Exception as e:  # noqa: BLE001
            print(f"    {u.split('/')[-1][:24]:<24} EROARE: {e}")
    print(f"\n  {'TOTAL prima incarcare':<26}{(wire_total + cdn_wire)/1024:>7.1f} KB pe fir")

    kb = state_wire / 1024
    print(f"\n  Cu tab-ul deschis (state.json la 60 s):")
    print(f"    {kb:.1f} KB pe fir x 60/ora        = {kb * 60 / 1024:>6.2f} MB/ora")
    print(f"    x 8 h pe zi, 30 zile            = {kb * 60 * 8 * 30 / 1024 / 1024:>6.2f} GB/luna")

    # ------------------------------------------------------------ 2. incarcatura
    print("\n" + "=" * 74)
    print("2. STRUCTURA state.json")
    print("=" * 74)
    st = json.loads(state_raw)
    total_bytes = len(state_raw)
    for key in ("detections", "stats", "alerts", "imagery", "burn", "frp_products"):
        v = st.get(key)
        n = len(json.dumps(v, ensure_ascii=False).encode()) if v is not None else 0
        pct = 100 * n / total_bytes if total_bytes else 0
        print(f"  {key:<16}{n/1024:>8.1f} KB  {pct:>5.1f}%")
    dets = st.get("detections") or {}
    if dets:
        k0 = next(iter(dets))
        one = json.dumps(dets[k0], ensure_ascii=False)
        print(f"\n  detectii: {len(dets)}")
        print(f"  o detectie: {len(one)} bytes")
        print(f"  cheia duplica datele: {len(json.dumps(k0))} bytes x {len(dets)} "
              f"= {len(json.dumps(k0))*len(dets)/1024:.1f} KB ({100*len(json.dumps(k0))*len(dets)/total_bytes:.0f}%)")
        print("  (dupa gzip costul pe fir e mic — prioritate scazuta)")

    # ------------------------------------------------------------------ 3. head
    print("\n" + "=" * 74)
    print("3. <head>")
    print("=" * 74)
    checks = {
        "meta description": r'<meta[^>]+name=["\']description',
        "Open Graph": r'property=["\']og:',
        "Twitter card": r'name=["\']twitter:card',
        "favicon": r'rel=["\'](icon|shortcut icon)',
        "theme-color": r'name=["\']theme-color',
        "canonical": r'rel=["\']canonical',
        "lang=ro": r'<html[^>]+lang=["\']ro',
        "title": r"<title>",
    }
    for name, pat in checks.items():
        print(f"  [{'x' if re.search(pat, html, re.I) else ' '}] {name}")

    # -------------------------------------------------------------- 4. contrast
    print("\n" + "=" * 74)
    print("4. CONTRAST (WCAG AA: 4.5:1 text normal, 3:1 text mare)")
    print("=" * 74)
    pairs = [
        ("text principal", "#e6edf3", "#0e1116"),
        ("text secundar", "#8b98a5", "#161b22"),
        ("text pe card", "#e6edf3", "#161b22"),
        ("badge activ", "#7ee2b8", "#1f2937"),
        ("badge avertizare", "#ffd479", "#1f2937"),
        ("badge eroare", "#ff9c9c", "#1f2937"),
        ("KPI portocaliu", "#f76b15", "#0d1117"),
        ("link", "#3b82f6", "#161b22"),
    ]
    worst = 99.0
    for name, fg, bg in pairs:
        r = contrast(fg, bg)
        worst = min(worst, r)
        verdict = "OK" if r >= 4.5 else ("doar text mare" if r >= 3 else "SUB PRAG")
        print(f"  {name:<20}{r:>5.2f}:1   {verdict}")
    print(f"  cel mai slab raport: {worst:.2f}:1")

    # ---------------------------------------------------------------- 5. mobil
    print("\n" + "=" * 74)
    print("5. MOBIL / RESPONSIVE")
    print("=" * 74)
    mq = re.findall(r"@media[^{]+", html)
    print(f"  media queries: {len(mq)}")
    for q in mq:
        print(f"     {q.strip()}")
    m = re.search(r"grid-template-columns:\s*([^;]+);", html)
    print(f"  grila de baza: {m.group(1).strip() if m else '?'}")
    override = bool(re.search(r"@media[^{]*900px[^@]*?grid-template-columns", html, re.S))
    print(f"  coloana fixa anulata pe mobil: {'DA' if override else 'NU — layout rupt pe telefon'}")
    map_h = re.search(r"@media[^{]*900px.{0,400}?#map\s*\{[^}]*height", html, re.S)
    print(f"  inaltime harta setata separat pe mobil: {'DA' if map_h else 'NU'}")
    vp = re.search(r'name=["\']viewport["\'][^>]*content=["\']([^"\']+)', html)
    print(f"  viewport: {vp.group(1) if vp else 'LIPSESTE'}")

    # ------------------------------------------------------- 6. cost reimprospatare
    print("\n" + "=" * 74)
    print("6. COSTUL REIMPROSPATARII")
    print("=" * 74)
    try:
        _w, _d, js_raw, _h = fetch(base + "app.js")
        app_js = js_raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        app_js = ""
    guards = {
        "sar peste re-randare daca datele nu s-au schimbat": r"lastRenderSig",
        "overlay de arsura recreat doar la schimbare": r"lastBurnSig",
        "miniaturi reconstruite doar la schimbare": r"dataset\.sig",
        "grafic desenat local, fara biblioteca": r"getContext\(['\"]2d['\"]\)",
        "prag de prospetime": r"STALE_WARN_MIN",
        "aspect responsive la redimensionare": r"addEventListener\(['\"]resize",
    }
    for label, pat in guards.items():
        print(f"  [{'x' if re.search(pat, app_js) else ' '}] {label}")
    n = len(dets)
    print(f"\n  detectii in stare: {n}")
    print(f"  fara garda: ~{n * 2} obiecte recreate la fiecare minut")
    print(f"  cu garda:  doar cand se schimba 'updated' (la ~15 min, cat ruleaza CI-ul)")

    # ------------------------------------------------------ 7. dependente externe
    print("\n" + "=" * 74)
    print("7. DEPENDENTE EXTERNE")
    print("=" * 74)
    domains = set()
    for u in ext:
        domains.add(u.split("/")[2])
        print(f"  resursa: {u[:66]}")
    for u in re.findall(r"https?://[a-z0-9.{}]+", app_js):
        if any(k in u for k in ("tile", "arcgisonline", "basemaps")):
            domains.add(u.split("/")[2])
    print(f"\n  domenii externe: {len(domains)}")
    for d in sorted(domains):
        print(f"     {d}")
    print("  -> daca pica domeniul Leaflet, pagina rămâne fara harta")

    # ------------------------------------------------ 8. structura geometriilor
    print("\n" + "=" * 74)
    print("8. STRUCTURA GEOJSON (un nivel in plus = poligon care nu se deseneaza)")
    print("=" * 74)
    for name in ("domogled.geojson", "portiledefier.geojson"):
        try:
            _w, _d, raw, _h = fetch(base + name)
        except Exception:  # noqa: BLE001
            continue
        g = json.loads(raw)
        t, c = g.get("type"), g.get("coordinates")
        ok, detail = False, "structura neasteptata"
        if t == "Polygon" and isinstance(c, list) and c and isinstance(c[0], list) and c[0]:
            p0 = c[0][0]
            ok = isinstance(p0, list) and len(p0) == 2 and isinstance(p0[0], (int, float))
            detail = f"{len(c)} inele, primul cu {len(c[0])} puncte"
            if ok and c[0][0] != c[0][-1]:
                ok, detail = False, detail + " (inel neinchis)"
        elif t == "MultiPolygon" and isinstance(c, list) and c:
            ok, detail = True, f"{len(c)} poligoane"
        print(f"  [{'x' if ok else ' '}] {name:<24} {t}, {detail}")
        if not ok:
            print("      [!] structura nu corespunde tipului — conturul nu se deseneaza pe harta")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
