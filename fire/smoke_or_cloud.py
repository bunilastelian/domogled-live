#!/usr/bin/env python
"""
smoke_or_cloud.py - test decisiv: masa alba din scena de 4 octombrie este fum sau nor?

Metoda: banda SCL (Scene Classification Layer) din produsul Sentinel-2 L2A, generata de
Sen2Cor. Sen2Cor clasifica norii (clasele 8, 9, 10) si umbrele de nor (3), dar NU are
o clasa pentru fum - fumul rămâne clasificat ca vegetatie/sol. Deci:
   * daca pixelii albi sunt clasa 8/9/10 -> sunt NORI
   * daca sunt clasa 4/5 -> este FUM (sau alt aerosoli) peste vegetatie

Clasele SCL: 0 fara date, 1 saturat, 2 intunecat, 3 umbra nor, 4 vegetatie,
5 sol gol, 6 apa, 7 neclasificat, 8 nor probabilitate medie, 9 nor probabilitate mare,
10 cirrus subtire, 11 zapada.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from cdse import CDSE, CDSEError  # noqa: E402
from s2_fire import find_scene, get_asset_bytes  # noqa: E402
from cdse import normalize_stac  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache" / "s2"
OUT = HERE / "out"
FIRE = (44.8982, 22.4785)
LAT, LON = FIRE
BOX_KM = 6.0

SCL_NAMES = {0: "fără date", 1: "saturat", 2: "întunecat", 3: "umbra norului",
             4: "vegetație", 5: "sol gol", 6: "apă", 7: "neclasificat",
             8: "nor (prob. medie)", 9: "nor (prob. mare)", 10: "cirrus subțire",
             11: "zăpadă"}


def main() -> int:
    client = CDSE()
    dates = ["2026-10-04", "2026-09-27", "2026-08-02"]
    results = {}

    for date in dates:
        try:
            feat = find_scene(client, (LON, LAT), date)
            rec = normalize_stac(feat)
            path = get_asset_bytes(client, rec, "SCL_20m")
        except CDSEError as exc:
            print(f"[!] {date}: {exc}")
            continue

        asset = feat["assets"]["SCL_20m"]
        sx, _, ox, _, sy, oy = asset["proj:transform"][:6]
        epsg = int(str(asset["proj:code"]).split(":")[1])
        conv = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        x, y = conv.transform(LON, LAT)
        half_px = int(BOX_KM * 1000 / 2 / abs(sx))
        cx = int(round((x - ox) / sx))
        cy = int(round((y - oy) / sy))
        win = (cx - half_px, cy - half_px, cx + half_px, cy + half_px)

        with Image.open(path) as im:
            arr = np.array(im.crop(win))
        total = arr.size
        counts = Counter(arr.ravel().tolist())

        print(f"\n=== {date}  scena {feat['id'][:46]} ===")
        print(f"    nori in metadata tile: {feat['properties'].get('eo:cloud_cover')}%   "
              f"fereastra {BOX_KM:.0f}x{BOX_KM:.0f} km ({total} pixeli)")
        cloudy = sum(counts.get(c, 0) for c in (3, 8, 9, 10))
        print(f"    {'clasa SCL':<24}{'pixeli':>10}{'%':>8}")
        for cls, n in sorted(counts.items()):
            if n / total > 0.005:
                print(f"    {SCL_NAMES.get(cls, str(cls)):<24}{n:>10}{100*n/total:>7.1f}%")
        print(f"    -> nor/umbra/cirrus: {100*cloudy/total:.1f}%   "
              f"vegetatie+sol: {100*(counts.get(4,0)+counts.get(5,0))/total:.1f}%")
        results[date] = {"cloud_pct": 100 * cloudy / total,
                         "veg_pct": 100 * (counts.get(4, 0) + counts.get(5, 0)) / total}

    print("\n=== CONCLUZIE ===")
    for date, r in results.items():
        what = "NOR" if r["cloud_pct"] > 50 else ("fum/aerosol peste teren" if r["cloud_pct"] < 20 else "mixt")
        print(f"   {date}: {r['cloud_pct']:5.1f}% nor/umbra/cirrus  ->  {what}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
