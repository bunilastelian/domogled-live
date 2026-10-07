#!/usr/bin/env python
"""
weather.py - meteo, indice de pericol de incendiu si directia de propagare.

Surse (Open-Meteo, gratuite, fara cheie):
  * forecast orar: temperatura, umiditate relativa, vant, rafale, precipitatii,
    deficit de presiune a vaporilor (VPD), umezeala solului
  * elevatie pe grid - pentru panta si expozitie

Indicele de pericol: indicele Angström, forma clasica si transparenta:

        I = (H / 20) + (27 - T) / 10

  unde H = umiditatea relativa in %, T = temperatura in °C. Cu cat I e mai mic,
  cu atat riscul e mai mare. Pragurile uzuale:
        I < 2      pericol foarte ridicat
        2 - 2.5    ridicat
        2.5 - 3    moderat
        3 - 4      scazut
        > 4        fara pericol semnificativ

  Nu folosim FWI canadian: nu e disponibil pe API-ul gratuit, iar o implementare
  partiala ar da un numar care pare oficial fara sa fie. Angström e verificabil
  din datele brute pe care le publicam alaturi.
"""

from __future__ import annotations

import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

UA = {"User-Agent": "domogled-live/2.0"}
FORECAST = "https://api.open-meteo.com/v1/forecast"
ELEVATION = "https://api.open-meteo.com/v1/elevation"

COMPASS = ["N", "NE", "E", "SE", "S", "SV", "V", "NV"]

HOURLY = ("temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,"
          "wind_gusts_10m,precipitation,vapour_pressure_deficit,soil_moisture_0_to_7cm")


def _get(url: str, timeout: int = 45) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def compass(deg: float | None) -> str:
    if deg is None:
        return "?"
    return COMPASS[int((deg % 360) / 45 + 0.5) % 8]


def angstrom(temp: float | None, rh: float | None) -> float | None:
    """Indicele Angström. Mai mic = risc mai mare."""
    if temp is None or rh is None:
        return None
    return round(rh / 20 + (27 - temp) / 10, 3)


def danger_class(idx: float | None) -> tuple[str, str]:
    """(eticheta, culoare) pentru indicele Angström."""
    if idx is None:
        return ("necunoscut", "#6b7684")
    if idx < 2:
        return ("foarte ridicat", "#e5484d")
    if idx < 2.5:
        return ("ridicat", "#f76b15")
    if idx < 3:
        return ("moderat", "#f5d90a")
    if idx < 4:
        return ("scăzut", "#30a46c")
    return ("fără pericol semnificativ", "#3b82f6")


def slope_aspect(center: tuple[float, float], span_km: float = 2.0) -> dict:
    """
    Panta si expozitia, din 8 puncte de elevatie in jurul centrului.

    Intoarce `upslope_deg` = directia in care terenul urca la scara data (incotro
    se propaga focul cel mai repede pe panta) si `slope_pct`.

    Atentie: daca focarul e pe un vârf sau pe o creasta, toate punctele din jur
    sunt mai jos, gradientul local e mic si `on_summit` e True — atunci panta NU
    determina directia de propagare, iar vantul rămâne factorul principal.
    """
    lat, lon = center
    d = span_km / 111.0
    ring = [(lat + d, lon), (lat + d, lon + d), (lat, lon + d), (lat - d, lon + d),
            (lat - d, lon), (lat - d, lon - d), (lat, lon - d), (lat + d, lon - d)]
    pts = [(lat, lon)] + ring
    lats = ",".join(f"{p[0]:.5f}" for p in pts)
    lons = ",".join(f"{p[1]:.5f}" for p in pts)
    try:
        el = _get(f"{ELEVATION}?latitude={lats}&longitude={lons}")["elevation"]
    except Exception:  # noqa: BLE001
        return {}
    z0, r = el[0], el[1:]
    if z0 is None or any(z is None for z in r):
        return {}

    dlat_m = d * 111000.0
    dlon_m = d * 111000.0 * math.cos(math.radians(lat))
    gx = ((r[1] + r[2] + r[3]) - (r[5] + r[6] + r[7])) / (3 * dlon_m)
    gy = ((r[0] + r[1] + r[7]) - (r[3] + r[4] + r[5])) / (3 * dlat_m)
    slope = math.hypot(gx, gy) * 100
    up_deg = (math.degrees(math.atan2(gx, gy))) % 360 if (gx or gy) else None

    return {
        "elevation_m": z0,
        "mean_ring_m": round(sum(r) / len(r), 1),
        "span_km": span_km,
        "on_summit": all(z < z0 for z in r),
        "upslope_deg": None if up_deg is None else round(up_deg, 1),
        "upslope_compass": compass(up_deg),
        "slope_pct": round(slope, 1),
        "steepest_down_deg": None if up_deg is None else round((up_deg + 180) % 360, 1),
    }


