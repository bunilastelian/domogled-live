#!/usr/bin/env python3
"""
Descarca retroactiv produsele SEN3 SL_2_FRP deja catalogate in state.json,
ca sa putem genera hartile termice pentru toate trecerile din istoric.

Ruleaza o singura data (sau cand vrei sa re-cache-uiesti dupa o curatare).
Produsele sunt listate in state.zones.<zona>.frp_products si in state["frp_products"].
"""
import json
import sys
from pathlib import Path

ROOT = Path("/home/lili/projects/domogled-live")
sys.path.insert(0, str(ROOT / "live"))
sys.path.insert(0, str(ROOT))

import os
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
from cdse import CDSE, CDSEError  # noqa: E402

STATE = ROOT / "live" / "state" / "state.json"
DEST = ROOT / "live" / "state" / "frp"


def main() -> int:
    st = json.loads(STATE.read_text(encoding="utf-8"))
    names = set()
    for z in (st.get("zones") or {}).values():
        names.update(z.get("frp_products") or [])
    names = sorted(names)
    print(f"{len(names)} produse catalogate")

    DEST.mkdir(parents=True, exist_ok=True)
    have = {p.stem for p in DEST.glob("*.zip")}
    print(f"{len(have)} deja pe disc")

    client = CDSE()
    ok = fail = skip = 0
    for i, name in enumerate(names, 1):
        if name in have:
            skip += 1
            continue
        try:
            p = client.download_product(name, dest=DEST)
            ok += 1
            print(f"[{i}/{len(names)}] OK {name[:52]} ({p.stat().st_size // 1024} KB)")
        except CDSEError as e:
            fail += 1
            print(f"[{i}/{len(names)}] FAIL {name[:52]}: {str(e)[:110]}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"[{i}/{len(names)}] ERR {name[:52]}: {type(e).__name__}: {str(e)[:90]}")

    print(f"\ndescarcate: {ok}, sari peste: {skip}, esuate: {fail}")
    total = sum(p.stat().st_size for p in DEST.glob("*.zip"))
    print(f"total pe disc: {total / 1048576:.1f} MB in {len(list(DEST.glob('*.zip')))} fisiere")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
