#!/usr/bin/env python3
"""
refire.py - recalculeaza lista de focare din detectiile zonei, in loc.

Necesar o singura data, dupa migrate_zones.py: focarele mostenite din zonele
vechi fusesera calculate separat, fiecare pe cate un bbox, deci focarele de la
granita apareau de doua ori cu centre diferite. Aici le recalculam pe
reuniunea completa de detectii, cu un singur set de parcuri.

Nu atinge detectiile. Scrie doar campul "fires".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fires as F  # noqa: E402


def parse(k: str) -> dict:
    p = k.split("|")
    return {"src": p[0], "sensor": p[1], "date": p[2], "time": p[3],
            "lat": float(p[4]), "lon": float(p[5])}


def main() -> int:
    state_file = HERE / "state" / "state.json"
    zcfg = json.loads((HERE / "zones.json").read_text(encoding="utf-8"))
    zone = zcfg["zones"][0]
    zid = zone["id"]

    state = json.loads(state_file.read_text(encoding="utf-8"))
    zs = state["zones"][zid]

    # parcurile, cu geometria incarcata
    parks = []
    for p in zone.get("parks") or []:
        gp = HERE / "web" / (p.get("geojson") or "")
        if gp.exists():
            parks.append({"name": p.get("name"), "_geom": json.loads(gp.read_text(encoding="utf-8"))})
    print(f"parcuri incarcate: {[p['name'] for p in parks]}")

    dets = {k: parse(k) for k in (zs.get("detections") or {})}
    print(f"detectii: {len(dets)}")

    before = len(zs.get("fires") or [])
    new_fires = F.find_fires(dets, zone, parks=parks)
    zs["fires"] = new_fires
    print(f"focare: {before} (mostenite) -> {len(new_fires)} (recalculate)")
    for f in new_fires:
        print(f"  #{f['rank']}  {f['lat']:.4f},{f['lon']:.4f}  {f['points']:>4}p  {f['days']}z  "
              f"parcuri: {f['parks']}  {f['nearest_place']} {f['nearest_km']}km")

    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(state_file)
    print(f"\nscris {state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
