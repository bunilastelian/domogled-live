#!/usr/bin/env python
"""
fire_analysis.py - analiza focarelor din zona Domogled - Valea Cernei / Baile Herculane.

Ce face:
  1. ia detectiile termice NASA FIRMS (VIIRS 375 m SNPP + NOAA-20, MODIS 1 km) pentru regiune
  2. ia conturul Parcului National Domogled - Valea Cernei din OpenStreetMap (Nominatim)
  3. grupeaza detectiile in focare distincte (single-linkage, raza configurabila)
  4. spune care focare sunt IN parc si care in afara
  5. scrie out/clusters.csv si out/detections.csv

Rulare:
  python fire_analysis.py                # fereastra implicita: 7 zile (cat da FIRMS public)
  python fire_analysis.py --radius 3     # grupare mai larga
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
CACHE = HERE / "cache"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

FIRMS = {
    "VIIRS-SNPP-375m": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Europe_7d.csv",
    "VIIRS-NOAA20-375m": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_7d.csv",
    "MODIS-1km": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Europe_7d.csv",
}

# regiunea larga de lucru: Domogled - Cerna - Mehedinti - Caras
REGION = (21.7, 44.3, 23.3, 45.6)  # W, S, E, N

UA = {"User-Agent": "copernicus-fire-analysis/1.0 (research, contact: local)"}


# ----------------------------------------------------------------- utilitare
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch(url: str, cache_name: str, timeout: int = 90) -> str:
    path = CACHE / cache_name
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read().decode("utf-8", "replace")
    path.write_text(data, encoding="utf-8")
    return data


def nominatim(query: str, cache_name: str, polygon: bool = False):
    params = {"q": query, "format": "json", "limit": 1}
    if polygon:
        params["polygon_geojson"] = 1
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    txt = fetch(url, cache_name)
    data = json.loads(txt)
    return data[0] if data else None


def point_in_poly(lon: float, lat: float, geom: dict) -> bool:
    """Test punct-in-poligon pentru Polygon / MultiPolygon GeoJSON (ray casting)."""
    polys = []
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return False

    for poly in polys:
        if not poly:
            continue
        ring = poly[0]
        inside = False
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
            if ((y1 > lat) != (y2 > lat)) and (lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1):
                inside = not inside
        if inside:
            return True
    return False


# ------------------------------------------------------------ incarcare date
def load_firms() -> list[dict]:
    rows = []
    W, S, E, N = REGION
    for sensor, url in FIRMS.items():
        txt = fetch(url, f"firms_{sensor}.csv")
        for d in csv.DictReader(io.StringIO(txt)):
            try:
                lon, lat = float(d["longitude"]), float(d["latitude"])
            except (KeyError, ValueError):
                continue
            if not (W <= lon <= E and S <= lat <= N):
                continue
            frp = d.get("frp") or ""
            try:
                frp = float(frp)
            except ValueError:
                frp = None
            conf = str(d.get("confidence") or "").strip()
            # MODIS da confidence in procente (0-100); VIIRS da l/n/h
            conf_pct = None
            if conf.isdigit():
                conf_pct = int(conf)
            elif conf.lower().startswith("h"):
                conf_pct = 90
            elif conf.lower().startswith("n"):
                conf_pct = 60
            elif conf.lower().startswith("l"):
                conf_pct = 30
            rows.append({
                "sensor": sensor,
                "lat": lat,
                "lon": lon,
                "date": d["acq_date"],
                "time_utc": d["acq_time"].zfill(4),
                "frp_mw": frp,
                "confidence": conf,
                "confidence_pct": conf_pct,
                "daynight": d.get("daynight", ""),
                "satellite": d.get("satellite", ""),
                "bright_ti4": d.get("bright_ti4") or d.get("brightness"),
                "bright_ti5": d.get("bright_ti5") or d.get("bright_t31"),
            })
    rows.sort(key=lambda r: (r["date"], r["time_utc"]))
    return rows


# --------------------------------------------------------------- grupare
def cluster(points: list[dict], radius_km: float) -> list[list[int]]:
    """Single-linkage clustering cu prag de distanta (union-find)."""
    n = len(points)
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

    # grila simpla pentru a evita O(n^2) complet
    cell = radius_km / 111.0
    grid: dict[tuple[int, int], list[int]] = {}
    for i, p in enumerate(points):
        grid.setdefault((int(p["lat"] / cell), int(p["lon"] / cell)), []).append(i)

    for i, p in enumerate(points):
        cy, cx = int(p["lat"] / cell), int(p["lon"] / cell)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for j in grid.get((cy + dy, cx + dx), []):
                    if j <= i:
                        continue
                    q = points[j]
                    if haversine_km(p["lat"], p["lon"], q["lat"], q["lon"]) <= radius_km:
                        union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)


def describe(rows: list[dict], idxs: list[int], park_geom, parks: list[tuple[str, dict]]) -> dict:
    pts = [rows[i] for i in idxs]
    lats = [p["lat"] for p in pts]
    lons = [p["lon"] for p in pts]
    frps = [p["frp_mw"] for p in pts if p["frp_mw"] is not None]
    clon = sum(lons) / len(lons)
    clat = sum(lats) / len(lats)
    days = sorted({p["date"] for p in pts})

    span_km = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = haversine_km(pts[i]["lat"], pts[i]["lon"], pts[j]["lat"], pts[j]["lon"])
            span_km = max(span_km, d)

    inside = []
    for name, geom in parks:
        if any(point_in_poly(p["lon"], p["lat"], geom) for p in pts):
            inside.append(name)

    return {
        "n_detections": len(pts),
        "lat": round(clat, 5),
        "lon": round(clon, 5),
        "lat_min": min(lats), "lat_max": max(lats),
        "lon_min": min(lons), "lon_max": max(lons),
        "lat_span_km": round((max(lats) - min(lats)) * 111, 2),
        "lon_span_km": round((max(lons) - min(lons)) * 111 * math.cos(math.radians(clat)), 2),
        "max_pair_km": round(span_km, 2),
        "first_date": days[0],
        "last_date": days[-1],
        "n_days": len(days),
        "days": ",".join(days),
        "frp_max_mw": round(max(frps), 1) if frps else None,
        "frp_sum_mw": round(sum(frps), 1) if frps else None,
        "frp_mean_mw": round(sum(frps) / len(frps), 2) if frps else None,
        "sensors": ",".join(sorted({p["sensor"] for p in pts})),
        "confidence_high_or_nom": sum(1 for p in pts if (p["confidence_pct"] or 0) >= 60),
        "day_detections": sum(1 for p in pts if p["daynight"] == "D"),
        "night_detections": sum(1 for p in pts if p["daynight"] == "N"),
        "in_protected": "|".join(inside) if inside else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=float, default=2.0, help="raza de grupare in km (implicit 2)")
    ap.add_argument("--min-detections", type=int, default=3, help="ignora grupurile mai mici")
    args = ap.parse_args()

    print("=== 1. Detectii termice NASA FIRMS (ultimele 7 zile) ===")
    rows = load_firms()
    print(f"    {len(rows)} detectii in regiunea {REGION}")
    print(f"    interval: {rows[0]['date']} .. {rows[-1]['date']}")
    print(f"    senzori: {dict(Counter(r['sensor'] for r in rows))}")
    print(f"    zi/noapte: {dict(Counter(r['daynight'] for r in rows))}")

    print("\n=== 2. Arii protejate (OpenStreetMap) ===")
    protected: list[tuple[str, dict]] = []
    for label, query, cache in [
        ("Parcul National Domogled-Valea Cernei", "Parcul National Domogled-Valea Cernei", "osm_park_domogled.json"),
        ("Parcul Natural Portile de Fier", "Parcul Natural Portile de Fier", "osm_park_portiledefier.json"),
    ]:
        try:
            res = nominatim(query, cache, polygon=True)
        except Exception as exc:  # noqa: BLE001
            print(f"    [!] {label}: {type(exc).__name__}: {exc}")
            continue
        if not res or "geojson" not in res:
            print(f"    [!] {label}: fara geometrie in OSM")
            continue
        geom = res["geojson"]
        protected.append((label, geom))
        n = len(geom["coordinates"][0]) if geom["type"] == "Polygon" else sum(len(p[0]) for p in geom["coordinates"])
        print(f"    {label}: {geom['type']}, {n} puncte in contur")

    print(f"\n=== 3. Grupare in focare (raza {args.radius} km) ===")
    groups = cluster(rows, args.radius)
    clusters = []
    for idxs in groups:
        if len(idxs) < args.min_detections:
            continue
        clusters.append(describe(rows, idxs, None, protected))
    clusters.sort(key=lambda c: (c["frp_sum_mw"] or 0), reverse=True)
    print(f"    {len(clusters)} focare cu >= {args.min_detections} detectii")

    hdr = f"{'#':>3} {'lat':>9} {'lon':>9} {'det':>4} {'zile':>5} {'FRPmax':>7} {'FRPsum':>8} {'intindere':>10}  arie protejata"
    print("\n" + hdr)
    print("-" * len(hdr))
    for i, c in enumerate(clusters, 1):
        print(f"{i:>3} {c['lat']:>9.4f} {c['lon']:>9.4f} {c['n_detections']:>4} {c['n_days']:>5} "
              f"{c['frp_max_mw'] or 0:>7.1f} {c['frp_sum_mw'] or 0:>8.1f} "
              f"{c['max_pair_km']:>7.1f} km  {c['in_protected'] or '-'}")

    # scrie rezultatele
    with open(OUT / "detections.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(OUT / "clusters.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(clusters[0].keys()))
        w.writeheader()
        w.writerows(clusters)

    print(f"\nScris: {OUT / 'detections.csv'}")
    print(f"Scris: {OUT / 'clusters.csv'}")

    print("\n=== 4. Total pe zile ===")
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    for day in sorted(by_day):
        pts = by_day[day]
        frps = [p["frp_mw"] for p in pts if p["frp_mw"] is not None]
        inpark = sum(1 for p in pts if any(point_in_poly(p["lon"], p["lat"], g) for _, g in protected))
        print(f"    {day}: {len(pts):>4} detectii  FRP sum {sum(frps):>7.1f} MW  FRP max {max(frps) if frps else 0:>6.1f} MW"
              f"   din care in arii protejate: {inpark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
