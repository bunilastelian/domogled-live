#!/usr/bin/env python
"""
burn_scar.py - cicatricea de incendiu, recalculata automat si publicata pe harta.

La fiecare scena Sentinel-2 noua peste zona:
  1. alege o scena de referinta de dinainte de incendiu (cea mai putin noroasa din
     fereastra de dinainte de 9 august 2026)
  2. descarca B8A (NIR) si B12 (SWIR2) la 20 m pentru referinta si pentru scena recenta
  3. calculeaza NBR si dNBR = NBR_inainte - NBR_dupa
  4. scade din dNBR medianul zonei de fundal - seceta scade NBR in toata regiunea,
     deci dNBR brut umfla suprafata "arsa"
  5. scrie un PNG cu transparenta, georeferentiat, pentru suprapunere pe harta
  6. intoarce suprafetele pe clase, in hectare

Rezultatul e o estimare proprie, nu o delimitare oficiala.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from cdse import CDSE, CDSEError  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

PIX = 20                      # m, rezolutia benzilor B8A/B12
HA_PER_PX = PIX * PIX / 10000.0
BOX_KM = 22.0                 # latura ferestrei de analiza
FIRE_START = "2026-08-09"     # prima zi a primului incendiu
BASELINE_LOOKBACK = 30        # zile inainte de FIRE_START in care caut referinta
POST_CLOUD_MAX = 35.0         # % nori acceptat pentru scena "dupa"
BACKGROUND_RADIUS_KM = 8.0    # peste atat = fundal, pentru corectia de seceta

# Clase de severitate: (prag minim dNBR, culoare RGBA).
#
# Clasa 0.10-0.27 se calculeaza statistic, dar NU se deseneaza: dupa corectia de seceta
# rămâne dominata de zgomot (uscarea sezoniera a vegetatiei), răspândita uniform pe toata
# fereastra. Peste 0.27 semnalul devine coerent spatial si corespunde arsurii reale.
OVERLAY_MIN = 0.27
CLASSES = [
    (0.10, (250, 214, 20, 120)),    # ars usor - doar statistica
    (0.27, (245, 124, 0, 175)),     # moderat-scazut
    (0.44, (229, 72, 77, 205)),     # moderat-ridicat
    (0.66, (140, 60, 190, 225)),    # ridicat
]
CLASS_NAMES = ["ars ușor", "moderat-scăzut", "moderat-ridicat", "ridicat"]


def _https(asset: dict | None) -> str | None:
    """Linkul https dintr-un asset STAC BRUT (stac_search nu il normalizeaza)."""
    if not asset:
        return None
    return ((asset.get("alternate") or {}).get("https") or {}).get("href")


def _bands(client: CDSE, scene: dict, key: str, cache: Path) -> Path:
    a = scene["assets"][key]
    url = _https(a)
    if not url:
        raise CDSEError(f"{scene['id']}: asset-ul {key} nu are link https")
    href = str(a.get("href") or "")
    suffix = Path((href.replace("s3://", "") or url).split("?")[0]).suffix or ".jp2"
    return client.download_asset(url, filename=f"{scene['id']}_{key}{suffix}",
                                 dest=cache, size=a.get("file:size"))


def _window(scene: dict, center: tuple[float, float], box_km: float, key: str = "B8A_20m"):
    """Fereastra de pixeli pentru un patrat de box_km in jurul centrului, plus georeferentierea."""
    a = scene["assets"][key]
    sx, _, ox, _, sy, oy = a["proj:transform"][:6]
    epsg = int(str(a["proj:code"]).split(":")[1])
    conv = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x, y = conv.transform(center[1], center[0])
    half = box_km * 1000 / 2
    win = (int(round((x - half - ox) / sx)), int(round((y + half - oy) / sy)),
           int(round((x + half - ox) / sx)), int(round((y - half - oy) / sy)))
    return win, {"sx": sx, "sy": sy, "ox": ox, "oy": oy, "epsg": epsg}


def _corners(win, geo) -> list[list[float]]:
    """Colturile ferestrei in lat/lon, pentru L.imageOverlay: [[S, W], [N, E]]."""
    inv = Transformer.from_crs(f"EPSG:{geo['epsg']}", "EPSG:4326", always_xy=True)
    left, top, right, bottom = win

    def ll(col, row):
        x = geo["ox"] + col * geo["sx"]
        y = geo["oy"] + row * geo["sy"]
        lon, lat = inv.transform(x, y)
        return [round(lat, 5), round(lon, 5)]

    return [ll(left, bottom), ll(right, top)]


def _has_bands(f: dict) -> bool:
    return bool(_https(f["assets"].get("B8A_20m")) and _https(f["assets"].get("B12_20m")))


def _pick_baseline(client: CDSE, center, from_date: str, to_date: str) -> dict:
    feats = client.stac_search(["sentinel-2-l2a"], point=(center[1], center[0]),
                               start=from_date, end=to_date, limit=40)
    good = [f for f in feats if _has_bands(f)]
    if not good:
        raise CDSEError(f"nicio scena de referinta cu B8A+B12 intre {from_date} si {to_date}")
    good.sort(key=lambda f: f["properties"].get("eo:cloud_cover") or 100)
    return good[0]


def pick_recent(client: CDSE, center, days: int = 15) -> dict:
    end = date.today()
    start = end - timedelta(days=days)
    feats = client.stac_search(["sentinel-2-l2a"], point=(center[1], center[0]),
                               start=start.isoformat(), end=end.isoformat(), limit=40)
    good = [f for f in feats if _has_bands(f)
            and (f["properties"].get("eo:cloud_cover") or 100) <= POST_CLOUD_MAX]
    if not good:
        raise CDSEError(f"nicio scena recenta cu mai putin de {POST_CLOUD_MAX}% nori in {days} zile")
    good.sort(key=lambda f: f["properties"]["datetime"], reverse=True)
    return good[0]


# vechiul nume, pastrat pentru compatibilitate
_pick_recent = pick_recent


def _nbr(client: CDSE, scene: dict, cache: Path, win) -> np.ndarray:
    """NBR = (NIR - SWIR2) / (NIR + SWIR2), decupat pe fereastra data."""
    left, top, right, bottom = win
    nir = np.array(Image.open(_bands(client, scene, "B8A_20m", cache))).astype("float32")
    swir = np.array(Image.open(_bands(client, scene, "B12_20m", cache))).astype("float32")
    nir = nir[top:bottom, left:right]
    swir = swir[top:bottom, left:right]
    denom = nir + swir
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(denom > 0, (nir - swir) / denom, np.nan)


def compute(client: CDSE, center: tuple[float, float], out_png: Path, cache: Path,
            post: dict | None = None) -> dict:
    """Calculeaza dNBR si scrie PNG-ul georeferentiat. Intoarce statisticile.

    `post` poate fi scena recenta deja aleasa (ca sa nu repetam interogarea STAC).
    """
    cache.mkdir(parents=True, exist_ok=True)
    fs = date.fromisoformat(FIRE_START)
    base = _pick_baseline(client, center,
                          (fs - timedelta(days=BASELINE_LOOKBACK)).isoformat(),
                          (fs - timedelta(days=1)).isoformat())
    post = post or pick_recent(client, center)
    if base["id"] == post["id"]:
        raise CDSEError("scena de referinta si cea recenta coincid")

    win, geo = _window(post, center, BOX_KM)
    nbr_pre = _nbr(client, base, cache, win)
    nbr_post = _nbr(client, post, cache, win)
    if nbr_pre.shape != nbr_post.shape:
        raise CDSEError(f"forme diferite: {nbr_pre.shape} vs {nbr_post.shape}")

    dnbr = nbr_pre - nbr_post
    valid = np.isfinite(dnbr)

    # corectia de seceta: medianul zonei de fundal (departe de focar)
    h, w = dnbr.shape
    yy, xx = np.mgrid[0:h, 0:w]
    dist_km = np.hypot(xx - w / 2, yy - h / 2) * PIX / 1000.0
    bg = valid & (dist_km > BACKGROUND_RADIUS_KM)
    bg_med = float(np.nanmedian(dnbr[bg])) if bg.any() else 0.0
    dnbr_c = dnbr - bg_med

    # PNG cu transparenta; clasele severe se aplica ultimele, deci ramân deasupra
    rgba = np.zeros((h, w, 4), dtype="uint8")
    for thr, color in CLASSES:
        if thr < OVERLAY_MIN:
            continue                       # clasa de seceta nu se deseneaza
        m = valid & (dnbr_c >= thr)
        for i in range(4):
            rgba[..., i][m] = color[i]
    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").resize((w // 2, h // 2), Image.NEAREST).save(out_png)

    # clasele sunt NESTED (pixelii severi sunt si in clasa usoara), deci numaratorile
    # cumulative descresc. Banda unei clase = cumulativul ei minus cumulativul urmator.
    cum = [float((valid & (dnbr_c >= thr)).sum() * HA_PER_PX) for thr, _c in CLASSES]
    cum.append(0.0)
    area_by_class = [
        {"clasa": name, "prag": thr, "ha": round(cum[i] - cum[i + 1], 1)}
        for i, ((thr, _c), name) in enumerate(zip(CLASSES, CLASS_NAMES))
    ]

    core = valid & (dist_km <= 2.0)
    return {
        "post_id": post["id"], "post_date": post["properties"]["datetime"][:10],
        "post_cloud": post["properties"].get("eo:cloud_cover"),
        "base_id": base["id"], "base_date": base["properties"]["datetime"][:10],
        "base_cloud": base["properties"].get("eo:cloud_cover"),
        "background_dnbr": round(bg_med, 4),
        "ha_total": round(cum[0], 1),            # dNBR corectat >= 0.10
        "ha_moderat_plus": round(cum[1], 1),     # >= 0.27
        "ha_sever": round(cum[-2], 1),           # >= 0.66
        "clase": area_by_class,
        "dnbr_mediu_focar": round(float(np.nanmean(dnbr_c[core])), 4) if core.any() else None,
        "png": out_png.name,
        "bounds": _corners(win, geo),
        "box_km": BOX_KM,
        "pix_m": PIX,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nota": ("estimare proprie din dNBR Sentinel-2, corectata cu medianul de fundal; "
                 "nu este o delimitare oficiala"),
    }


def main() -> int:
    """Rulare directa, pentru test."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=44.8982)
    ap.add_argument("--lon", type=float, default=22.4785)
    ap.add_argument("--out", default=str(HERE / "web" / "img" / "arsura.png"))
    args = ap.parse_args()
    st = compute(CDSE(), (args.lat, args.lon), Path(args.out), HERE / "state" / "burn")
    for k, v in st.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
