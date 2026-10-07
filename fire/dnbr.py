#!/usr/bin/env python
"""
dnbr.py - estimarea suprafetei arse prin diferenta de NBR (dNBR) din Sentinel-2.

Metoda (standard USGS / Key & Benson):
   NBR  = (NIR - SWIR2) / (NIR + SWIR2)      -> B8A si B12, ambele la 20 m
   dNBR = NBR_inainte - NBR_dupa              -> cat de mult a scazut vegetatia
Clase de severitate (dNBR):
   < 0.10  nears / regenerat
   0.10-0.27  severitate scazuta
   0.27-0.44  moderat-scazuta
   0.44-0.66  moderat-ridicata
   > 0.66  ridicata

Atentie: scena din 4 octombrie are o pana activa de fum deasupra zonei, ceea ce poate
influenta NBR. Rezultatul e o estimare, nu o delimitare oficiala.

Rulare: python dnbr.py
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from cdse import CDSE  # noqa: E402
from s2_fire import find_scene  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache" / "s2"
OUT = HERE / "out"

FIRE = (44.8982, 22.4785)
BOX_KM = 22.0
DATES = {"pre": "2026-08-02", "post": "2026-10-04"}

CLASSES = [
    (0.00, 0.10, "nears / regenerat", "#2e7d32"),
    (0.10, 0.27, "severitate scăzută", "#fdd835"),
    (0.27, 0.44, "moderat-scăzută", "#fb8c00"),
    (0.44, 0.66, "moderat-ridicată", "#e53935"),
    (0.66, 9.99, "ridicată", "#6a1b9a"),
]


def band_path(scene_id: str, key: str) -> Path | None:
    hits = [p for p in CACHE.glob(f"{scene_id}_{key}.*") if p.suffix.lower() in (".jp2", ".tif", ".tiff")]
    return hits[0] if hits else None


def crop_window(client: CDSE, date: str) -> tuple[tuple[int, int, int, int], dict]:
    """Aceeasi fereastra de pixeli folosita de s2_fire.py (20 m)."""
    feat = find_scene(client, (FIRE[1], FIRE[0]), date)
    asset = feat["assets"]["B8A_20m"]
    tr = asset["proj:transform"]
    code = int(str(asset["proj:code"]).split(":")[1])
    sx, _, ox, _, sy, oy = tr[:6]
    conv = Transformer.from_crs("EPSG:4326", f"EPSG:{code}", always_xy=True)
    x, y = conv.transform(FIRE[1], FIRE[0])
    half = BOX_KM * 1000 / 2
    win = (int(round((x - half - ox) / sx)), int(round((y + half - oy) / sy)),
           int(round((x + half - ox) / sx)), int(round((y - half - oy) / sy)))
    return win, {"sx": sx, "sy": sy, "ox": ox, "oy": oy, "epsg": code, "id": feat["id"],
                 "datetime": feat["properties"]["datetime"], "cloud": feat["properties"].get("eo:cloud_cover")}


def load_crop(scene_id: str, key: str, win) -> np.ndarray:
    p = band_path(scene_id, key)
    if p is None:
        raise SystemExit(f"Lipseste banda {key} pentru {scene_id} - ruleaza s2_fire.py")
    with Image.open(p) as im:
        return np.array(im.crop(win)).astype("float32")


def latlon_to_px(lat: float, lon: float, meta: dict, win) -> tuple[float, float]:
    conv = Transformer.from_crs("EPSG:4326", f"EPSG:{meta['epsg']}", always_xy=True)
    x, y = conv.transform(lon, lat)
    px = (x - meta["ox"]) / meta["sx"] - win[0]
    py = (y - meta["oy"]) / meta["sy"] - win[1]
    return px, py


def main() -> int:
    client = CDSE()
    data = {}
    for tag, date in DATES.items():
        win, meta = crop_window(client, date)
        nir = load_crop(meta["id"], "B8A_20m", win)
        swir = load_crop(meta["id"], "B12_20m", win)
        if nir.shape != swir.shape:
            raise SystemExit(f"Forme diferite la {tag}: {nir.shape} vs {swir.shape}")
        nbr = np.where(nir + swir > 0, (nir - swir) / (nir + swir), np.nan)
        data[tag] = {"win": win, "meta": meta, "nir": nir, "swir": swir, "nbr": nbr}
        print(f"{tag:<5} {date}  {meta['id']}")
        print(f"      nori {meta['cloud']}%  crop {nir.shape[1]}x{nir.shape[0]} px @20m "
              f"= {nir.shape[1]*20/1000:.1f} x {nir.shape[0]*20/1000:.1f} km")

    dnbr = data["pre"]["nbr"] - data["post"]["nbr"]
    valid = np.isfinite(dnbr)
    px_area_ha = (20 * 20) / 10000.0     # 20 m x 20 m in hectare
    total_ha = valid.sum() * px_area_ha
    print(f"\n=== Suprafata analizata: {total_ha:,.0f} ha ({total_ha/100:.0f} km²) ===")
    print(f"{'clasa dNBR':<24}{'ha':>10}{'% din suprafata':>18}")
    print("-" * 52)
    burn_ha = 0.0
    for lo, hi, label, _c in CLASSES:
        n = int(((dnbr >= lo) & (dnbr < hi) & valid).sum())
        ha = n * px_area_ha
        if lo >= 0.10:
            burn_ha += ha
        print(f"{label:<24}{ha:>10,.0f}{100*ha/total_ha:>17.1f}%")
    print("-" * 52)
    print(f"{'TOTAL afectat (dNBR>=0.10)':<24}{burn_ha:>10,.0f} ha")

    # ---- figura
    with open(OUT / "detections.csv", encoding="utf-8") as fh:
        firms = list(csv.DictReader(fh))
    esa = []
    if (OUT / "esa_frp_detections.csv").exists():
        with open(OUT / "esa_frp_detections.csv", encoding="utf-8") as fh:
            esa = list(csv.DictReader(fh))

    fig, axes = plt.subplots(2, 2, figsize=(17, 16), facecolor="white")
    extent_km = (-BOX_KM / 2, BOX_KM / 2, -BOX_KM / 2, BOX_KM / 2)

    def overlay(ax, meta, win, size="firms"):
        conv_from = "EPSG:4326"
        for r in firms:
            px, py = latlon_to_px(float(r["lat"]), float(r["lon"]), meta, win)
            if -50 <= px < data["post"]["nir"].shape[1] + 50 and -50 <= py < data["post"]["nir"].shape[0] + 50:
                ax.plot(px, py, "o", ms=2.5, mfc="none", mec="black", mew=0.6, alpha=0.85)
        for r in esa:
            px, py = latlon_to_px(float(r["lat"]), float(r["lon"]), meta, win)
            if -50 <= px < data["post"]["nir"].shape[1] + 50 and -50 <= py < data["post"]["nir"].shape[0] + 50:
                ax.plot(px, py, "o", ms=9, mfc="none", mec="#00e5ff", mew=1.4, alpha=0.9)

    post_tci = None
    p = band_path(data["post"]["meta"]["id"], "TCI_20m")
    if p:
        with Image.open(p) as im:
            post_tci = np.array(im.crop(data["post"]["win"]))

    pre_tci = None
    p = band_path(data["pre"]["meta"]["id"], "TCI_20m")
    if p:
        with Image.open(p) as im:
            pre_tci = np.array(im.crop(data["pre"]["win"]))

    ax = axes[0][0]
    ax.imshow(data["pre"]["nbr"], cmap="gray", vmin=-0.2, vmax=1.0)
    ax.set_title(f"NBR înainte de incendiu — {DATES['pre']}\n(alb = vegetație sănătoasă)", fontsize=11)

    ax = axes[0][1]
    ax.imshow(data["post"]["nbr"], cmap="gray", vmin=-0.2, vmax=1.0)
    ax.set_title(f"NBR după incendiu — {DATES['post']}\n(petecile negre = suprafață arsă)", fontsize=11)
    overlay(ax, data["post"]["meta"], data["post"]["win"])

    ax = axes[1][0]
    rgb = np.zeros(dnbr.shape + (3,))
    cmap_colors = [(0.18, 0.49, 0.20), (0.99, 0.85, 0.21), (0.98, 0.55, 0.00),
                   (0.90, 0.22, 0.21), (0.42, 0.10, 0.60)]
    for (lo, hi, _label, _c), col in zip(CLASSES, cmap_colors):
        m = (dnbr >= lo) & (dnbr < hi) & valid
        for k in range(3):
            rgb[..., k][m] = col[k]
    ax.imshow(rgb)
    ax.set_title(f"Hartă dNBR — suprafață afectată {burn_ha:,.0f} ha\n"
                 f"(galben = ars ușor, roșu = sever, violet = sever ridicat)", fontsize=11)
    overlay(ax, data["post"]["meta"], data["post"]["win"])

    ax = axes[1][1]
    if post_tci is not None:
        ax.imshow(post_tci)
        ax.set_title(f"True color {DATES['post']} (10 m) — pana de fum activă\n"
                     f"inele negru = detecție NASA FIRMS, albastru = detecție ESA SLSTR", fontsize=11)
        overlay(ax, data["post"]["meta"], data["post"]["win"])
        ax.set_xlim(0, post_tci.shape[1])
        ax.set_ylim(post_tci.shape[0], 0)
    else:
        ax.text(0.5, 0.5, "lipsește TCI_20m", ha="center")
    ax.set_xticks([])
    ax.set_yticks([])

    fig.suptitle("Parcul Național Domogled – Valea Cernei: analiză Sentinel-2\n"
                 f"scena {DATES['pre']} (înainte) vs {DATES['post']} (după), câmp de {BOX_KM:.0f} km",
                 fontsize=14, y=0.98)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out = OUT / "analiza_dnbr.png"
    fig.savefig(out, dpi=95)
    print(f"\nScris: {out}")

    with open(OUT / "dnbr_stats.json", "w", encoding="utf-8") as fh:
        json.dump({"total_ha": total_ha, "afectat_ha": burn_ha,
                   "clase": [{"clasa": c[2], "ha": float(((dnbr >= c[0]) & (dnbr < c[1]) & valid).sum() * px_area_ha)}
                             for c in CLASSES],
                   "scena_pre": data["pre"]["meta"]["id"], "scena_post": data["post"]["meta"]["id"]},
                  fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
