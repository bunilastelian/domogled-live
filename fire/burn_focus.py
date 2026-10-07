#!/usr/bin/env python
"""
burn_focus.py - vedere de detaliu si estimare corectata a suprafetei arse.

Problema cu dNBR brut: intre 2 august si 4 octombrie 2026 vegetatia din toata
regiunea a fost afectata de seceta (IGSU: "litiera, solul forestier si materialul
lemnos cazut sunt uscate in profunzime"). NBR scade regional din cauza secetei,
deci dNBR brut umfla suprafata "arsa".

Corectie: scadem din dNBR medianul zonei de fundal (departe de focar), ca sa
izolam semnalul local al incendiului.
"""

from __future__ import annotations

import csv
import json
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

FIRE = (44.8982, 22.4785)   # lat, lon
PRE, POST = "2026-08-02", "2026-10-04"
PIX = 20                    # m, benzile B8A/B12
HA_PER_PX = PIX * PIX / 10000


def band(scene_id: str, key: str) -> Path:
    for p in CACHE.glob(f"{scene_id}_{key}.*"):
        if p.suffix.lower() in (".jp2", ".tif"):
            return p
    raise SystemExit(f"lipseste {key} pentru {scene_id}")


def meta_for(client: CDSE, date: str, key: str = "B8A_20m"):
    feat = find_scene(client, (FIRE[1], FIRE[0]), date)
    a = feat["assets"][key]
    sx, _, ox, _, sy, oy = a["proj:transform"][:6]
    return {"id": feat["id"], "sx": sx, "sy": sy, "ox": ox, "oy": oy,
            "epsg": int(str(a["proj:code"]).split(":")[1]),
            "datetime": feat["properties"]["datetime"]}


def px_of(lat, lon, m):
    conv = Transformer.from_crs("EPSG:4326", f"EPSG:{m['epsg']}", always_xy=True)
    x, y = conv.transform(lon, lat)
    return (x - m["ox"]) / m["sx"], (y - m["oy"]) / m["sy"]


