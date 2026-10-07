#!/usr/bin/env python
"""
esa_frp.py - detectii de foc din produsul ESA Sentinel-3 SLSTR Level 2 FRP.

Spre deosebire de NASA FIRMS (care da doar puncte), produsul ESA da, pentru fiecare
pixel de foc: lat/lon, ora exacta (UTC), FRP in MW, eroarea FRP, temperatura de
brilianță MWIR, unghiurile de observare, schema folosita (MWIR 1 km / SWIR 500 m)
si clasa de incredere.

Rulare:
  python esa_frp.py --days 14
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from cdse import CDSE, CDSEError  # noqa: E402

HERE = Path(__file__).resolve().parent
WORK = HERE / "cache" / "frp"
OUT = HERE / "out"
WORK.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

# zona de interes: Domogled - Valea Cernei - Baile Herculane
AOI = (22.20, 44.70, 22.80, 45.15)   # W, S, E, N
# focarul principal identificat prin clustering FIRMS
FIRE = (44.8982, 22.4785)


def dist_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


SCHEMES = {
    "MWIR1km-standard": "FRP_MWIR1km_standard.csv",
    "MWIR1km-alternative": "FRP_MWIR1km_alternative.csv",
    "SWIR500m": "FRP_SWIR500m.csv",
}


def parse_sen3(zip_path: Path) -> list[dict]:
    """Extrage randurile din CSV-urile de FRP dintr-un produs .SEN3 (zip)."""
    rows: list[dict] = []
    with zipfile.ZipFile(zip_path) as z:
        for scheme, member in SCHEMES.items():
            target = next((n for n in z.namelist() if n.endswith(member)), None)
            if not target:
                continue
            text = z.read(target).decode("utf-8", "replace")
            lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
            if not lines:
                continue
            for d in csv.DictReader(io.StringIO("\n".join(lines))):
                try:
                    lat, lon = float(d["lat(deg)"]), float(d["lon(deg)"])
                except (KeyError, ValueError):
                    continue
                if not (AOI[0] <= lon <= AOI[2] and AOI[1] <= lat <= AOI[3]):
                    continue
                def num(key):
                    try:
                        return float(d.get(key) or "")
                    except ValueError:
                        return None
                rows.append({
                    "scheme": scheme,
                    "lat": lat,
                    "lon": lon,
                    "date": d.get("day"),
                    "time_utc": d.get("time"),
                    "daynight": d.get("D/N"),
                    "frp_mw": num("FRP(MW)"),
                    "frp_err_mw": num("FRPerr(MW)"),
                    "confidence_pct": num("confidence(%)") or num("SWIR_SAA_confidence(%)"),
                    "confidence_class": d.get("confidence_class(lower=0;nominal=1;higher=2)"),
                    "bt_k": num("MWIR_BT(K)"),
                    "ifov_m2": num("IFOV_area(m2)"),
                    "sza": num("SZA(deg)"),
                    "vza": num("VZA(deg)"),
                    "satellite": d.get("satellite"),
                    "product": zip_path.stem.split("_")[0] + "_" + zip_path.stem.split("_")[1],
                    "dist_to_main_fire_km": round(dist_km(FIRE[0], FIRE[1], lat, lon), 2),
                })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="cate zile in urma")
    ap.add_argument("--force", action="store_true", help="re-descarca produsele deja salvate")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    client = CDSE()

    print(f"=== Caut produse Sentinel-3 SLSTR FRP peste zona, {start} .. {end} ===")
    prods = client.odata_products(
        "SENTINEL-3", top=200, point=((AOI[0] + AOI[2]) / 2, (AOI[1] + AOI[3]) / 2),
        start=start.isoformat(), end=end.isoformat(), match="SL_2_FRP",
    )
    # exista doua variante ale aceluiasi produs: NRT (~2 MB) si NT reprocesat (~75 MB),
    # cu aceleasi date de foc. Pastram NRT, ca sa nu descarcam de 35 de ori mai mult.
    nrt = [p for p in prods if "_O_NR" in (p.get("Name") or "")]
    skipped = len(prods) - len(nrt)
    prods = nrt or prods
    print(f"    {len(prods)} produse FRP acopera zona" + (f" (ignorate {skipped} variante NT de ~75 MB)" if skipped else ""))

    all_rows: list[dict] = []
    for i, p in enumerate(sorted(prods, key=lambda x: x.get("Name", "")), 1):
        name = p["Name"]
        local = WORK / f"{name}.zip"
        try:
            if not local.exists() or args.force:
                client.download_product(p["Id"], dest=WORK)
            # download_product scrie <nume>.zip (numele vine fara .zip in OData)
            candidates = list(WORK.glob(f"{name}*.zip"))
            if not candidates:
                print(f"    [!] {name}: fisier lipsa dupa descarcare")
                continue
            rows = parse_sen3(candidates[0])
            cd = p.get("ContentDate") or {}
            got = len(rows)
            print(f"    [{i:>2}/{len(prods)}] {(cd.get('Start') or '')[:19]}  {name[:52]}  -> {got} detectii in AOI")
            all_rows.extend(rows)
        except CDSEError as exc:
            print(f"    [!] {name}: {exc}")

    print(f"\n=== TOTAL {len(all_rows)} detectii FRP (ESA) in zona de interes ===")
    if not all_rows:
        print("    Nicio detectie. Zona de interes e restransa (22.20-22.80 E, 44.70-45.15 N).")
        return 0

    all_rows.sort(key=lambda r: (r["date"] or "", r["time_utc"] or ""))
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in all_rows:
        by_day[r["date"]].append(r)

    print(f"\n{'data':<12}{'det':>5}{'FRP sum':>10}{'FRP max':>9}{'BT max K':>10}{'conf med':>10}  orare (UTC)")
    for day in sorted(by_day):
        pts = by_day[day]
        frps = [p["frp_mw"] for p in pts if p["frp_mw"] is not None]
        bts = [p["bt_k"] for p in pts if p["bt_k"] is not None]
        confs = [p["confidence_pct"] for p in pts if p["confidence_pct"] is not None]
        times = sorted({p["time_utc"] for p in pts})
        print(f"{day:<12}{len(pts):>5}{sum(frps):>10.1f}{max(frps) if frps else 0:>9.1f}"
              f"{max(bts) if bts else 0:>10.1f}{(sum(confs)/len(confs)) if confs else 0:>10.1f}  {','.join(times)}")

    print(f"\n=== Detectii in raza de 5 km de focarul principal ({FIRE[0]}, {FIRE[1]}) ===")
    near = [r for r in all_rows if r["dist_to_main_fire_km"] <= 5]
    print(f"    {len(near)} detectii din {len(all_rows)} ({100*len(near)/len(all_rows):.0f}%)")
    print(f"\n{'data':<12}{'ora UTC':<10}{'lat':>10}{'lon':>10}{'FRP MW':>8}{'err':>7}{'BT K':>8}{'conf%':>7}  {'km':>5} schema")
    for r in near:
        print(f"{r['date']:<12}{r['time_utc']:<10}{r['lat']:>10.4f}{r['lon']:>10.4f}"
              f"{r['frp_mw'] or 0:>8.2f}{r['frp_err_mw'] or 0:>7.2f}{r['bt_k'] or 0:>8.1f}"
              f"{r['confidence_pct'] or 0:>7.1f}  {r['dist_to_main_fire_km']:>5.2f} {r['scheme']}")

    with open(OUT / "esa_frp_detections.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nScris: {OUT / 'esa_frp_detections.csv'}")
    print(f"Scheme folosite: {dict(Counter(r['scheme'] for r in all_rows))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
