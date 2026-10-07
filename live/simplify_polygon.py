#!/usr/bin/env python
"""
simplify_polygon.py - reduce contururile de parc pentru harta.

Contururile brute din OpenStreetMap au mii de puncte: 6436 pentru Domogled si
13968 pentru Portile de Fier, adica zeci de KB comprimat incarcati la fiecare
vizita, pentru o linie care se vede la zoom 12.

ATENTIE la structura GeoJSON, aici am gresit prima data:
    Polygon       coordinates = [ inel, inel, ... ]     inel = [ punct, ... ]
    MultiPolygon  coordinates = [ poligon, ... ]        poligon = [ inel, ... ]
Un nivel in plus produce un Polygon care pare valid dar nu se deseneaza.
validate() verifica structura dupa simplificare.

Rulare:
    python simplify_polygon.py --all
    python simplify_polygon.py --zone portiledefier --tol 0.0012
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent / "fire" / "cache"
WEB = HERE / "web"

SOURCES = {
    "domogled": ("osm_park_domogled.json", "domogled.geojson"),
    "portiledefier": ("osm_park_portiledefier.json", "portiledefier.geojson"),
}


def perp_dist(p, a, b) -> float:
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return ((x - (x1 + t * dx)) ** 2 + (y - (y1 + t * dy)) ** 2) ** 0.5


def rdp(points: list, tol: float) -> list:
    """Douglas-Peucker iterativ, ca sa nu rupem stiva pe inele mari."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        dmax, idx = 0.0, -1
        for k in range(i + 1, j):
            d = perp_dist(points[k], points[i], points[j])
            if d > dmax:
                dmax, idx = d, k
        if dmax > tol and idx > 0:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


def simplify_ring(ring: list, tol: float) -> list:
    out = rdp(ring, tol)
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])              # inelul trebuie sa fie inchis
    return out


def simplify_geom(geom: dict, tol: float) -> dict:
    if geom["type"] == "Polygon":
        return {"type": "Polygon",
                "coordinates": [simplify_ring(r, tol) for r in geom["coordinates"]]}
    if geom["type"] == "MultiPolygon":
        return {"type": "MultiPolygon",
                "coordinates": [[simplify_ring(r, tol) for r in poly] for poly in geom["coordinates"]]}
    raise ValueError(f"tip de geometrie neasteptat: {geom['type']}")


def count_points(geom: dict) -> int:
    if geom["type"] == "Polygon":
        return sum(len(r) for r in geom["coordinates"])
    return sum(len(r) for poly in geom["coordinates"] for r in poly)


def validate(geom: dict) -> list[str]:
    """Structura chiar corespunde tipului declarat? Aici a scapat bug-ul prima data."""
    errs = []
    c = geom.get("coordinates")
    if geom.get("type") == "Polygon":
        if not isinstance(c, list) or not c:
            return ["coordinates gol"]
        for i, ring in enumerate(c):
            if not isinstance(ring, list) or not ring:
                errs.append(f"inelul {i} e gol sau nu e lista")
                continue
            p0 = ring[0]
            if not isinstance(p0, list) or len(p0) != 2 or not isinstance(p0[0], (int, float)):
                errs.append(f"inelul {i} nu contine perechi [lon, lat] — probabil un nivel in plus")
                continue
            if len(ring) < 4:
                errs.append(f"inelul {i} are doar {len(ring)} puncte (minim 4)")
            elif ring[0] != ring[-1]:
                errs.append(f"inelul {i} nu e inchis")
    return errs


def run(zone: str, tol: float) -> bool:
    src_name, dst_name = SOURCES[zone]
    src = CACHE / src_name
    if not src.exists():
        print(f"[!] {zone}: lipseste {src}")
        return False
    raw = json.loads(src.read_text(encoding="utf-8"))
    geom = raw[0]["geojson"] if isinstance(raw, list) else raw

    before = count_points(geom)
    out = simplify_geom(geom, tol)
    after = count_points(out)
    errs = validate(out)

    dst = WEB / dst_name
    dst.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    gz = len(gzip.compress(dst.read_bytes(), 9))

    print(f"  {zone}: {before} -> {after} puncte ({100*after/before:.0f}%), "
          f"{dst.stat().st_size/1024:.1f} KB brut / {gz/1024:.1f} KB gzip  "
          f"[{'OK' if not errs else 'STRUCTURA GRESITA'}]")
    for e in errs:
        print(f"     [!] {e}")
    return not errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", choices=sorted(SOURCES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tol", type=float, default=0.0008, help="toleranta in grade (~89 m)")
    a = ap.parse_args()

    zones = sorted(SOURCES) if (a.all or not a.zone) else [a.zone]
    ok = all(run(z, a.tol) for z in zones)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
