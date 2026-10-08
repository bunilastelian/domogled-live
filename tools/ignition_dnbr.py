#!/usr/bin/env python3
"""
Calculeaza dNBR pe serii temporale ca sa gasim CAND a inceput incendiul.

Ideea: luam o scena de REFERINTA (pre-sezon, ex. 21 iulie cu 0% nori) si
comparam NBR-ul ei cu NBR-ul fiecarei scene ulterioare. Acolo unde vegetatia
sanatoasa s-a transformat in suprafata arsă, NBR scade brusc.

NBR = (NIR - SWIR2) / (NIR + SWIR2)      [B08 si B12 in Sentinel-2]
dNBR = NBR_referinta - NBR_scena

Interpretare USGS:
  < 0.10  regenerare / fara schimbare
  0.10-0.27  ars usor
  0.27-0.66  moderat
  > 0.66  sever

ATENTIE la fenologie: vara vegetatia se usuca natural, ceea ce scade NBR si
fara foc. De aceea urmarim SALTUL brusc intre doua scene consecutive, nu
valoarea absoluta. O crestere graduala de la uscare e normala; un salt de
0.2+ intr-o saptamana inseamna foc.

Folosim Sentinel Hub Statistical API ca sa nu descarcam scene intregi (fiecare
ar fi ~800 MB). Primim doar statistici pe poligonul focarului. ~2s/scena.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

STAT = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

# evalscript: NBR + masca de nori/apa/zapada
EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: [{bands: ["B08","B12","SCL","dataMask"]}],
    output: [
      {id: "nbr", bands: 1, sampleType: "FLOAT32"},
      {id: "dataMask", bands: 1}
    ]
  };
}
function evaluatePixel(s) {
  let nbr = (s.B08 - s.B12) / (s.B08 + s.B12);
  // SCL: 3=umbra nor, 8=nor mediu, 9=nor dens, 10=cirrus, 11=zapada, 6=apa
  let bad = [0,1,3,6,8,9,10,11].includes(s.SCL);
  return {nbr: [nbr], dataMask: [s.dataMask && !bad ? 1 : 0]};
}
"""


def token() -> str:
    import cdse
    return cdse.CDSE().token()


def nbr_for_scene(tk: str, bbox: list, date: str) -> dict | None:
    """NBR median in bbox, pentru o singura zi."""
    d0 = f"{date}T00:00:00Z"
    d1 = (datetime.fromisoformat(f"{date}T00:00:00+00:00")
          + timedelta(days=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    body = {
        "input": {
            "bounds": {"bbox": list(bbox), "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{
                "type": "sentinel-2-l2a",
                "dataFilter": {"timeRange": {"from": d0, "to": d1},
                               "maxCloudCoverage": 60},
            }],
        },
        "aggregation": {
            "timeRange": {"from": d0, "to": d1},
            "aggregationInterval": {"of": "P1D"},
            "evalscript": EVALSCRIPT,
            "resx": 0.0004, "resy": 0.0004,     # ~40 m, mai grosier = mai rapid
        },
        "calculations": {"default": {"statistics": {"default": {"percentiles": {"k": [50]}}}}},
    }
    r = requests.post(STAT, json=body, headers={"Authorization": f"Bearer {tk}"},
                      timeout=180)
    if r.status_code != 200:
        return None
    data = r.json().get("data") or []
    if not data:
        return None
    st = (((data[0].get("outputs") or {}).get("nbr") or {})
          .get("bands") or {}).get("B0") or {}
    stats = (st.get("stats") or {})
    p50 = ((st.get("percentiles") or {}).get("50.0"))
    if p50 is None and stats.get("mean") is None:
        return None
    return {
        "date": date,
        "nbr_p50": p50 if p50 is not None else stats.get("mean"),
        "nbr_mean": stats.get("mean"),
        "valid_px": (st.get("sampleCount") or 0),
    }


def main() -> int:
    ign = json.loads((ROOT / "live" / "state" / "ignition.json").read_text(encoding="utf-8"))
    tk = token()
    out = {}

    for zid, z in ign.items():
        scenes = z.get("scene") or []
        if len(scenes) < 3:
            continue
        # referinta = cea mai veche scena curata (pre-sezon)
        ref = scenes[0]
        print(f"\n=== {z['nume']} ===")
        print(f"  referinta: {ref['date']} (nori {ref['cloud']:.1f}%)")

        # bbox per zona din state.json
        st = json.loads((ROOT / "live" / "state" / "state.json").read_text(encoding="utf-8"))
        bbox = None
        for cfg in (st.get("zone_list") or []):
            if cfg.get("id") == zid:
                bbox = cfg.get("bbox")
        if not bbox:
            continue

        series = []
        for s in scenes:
            v = nbr_for_scene(tk, bbox, s["date"])
            if v:
                series.append({**v, "cloud": s["cloud"]})
                print(f"    {v['date']}  NBR_p50={v['nbr_p50']:.4f}  "
                      f"px={v['valid_px']:6d}  nori={s['cloud']:.1f}%")
            time.sleep(0.4)

        if not series:
            continue

        ref_nbr = series[0]["nbr_p50"]
        for row in series:
            row["dnbr"] = round(ref_nbr - row["nbr_p50"], 4)
            row["clasa"] = ("ars sever" if row["dnbr"] > 0.66 else
                            "ars moderat" if row["dnbr"] > 0.27 else
                            "ars usor" if row["dnbr"] > 0.10 else "fara")

        # detectam saltul: prima scena unde dNBR > 0.10 SI saltul fata de
        # scena anterioara > 0.05 (ca sa nu prindem uscarea graduală)
        ignited = None
        for i in range(1, len(series)):
            cur, prev = series[i], series[i - 1]
            if cur["dnbr"] > 0.10 and (cur["dnbr"] - prev["dnbr"]) > 0.05:
                ignited = {"data": cur["date"], "dnbr": cur["dnbr"],
                           "dnbr_anterior": prev["dnbr"],
                           "salt": round(cur["dnbr"] - prev["dnbr"], 4),
                           "data_anterioara": prev["date"]}
                break

        out[zid] = {
            "nume": z["nume"],
            "referinta": ref["date"],
            "serie": series,
            "aprindere_detectata": ignited,
            "dnbr_max": max(r["dnbr"] for r in series),
            "dnbr_max_data": max(series, key=lambda r: r["dnbr"])["date"],
        }
        if ignited:
            print(f"  >>> APRINDERE: intre {ignited['data_anterioara']} si "
                  f"{ignited['data']} (dNBR {ignited['dnbr_anterior']} -> "
                  f"{ignited['dnbr']}, salt +{ignited['salt']})")
        else:
            print("  >>> nu am prins un salt clar (poate focarul e prea mic "
                  "sau scenele prea rare)")

    outp = ROOT / "live" / "state" / "ignition_series.json"
    outp.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nscris {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
