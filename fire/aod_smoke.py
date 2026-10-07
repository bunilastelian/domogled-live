#!/usr/bin/env python
"""
aod_smoke.py - confirmare cantitativa ca masa alba din 4 octombrie este FUM.

Produsul Sentinel-3 SLSTR L2 AOD contine, pe langa AOD_550, urmatoarele variabile
relevante pentru fum:
   Smoke_Index  - indicele de fum al produsului
   AFRI         - indice de aerosol absorbant (fum/ceață vulcanica)
   SSA_550      - albedo de difuzie simpla (fumul are SSA scazut, <0.95)
Comparatie: zona incendiului vs o zona de referinta fara incendiu, in aceeasi scena.
"""

from __future__ import annotations

import sys
from pathlib import Path

import netCDF4
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

TMP = Path(r"D:\copernicus\data\aod_tmp")
FIRE = (44.8982, 22.4785)
VAR = ["AOD_550", "AOD_550_Land", "Smoke_Index", "AFRI", "SSA_550", "cloud_fraction_nadir", "ANG550_865"]


def main() -> int:
    sub = [d for d in TMP.iterdir() if d.is_dir()]
    nc = next(d for d in sub if (d / "NRT_AOD.nc").exists()) / "NRT_AOD.nc"
    ds = netCDF4.Dataset(nc)
    lat = np.array(ds.variables["latitude"][:])
    lon = np.array(ds.variables["longitude"][:])
    print(f"Produs: {getattr(ds, 'product_name', nc.name)}")
    print(f"Grid: {lat.shape}, lat {np.nanmin(lat):.2f}..{np.nanmax(lat):.2f}, "
          f"lon {np.nanmin(lon):.2f}..{np.nanmax(lon):.2f}\n")

    def mask_around(plat, plon, rad_km):
        dlat = (lat - plat) * 111.0
        dlon = (lon - plon) * 111.0 * np.cos(np.radians(plat))
        return np.hypot(dlat, dlon) <= rad_km

    zones = {
        "zona incendiului (r=8 km)": mask_around(*FIRE, 8),
        "referinta: 40-80 km vest (fara incendiu)": mask_around(FIRE[0], FIRE[1] - 0.75, 20),
        "referinta: 40-80 km est (fara incendiu)": mask_around(FIRE[0] + 0.15, FIRE[1] + 0.85, 20),
    }

    print(f"{'variabila':<22}" + "".join(f"{k[:34]:>36}" for k in zones))
    print("-" * (22 + 36 * len(zones)))
    for v in VAR:
        if v not in ds.variables:
            continue
        arr = np.array(ds.variables[v][:], dtype="float64")
        arr = np.ma.masked_invalid(arr)
        fill = getattr(ds.variables[v], "_FillValue", None)
        if fill is not None:
            arr = np.ma.masked_equal(arr, fill)
        arr = np.ma.masked_less(arr, -100)
        row = f"{v:<22}"
        for zname, m in zones.items():
            sel = arr[m]
            if sel.count() == 0:
                row += f"{'fara date':>36}"
            else:
                row += f"{sel.mean():>18.3f} (n={sel.count():<6})"[:36].rjust(36)
        print(row)

    print("\n=== Valori in zona incendiului, pixel cu pixel (AOD_550, Smoke_Index, AFRI) ===")
    m = mask_around(*FIRE, 6)
    rows, cols = np.where(m)
    aod = np.array(ds.variables["AOD_550"][:], dtype="float64")
    si = np.array(ds.variables["Smoke_Index"][:], dtype="float64") if "Smoke_Index" in ds.variables else None
    af = np.array(ds.variables["AFRI"][:], dtype="float64") if "AFRI" in ds.variables else None
    print(f"{'lat':>9}{'lon':>9}{'AOD550':>9}{'SmokeIdx':>10}{'AFRI':>8}")
    for r, c in list(zip(rows, cols))[:25]:
        a, s = aod[r, c], (si[r, c] if si is not None else float("nan"))
        f = af[r, c] if af is not None else float("nan")
        def fmt(x):
            return "   -  " if (x is None or not np.isfinite(x) or x < -100) else f"{x:7.3f}"
        print(f"{lat[r,c]:>9.3f}{lon[r,c]:>9.3f}{fmt(a):>9}{fmt(s):>10}{fmt(f):>8}")

    if si is not None:
        z = np.ma.masked_invalid(si)[mask_around(*FIRE, 8)]
        ref = np.ma.masked_invalid(si)[mask_around(FIRE[0], FIRE[1] - 0.75, 20)]
        print(f"\nSmoke_Index mediu: zona incendiu {z.mean():.3f}  vs referinta {ref.mean():.3f}"
              f"  -> raport {z.mean()/ref.mean() if ref.mean() else float('nan'):.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
