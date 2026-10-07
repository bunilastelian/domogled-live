#!/usr/bin/env python3
"""
Extractie de context din state.json pentru harness.

DE CE EXISTA ACEST FISIER:
Prima versiune a harness-ului ghicise numele campurilor (weather.humidity,
rain_48h etc.) si a raportat "date lipsa" pentru informatii care EXISTAU in
state.json sub alte nume (humidity_pct, precip_next48_mm). Modelul a analizat
corect, dar pe un context saracit - si a semnalat onest lipsurile, ceea ce a
facut bug-ul vizibil. Acest modul centralizeaza maparea ca sa nu se repete.

Toate numele de aici sunt verificate pe state.json real. Daca un camp se
schimba in collector, se schimba intr-un singur loc.
"""

from __future__ import annotations

from typing import Any


def _g(d: dict | None, *path, default=None):
    """get imbricat: _g(st, 'danger', 'index')"""
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def stats_line(st: dict) -> str:
    """Agregarile principale + seria zilnica (esentiala pentru trend)."""
    parts = [
        f"{st.get('total')} detectii totale ({st.get('nasa')} NASA + {st.get('esa')} ESA)",
        f"{st.get('last24_count')} in 24h, FRP 24h {st.get('last24_frp')} MW "
        f"(max {st.get('last24_max')} MW)",
    ]
    days = st.get("days") or []
    if days:
        ser = ", ".join(f"{d.get('date','')[5:]}: {d.get('count')} det/{d.get('frp')} MW"
                        for d in days[-8:])
        parts.append(f"SERIE ZILNICA (ultimele {len(days[-8:])} zile): {ser}")
        # varful istoric - cel mai util reper pentru "a evoluat sau nu"
        peak = max(days, key=lambda d: d.get("frp") or 0)
        parts.append(f"VARF ISTORIC: {peak.get('date')} cu {peak.get('frp')} MW "
                     f"in {peak.get('count')} detectii")
        if len(days) >= 3:
            a, b = days[-3].get("frp") or 0, days[-1].get("frp") or 0
            if a:
                parts.append(f"trend 3 zile: {a} -> {b} MW ({100*(b-a)/a:+.0f}%)")
    return "; ".join(parts)


def weather_line(w: dict | None) -> str:
    """Meteo cu numele REALE din state.json."""
    if not w:
        return "meteo: lipseste"
    return (
        f"meteo: {w.get('temp_c')}C, RH {w.get('humidity_pct')}%, "
        f"vant {w.get('wind_kmh')} km/h din {w.get('wind_from_compass')} "
        f"({w.get('wind_from_deg')}deg) -> pana spre {w.get('downwind_compass')}, "
        f"rafale {w.get('gust_kmh')} km/h, "
        f"precipitatii acum {w.get('precip_now_mm')} mm / urmatoarele 48h "
        f"{w.get('precip_next48_mm')} mm, "
        f"VPD {w.get('vpd_kpa')} kPa, umezeala sol {w.get('soil_moisture')}, "
        f"pericol {_g(w,'danger','name')} {_g(w,'danger','index')} "
        f"({_g(w,'danger','class')}), in 48h min {_g(w,'danger','next48_min')}"
    )


def terrain_line(w: dict | None) -> str:
    t = (w or {}).get("terrain")
    if not t:
        return ""
    return (f"teren: {t.get('elevation_m')} m, panta {t.get('slope_pct')}%, "
            f"panta urcata spre {t.get('upslope_compass')}, "
            f"cel mai abrupt in jos spre {t.get('steepest_down_deg')}deg, "
            f"{'pe varf' if t.get('on_summit') else 'pe versant'}")


def fire_line(f: dict) -> str:
    """Un focar, cu toate campurile care conteaza pentru evolutie."""
    return (
        f"focar {f.get('id')} (rank {f.get('rank')}): {f.get('points')} puncte, "
        f"{f.get('days')} zile (din {str(f.get('first'))[:10]}), "
        f"FRP max {f.get('frp_max')} MW, FRP total {f.get('frp_sum')} MW; "
        f"ULTIMELE 24h: {f.get('frp_last24')} MW / {f.get('det_last24')} det; "
        f"CELE 24h ANTERIOARE: {f.get('frp_prev24')} MW / {f.get('det_prev24')} det "
        f"-> TREND {f.get('trend')}; "
        f"extindere {f.get('extent_km')} km, la {f.get('nearest_km')} km de "
        f"{f.get('nearest_place')}"
        + (" [IN PARC]" if f.get("in_park") else " [in afara parcului]")
    )


