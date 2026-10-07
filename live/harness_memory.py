#!/usr/bin/env python3
"""
Memoria harness-ului Domogled.

Salveaza un snapshot la fiecare rulare, ca sistemul sa poata raspunde la
intrebarea "a evoluat sau nu?" cu date, nu cu impresii.

Format: JSONL append-only (o linie = o rulare). Nu rescriem niciodata
fisierul, deci nu pierdem istoricul si nu putem strica datele la o eroare
de scriere partiala.

De ce JSONL si nu SQLite: volumul e minuscul (8 rulari/zi = ~3000 linii/an),
iar append-ul e atomic pe orice filesystem serios. SQLite ar adauga
dependinte si blocaje de fisier pentru zero beneficiu la scara asta.

Citirea pentru context: `recent_summary()` intoarce ultimele N snapshot-uri
intr-o forma compacta, gata de bagat in prompt.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HISTORY_FILE = Path(os.getenv(
    "DOMOGLED_HISTORY",
    "/home/lili/projects/domogled-live/live/state/harness_history.jsonl",
))

# Cate snapshot-uri trimitem ca context modelului. 12 x 3h = 36h de istoric:
# suficient ca sa vada un trend, destul de putin ca sa nu umfle promptul.
CONTEXT_POINTS = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_history() -> list[dict]:
    """Toate snapshot-urile, cel mai vechi primul. Ignora liniile corupte."""
    if not HISTORY_FILE.exists():
        return []
    out = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue        # linie trunchiata (ex. crash la scriere) - o sarim
    return out


def snapshot(state: dict, analysis: dict | None = None) -> dict:
    """Extrage esenta unei stari, fara balastul de detectii individuale.

    Nu salvam sutele de detectii: memoria trebuie sa fie mica si comparabila
    intre rulari. Pastram doar agregarile care conteaza pentru trend.
    """
    zones = {}
    for zid, z in (state.get("zones") or {}).items():
        st = z.get("stats") or {}
        fires = []
        for f in (z.get("fires") or []):
            fires.append({
                "id": f.get("id"),
                "lat": round(f.get("lat") or 0, 4),
                "lon": round(f.get("lon") or 0, 4),
                "points": f.get("points"),
                "days": f.get("days"),
                "frp_24h": f.get("frp_last24"),
                "trend": f.get("trend"),
            })
        w = z.get("weather") or {}
        im = z.get("imagery") or {}
        # ultima scena disponibila (cea mai recenta dupa datetime)
        last_scene = None
        if im:
            best = max(im.values(), key=lambda v: v.get("datetime") or "")
            last_scene = {"file": best.get("file"), "datetime": best.get("datetime"),
                          "cloud": best.get("cloud")}

        zones[zid] = {
            "name": z.get("name"),
            "total": st.get("total"),
            "last24_count": st.get("last24_count"),
            "last24_frp": st.get("last24_frp"),
            "last24_max": st.get("last24_max"),
            "fires": fires,
            "scene": last_scene,
            # cicatricea: cifrele dNBR, nu doar imaginea
            "burn_ha": (z.get("burn") or {}).get("ha_total") if z.get("burn") else None,
            "burn_ha_mod": (z.get("burn") or {}).get("ha_moderat_plus") if z.get("burn") else None,
            # meteo cu numele REALE din state.json (vezi harness_context)
            "weather": {
                "temp": w.get("temp_c"),
                "rh": w.get("humidity_pct"),
                "wind_kmh": w.get("wind_kmh"),
                "wind_dir": w.get("wind_from_compass"),
                "downwind": w.get("downwind_compass"),
                "gust_kmh": w.get("gust_kmh"),
                "rain48": w.get("precip_next48_mm"),
                "vpd": w.get("vpd_kpa"),
                "soil": w.get("soil_moisture"),
                "danger": (w.get("danger") or {}).get("index"),
                "danger_class": (w.get("danger") or {}).get("class"),
            } if w else None,
        }

    return {
        "ts": _now(),
        "cycles": state.get("cycles"),
        "updated": state.get("updated"),
        "zones": zones,
        "analysis": analysis or {},
    }


def append(snap: dict) -> None:
    """Adauga un snapshot la istoric. Append pur, niciodata rescriere."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, ensure_ascii=False) + "\n")


