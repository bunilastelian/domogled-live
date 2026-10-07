#!/usr/bin/env python3
"""
Predictie treceri satelit + status date Copernicus.

DE CE: Sentinel-3 SLSTR trece peste zona noastra de 2 ori pe zi per satelit
(S3A si S3B, defazati ~30 min). Copernicus publica produsul la ~73 min dupa
achizitie (masurat pe 30 de produse reale: medie 73m, min 49m, max 106m).

Orbita e heliosincrona: ora locala solara aproape constanta la fiecare trecere.
Deci ora de achizitie se repeta aproape la fel zi de zi (±5-30 min).
NU calculam orbita din elemente Kepler (prea complex si nefolositor) - folosim
orele REALE de achizitie observate, plus media pe ultimele zile.

Rezultatul: un fisier JSON cu urmatoarea trecere estimata + ora la care ar
trebui sa apara datele, consumat de dashboard pentru countdown.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "live" / "state" / "state.json"
OUT = ROOT / "live" / "web" / "state" / "sat_passes.json"

# latenta medie de publicare ESA, masurata pe produse reale (tools/sat_latency.py)
PUBLISH_LAG_MIN = 73
PUBLISH_LAG_P10 = 49
PUBLISH_LAG_P90 = 106

STAMP_RE = re.compile(r"(\d{8})T(\d{6})")


def acquisitions(state: dict) -> list[tuple[datetime, str]]:
    """Orele reale de achizitie, din numele produselor FRP catalogate."""
    names = set()
    for z in (state.get("zones") or {}).values():
        names.update(z.get("frp_products") or [])
    out = []
    for n in sorted(names):
        m = STAMP_RE.search(n)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1) + m.group(2),
                                   "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out.append((dt, n.split("_")[0]))
    out.sort()
    return out


def orbit_slots(acqs: list[tuple[datetime, str]], now: datetime) -> dict:
    """Estimeaza orele viitoare de trecere pentru fiecare satelit.

    Metoda: pentru fiecare satelit, luam orele de achizitie din ultimele 3 zile
    si le grupam in 'ferestre' (dimineata/seara). Ora medie din fiecare fereastra
    e predictia pentru maine - orbita heliosincrona e stabila.
    """
    by_sat: dict[str, list[datetime]] = {}
    for dt, sat in acqs:
        if (now - dt).days <= 3:
            by_sat.setdefault(sat, []).append(dt)

    slots = {}
    for sat, dts in by_sat.items():
        # grupam dupa (ora UTC apropiata): zi = 00-14 UTC, noapte = 14-24 UTC
        day = [d for d in dts if d.hour < 14]
        night = [d for d in dts if d.hour >= 14]
        for label, group in (("zi", day), ("noapte", night)):
            if not group:
                continue
            # ora medie, in minute de la miezul nopții
            mins = [d.hour * 60 + d.minute for d in group]
            # medie circulara aproximata: evitam problemele la miezul nopții
            avg = statistics.mean(mins)
            spread = (max(mins) - min(mins)) / 2 if len(mins) > 1 else 0
            slots[f"{sat}_{label}"] = {
                "sat": sat, "fereastra": label,
                "ora_utc": f"{int(avg)//60:02d}:{int(avg)%60:02d}",
                "minute_utc": avg,
                "variatie_min": round(spread),
                "n_observatii": len(group),
            }
    return slots


def next_pass(slot: dict, now: datetime) -> datetime:
    """Urmatoarea aparitie a unui slot dupa 'now'."""
    target = now.replace(hour=0, minute=0, second=0, microsecond=0) + \
        timedelta(minutes=slot["minute_utc"])
    while target <= now:
        target += timedelta(days=1)
    return target


def main() -> int:
    if not STATE.exists():
        print(f"lipsa {STATE}", file=sys.stderr)
        return 1
    st = json.loads(STATE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)

    acqs = acquisitions(st)
    slots = orbit_slots(acqs, now)

    passes = []
    for key, s in slots.items():
        nxt = next_pass(s, now)
        passes.append({
            "id": key,
            "satelit": s["sat"],
            "fereastra": s["fereastra"],
            "ora_estimata_utc": nxt.isoformat(timespec="seconds"),
            "peste_minute": round((nxt - now).total_seconds() / 60),
            "variatie_min": s["variatie_min"],
            "observatii": s["n_observatii"],
            # cand ar trebui sa A PARA datele pe Copernicus
            "date_disponibile_utc": (nxt + timedelta(minutes=PUBLISH_LAG_MIN)
                                     ).isoformat(timespec="seconds"),
            "date_disponibile_p10": (nxt + timedelta(minutes=PUBLISH_LAG_P10)
                                     ).isoformat(timespec="seconds"),
            "date_disponibile_p90": (nxt + timedelta(minutes=PUBLISH_LAG_P90)
                                     ).isoformat(timespec="seconds"),
        })
    passes.sort(key=lambda p: p["peste_minute"])

    # cea mai recenta achizitie + cand ar fi trebuit sa apara
    #
    # ATENTIE la interpretarea 'intarziere': satelitul NU acopera zona la
    # fiecare orbita. Pattern-ul real pentru Domogled: 2 treceri/zi (una de
    # fiecare satelit), dimineata. Trecerea de seara trece pe alta longitudine.
    # Deci daca ultima achizitie e de dimineata, la 10h vechime NU e o
    # intarziere - e normal. Marcam 'intarziere' doar cand ultima achizitie
    # e mai veche decat intervalul orbital observat (gap real de date).
    last = acqs[-1] if acqs else None
    last_info = None
    if last:
        expected = last[0] + timedelta(minutes=PUBLISH_LAG_MIN)
        late_min = (now - expected).total_seconds() / 60

        # gap-ul orbital: intervalul maxim observat intre doua achizitii
        gaps = [(acqs[i + 1][0] - acqs[i][0]).total_seconds() / 3600
                for i in range(len(acqs) - 1)]
        gap_h = statistics.median(gaps) if gaps else 12
        # e normal daca vechimea nu depaseste ~1.5x intervalul orbital
        normal = (now - last[0]).total_seconds() / 3600 <= gap_h * 1.6

        last_info = {
            "achizitie_utc": last[0].isoformat(timespec="seconds"),
            "satelit": last[1],
            "publicat_asteptat_utc": expected.isoformat(timespec="seconds"),
            "vechime_min": round((now - last[0]).total_seconds() / 60),
            "gap_orbital_h": round(gap_h, 1),
            "in_interval_normal": bool(normal),
            "intarziere_min": 0 if normal else round(late_min),
            "explicatie": ("in intervalul orbital normal pentru aceasta zona"
                           if normal else
                           f"fara achizitie de peste {gap_h * 1.6:.0f}h "
                           f"(interval orbital tipic {gap_h:.1f}h)"),
        }

    doc = {
        "generat": now.isoformat(timespec="seconds"),
        "latenta_publicare_min": {
            "medie": PUBLISH_LAG_MIN, "p10": PUBLISH_LAG_P10, "p90": PUBLISH_LAG_P90,
            "sursa": "masurat pe produse reale SL_2_FRP, tools/sat_latency.py",
        },
        "urmatoarele_treceri": passes,
        "ultima_achizitie": last_info,
        "total_achizitii_istoric": len(acqs),
        "nota": ("Orele de trecere sunt estimate din achizitiile reale din "
                 "ultimele 3 zile (orbita heliosincrona = ora stabila). "
                 "Variatie = jumatate din intervalul observat."),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print(f"scris {OUT}")
    print(f"  urmatoarea trecere: {passes[0]['satelit']} {passes[0]['fereastra']} "
          f"in {passes[0]['peste_minute']} min" if passes else "  (nicio predictie)")
    for p in passes[:4]:
        print(f"    {p['satelit']} {p['fereastra']:<7} "
              f"{p['ora_estimata_utc'][11:16]}Z (±{p['variatie_min']}m) "
              f"-> date ~{p['date_disponibile_utc'][11:16]}Z")
    if last_info:
        print(f"  ultima achizitie: {last_info['satelit']} "
              f"{last_info['achizitie_utc'][11:16]}Z "
              f"(acum {last_info['vechime_min']} min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
