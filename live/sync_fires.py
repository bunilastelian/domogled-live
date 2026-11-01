#!/usr/bin/env python3
"""
sync_fires.py - detecteaza focarele care ard in acelasi ritm.

De ce conteaza: doua incendii la 19 km unul de altul, cu acelasi numar de
puncte si vârf in exact aceleasi 48 h, nu sunt doua evenimente independente -
sunt semnatura unei cauze comune (furtuna de fulgere uscate, o zi de vant
puternic, aprindere deliberata). Citite separat, raportul sugereaza doua
intamplari; citite impreuna, spune cu totul altceva.

Modulul adauga pe fiecare focar un camp `sync` (None sau dict) si intoarce
grupurile gasite. Nu modifica geometria si nu inventeaza date: comparatie
doar intre focarele care exista deja.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanta in km intre doua puncte."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1.0, x)))


def _daily(dets: list[dict]) -> dict[str, int]:
    """Detectii pe zi (YYYY-MM-DD -> numar)."""
    out: dict[str, int] = {}
    for d in dets:
        k = d.get("date")
        if k:
            out[k] = out.get(k, 0) + 1
    return out


def _peak_day(daily: dict[str, int]) -> str | None:
    if not daily:
        return None
    return max(daily.items(), key=lambda kv: kv[1])[0]


def _series_corr(a: dict[str, int], b: dict[str, int]) -> float | None:
    """Corelatie Pearson intre seriile zilnice, pe zilele comune.

    Intoarce None doar daca sunt mai putin de 3 zile comune.

    Atentie la seriile constante: daca ambele focare au exact acelasi numar
    de detectii in fiecare zi, deviatia standard e zero si Pearson nu e
    definit. Matematic e nedeterminat, dar practic e CEA MAI puternica
    dovada de sincronism - doua focare care se comporta identic. Tratarea
    lor ca "necunoscut" rata exact cazul care ne intereseaza, asa ca le
    dam 1.0. Daca doar una e constanta, atunci chiar nu se poate spune
    nimic: ramane None.
    """
    zile = sorted(set(a) & set(b))
    if len(zile) < 3:
        return None
    xs = [a[z] for z in zile]
    ys = [b[z] for z in zile]
    n = len(zile)
    mx, my = sum(xs) / n, sum(ys) / n
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 and dy == 0:
        return 1.0          # ambele constante: comportament identic
    if dx == 0 or dy == 0:
        return None         # doar una constanta: raport nedeterminat
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return num / (dx * dy)


def find_sync_groups(fires: list[dict], dets_by_fire: list[list[dict]],
                     window_h: int = 48, min_corr: float = 0.6,
                     max_km: float = 40.0) -> list[dict]:
    """Grupeaza focarele care par sa fi pornit din aceeasi cauza.

    Criterii (toate trebuie indeplinite):
      - vârfurile lor cad in aceeasi fereastra de `window_h` ore
      - seriile zilnice sunt corelate (r >= min_corr)
      - sunt la cel mult `max_km` unul de altul

    Fiecare criteriu singur ar da false pozitive: doua incendii pot avea
    vârful in aceeasi saptamana fara sa aiba legatura. Le cerem pe toate trei.
    """
    n = len(fires)
    if n < 2:
        return []

    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    perechi = []
    for i in range(n):
        for j in range(i + 1, n):
            di = _daily(dets_by_fire[i])
            dj = _daily(dets_by_fire[j])
            pi, pj = _peak_day(di), _peak_day(dj)
            if not pi or not pj:
                continue

            # 1. vârfuri apropiate in timp
            t_i = datetime.strptime(pi, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            t_j = datetime.strptime(pj, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dh = abs((t_i - t_j).total_seconds()) / 3600
            if dh > window_h:
                continue

            # 2. serii corelate
            r = _series_corr(di, dj)
            if r is None or r < min_corr:
                continue

            # 3. apropiate geografic
            km = _haversine(fires[i]["lat"], fires[i]["lon"],
                            fires[j]["lat"], fires[j]["lon"])
            if km > max_km:
                continue

            union(i, j)
            perechi.append({"i": i, "j": j, "km": round(km, 1),
                            "ore_intre_varfuri": round(dh, 1),
                            "corelatie": round(r, 2),
                            "varf_comun": pi})

    # grupurile rezultate
    grupuri: dict[int, list[int]] = {}
    for i in range(n):
        grupuri.setdefault(find(i), []).append(i)

    out = []
    for membri in grupuri.values():
        if len(membri) < 2:
            continue
        leg = [p for p in perechi if p["i"] in membri and p["j"] in membri]
        out.append({
            "membri": sorted(membri),
            "focare": [fires[m]["rank"] for m in sorted(membri)],
            "n": len(membri),
            "varf_comun": leg[0]["varf_comun"] if leg else None,
            "corelatie_min": min(p["corelatie"] for p in leg) if leg else None,
            "distanta_max_km": max(p["km"] for p in leg) if leg else None,
            "ore_intre_varfuri_max": max(p["ore_intre_varfuri"] for p in leg) if leg else None,
        })
    out.sort(key=lambda g: -g["n"])
    return out


def assign_points(fires: list[dict], dets: list[dict]) -> list[list[dict]]:
    """Atribuie fiecare detectie focarului cel mai apropiat.

    Nu folosim gruparea interna a lui `cluster`, pentru ca aici vrem o
    impartire explicita: fiecare punct merge la EXACT un focar (cel mai
    apropiat), ca seriile zilnice sa fie complete si sa nu se suprapuna.
    """
    out: list[list[dict]] = [[] for _ in fires]
    for d in dets:
        best, best_km = None, float("inf")
        for k, f in enumerate(fires):
            km = _haversine(d["lat"], d["lon"], f["lat"], f["lon"])
            if km < best_km:
                best, best_km = k, km
        if best is not None:
            out[best].append(d)
    return out


def attach(fires: list[dict], groups: list[dict]) -> None:
    """Scrie pe fiecare focar un camp `sync` (None sau descrierea grupului).

    Aditiv: nu sterge si nu modifica alte campuri, deci interfata si
    raportul continua sa functioneze daca ignoră `sync`.
    """
    for g in groups:
        for idx in g["membri"]:
            if 0 <= idx < len(fires):
                fires[idx]["sync"] = {
                    "grup": g["focare"],
                    "n": g["n"],
                    "varf_comun": g["varf_comun"],
                    "corelatie": g["corelatie_min"],
                    "distanta_max_km": g["distanta_max_km"],
                    "ore_intre_varfuri": g["ore_intre_varfuri_max"],
                }
    for f in fires:
        f.setdefault("sync", None)


def main() -> int:
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))

    state_file = root / "state" / "state.json"
    if not state_file.exists():
        print("lipseste state/state.json")
        return 1
    st = json.loads(state_file.read_text(encoding="utf-8"))
    zone = st["zones"][list(st["zones"])[0]]

    import fires as F

    zcfg = json.loads((root / "zones.json").read_text(encoding="utf-8"))["zones"][0]
    parks = []
    for p in zcfg.get("parks") or []:
        gp = root / "web" / (p.get("geojson") or "")
        if gp.exists():
            parks.append({"name": p.get("name"),
                          "_geom": json.loads(gp.read_text(encoding="utf-8"))})

    dets = list(zone["detections"].values())
    fires = F.find_fires(zone["detections"], zcfg, parks=parks)
    print(f"focare: {len(fires)}")
    for f in fires:
        print(f"  #{f['rank']} {f['lat']:.4f},{f['lon']:.4f}  {f['points']}p  {f['days']} zile")

    grupuri = find_sync_groups(fires, assign_points(fires, dets))
    attach(fires, grupuri)
    print(f"\nfocare sincrone: {len(grupuri)} grup(uri)")
    for g in grupuri:
        print(f"  focare {g['focare']}  vârf comun {g['varf_comun']}  "
              f"r>={g['corelatie_min']}  max {g['distanta_max_km']} km  "
              f"vârfuri la {g['ore_intre_varfuri_max']} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