def deliver(snap: dict, analysis: dict | None = None) -> dict:
    """Adauga analiza la snapshot si il scrie. Returneaza snapshot-ul final."""
    if analysis:
        snap["analysis"] = analysis
    append(snap)
    return snap


def _delta(cur, prev, key):
    """Diferenta pe un camp numeric intre doua zone. None daca lipseste ceva."""
    a = (cur or {}).get(key)
    b = (prev or {}).get(key)
    if a is None or b is None:
        return None
    try:
        return round(float(a) - float(b), 2)
    except (TypeError, ValueError):
        return None


def compare(state: dict) -> dict:
    """Compara starea curenta cu ultimul snapshot din istoric.

    intoarce un dict cu diferenta pe fiecare agregare. Folosit direct in
    prompt, ca modelul sa nu fie nevoit sa ghiceaca evolutia.
    """
    hist = load_history()
    if not hist:
        return {"first_run": True, "zones": {}}

    prev = hist[-1]
    cur = snapshot(state)
    out = {"first_run": False, "since": prev.get("ts"), "hours_elapsed": None,
           "zones": {}}

    try:
        t0 = datetime.fromisoformat(prev["ts"])
        t1 = datetime.fromisoformat(cur["ts"])
        out["hours_elapsed"] = round((t1 - t0).total_seconds() / 3600, 2)
    except (KeyError, ValueError):
        pass

    for zid, z in cur["zones"].items():
        pz = (prev.get("zones") or {}).get(zid) or {}
        d: dict[str, Any] = {k: _delta(z, pz, k) for k in
                             ("total", "last24_count", "last24_frp", "last24_max",
                              "burn_ha", "burn_ha_mod")}

        # focarele: potrivim dupa id, ca sa prindem si focare noi si cele stinse
        pf = {f.get("id"): f for f in (pz.get("fires") or [])}
        cf = {f.get("id"): f for f in (z.get("fires") or [])}
        new_fires, gone_fires, grown = [], [], []
        for fid, f in cf.items():
            if fid not in pf:
                new_fires.append({"id": fid, "lat": f.get("lat"), "lon": f.get("lon"),
                                  "frp_24h": f.get("frp_24h"), "points": f.get("points")})
                continue
            dfrp = _delta(f, pf[fid], "frp_24h")
            if dfrp is not None and abs(dfrp) >= 5:
                grown.append({"id": fid, "delta_frp": dfrp,
                              "from": pf[fid].get("frp_24h"), "to": f.get("frp_24h"),
                              "trend": f.get("trend")})
        for fid, f in pf.items():
            if fid not in cf:
                gone_fires.append({"id": fid, "last_frp": f.get("frp_24h")})

        # schimbare de scena satelitara = imagine noua disponibila
        ps = (pz.get("scene") or {}).get("file")
        cs = (z.get("scene") or {}).get("file")

        d.update({"new_fires": new_fires, "gone_fires": gone_fires,
                  "frp_changes": grown, "new_scene": bool(cs and cs != ps),
                  "prev_scene": ps, "cur_scene": cs})
        out["zones"][zid] = d

    return out


def context_text(state: dict, n: int = CONTEXT_POINTS) -> str:
    """Ultimele N snapshot-uri ca text compact, pentru prompt.

    Format tabelar scurt ca modelul sa vada seria, nu sa o reconstruiasca.
    """
    hist = load_history()[-n:]
    if not hist:
        return "(fara istoric - prima rulare)"

    lines = []
    for s in hist:
        ts = (s.get("ts") or "")[:16].replace("T", " ")
        parts = []
        for zid, z in (s.get("zones") or {}).items():
            frp = z.get("last24_frp")
            c24 = z.get("last24_count")
            nf = len(z.get("fires") or [])
            short = (z.get("name") or zid)[:14]
            parts.append(f"{short}: FRP24h={frp} det24h={c24} focare={nf}")
        lines.append(f"{ts} | " + " | ".join(parts))
    return "\n".join(lines)


def latest_analysis() -> dict | None:
    """Ultima analiza AI salvata, daca exista."""
    for s in reversed(load_history()):
        if s.get("analysis"):
            return {"ts": s.get("ts"), **(s["analysis"])}
    return None