def zone_weather(center: tuple[float, float], days: int = 3) -> dict:
    """Meteo curent, indice de pericol, directie de propagare si pana de fum."""
    lat, lon = center
    q = urllib.parse.urlencode({
        "latitude": f"{lat}", "longitude": f"{lon}",
        "hourly": HOURLY, "current": (
            "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,"
            "wind_gusts_10m,precipitation,vapour_pressure_deficit"),
        "timezone": "UTC", "forecast_days": days,
    })
    d = _get(f"{FORECAST}?{q}")
    cur = d.get("current") or {}
    h = d.get("hourly") or {}

    temp = cur.get("temperature_2m")
    rh = cur.get("relative_humidity_2m")
    wdir = cur.get("wind_direction_10m")
    wspeed = cur.get("wind_speed_10m")
    idx = angstrom(temp, rh)
    label, colour = danger_class(idx)

    # vantul vine DIN wdir, deci pana merge SPRE wdir + 180
    to_deg = None if wdir is None else (wdir + 180) % 360

    series = []
    for i, t in enumerate(h.get("time", [])[:48]):
        def v(k):
            a = h.get(k)
            return a[i] if a and i < len(a) else None
        series.append({
            "t": t,
            "temp": v("temperature_2m"),
            "rh": v("relative_humidity_2m"),
            "wind": v("wind_speed_10m"),
            "dir": v("wind_direction_10m"),
            "precip": v("precipitation"),
            "angstrom": angstrom(v("temperature_2m"), v("relative_humidity_2m")),
        })

    # precipitatii in urmatoarele 48 h - cea mai importanta informatie practica
    precip48 = sum(x["precip"] or 0 for x in series[:48])
    best = next((x for x in series if x["angstrom"] is not None), None)

    return {
        "source": "Open-Meteo",
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "time": cur.get("time"),
        "temp_c": temp,
        "humidity_pct": rh,
        "wind_kmh": wspeed,
        "gust_kmh": cur.get("wind_gusts_10m"),
        "wind_from_deg": wdir,
        "wind_from_compass": compass(wdir),
        "downwind_deg": to_deg,
        "downwind_compass": compass(to_deg),
        "precip_now_mm": cur.get("precipitation"),
        "precip_next48_mm": round(precip48, 1),
        "vpd_kpa": cur.get("vapour_pressure_deficit"),
        "soil_moisture": (h.get("soil_moisture_0_to_7cm") or [None])[0],
        "elevation_m": d.get("elevation"),
        "danger": {"index": idx, "name": "Angström", "class": label, "color": colour,
                   "formula": "I = (H/20) + (27-T)/10; mai mic = mai periculos",
                   "next48_min": best["angstrom"] if best else None},
        "terrain": slope_aspect(center),
        "series": series[:24],
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=44.8982)
    ap.add_argument("--lon", type=float, default=22.4785)
    a = ap.parse_args()
    w = zone_weather((a.lat, a.lon))
    for k, v in w.items():
        if k == "series":
            print(f"  series: {len(v)} ore, prima {v[0]['t']} ({v[0]['temp']}C, {v[0]['rh']}%, "
                  f"{v[0]['wind']} km/h, Angström {v[0]['angstrom']})")
        else:
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
