#!/usr/bin/env python3
"""
Harta termica Sentinel-3 SLSTR.

DE CE: Sentinel-2 (true color, 60 m) trece la 5 zile si doar ziua - in 8 zile
avem 3 scene, una cu 60% nori. Sentinel-3 SLSTR trece de 2-4 ori pe zi, are
canal termic (MWIR) si prinde 75% din detectii NOAPTEA, cand focul e cel mai
vizibil (fara lumina solara care mascheaza semnalul).

Rezultatul: o imagine termica pe fiecare trecere, colorata dupa temperatura
de radiatie (bt_k) si marimea punctului dupa FRP. Asta arata FRONTUL ACTIV,
nu cicatricea - complementar fata de dNBR-ul din S2.

DATELE: produsele SL_2_FRP contin CSV-uri (nu NetCDF - nu avem nevoie de
xarray/netCDF4). Trei scheme:
  - MWIR1km-standard / alternative: canal termic 1 km, praguri diferite
  - SWIR500m: 500 m, cea mai fina rezolutie
Fiecare rand: lat(deg), lon(deg), FRP(MW), confidence(%), D/N, MWIR_BT(K),
              day, time, satellite

CAPCANA: aceleasi pixeli apar in toate 3 schemele (acelasi eveniment fizic
vazut prin filtre diferite). Daca le desenam pe toate suprapus, umflam
artificial numarul. Implicit folosim DOAR standard + SWIR500m, si marcam
sursa fiecarui punct ca sa nu induc in eroare.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Numele produsului: S3A_SL_2_FRP____20260930T084722_20260930T085022_...
# Prima data din nume = inceputul achizitiei. split("_") NU merge (sunt
# underscore-uri repetate inaintea datei), deci regex.
STAMP_RE = re.compile(r"(\d{8})T(\d{6})")

ROOT = Path("/home/lili/projects/domogled-live")
FRP_CACHE = ROOT / "live" / "state" / "frp"
IMG_DIR = ROOT / "live" / "web" / "img"
STATE_DIR = ROOT / "live" / "web" / "state"
STATE = ROOT / "live" / "state"
INDEX = STATE_DIR / "s3_thermal.json"
ZONES_FILE = ROOT / "live" / "zones.json"

# schemele pe care le desenam. alternative e duplicatul standard-ului cu alt
# filtru -> il sari ca sa nu dublam punctele.
USE_SCHEMES = {
    "SWIR500m": "FRP_SWIR500m.csv",
    "MWIR1km-standard": "FRP_MWIR1km_standard.csv",
}


def _color_for(bt_k: float, frp: float, scheme: str = "") -> tuple[int, int, int, int]:
    """Culoare dupa temperatura de radiatie, cu FRP ca rezerva.

    Praguri pe datele reale: fundal ~300K, detectii 308-330K, varfuri peste
    350K (376K = 103C la suprafata, arsura activa).

    ATENTIE: FRP_SWIR500m.csv NU are coloana MWIR_BT(K) - e alt senzor (SWIR,
    nu MWIR termic). Pentru acele randuri bt=0, deci culoarea se ia din FRP,
    cu un decalaj ca sa nu para ca un foc rece. Le marcam vizual diferit
    (contur punctat) ca sa nu induc in eroare ca ar avea temperatura masurata.
    """
    has_bt = (bt_k or 0) > 200
    t = bt_k if has_bt else 300 + min(60, (frp or 0) * 1.5)
    if t < 315:
        return (250, 204, 21, 120)      # galben - caldut, poate fals
    if t < 325:
        return (249, 115, 22, 190)      # portocaliu - foc activ
    if t < 335:
        return (220, 38, 38, 225)       # rosu - foc puternic
    if t < 350:
        return (157, 23, 77, 240)       # visiniu - intens
    return (255, 255, 255, 255)         # alb - extrem (>350K = 77C la sol)


def _radius(frp: float) -> float:
    return max(2.5, min(20, 2.5 + float(np.sqrt(max(frp or 0, 0))) * 0.9))


def read_products(zone_bbox: tuple[float, float, float, float],
                  limit: int | None = None) -> list[dict]:
    """Citeste toate produsele SEN3 din cache, filtreaza pe zona.

    Returneaza o lista de 'treceri' (o intrare per produs), fiecare cu
    punctele ei. Sortate cronologic.
    """
    if not FRP_CACHE.exists():
        return []

    w, s, e, n = zone_bbox
    passes: dict[str, dict] = {}

    for zp in sorted(FRP_CACHE.glob("*.zip")):
        name = zp.stem
        try:
            with zipfile.ZipFile(zp) as z:
                for scheme, member in USE_SCHEMES.items():
                    fname = next((m for m in z.namelist() if m.endswith(member)), None)
                    if not fname:
                        continue
                    text = z.read(fname).decode("utf-8", "replace")
                    lines = [l for l in text.splitlines()
                             if l.strip() and not l.startswith("#")]
                    for row in csv.DictReader(io.StringIO("\n".join(lines))):
                        try:
                            lat = float(row["lat(deg)"])
                            lon = float(row["lon(deg)"])
                        except (KeyError, ValueError):
                            continue
                        if not (s <= lat <= n and w <= lon <= e):
                            continue

                        def num(k):
                            try:
                                return float(row.get(k) or 0)
                            except ValueError:
                                return 0.0

                        # identificam trecerea dupa numele produsului
                        key = name
                        p = passes.setdefault(key, {
                            "id": name, "scheme_pts": {},
                            "date": row.get("day"), "time": row.get("time"),
                            "dn": row.get("D/N"), "sat": row.get("satellite"),
                            "points": [],
                        })
                        p["points"].append({
                            "lat": lat, "lon": lon,
                            "frp": round(num("FRP(MW)"), 2),
                            "bt": round(num("MWIR_BT(K)"), 1),
                            "conf": round(num("confidence(%)")
                                         or num("SWIR_SAA_confidence(%)")),
                            "scheme": scheme,
                        })
        except (zipfile.BadZipFile, OSError, KeyError):
            continue

    out = [p for p in passes.values() if p["points"]]
    # ordonare cronologica crescatoare (numele produsului incepe cu data)
    out.sort(key=lambda p: p["id"])
    return out[-limit:] if limit else out


def render_pass(p: dict, bbox: tuple[float, float, float, float],
                size: tuple[int, int] = (700, 560)) -> Image.Image:
    """Deseneaza o trecere SLSTR ca harta termica pe fundal inchis."""
    w, s, e, n = bbox
    W, H = size
    img = Image.new("RGBA", (W, H), (12, 16, 22, 255))
    dr = ImageDraw.Draw(img, "RGBA")

    # grila subtire ca sa se vada scara
    for i in range(1, 5):
        x = W * i / 5
        y = H * i / 5
        dr.line([(x, 0), (x, H)], fill=(38, 48, 61, 255), width=1)
        dr.line([(0, y), (W, y)], fill=(38, 48, 61, 255), width=1)

    if not p["points"]:
        return img

    def xy(lat, lon):
        x = (lon - w) / (e - w) * W
        y = (n - lat) / (n - s) * H
        return x, y

    # desenam de la rece la cald, ca punctele fierbinti sa fie deasupra
    for pt in sorted(p["points"], key=lambda q: (q["bt"] or 0, q["frp"] or 0)):
        x, y = xy(pt["lat"], pt["lon"])
        r = _radius(pt["frp"])
        col = _color_for(pt["bt"], pt["frp"], pt.get("scheme", ""))
        # halo difuz = efect termic
        dr.ellipse([x - r * 2.1, y - r * 2.1, x + r * 2.1, y + r * 2.1],
                   fill=(col[0], col[1], col[2], max(18, col[3] // 6)))
        # SWIR500m nu are temperatura masurata -> contur punctat, ca sa nu
        # para ca avem BT acolo unde de fapt nu avem
        has_bt = (pt.get("bt") or 0) > 200
        dr.ellipse([x - r, y - r, x + r, y + r], fill=col,
                   outline=(255, 255, 255, 170), width=2 if has_bt else 1)

    # etichete cu metadatele trecerii
    dt = f"{p['date']} {p['time']}Z" if p.get("date") else p["id"][:22]
    dn = {"D": "zi", "N": "noapte"}.get(str(p.get("dn") or ""), "?")
    dr.rectangle([0, 0, W, 30], fill=(10, 14, 19, 235))
    dr.text((8, 8), f"Sentinel-3 SLSTR  |  {dt}  |  {dn}  |  "
                    f"{len(p['points'])} pixeli termici",
            fill=(230, 237, 243, 255))

    frp_max = max((q["frp"] or 0) for q in p["points"])
    bt_max = max((q["bt"] or 0) for q in p["points"])
    dr.rectangle([0, H - 26, W, H], fill=(10, 14, 19, 235))
    dr.text((8, H - 20), f"FRP max {frp_max:.1f} MW  |  "
                         f"BT max {bt_max:.0f} K ({bt_max - 273.15:.0f}C)  |  "
                         f"satelit {p.get('sat','S3')}",
            fill=(139, 152, 165, 255))

    return img


def build_index(bbox: tuple[float, float, float, float],
                zone_id: str = "domogled") -> list[dict]:
    """Genereaza imaginile pentru toate trecerile si un index JSON.

    Indexul e folosit de timeline-ul din dashboard ca sa stie ce imagini sa
    afiseze pentru fiecare data selectata.
    """
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    passes = read_products(bbox)
    if not passes:
        return []

    index = []
    for p in passes:
        # numele imaginii: scurt si fara caractere problematice
        short = p["id"].replace(".SEN3", "")
        outp = IMG_DIR / f"s3_{zone_id}_{short}.png"
        img = render_pass(p, bbox)
        img.convert("RGB").save(outp, "PNG", optimize=True)

        # ora reala a achizitiei, din numele produsului (ex. 20260930T084722)
        t_iso = None
        m = STAMP_RE.search(short)
        if m:
            try:
                t_iso = datetime.strptime(f"{m.group(1)}T{m.group(2)}",
                                          "%Y%m%dT%H%M%S").replace(
                    tzinfo=timezone.utc).isoformat(timespec="seconds")
            except ValueError:
                t_iso = None

        # punctele cu temperatura masurata (MWIR) vs doar SWIR
        n_bt = sum(1 for q in p["points"] if (q.get("bt") or 0) > 200)

        index.append({
            "file": outp.name,
            "id": p["id"],
            "datetime": t_iso,
            "date": (t_iso or "")[:10] or p.get("date"),
            "time": p.get("time"),
            "daynight": p.get("dn"),
            "points": len(p["points"]),
            "points_bt": n_bt,
            "frp_max": max((q["frp"] or 0) for q in p["points"]),
            "frp_sum": round(sum((q["frp"] or 0) for q in p["points"]), 1),
            "bt_max": max(((q["bt"] or 0) for q in p["points"]), default=0),
            "sat": p.get("sat"),
            "bytes": outp.stat().st_size,
        })

    return index


def main(zone_list: list | None = None) -> int:
    """Regenereaza toate hartile termice + indexul JSON.

    Apelata din collector.py (refresh_imagery) ca imaginile sa nu mai ramana
    in urma cu zile. Daca zone_list e dat, il folosim direct (evita o citire
    in plus si tine zona sincronizata cu collectorul).
    """
    if zone_list is None:
        cfg = json.loads(ZONES_FILE.read_text(encoding="utf-8"))
        zone_list = cfg.get("zones") or cfg.get("zone_list") or []

    index_all: dict[str, list] = {}
    n_img = 0
    for z in zone_list:
        zid, bbox = z.get("id"), z.get("bbox")
        if not (zid and bbox):
            continue
        idx = build_index(tuple(bbox), zid)
        index_all[zid] = idx
        n_img += len(idx)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index_all, indent=2, ensure_ascii=False),
                     encoding="utf-8")
    print(f"scris {INDEX} — {n_img} harti termice ({len(index_all)} zone)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
