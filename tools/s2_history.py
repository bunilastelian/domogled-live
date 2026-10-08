#!/usr/bin/env python3
"""
Construieste istoricul extins de scene Sentinel-2 pentru timeline.

PROBLEMA: aplicatia afiseaza doar 9 zile (de cand a inceput colectarea locala).
Dar arhiva CDSE are scene din 2015. Incendiul arde de ~2 luni, deci utilizatorul
nu vede contextul real.

SOLUTIA: catalogam TOATE scenele curate (nori < 30%) din mai pana azi si le
publicam ca index. Timeline-ul le arata pe cele care exista pentru intervalul
selectat - daca nu exista scena intr-o zi, pur si simplu nu arata nimic.

IMPORTANT - ce NU facem: nu pretindem ca stim data aprinderii. Am incercat sa
o determinam din dNBR pe serii temporale si NU a iesit: dNBR maxim 0.038, sub
pragul de 0.10 care marcheaza "ars". Motivul probabil: foc de suprafata (arde
litiera, nu coronamentul) sau focar NASA dispersat. Mai bine fara data decat
cu una gresita.

Output: live/web/state/s2_history.json + copierea scenelor in web/img/
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

CATALOG = "https://stac.dataspace.copernicus.eu/v1/search"
OUT = ROOT / "live" / "web" / "state" / "s2_history.json"


def search(bbox: list, dt_from: str, dt_to: str, max_cloud: float) -> list[dict]:
    """Cauta scene, cu PAGINARE. CDSE limiteaza 'limit' la 100 per cerere.

    Capcana: cu limit=250 primim HTTP 400 'Limit of 250 exceeds maximum'.
    Deci mergem paginat cu token-ul 'next' pana se termina rezultatele.
    Endpoint: stac.dataspace.copernicus.eu (catalogue.dataspace.* da erori
    pe acelasi payload).
    """
    import cdse
    tk = cdse.CDSE().token()
    H = {"Authorization": f"Bearer {tk}"}
    base = {
        "collections": ["sentinel-2-l2a"],
        "bbox": list(bbox),
        "datetime": f"{dt_from}T00:00:00Z/{dt_to}T00:00:00Z",
        "limit": 100,
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
    }

    feats: list[dict] = []
    body = dict(base)
    for page in range(12):               # max 12 pagini = 1200 scene
        data = None
        for attempt in range(4):
            try:
                r = requests.post(CATALOG, json=body, headers=H, timeout=120)
                if r.status_code == 200:
                    data = r.json()
                    break
                if attempt == 3:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:180]}")
            except requests.RequestException as exc:
                if attempt == 3:
                    raise RuntimeError(f"{type(exc).__name__}: {str(exc)[:140]}")
            time.sleep(3 + attempt * 4)

        got = (data or {}).get("features", [])
        feats.extend(got)
        if not got or len(got) < base["limit"]:
            break

        # token de continuare din linkul 'next'
        nxt = next((l for l in (data.get("links") or [])
                    if l.get("rel") == "next" and l.get("body")), None)
        if not nxt:
            break
        body = nxt["body"]
        time.sleep(0.5)

    # una per zi (cea mai putin noroasa)
    by_day: dict[str, dict] = {}
    for f in feats:
        p = f["properties"]
        day = p["datetime"][:10]
        cc = p.get("eo:cloud_cover", 99)
        if day not in by_day or cc < by_day[day]["cloud"]:
            by_day[day] = {
                "date": day,
                "datetime": p["datetime"],
                "id": f["id"],
                "cloud": round(cc, 2),
                "platform": (p.get("platform") or f["id"][:3]),
            }
    return sorted(by_day.values(), key=lambda x: x["date"])


def main() -> int:
    st = json.loads((ROOT / "live" / "state" / "state.json").read_text(encoding="utf-8"))
    out: dict[str, list] = {}

    for cfg in (st.get("zone_list") or []):
        zid, name, bbox = cfg.get("id"), cfg.get("name"), cfg.get("bbox")
        if not (zid and bbox):
            continue
        print(f"\n=== {name} ({zid}) ===")
        scenes = []
        for attempt in range(3):
            try:
                scenes = search(bbox, "2026-04-15", "2026-10-09", max_cloud=35)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"   incercare {attempt+1} esuata: {type(exc).__name__}: "
                      f"{str(exc)[:120]}")
                time.sleep(4)
        print(f"   {len(scenes)} zile cu scene curate (nori <35%)")
        for s in scenes:
            print(f"     {s['date']}  {s['platform']}  nori={s['cloud']:5.1f}%")
        out[zid] = scenes

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    tot = sum(len(v) for v in out.values())
    print(f"\nscris {OUT} — {tot} scene in total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
