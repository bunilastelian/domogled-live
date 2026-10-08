#!/usr/bin/env python3
"""
Determina DATA REALA DE START a incendiului, din serii temporale Sentinel-2.

PROBLEMA: aplicatia afiseaza "9 zile" (de cand a inceput colectarea), dar
incendiul arde de ~2 luni. NASA FIRMS public ofera doar 7 zile de detections,
deci nu putem lua istoricul termic.

SOLUTIA: folosim dNBR pe serii temporale de scene Sentinel-2 (arhiva CDSE
merge ani in urma). dNBR = diferenta intre reflectanta in infrarosu apropiat
inainte si dupa foc. Daca luam o scena de REFERINTA (pre-incendiu) si o
comparam cu fiecare scena ulterioara, data la care dNBR sare de prag = aprindere.

METODA:
  1. scenă de referință = cea mai curată scenă dinainte de sezon (iulie/august)
  2. pentru fiecare scenă ulterioară: dNBR mediu în zona focarului
  3. primul salt semnificativ = data de start estimată

CAPCANA: dNBR e afectat de fenologie (vegetatia se usuca natural toamna) si de
nori. De aceea comparația se face pe MEDIANA in zona focarului (nu medie, care
e trasă de outlieri) si doar pe scene cu nori putini.

NU inlocuieste datele termice NASA (astea spun CAND arde activ). Spune doar
CAND a aparut cicatricea - o limita inferioara buna pentru start.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "live"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

CATALOG = "https://catalogue.dataspace.copernicus.eu/STAC/search"
PROCESS = "https://sh.dataspace.copernicus.eu/api/v1/process"

OUT = ROOT / "live" / "state" / "ignition.json"

# praguri dNBR standard USGS
DNBR_LIGHT = 0.10
DNBR_MODERATE = 0.27
DNBR_SEVERE = 0.66


def cdse_token() -> str:
    import cdse
    return cdse.CDSE().token()


def search_scenes(bbox, dt_from: str, dt_to: str, max_cloud: float = 20) -> list[dict]:
    """Scene S2 curate, una per zi (cea mai putin noroasa)."""
    import cdse
    tk = cdse.CDSE().token()
    H = {"Authorization": f"Bearer {tk}"}
    body = {
        "collections": ["sentinel-2-l2a"],
        "bbox": list(bbox),
        "datetime": f"{dt_from}T00:00:00Z/{dt_to}T00:00:00Z",
        "limit": 200,
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
    }
    r = requests.post(CATALOG, json=body, headers=H, timeout=120)
    r.raise_for_status()
    feats = r.json().get("features", [])

    by_day: dict[str, dict] = {}
    for f in feats:
        p = f["properties"]
        day = p["datetime"][:10]
        cc = p.get("eo:cloud_cover", 99)
        cur = by_day.get(day)
        if cur is None or cc < cur["cloud"]:
            by_day[day] = {
                "date": day, "id": f["id"], "cloud": cc,
                "datetime": p["datetime"],
            }
    return sorted(by_day.values(), key=lambda x: x["date"])


def main() -> int:
    st = json.loads((ROOT / "live" / "state" / "state.json").read_text(encoding="utf-8"))
    zones = []
    for zid, z in (st.get("zones") or {}).items():
        for cfg in (st.get("zone_list") or []):
            if cfg.get("id") == zid and cfg.get("bbox"):
                zones.append((zid, cfg["name"], tuple(cfg["bbox"])))

    if not zones:
        print("nu am gasit bbox in state.json", file=sys.stderr)
        return 1

    out: dict[str, dict] = {}
    for zid, zname, bbox in zones:
        print(f"\n=== {zname} ({zid}) ===")
        scenes = search_scenes(bbox, "2026-05-01", "2026-10-08", max_cloud=20)
        print(f"  {len(scenes)} zile cu scene curate")
        for s in scenes:
            print(f"    {s['date']}  nori={s['cloud']:5.1f}%")

        out[zid] = {
            "nume": zname,
            "scene": scenes,
            "referinta_sugerata": scenes[0]["date"] if scenes else None,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nscris {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
