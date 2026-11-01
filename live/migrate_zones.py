#!/usr/bin/env python3
"""
migrate_zones.py - trece state.json de la mai multe zone la o singura regiune.

Ruleaza o singura data, inainte de a porni colectorul cu zones.json v3.

Ce face:
  1. face backup la state.json si zones.json
  2. reuneste detectiile din TOATE zonele vechi intr-un singur set (deduplicat
     pe cheie - cheile con tin deja sursa, senzor, data, ora si coordonate,
     deci doua detectii identice din zone diferite se numara o singura data)
  3. muta rezultatul sub zona noua, past rand istoricul de focare si alerte
  4. NU sterge nimic din datele vechi: le pastreaza sub state["zones_legacy"],
     ca sa poata fi verificate sau refolosite

Idempotent: daca rula de doua ori, a doua oara nu schimba nimic.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def backup(p: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = p.with_name(f"{p.stem}.backup-{stamp}{p.suffix}")
    shutil.copy2(p, dst)
    return dst


def main() -> int:
    state_file = HERE / "state" / "state.json"
    zones_file = HERE / "zones.json"

    if not state_file.exists():
        print(f"lipseste {state_file}")
        return 1

    state = json.loads(state_file.read_text(encoding="utf-8"))
    zcfg = json.loads(zones_file.read_text(encoding="utf-8"))
    new_zone = zcfg["zones"][0]
    nid = new_zone["id"]

    # --- deja migrat? ---
    if state.get("version") == 3 and list((state.get("zones") or {}).keys()) == [nid]:
        print(f"deja migrat: o singura zona '{nid}', versiune 3. Nu fac nimic.")
        return 0

    b1 = backup(state_file)
    b2 = backup(zones_file)
    print(f"backup stare : {b1.name}")
    print(f"backup zone  : {b2.name}")

    old_zones = state.get("zones") or {}
    print(f"\nzone vechi: {list(old_zones.keys())}")

    # --- reuniunea detectiilor ---
    merged: dict = {}
    per_zone_counts: dict[str, int] = {}
    for zid, zv in old_zones.items():
        dets = zv.get("detections") or {}
        per_zone_counts[zid] = len(dets)
        for k, v in dets.items():
            if k not in merged:
                # pastram purtatorul de zona pentru trasabilitate, dar nu il
                # lasam sa dubleze o detectie care apare in ambele
                merged[k] = v

    total_before = sum(per_zone_counts.values())
    print(f"detectii per zona veche: {per_zone_counts}")
    print(f"insumate: {total_before}  ->  unice dupa reuniune: {len(merged)}")
    print(f"duplicate eliminate: {total_before - len(merged)}")

    # --- sursele si restul metadata: luam valorile zonei implicite vechi ---
    default_old = state.get("default_zone") or next(iter(old_zones), None)
    donor = old_zones.get(default_old) or (next(iter(old_zones.values())) if old_zones else {})

    # focare: le reclam pe toate din toate zonele, deduplicate dupa centru
    # (focarul de la granita aparea in ambele, cu centre usor diferite)
    fires: list = []
    seen_centres: set[tuple] = set()
    for zid, zv in old_zones.items():
        for f in (zv.get("fires") or []):
            key = (round(float(f.get("lat", 0)), 3), round(float(f.get("lon", 0)), 3))
            if key in seen_centres:
                continue
            seen_centres.add(key)
            f = dict(f)
            f["_from_zone"] = zid
            fires.append(f)
    fires.sort(key=lambda f: (f.get("frp_last24") or 0, f.get("frp_sum") or 0), reverse=True)
    for i, f in enumerate(fires, 1):
        f["rank"] = i
    print(f"\nfocare in zonele vechi: "
          f"{[len(zv.get('fires') or []) for zv in old_zones.values()]}  ->  "
          f"dupa dedup pe centru: {len(fires)}")

    # alerte: reuniune simpla, sortate dupa timp daca exista
    alerts: list = []
    for zid, zv in old_zones.items():
        for a in (zv.get("alerts") or []):
            a = dict(a)
            a.setdefault("zone", zid)
            alerts.append(a)

    # --- construim starea noua ---
    new: dict = {
        "version": 3,
        "cycles": state.get("cycles") or 0,
        "zones": {
            nid: {
                "detections": merged,
                "alerts": alerts,
                "imagery": donor.get("imagery") or {},
                "burn": donor.get("burn") or {},
                "fires": fires,
                "weather": donor.get("weather") or {},
                "frp_products": donor.get("frp_products") or [],
                "cycles": donor.get("cycles") or 0,
                "aoi": {
                    "name": new_zone["name"],
                    "bbox": new_zone["bbox"],
                    "center": new_zone["center"],
                    "zoom": new_zone.get("zoom", 12),
                    "park_file": ((new_zone.get("parks") or [{}])[0] or {}).get("geojson", ""),
                    "parks": new_zone.get("parks") or [],
                },
                "id": nid,
                "name": new_zone["name"],
                "short": new_zone["short"],
            }
        },
        "zone_list": [{k: new_zone.get(k) for k in ("id", "name", "short", "bbox", "center",
                                                    "zoom", "places", "parks")}],
        "default_zone": nid,
        "updated": state.get("updated"),
    }

    # datele vechi NU se pierd: rămân accesibile pentru comparatie
    new["zones_legacy"] = old_zones
    new["migrated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new["migrated_from"] = list(old_zones.keys())

    # scriere atomica, ca colectorul sa nu vada niciodata un fisier pe jumatate
    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    tmp.replace(state_file)
    print(f"\nscris {state_file} ({state_file.stat().st_size} bytes)")

    # --- verificare ---
    chk = json.loads(state_file.read_text(encoding="utf-8"))
    z = chk["zones"][nid]
    print("\n=== VERIFICARE DUPĂ MIGRARE ===")
    print(f"  versiune      : {chk['version']}")
    print(f"  zona unica    : {nid}  ({z['name']})")
    print(f"  detectii      : {len(z['detections'])}  (inainte: {total_before} insumate)")
    print(f"  focare        : {len(z['fires'])}")
    print(f"  alerte        : {len(z['alerts'])}")
    print(f"  bbox          : {z['aoi']['bbox']}")
    print(f"  parcuri       : {[p.get('name') for p in z['aoi']['parks']]}")
    print(f"  zone vechi    : pastrate in zones_legacy ({list((chk.get('zones_legacy') or {}).keys())})")

    assert len(z["detections"]) == len(merged), "numarul de detectii nu corespunde!"
    print("\nOK - migrare completa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
