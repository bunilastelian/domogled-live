#!/usr/bin/env python3
"""
Determina data de start a incendiului, masurand DOAR pe perimetrul focarului.

DE CE a esuat prima incercare: am masurat NBR pe bbox-ul INTREG al zonei
(~22 x 20 km = 440 km²). Focarul ars are cateva sute de hectare - diluat in
440 km², semnalul dispare in media de fond. Rezultatul: o curba lina de uscare
sezoniera, fara niciun salt.

FIX: construim un poligon MIC in jurul focarului (raza ~1.5 km fata de
centroidul detectiilor NASA) si masuram DOAR acolo. Asa semnalul focalizat
devine vizibil.

Metoda: NBR mediu in poligonul focarului, pe fiecare scena curata. Cand
vegetatia sanatoasa se transforma in cenusa, NBR scade brusc. Pragurile USGS
pe dNBR: 0.10 ars usor, 0.27 moderat, 0.66 sever.

ATENTIE fenologie: comparam mereu cu SCENA DE REFERINTA (pre-sezon), nu intre
scene consecutive - asa uscarea naturala se scade singura.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

STAT = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

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
  let bad = [0,1,3,6,8,9,10,11].includes(s.SCL);
  return {nbr: [nbr], dataMask: [s.dataMask && !bad ? 1 : 0]};
}
"""


def token() -> str:
    import cdse
    return cdse.CDSE().token()


def circle_geojson(lat: float, lon: float, radius_km: float) -> dict:
    """Poligon ~circular in jurul focarului (12 laturi e destul)."""
    pts = []
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    for i in range(13):
        a = 2 * math.pi * i / 12
        pts.append([lon + dlon * math.cos(a), lat + dlat * math.sin(a)])
    return {"type": "Polygon", "coordinates": [pts]}


def nbr_polygon(tk: str, poly: dict, date: str) -> dict | None:
    d0 = f"{date}T00:00:00Z"
    d1 = (datetime.fromisoformat(f"{date}T00:00:00+00:00") + timedelta(days=1)
          ).isoformat(timespec="seconds").replace("+00:00", "Z")
    body = {
        "input": {
            "bounds": {"geometry": poly,
                       "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [{"type": "sentinel-2-l2a",
                      "dataFilter": {"timeRange": {"from": d0, "to": d1},
                                     "maxCloudCoverage": 80}}],
        },
        "aggregation": {
            "timeRange": {"from": d0, "to": d1},
            "aggregationInterval": {"of": "P1D"},
            "evalscript": EVALSCRIPT,
            "resx": 0.0002, "resy": 0.0002,      # ~20 m - fin in zona mica
        },
        "calculations": {"default": {"statistics": {"default": {
            "percentiles": {"k": [10, 50, 90]}}}}},
    }
    r = requests.post(STAT, json=body, headers={"Authorization": f"Bearer {tk}"},
                      timeout=180)
    if r.status_code != 200:
        return None
    data = r.json().get("data") or []
    if not data:
        return None
    st = ((((data[0].get("outputs") or {}).get("nbr") or {}).get("bands") or {})
          .get("B0") or {})
    # ATENTIE la structura: sampleCount si percentiles sunt in 'stats',
    # nu la nivelul band-ului. Prima versiune citea gresit -> px=0.
    stats = st.get("stats") or {}
    pct = stats.get("percentiles") or st.get("percentiles") or {}
    if stats.get("mean") is None and pct.get("50.0") is None:
        return None
    return {
        "date": date,
        "nbr_p50": pct.get("50.0"),
        "nbr_p10": pct.get("10.0"),
        "nbr_mean": stats.get("mean"),
        "nbr_stdev": stats.get("stDev"),
        "px_total": stats.get("sampleCount") or 0,
        "px_nodata": stats.get("noDataCount") or 0,
    }


