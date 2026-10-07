#!/usr/bin/env python
"""
simplify_polygon.py - reduce conturul parcului pentru harta.

Conturul brut din OpenStreetMap are 6436 de puncte si 43 KB comprimat — 70% din
payload-ul propriu al site-ului, incarcat la fiecare vizita, pentru o linie care se
vede la zoom 12. Aplicam Douglas-Peucker si pastram doar detaliul vizibil.

Rulare: python simplify_polygon.py [--tol 0.0008]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "fire" / "cache" / "osm_park_domogled.json"
DST = HERE / "web" / "domogled.geojson"


def perp_dist(p, a, b) -> float:
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    px, py = x1 + t * dx, y1 + t * dy
    return ((x - px) ** 2 + (y - py) ** 2) ** 0.5


def rdp(points: list, tol: float) -> list:
    """Douglas-Peucker iterativ (fara recursivitate, ca sa nu rupem stiva)."""
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
    """Pastreaza inelul inchis dupa simplificare."""
    out = rdp(ring, tol)
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.0008,
                    help="toleranta in grade (0.0008 ~ 89 m)")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"[!] lipseste {SRC}")
        return 1
    geom = json.loads(SRC.read_text(encoding="utf-8"))[0]["geojson"]

    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    before = sum(len(r) for p in polys for r in p)
    out_polys = [[simplify_ring(ring, args.tol) for ring in poly] for poly in polys]
    after = sum(len(r) for p in out_polys for r in p)

    new = {"type": geom["type"], "coordinates": out_polys}
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps(new, separators=(",", ":")), encoding="utf-8")

    raw = DST.stat().st_size
    import gzip
    gz = len(gzip.compress(DST.read_bytes(), 9))
    print(f"puncte: {before} -> {after}  ({100*after/before:.0f}%)")
    print(f"fisier: {raw/1024:.1f} KB brut, {gz/1024:.1f} KB gzip")
    print(f"scris: {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
