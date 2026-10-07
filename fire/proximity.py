#!/usr/bin/env python
"""
proximity.py - separa incendiul forestier de alte surse termice din zona.

Intrebarea: detectiile de langa Baile Herculane sunt focul forestier care a ajuns
la marginea orasului, sau arderi mici de gradina/miristi?

Criterii de separare:
  * FRP (puterea radiativa) - focul de padure are FRP de ordinul MW, arderile mici sub 2 MW
  * persistenta - acelasi loc arde in multe zile consecutive
  * altitudinea aproximativa - focul de padure e pe versant, nu in luncă
"""

from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

TOWNS = {
    "Băile Herculane": (44.8785, 22.4144),
    "Pecinișca": (44.8712, 22.4187),
    "Mehadia": (44.9036, 22.3667),
    "Orșova": (44.7235, 22.3983),
}
FIRE_CORE = (44.8982, 22.4785)


def dist_km(a, b) -> float:
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def main() -> int:
    with open(OUT / "detections.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
        r["frp_mw"] = float(r["frp_mw"]) if r["frp_mw"] else 0.0
        r["d_core"] = dist_km(FIRE_CORE, (r["lat"], r["lon"]))

    # zona incendiului forestier: in raza de 6 km de centrul focarului
    core = [r for r in rows if r["d_core"] <= 6]
    print(f"=== Zona incendiului forestier (<= 6 km de {FIRE_CORE}) ===")
    lats = [r["lat"] for r in core]
    lons = [r["lon"] for r in core]
    clat = sum(lats) / len(lats)
    print(f"   detectii: {len(core)} din {len(rows)}")
    print(f"   bbox: {min(lats):.4f}..{max(lats):.4f} N  x  {min(lons):.4f}..{max(lons):.4f} E")
    print(f"   intindere: {(max(lats)-min(lats))*111:.1f} km (N-S) x "
          f"{(max(lons)-min(lons))*111*math.cos(math.radians(clat)):.1f} km (E-V)")
    frps = [r["frp_mw"] for r in core]
    print(f"   FRP: min {min(frps):.2f} MW, mediu {sum(frps)/len(frps):.2f} MW, max {max(frps):.1f} MW")
    print(f"   zile cu detectii: {len({r['date'] for r in core})} din 8")
    print(f"   detectii cu FRP >= 5 MW: {sum(1 for f in frps if f >= 5)}")

    print("\n=== Detectii in raza de 3 km de localitati: foc forestier sau altceva? ===")
    for town, coords in TOWNS.items():
        near = [r for r in rows if dist_km(coords, (r["lat"], r["lon"])) <= 3]
        if not near:
            continue
        near.sort(key=lambda r: dist_km(coords, (r["lat"], r["lon"])))
        frps = [r["frp_mw"] for r in near]
        print(f"\n   {town}: {len(near)} detectii in 3 km")
        print(f"      FRP: min {min(frps):.2f}, mediu {sum(frps)/len(frps):.2f}, max {max(frps):.2f} MW")
        print(f"      zile: {sorted({r['date'] for r in near})}")
        print(f"      cea mai apropiata: {near[0]['lat']:.5f},{near[0]['lon']:.5f} "
              f"({dist_km(coords, (near[0]['lat'], near[0]['lon'])):.2f} km) "
              f"{near[0]['date']} {near[0]['time_utc']} UTC FRP {near[0]['frp_mw']:.2f} MW "
              f"[{near[0]['sensor']}]")

    # cate detectii "mici" (sub 2 MW) exista in toata regiunea
    small = [r for r in rows if r["frp_mw"] < 2]
    print(f"\n=== Detectii slabe (FRP < 2 MW) in toata regiunea: {len(small)} din {len(rows)} "
          f"({100*len(small)/len(rows):.0f}%) ===")
    print("   (in zonele agricole de la sud, acestea sunt tipic arderi de miristi/vegetatie)")

    # separare pe "in parc / in afara parcului" folosind distanta la focar
    print("\n=== Distributia FRP in functie de distanta la focarul principal ===")
    bands = [(0, 2), (2, 6), (6, 12), (12, 30), (30, 200)]
    print(f"{'distanta':<16}{'detectii':>10}{'FRP mediu':>12}{'FRP max':>10}{'zile':>7}")
    for lo, hi in bands:
        sel = [r for r in rows if lo <= r["d_core"] < hi]
        if not sel:
            continue
        f = [r["frp_mw"] for r in sel]
        print(f"{f'{lo}-{hi} km':<16}{len(sel):>10}{sum(f)/len(f):>12.2f}{max(f):>10.1f}"
              f"{len({r['date'] for r in sel}):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