def burn_line(b: dict | None) -> str:
    """Cicatricea de ardere dNBR - date cantitative, nu doar imaginea."""
    if not b:
        return "cicatrice dNBR: nu s-a putut calcula"
    clases = ", ".join(f"{c.get('clasa')}: {c.get('ha')} ha"
                       for c in (b.get("clase") or [])[1:])   # sarim "ars usor" (zgomot)
    return (
        f"cicatrice dNBR ({b.get('base_date')} vs {b.get('post_date')}, "
        f"nori {b.get('post_cloud')}): "
        f"{b.get('ha_total')} ha identificate cu semnal, "
        f"{b.get('ha_moderat_plus')} ha moderat+, {b.get('ha_sever')} ha sever; "
        f"dNBR mediu pe focar {b.get('dnbr_mediu_focar')}, "
        f"fundal {b.get('background_dnbr')}"
        + (f"; clase: {clases}" if clases else "")
    )


def zone_context(zid: str, z: dict, hist_days: int = 8) -> str:
    """Tot contextul numeric al unei zone, gata de prompt. O linie per aspect."""
    st = z.get("stats") or {}
    lines = [f"CIFRE: {stats_line(st)}", weather_line(z.get("weather"))]
    t = terrain_line(z.get("weather"))
    if t:
        lines.append(t)
    for f in (z.get("fires") or [])[:3]:
        lines.append(fire_line(f))
    if z.get("burn"):
        lines.append(burn_line(z["burn"]))
    if z.get("aoi"):
        aoi = z["aoi"]
        lines.append(f"AOI: centru {aoi.get('center')}, "
                     f"{aoi.get('area_km2')} km2" if isinstance(aoi, dict) else "")
    return "\n".join(x for x in lines if x)


def zone_context_compact(zid: str, z: dict) -> str:
    """Varianta scurta, pentru promptul de viziune (unde spatiul e pretios)."""
    st = z.get("stats") or {}
    w = z.get("weather") or {}
    parts = [f"{st.get('total')} detectii totale, {st.get('last24_count')} in 24h, "
             f"FRP 24h {st.get('last24_frp')} MW"]
    for f in (z.get("fires") or [])[:2]:
        parts.append(f"focar {f.get('id')}: {f.get('days')} zile, "
                     f"FRP24h {f.get('frp_last24')} MW (anterior {f.get('frp_prev24')}), "
                     f"trend {f.get('trend')}")
    if w:
        parts.append(f"meteo {w.get('temp_c')}C, RH {w.get('humidity_pct')}%, "
                     f"vant {w.get('wind_kmh')} km/h din {w.get('wind_from_compass')}")
    return "; ".join(parts)


def comparison_line(zid: str, d: dict, hours: float | None) -> list[str]:
    """Linii despre ce s-a schimbat fata de snapshot-ul anterior."""
    out = []
    h = f"{hours}h" if hours else "rularea anterioara"
    for key, label in (("last24_frp", "FRP24h"), ("last24_count", "det24h"),
                       ("total", "total det")):
        v = d.get(key)
        if v is not None:
            out.append(f"{label} fata de acum {h}: {v:+}")
    for nf in (d.get("new_fires") or []):
        out.append(f"FOCAR NOU la {nf.get('lat')},{nf.get('lon')} "
                   f"FRP {nf.get('frp_24h')} MW, {nf.get('points')} puncte")
    for gf in (d.get("gone_fires") or []):
        out.append(f"FOCAR DISPARUT: {gf.get('id')} (ultim FRP {gf.get('last_frp')} MW)")
    for ch in (d.get("frp_changes") or []):
        out.append(f"schimbare FRP la {ch.get('id')}: {ch.get('from')} -> "
                   f"{ch.get('to')} ({ch.get('delta_frp'):+} MW), trend {ch.get('trend')}")
    if d.get("new_scene"):
        out.append(f"IMAGINE NOUA disponibila: {str(d.get('cur_scene'))[:60]}")
    return out