def main() -> int:
    client = CDSE()
    m_pre, m_post = meta_for(client, PRE), meta_for(client, POST)

    # fereastra de 22 km centrata pe focar (aceeasi folosita de s2_fire.py)
    BOX_KM = 22.0
    fx_full, fy_full = px_of(FIRE[0], FIRE[1], m_post)
    half = BOX_KM * 1000 / 2 / PIX
    win = (int(round(fx_full - half)), int(round(fy_full - half)),
           int(round(fx_full + half)), int(round(fy_full + half)))
    print(f"Fereastra de analiza: 22 x 22 km = {22*22*100:,.0f} ha")

    def load(meta, key):
        with Image.open(band(meta["id"], key)) as im:
            return np.array(im.crop(win)).astype("float32")

    nir_pre, swir_pre = load(m_pre, "B8A_20m"), load(m_pre, "B12_20m")
    nir_post, swir_post = load(m_post, "B8A_20m"), load(m_post, "B12_20m")

    nbr_pre = np.where(nir_pre + swir_pre > 0, (nir_pre - swir_pre) / (nir_pre + swir_pre), np.nan)
    nbr_post = np.where(nir_post + swir_post > 0, (nir_post - swir_post) / (nir_post + swir_post), np.nan)
    dnbr = nbr_pre - nbr_post
    valid = np.isfinite(dnbr)

    # masca de fundal: tot ce e la peste 8 km de focar (in acelasi tile)
    fx, fy = fx_full - win[0], fy_full - win[1]
    yy, xx = np.mgrid[0:dnbr.shape[0], 0:dnbr.shape[1]]
    dist_px = np.hypot(xx - fx, yy - fy) * PIX / 1000.0     # km
    background = valid & (dist_px > 8)

    bg_med = float(np.nanmedian(dnbr[background]))
    bg_mad = float(np.nanmedian(np.abs(dnbr[background] - bg_med)))
    print(f"Fundal (peste 8 km de focar): dNBR median = {bg_med:+.3f}, MAD = {bg_mad:.3f}")
    print("   -> seceta a scazut NBR regional cu", f"{bg_med:+.3f}", "; scadem asta din semnal.")

    dnbr_corr = dnbr - bg_med
    print(f"\n{'prag dNBR brut':<18}{'ha brut':>12}{'ha corectat':>14}")
    print("-" * 44)
    for thr in (0.10, 0.20, 0.27, 0.35, 0.44):
        raw = float(((dnbr >= thr) & valid).sum() * HA_PER_PX)
        cor = float(((dnbr_corr >= thr) & valid).sum() * HA_PER_PX)
        print(f"{'>= ' + str(thr):<18}{raw:>12,.0f}{cor:>14,.0f}")

    # zona de foc: <= 2 km de centru
    zone = valid & (dist_px <= 2)
    print(f"\nZona focarului (raza 2 km, {zone.sum()*HA_PER_PX:,.0f} ha):")
    print(f"   dNBR mediu {np.nanmean(dnbr[zone]):+.3f}  vs fundal {bg_med:+.3f}"
          f"   -> diferenta {np.nanmean(dnbr[zone])-bg_med:+.3f}")
    print(f"   NBR inainte {np.nanmean(nbr_pre[zone]):.3f} -> dupa {np.nanmean(nbr_post[zone]):.3f}")
    print(f"   in zona de foc, {100*((dnbr_corr>=0.27)&zone).sum()/max(zone.sum(),1):.0f}% din pixeli "
          f"au dNBR corectat >= 0.27")

    # ---------------- figuri de detaliu (10 m)
    # folosim cropul de 22 km deja georeferentiat, care e centrat pe focar
    crop22 = OUT / f"s2_{POST.replace('-', '')}_post_TCI_10m_crop.png"
    imgs = {}
    if crop22.exists():
        with Image.open(crop22) as im:
            big = im.convert("RGB")
        side = int(round(6 * 1000 / 10))               # 6 km la 10 m = 600 px
        cx, cy = big.size[0] // 2, big.size[1] // 2    # cropul de 22 km e centrat pe focar
        crop = big.crop((cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2))
        imgs["closeup"] = crop
        crop.resize((1000, 1000), Image.LANCZOS).save(OUT / "detaliu_10m_6km_2026-10-04.png")
        print(f"\nScris: {OUT / 'detaliu_10m_6km_2026-10-04.png'}  (6x6 km la 10 m, centrat pe focar)")
    else:
        print(f"[!] lipseste {crop22} - ruleaza s2_fire.py intai")

    # ---------------- figura finala cu doua panouri
    fig, axes = plt.subplots(1, 2, figsize=(19, 10), facecolor="white")

    # stanga: true color 22 km cu contur de arsura
    if "closeup" in imgs:
        ax = axes[0]
        ax.imshow(np.array(imgs["closeup"]))
        ax.set_title("Sentinel-2, 4 octombrie 2026, 09:31 UTC (11:31 local)\n"
                     "6 Ă— 6 km la 10 m â€” pana de fum deasupra zonei de incendiu", fontsize=12)
        ax.set_xticks([]); ax.set_yticks([])

    # dreapta: dNBR corectat, decupat pe aceeasi zona
    ax = axes[1]
    half_px = int(3 * 1000 / PIX)
    cxp, cyp = int(round(fx)), int(round(fy))
    sub = dnbr_corr[max(0, cyp - half_px):cyp + half_px, max(0, cxp - half_px):cxp + half_px]
    show = np.where(np.isfinite(sub), sub, 0)
    im = ax.imshow(show, cmap="RdYlGn_r", vmin=-0.2, vmax=0.8)
    ax.set_title("dNBR corectat cu fundalul (seceta eliminatÄ)\n"
                 "roČ™u = pierdere puternicÄ de vegetaČ›ie = arsurÄ", fontsize=12)
    ax.set_xticks([]); ax.set_yticks([])
    fig.colorbar(im, ax=ax, fraction=0.046, label="dNBR corectat")

    # conturul arsurii peste imaginea true color
    if "closeup" in imgs:
        ax2 = axes[0]
        m = np.where(np.isfinite(sub), sub >= 0.27, False)
        ax2.contour(m, levels=[0.5], colors="#00e5ff", linewidths=1.6)

    fig.suptitle("Parcul NaČ›ional Domogled â€“ Valea Cernei: cicatricea de incendiu\n"
                 "contur albastru = pixeli cu dNBR corectat â‰Ą 0,27 (ars moderatâ€“sever)",
                 fontsize=14, y=0.97)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    out = OUT / "detalii_arsura.png"
    fig.savefig(out, dpi=100)
    print(f"Scris: {out}")

    summary = {
        "fundal_dnbr_median": round(bg_med, 4),
        "fundal_dnbr_mad": round(bg_mad, 4),
        "ha_brut_dnbr_027": float(((dnbr >= 0.27) & valid).sum() * HA_PER_PX),
        "ha_corectat_dnbr_027": float(((dnbr_corr >= 0.27) & valid).sum() * HA_PER_PX),
        "dnbr_mediu_zona_focar": round(float(np.nanmean(dnbr[zone])), 4),
        "scena_pre": m_pre["id"], "scena_post": m_post["id"],
    }
    (OUT / "burn_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

