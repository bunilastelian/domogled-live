#!/usr/bin/env python3
"""
dedupe_slstr.py - elimina dublurile de algoritm SLSTR din starea existenta.

Produsul SLSTR vine cu trei scheme FRP: standard, alternative si SWIR500m.
"standard" si "alternative" sunt doi algoritmi pentru ACELASI pixel - nu doua
observatii independente. In state.json apareau 69 de perechi cu coordonate si
minute identice, numarate separat, ceea ce umfla totalul, FRP-ul cumulat si
numarul de puncte din focare.

Pentru fiecare pixel+minut pastram o singura detectie:
  1. MWIR1km-standard  (produsul nominal ESA)
  2. SWIR500m          (rezolutie mai buna - observatie distincta, o pastram)
  3. MWIR1km-alternative  doar daca nu exista standard pentru acel pixel

Idempotent: a doua rulare nu schimba nimic.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PRIORITY = {"MWIR1km-standard": 0, "SWIR500m": 1, "MWIR1km-alternative": 2}


def scheme_of(sensor: str) -> str:
    return str(sensor or "").replace("SLSTR ", "")


def main() -> int:
    state_file = HERE / "state" / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))

    zones = state.get("zones") or {}
    if not zones:
        print("state.json nu are zone - nimic de facut")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = state_file.with_name(f"state.backup-dedupe-{stamp}.json")
    shutil.copy2(state_file, backup)
    print(f"backup: {backup.name}")

    total_removed = 0
    for zid, zs in zones.items():
        dets = zs.get("detections") or {}
        if not dets:
            continue

        # grupam pe identitatea observatiei: pixel + data + ora
        groups: dict[tuple, list[str]] = {}
        for k, v in dets.items():
            key = (round(float(v.get("lat", 0)), 5), round(float(v.get("lon", 0)), 5),
                   v.get("date"), v.get("time"))
            groups.setdefault(key, []).append(k)

        drop: list[str] = []
        for key, keys in groups.items():
            if len(keys) < 2:
                continue
            # sortam dupa prioritatea schemei; primele raman
            keys_sorted = sorted(
                keys,
                key=lambda k: (PRIORITY.get(scheme_of(dets[k].get("sensor")), 9), k),
            )
            keep = keys_sorted[0]
            for k in keys_sorted[1:]:
                s = scheme_of(dets[k].get("sensor"))
                # SWIR500m e senzor diferit (500 m vs 1 km): il pastram chiar
                # daca se suprapune cu standard, e o observatie separata
                if s == "SWIR500m":
                    continue
                # restul sunt acelasi pixel: pastram doar daca NU avem deja
                # un standard in grup
                keeps_std = scheme_of(dets[keep].get("sensor")) == "MWIR1km-standard"
                if s == "MWIR1km-alternative" and keeps_std:
                    drop.append(k)

        for k in drop:
            dets.pop(k, None)
        total_removed += len(drop)

        if drop:
            print(f"  {zid}: {len(drop)} detectii duplicate eliminate "
                  f"({len(dets) + len(drop)} -> {len(dets)})")

    if not total_removed:
        print("nicio dublura gasita - nimic de eliminat")
        return 0

    # focarele se recalculeaza pe setul curatat, cu parcurile regiunii
    import fires as F

    zcfg = json.loads((HERE / "zones.json").read_text(encoding="utf-8"))
    zone = zcfg["zones"][0]
    parks = []
    for p in zone.get("parks") or []:
        gp = HERE / "web" / (p.get("geojson") or "")
        if gp.exists():
            parks.append({"name": p.get("name"),
                          "_geom": json.loads(gp.read_text(encoding="utf-8"))})

    def parse(k: str, v: dict) -> dict:
        return {"src": v.get("src"), "sensor": v.get("sensor"),
                "date": v.get("date"), "time": v.get("time"),
                "lat": float(v.get("lat")), "lon": float(v.get("lon")),
                "frp": v.get("frp")}

    for zid, zs in zones.items():
        dets = zs.get("detections") or {}
        if not dets:
            continue
        parsed = {k: parse(k, v) for k, v in dets.items()}
        before = len(zs.get("fires") or [])
        zs["fires"] = F.find_fires(parsed, zone, parks=parks)
        print(f"  {zid}: focare {before} -> {len(zs['fires'])}")

        # statisticile trebuie sa reflecte setul curatat
        st = zs.get("stats") or {}
        st["total"] = len(dets)
        st["nasa"] = sum(1 for v in dets.values() if v.get("src") == "NASA")
        st["esa"] = sum(1 for v in dets.values() if v.get("src") == "ESA")
        zs["stats"] = st

    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(state_file)

    print(f"\ntotal duplicate eliminate: {total_removed}")
    print(f"scris {state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
