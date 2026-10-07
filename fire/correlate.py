#!/usr/bin/env python
"""
correlate.py - corelare intre detectiile satelitare si reperele din teren.

Calculeaza:
  * distanta de la fiecare detectie la localitati (Baile Herculane, Pecinisca, Orsova)
  * care varfuri numite sunt in interiorul zonei arse/detectate
  * sectoarele raportate de IGSU/Rador si distanta lor fata de focarele detectate
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

# localitati (Nominatim/OSM)
TOWNS = {
    "Băile Herculane (centru)": (44.8785, 22.4144),
    "Pecinișca (sat, lângă stațiune)": (44.8712, 22.4187),
    "Orșova": (44.7235, 22.3983),
    "Drobeta-Turnu Severin": (44.6258, 22.6532),
    "Cerna-Sat": (45.1285, 22.6858),
    "Mehadia": (44.9036, 22.3667),
}
# varfuri / repere (OpenStreetMap, natural=peak)
PEAKS = {
    "Colțu Pietrii": (44.90550, 22.48217, 1228),
    "La Margina": (44.89279, 22.49422, 1026),
    "Hurcului": (44.90383, 22.44948, 1088),
    "Șușcu": (44.89190, 22.44871, 1192),
    "Cociu": (44.92506, 22.48442, 1120),
    "Domogledul Mic": (44.87658, 22.43856, None),
    "Domogledul Mare": (44.87161, 22.44364, 1105),
    "Babelor": (44.93160, 22.50010, 1103),
}
# repere raportate in teren (IGSU / Radio Romania)
FIELD = {
    "Cascada Cociului": (44.91838, 22.46485),
    "Crucea Albă (raportat, negeolocalizat)": None,
    "Șaua Padinei (raportat)": None,
    "Vârful Domogled - creasta spre Mehedinți (raportat)": None,
}


def dist_km(a, b) -> float:
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def load(name: str) -> list[dict]:
    p = OUT / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    firms = load("detections.csv")
    esa = load("esa_frp_detections.csv")
    for r in firms:
        r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
        r["frp_mw"] = float(r["frp_mw"]) if r["frp_mw"] else 0.0
    for r in esa:
        r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
        r["frp_mw"] = float(r["frp_mw"]) if r["frp_mw"] else 0.0

    print(f"Detectii: NASA FIRMS = {len(firms)}, ESA SLSTR FRP = {len(esa)}\n")

    print("=== 1. Cat de aproape a ajuns focul de localitati ===")
    print(f"{'localitate':<34}{'cea mai apropiata detectie':>28}{'data':>12}{'FRP':>8}{'nr in 10 km':>12}")
    print("-" * 96)
    for name, (lat, lon) in TOWNS.items():
        if not firms:
            break
        best = min(firms, key=lambda r: dist_km((lat, lon), (r["lat"], r["lon"])))
        d = dist_km((lat, lon), (best["lat"], best["lon"]))
        near10 = sum(1 for r in firms if dist_km((lat, lon), (r["lat"], r["lon"])) <= 10)
        print(f"{name[:33]:<34}{d:>22.2f} km{best['date']:>12}{best['frp_mw']:>8.1f}{near10:>12}")

    print("\n=== 2. Varfuri in interiorul zonei cu detectii ===")
    print(f"{'varf':<22}{'alt (m)':>8}{'dist. min. la o detectie':>26}{'detectii in 2 km':>18}")
    print("-" * 76)
    for name, (lat, lon, ele) in sorted(PEAKS.items(),
                                        key=lambda kv: min(dist_km((kv[1][0], kv[1][1]), (r["lat"], r["lon"]))
                                                           for r in firms) if firms else 0):
        if not firms:
            break
        dmin = min(dist_km((lat, lon), (r["lat"], r["lon"])) for r in firms)
        n2 = sum(1 for r in firms if dist_km((lat, lon), (r["lat"], r["lon"])) <= 2)
        print(f"{name:<22}{str(ele or '-'):>8}{dmin:>22.2f} km{n2:>18}")

    print("\n=== 3. Repere raportate in teren ===")
    for name, coords in FIELD.items():
        if coords is None:
            print(f"   {name}: negeolocalizat in OSM")
            continue
        d_firms = min(dist_km(coords, (r["lat"], r["lon"])) for r in firms) if firms else None
        d_esa = min(dist_km(coords, (r["lat"], r["lon"])) for r in esa) if esa else None
        print(f"   {name}: {coords[0]:.5f}, {coords[1]:.5f}")
        if d_firms is not None:
            print(f"      cea mai apropiata detectie FIRMS: {d_firms:.2f} km")
        if d_esa is not None:
            print(f"      cea mai apropiata detectie ESA:   {d_esa:.2f} km")

    print("\n=== 4. Intinderea reala a zonei cu focare (detectii FIRMS in parc) ===")
    if firms:
        lats = [r["lat"] for r in firms]
        lons = [r["lon"] for r in firms]
        clat = sum(lats) / len(lats)
        span_lat = (max(lats) - min(lats)) * 111
        span_lon = (max(lons) - min(lons)) * 111 * math.cos(math.radians(clat))
        print(f"   bbox: {min(lats):.4f}..{max(lats):.4f} N, {min(lons):.4f}..{max(lons):.4f} E")
        print(f"   intindere: {span_lat:.1f} km (N-S) x {span_lon:.1f} km (E-V)")
        print(f"   suprafata bbox: {span_lat * span_lon:.0f} km2 (plafon superior, nu suprafata arsa)")

    print("\n=== 5. Intensitatea pe zile (comparatie ESA vs NASA) ===")
    from collections import defaultdict
    e_by_day: dict[str, float] = defaultdict(float)
    for r in esa:
        e_by_day[r["date"]] += r["frp_mw"] or 0
    f_by_day: dict[str, float] = defaultdict(float)
    f_count: dict[str, int] = defaultdict(int)
    for r in firms:
        f_by_day[r["date"]] += r["frp_mw"] or 0
        f_count[r["date"]] += 1
    print(f"{'data':<12}{'FIRMS det':>10}{'FIRMS FRP':>11}{'ESA FRP':>10}")
    for day in sorted(set(e_by_day) | set(f_by_day)):
        print(f"{day:<12}{f_count.get(day,0):>10}{f_by_day.get(day,0):>11.0f}{e_by_day.get(day,0):>10.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
