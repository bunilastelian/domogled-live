#!/usr/bin/env python
"""
figura_dovada.py - figura finala cu dovezile, pentru materialul jurnalistic.

Panou A: Sentinel-2 true color, 4 oct 2026, 22 km, cu detectiile termice si reperele numite
Panou B: seria FRP pe zile (ESA SLSTR + NASA FIRMS)
Panou C: detaliu 10 m cu pana de fum
Panou D: cicatricea de arsura (dNBR corectat)
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from pyproj import Transformer
from matplotlib.lines import Line2D

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cdse import CDSE  # noqa: E402
from s2_fire import find_scene  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
FIRE = (44.8982, 22.4785)
POST = "2026-10-04"

PLACES = {
    "Băile Herculane": (44.8785, 22.4144, "s"),
    "Pecinișca": (44.8712, 22.4187, "s"),
    "Cascada Cociului": (44.91838, 22.46485, "*"),
    "Colțu Pietrii\n(1228 m)": (44.90550, 22.48217, "^"),
    "Domogledul Mare\n(1105 m)": (44.87161, 22.44364, "^"),
    "Domogledul Mic": (44.87658, 22.43856, "^"),
}


def load_geo(date: str, key: str, box_km: float):
    client = CDSE()
    feat = find_scene(client, (FIRE[1], FIRE[0]), date)
    a = feat["assets"][key]
    sx, _, ox, _, sy, oy = a["proj:transform"][:6]
    epsg = int(str(a["proj:code"]).split(":")[1])
    return {"sx": sx, "sy": sy, "ox": ox, "oy": oy, "epsg": epsg, "id": feat["id"]}


def to_px(lat, lon, g, size, box_km):
    conv = Transformer.from_crs("EPSG:4326", f"EPSG:{g['epsg']}", always_xy=True)
    x, y = conv.transform(lon, lat)
    # centrul imaginii = FIRE
    fx, fy = conv.transform(FIRE[1], FIRE[0])
    px = (x - fx) / g["sx"] + size / 2
    py = (y - fy) / g["sy"] + size / 2
    return px, py


def main() -> int:
    with open(OUT / "detections.csv", encoding="utf-8") as fh:
        firms = list(csv.DictReader(fh))
    with open(OUT / "esa_frp_detections.csv", encoding="utf-8") as fh:
        esa = list(csv.DictReader(fh))

    fig = plt.figure(figsize=(20, 15), facecolor="white")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.15, 1], hspace=0.16, wspace=0.13)

    # ---------------- A: true color 22 km
    p22 = OUT / f"s2_{POST.replace('-','')}_post_TCI_10m_crop.png"
    g10 = load_geo(POST, "TCI_10m", 22.0)
    ax = fig.add_subplot(gs[0, 0])
    if p22.exists():
        with Image.open(p22) as im:
            img = im.convert("RGB")
        side = img.size[0]
        ax.imshow(img)
        for r in firms:
            px, py = to_px(float(r["lat"]), float(r["lon"]), g10, side, 22.0)
            if 0 <= px < side and 0 <= py < side:
                ax.plot(px, py, "o", ms=3, mfc="none", mec="#111111", mew=0.7, alpha=0.8)
        for r in esa:
            px, py = to_px(float(r["lat"]), float(r["lon"]), g10, side, 22.0)
            if 0 <= px < side and 0 <= py < side:
                ax.plot(px, py, "o", ms=11, mfc="none", mec="#00b0ff", mew=1.7, alpha=0.95)
        for name, (lat, lon, mk) in PLACES.items():
            px, py = to_px(lat, lon, g10, side, 22.0)
            ax.plot(px, py, mk, color="#d81b60", ms=13, zorder=6)
            ax.annotate(name, (px, py), textcoords="offset points", xytext=(9, 6),
                        fontsize=9.5, color="#111111", zorder=7,
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.75, ec="none"))
        ax.set_title("A. Sentinel-2, 4 octombrie 2026, 09:31 UTC (11:31 ora locală) — 22 × 22 km la 10 m\n"
                     "masa albă = pană de fum (Sen2Cor clasifică doar 1,1% drept nor) · "
                     "cerc negru = detecție NASA FIRMS · cerc albastru = detecție ESA SLSTR FRP",
                     fontsize=11.5)
        ax.set_xticks([]); ax.set_yticks([])

    # ---------------- C: detaliu fum 6 km
    ax = fig.add_subplot(gs[0, 1])
    det = OUT / "detaliu_10m_6km_2026-10-04.png"
    if det.exists():
        with Image.open(det) as im:
            ax.imshow(im)
        ax.set_title("C. Detaliu 6 × 6 km la 10 m, centrat pe focar\n"
                     "pana de fum umple valea Cernei și versanții Domogledului",
                     fontsize=11.5)
        ax.set_xticks([]); ax.set_yticks([])

    # ---------------- B: seria FRP
    ax = fig.add_subplot(gs[1, 0])
    e_by_day: dict[str, float] = defaultdict(float)
    e_cnt: dict[str, int] = defaultdict(int)
    for r in esa:
        e_by_day[r["date"]] += float(r["frp_mw"] or 0)
        e_cnt[r["date"]] += 1
    f_by_day: dict[str, float] = defaultdict(float)
    f_cnt: dict[str, int] = defaultdict(int)
    for r in firms:
        f_by_day[r["date"]] += float(r["frp_mw"] or 0)
        f_cnt[r["date"]] += 1

    days = sorted(set(e_by_day) | set(f_by_day))
    x = np.arange(len(days))
    ax.bar(x - 0.21, [f_by_day.get(d, 0) for d in days], 0.42,
           label="NASA FIRMS (VIIRS 375 m + MODIS) — FRP cumulat", color="#ef6c00")
    ax.bar(x + 0.21, [e_by_day.get(d, 0) for d in days], 0.42,
           label="ESA Sentinel-3 SLSTR FRP — FRP cumulat", color="#1565c0")
    for i, d in enumerate(days):
        ax.text(i, max(f_by_day.get(d, 0), e_by_day.get(d, 0)) + 25,
                f"{f_cnt.get(d,0)}/{e_cnt.get(d,0)}", ha="center", fontsize=8.5, color="#333333")
    ax.set_xticks(x)
    ax.set_xticklabels([d[5:] for d in days], fontsize=10)
    ax.set_ylabel("FRP cumulat (MW)", fontsize=11)
    ax.set_xlabel("data (2026)   —   sub fiecare bară: număr de detecții NASA / ESA", fontsize=10)
    ax.set_title("B. Intensitatea incendiului, măsurată independent de două sisteme\n"
                 "vârful: 5–6 octombrie, exact perioada indicată de IGSU ca risc maxim",
                 fontsize=11.5)
    ax.legend(fontsize=9.5, loc="upper left")
    ax.grid(axis="y", alpha=0.3, ls=":")
    ax.axvspan(days.index("2026-10-05") - 0.5, days.index("2026-10-06") + 0.5,
               color="#ffcdd2", alpha=0.5, zorder=0)

    # ---------------- D: cicatricea
    ax = fig.add_subplot(gs[1, 1])
    sm = OUT / "burn_summary.json"
    d_img = OUT / "detalii_arsura.png"
    if d_img.exists():
        with Image.open(d_img) as im:
            ax.imshow(im)
        ax.set_title("D. Cicatricea de incendiu: dNBR corectat cu fundalul (seceta eliminată)\n"
                     "estimare independentă: 230–600 ha afectate, în funcție de prag",
                     fontsize=11.5)
        ax.set_xticks([]); ax.set_yticks([])

    fig.suptitle("Domogled – Valea Cernei: ce arată datele satelitare\n"
                 "analiză pe date originale Copernicus (Sentinel-1/2/3) și NASA FIRMS, 23 septembrie – 7 octombrie 2026",
                 fontsize=15, y=0.985)
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    out = OUT / "FIGURA_DOOVADA.png"
    fig.savefig(out, dpi=92)
    print(f"Scris: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
