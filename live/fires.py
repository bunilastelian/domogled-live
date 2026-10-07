#!/usr/bin/env python
"""
fires.py - grupare automata a detectiilor in focare distincte, cu tendinta.

Un focar produce zeci de puncte de detectie la treceri succesive ale satelitului.
Fara grupare, dashboard-ul arata o pată; cu grupare, arata "2 focare active, cel
din Domogled in crestere".

Metoda: single-linkage clustering cu prag de distanta (union-find), apoi pentru
fiecare grup: centru, intindere, perioada, intensitate si tendinta pe 24 h.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def dist_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _when(d: dict) -> datetime | None:
    t = (d.get("time") or "0000").zfill(4)
    try:
        return datetime.strptime(f"{d['date']} {t[:4]}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def point_in_poly(lon: float, lat: float, geom: dict) -> bool:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom.get("coordinates", [])
    for poly in polys:
        if not poly:
            continue
        ring = poly[0]
        inside = False
        for i in range(len(ring)):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[(i + 1) % len(ring)][0], ring[(i + 1) % len(ring)][1]
            if ((y1 > lat) != (y2 > lat)) and (lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1):
                inside = not inside
        if inside:
            return True
    return False


def cluster(dets: list[dict], radius_km: float = 2.0) -> list[list[dict]]:
    n = len(dets)
    if n == 0:
        return []
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    cell = radius_km / 111.0
    grid: dict[tuple[int, int], list[int]] = {}
    for i, p in enumerate(dets):
        grid.setdefault((int(p["lat"] / cell), int(p["lon"] / cell)), []).append(i)

    for i, p in enumerate(dets):
        cy, cx = int(p["lat"] / cell), int(p["lon"] / cell)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for j in grid.get((cy + dy, cx + dx), ()):
                    if j <= i:
                        continue
                    q = dets[j]
                    if dist_km((p["lat"], p["lon"]), (q["lat"], q["lon"])) <= radius_km:
                        union(i, j)

    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(dets[i])
    return sorted(groups.values(), key=len, reverse=True)


def describe(group: list[dict], zone: dict, park_geom: dict | None = None,
             now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    lats = [d["lat"] for d in group]
    lons = [d["lon"] for d in group]
    clat, clon = sum(lats) / len(lats), sum(lons) / len(lons)
    frps = [d.get("frp") or 0 for d in group]

    times = [t for t in (_when(d) for d in group) if t]
    first, last = (min(times), max(times)) if times else (None, None)
    days = sorted({d["date"] for d in group})

    def sum_between(lo_h: float, hi_h: float) -> tuple[float, int]:
        s, c = 0.0, 0
        for d in group:
            t = _when(d)
            if not t:
                continue
            age = (now - t).total_seconds() / 3600
            if lo_h <= age < hi_h:
                s += d.get("frp") or 0
                c += 1
        return s, c

    f24, n24 = sum_between(0, 24)
    f48, n48 = sum_between(24, 48)

    if f48 <= 0 and f24 > 0:
        trend, tcol = "în creștere", "#e5484d"
    elif f24 <= 0 and f48 > 0:
        trend, tcol = "în scădere", "#30a46c"
    elif f48 > 0 and f24 > f48 * 1.25:
        trend, tcol = "în creștere", "#e5484d"
    elif f48 > 0 and f24 < f48 * 0.75:
        trend, tcol = "în scădere", "#30a46c"
    else:
        trend, tcol = "staționar", "#f5d90a"

    extent = 0.0
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            extent = max(extent, dist_km((group[i]["lat"], group[i]["lon"]),
                                         (group[j]["lat"], group[j]["lon"])))

    nearest, nearest_km = None, None
    for name, (plat, plon) in (zone.get("places") or {}).items():
        d = dist_km((clat, clon), (plat, plon))
        if nearest_km is None or d < nearest_km:
            nearest, nearest_km = name, d

    in_park = None
    if park_geom:
        in_park = any(point_in_poly(d["lon"], d["lat"], park_geom) for d in group)

    return {
        "id": f"{zone['id']}-{round(clat, 3)}-{round(clon, 3)}",
        "zone": zone["id"],
        "lat": round(clat, 5),
        "lon": round(clon, 5),
        "points": len(group),
        "days": len(days),
        "first": first.isoformat(timespec="minutes") if first else None,
        "last": last.isoformat(timespec="minutes") if last else None,
        "frp_max": round(max(frps), 2),
        "frp_sum": round(sum(frps), 1),
        "frp_last24": round(f24, 1),
        "frp_prev24": round(f48, 1),
        "det_last24": n24,
        "det_prev24": n48,
        "trend": trend,
        "trend_color": tcol,
        "extent_km": round(extent, 2),
        "nasa": sum(1 for d in group if d.get("src") == "NASA"),
        "esa": sum(1 for d in group if d.get("src") == "ESA"),
        "in_park": in_park,
        "nearest_place": nearest,
        "nearest_km": round(nearest_km, 2) if nearest_km is not None else None,
        "elev_span_m": None,
    }


def find_fires(detections: dict | list, zone: dict, park_geom: dict | None = None,
               radius_km: float = 2.0, min_points: int = 3) -> list[dict]:
    """Detectiile zonei -> lista de focare, cele mai intense primele.

    Filtram intai pe bbox-ul zonei: altfel fiecare zona ar raporta focarele
    tuturor celorlalte.
    """
    dets = list(detections.values()) if isinstance(detections, dict) else list(detections)
    w, s, e, n = zone["bbox"]
    dets = [d for d in dets if w <= d["lon"] <= e and s <= d["lat"] <= n]
    groups = cluster(dets, radius_km)
    fires = [describe(g, zone, park_geom) for g in groups if len(g) >= min_points]
    fires.sort(key=lambda f: (f["frp_last24"], f["frp_sum"]), reverse=True)
    for i, f in enumerate(fires, 1):
        f["rank"] = i
    return fires


def main() -> int:
    """Test pe datele reale salvate de colector."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent
    sf = root / "state" / "state.json"
    if not sf.exists():
        print("lipseste state/state.json — ruleaza intai collector.py")
        return 1
    st = json.loads(sf.read_text(encoding="utf-8"))
    zj = json.loads((root / "zones.json").read_text(encoding="utf-8"))
    dets = st.get("detections") or {}
    for z in zj["zones"]:
        pg = None
        gp = root / "web" / (z.get("park") or {}).get("geojson", "")
        if gp.exists():
            pg = json.loads(gp.read_text(encoding="utf-8"))
        fires = find_fires(dets, z, pg)
        print(f"\n=== {z['name']}: {len(fires)} focare ===")
        for f in fires:
            print(f"  #{f['rank']}  {f['lat']:.4f},{f['lon']:.4f}  {f['points']:>3} puncte "
                  f"{f['days']} zile  FRP max {f['frp_max']:>6.1f} MW  "
                  f"24h {f['frp_last24']:>6.1f} fata de {f['frp_prev24']:>6.1f}  {f['trend']:<13} "
                  f"{f['extent_km']:>5.2f} km  "
                  f"{'IN PARC' if f['in_park'] else ('in afara' if f['in_park'] is False else '?')}  "
                  f"{f['nearest_place']} {f['nearest_km']} km")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
