#!/usr/bin/env python3
"""
Masoara LATENTA reala de publicare a produselor Sentinel-3 SLSTR FRP pe Copernicus.

INTREBAREA: dupa ce trece satelitul, cat timp trece pana produsul e disponibil
la download? Raspunsul decide cat de des are sens sa verificam.

METODA: pentru fiecare produs listat de API, comparam ora de ACHIZITIE (din
nume) cu ora de CREARE a produsului (campul 'creationDate' din metadata OData,
daca exista) sau cu 'online' (cand a devenit disponibil la download).

ATENTIE: daca produsul e deja vechi, timpul de creare nu ne spune nimic despre
cat a durat procesarea ESA - ci doar cand l-au procesat. Pentru latenta reala
ne trebuie produse RECENTE (ultimele ore), comparate cu ora de achizitie.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/home/lili/projects/domogled-live")
sys.path.insert(0, str(ROOT / "live"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import os  # noqa: E402
from collections import defaultdict  # noqa: E402

import requests  # noqa: E402

sys.path.insert(0, str(ROOT))
from cdse import CDSE, CDSEError  # noqa: E402

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"


def make_token() -> str:
    """Folosim exact clientul din proiect (client_credentials, nu user/pass)."""
    c = CDSE()
    if not c.configured:
        raise SystemExit("lipsesc CDSE_CLIENT_ID / CDSE_CLIENT_SECRET din .env")
    return c.token()


def main() -> int:
    tk = make_token()
    H = {"Authorization": f"Bearer {tk}"}

    # ultimele 5 zile de produse FRP, global (pattern-ul de publicare e global;
    # nu ne intereseaza doar zona noastra - vrem sa vedem ritmul ESA)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=5)
    f = (f"Collection/Name eq 'SENTINEL-3' and "
         f"contains(Name,'SL_2_FRP') and "
         f"ContentDate/Start ge {start:%Y-%m-%dT%H:%M:%S.000Z} and "
         f"ContentDate/Start le {end:%Y-%m-%dT%H:%M:%S.000Z}")
    params = {
        "$filter": f,
        "$select": "Name,CreationDate,ContentDate,Online",
        "$orderby": "ContentDate/Start desc",
        "$top": "80",
    }
    r = requests.get(ODATA, params=params, headers=H, timeout=120)
    r.raise_for_status()
    items = r.json().get("value", [])
    print(f"produse SL_2_FRP (global) in ultimele 5 zile: {len(items)}\n")

    if not items:
        print("(niciunul - filtrarea sau fereastra e prea stramta)")
        return 0

    print(f"{'achizitie (UTC)':<17} {'creat (UTC)':<17} {'latență':>8}  on  sat")
    print("-" * 62)
    lats = []
    for it in items[:40]:
        acq = it.get("ContentDate", {}).get("Start", "")
        cre = it.get("CreationDate", "") or ""
        try:
            a = datetime.fromisoformat(acq.replace("Z", "+00:00"))
            c = datetime.fromisoformat(cre.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        lat = (c - a).total_seconds() / 60
        if 0 < lat < 60 * 24 * 3:
            lats.append(lat)
        nm = it.get("Name", "")
        sat = nm.split("_")[0]
        print(f"{a.strftime('%d.%m %H:%M:%S'):<17} "
              f"{c.strftime('%d.%m %H:%M:%S'):<17} "
              f"{lat:7.0f}m  {'Y' if it.get('Online') else 'N':>3}  {sat}")

    if lats:
        import statistics
        print("-" * 62)
        print(f"latență publicare ESA: medie {statistics.mean(lats):.0f} min "
              f"({statistics.mean(lats)/60:.1f}h) | min {min(lats):.0f}m | "
              f"max {max(lats):.0f}m  (n={len(lats)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