def main() -> int:
    st = json.loads((ROOT / "live" / "state" / "state.json").read_text(encoding="utf-8"))
    scen_path = ROOT / "live" / "state" / "ignition.json"
    if not scen_path.exists():
        print("ruleaza intai tools/ignition_search.py", file=sys.stderr)
        return 1
    scen = json.loads(scen_path.read_text(encoding="utf-8"))

    tk = token()
    out = {}

    for zid, zst in (st.get("zones") or {}).items():
        fires = zst.get("fires") or []
        if not fires:
            print(f"{zid}: fara focare, sar")
            continue
        # focarul principal = cel cu cele mai multe puncte
        main_fire = max(fires, key=lambda f: f.get("points") or 0)
        lat, lon = main_fire["lat"], main_fire["lon"]
        print(f"\n=== {zst.get('name')} ===")
        print(f"  focar: {lat:.5f},{lon:.5f} ({main_fire.get('points')} puncte)")

        dates = [s["date"] for s in (scen.get(zid, {}).get("scene") or
                                     scen.get(zid, {}).get("scene") or [])]
        if not dates:
            dates = [s["date"] for s in scen.get(zid, {}).get("scene", [])]

        series = []
        for d in dates:
            # raza 1.2 km = acoperim focarul fara sa diluam
            # Statistical API are rate limit (~1 req/s) -> 429 daca trimitem
            # in rafala. Asteptam 1.2s intre cereri si reincercam la 429.
            v = None
            for attempt in range(4):
                v = nbr_polygon(tk, circle_geojson(lat, lon, 1.2), d)
                if v is not None:
                    break
                time.sleep(2 + attempt * 3)     # backoff la 429 / eroare
            if v:
                series.append(v)
                p50 = (f"{v['nbr_p50']:.4f}"
                       if isinstance(v["nbr_p50"], (int, float)) else "?")
                print(f"    {d}  NBR_p50={p50}  px={v['px_total']:6d}")
            else:
                print(f"    {d}  (fara date - nori sau rate limit)")
            time.sleep(1.3)

        if len(series) < 3:
            continue

        ref = series[0]
        ref_nbr = ref["nbr_p50"]
        for r_ in series:
            if r_["nbr_p50"] is None or ref_nbr is None:
                r_["dnbr"] = None
                continue
            r_["dnbr"] = round(ref_nbr - r_["nbr_p50"], 4)
            r_["clasa"] = ("ars sever" if r_["dnbr"] > 0.66 else
                           "ars moderat" if r_["dnbr"] > 0.27 else
                           "ars usor" if r_["dnbr"] > 0.10 else "fara")

        # aprindere = primul dNBR > 0.10 sustinut (2 scene consecutive peste prag)
        ignited = None
        for i in range(len(series) - 1):
            a, b = series[i], series[i + 1]
            if (a["dnbr"] or 0) > 0.10 and (b["dnbr"] or 0) > 0.10:
                ignited = {
                    "data_min": series[max(0, i - 1)]["date"],
                    "data_max": a["date"],
                    "dnbr": a["dnbr"],
                    "interval": f"{series[max(0,i-1)]['date']} .. {a['date']}",
                }
                break

        out[zid] = {
            "nume": zst.get("name"),
            "focar": {"lat": lat, "lon": lon, "raza_km": 1.2,
                      "puncte": main_fire.get("points")},
            "referinta": ref["date"],
            "serie": series,
            "aprindere": ignited,
            "dnbr_max": max((r_["dnbr"] or 0) for r_ in series),
            "dnbr_max_data": max(series, key=lambda r_: r_["dnbr"] or 0)["date"],
        }
        if ignited:
            print(f"  >>> APRINDERE intre {ignited['interval']} "
                  f"(dNBR {ignited['dnbr']})")
        else:
            print(f"  >>> fara aprindere clara; dNBR max "
                  f"{out[zid]['dnbr_max']} pe {out[zid]['dnbr_max_data']}")

    outp = ROOT / "live" / "state" / "ignition_fire.json"
    outp.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nscris {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
