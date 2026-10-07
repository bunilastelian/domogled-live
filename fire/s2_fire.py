#!/usr/bin/env python
"""
s2_fire.py - decupeaza imagini Sentinel-2 georeferentiate pe zona incendiului.

Descarca asset-urile dorite dintr-o scena Sentinel-2 L2A (prin STAC + OData),
transforma coordonatele geografice in pixel folosind proj:transform din STAC,
decupeaza zona de interes si scrie PNG-uri.

Compozitii:
  * true color  - din TCI (True Color Image) livrat de ESA
  * false color - SWIR2 / NIR / Rosu (B12, B8A, B04): arde-mai-putin = verde,
                  zone arse = rosu/portocaliu; standardul pentru cicatrici de incendiu

Rulare:
  python s2_fire.py --date 2026-10-04 --assets TCI_10m
  python s2_fire.py --date 2026-10-04 --assets B12_20m B8A_20m B04_20m --composite
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from cdse import CDSE, CDSEError, normalize_stac  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache" / "s2"
OUT = HERE / "out"
CACHE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

FIRE = (44.8982, 22.4785)          # lat, lon - focarul principal
COCIU = (44.91838, 22.46485)       # Cascada Cociului
HERCULANE = (44.8785, 22.4144)     # Baile Herculane


def find_scene(client: CDSE, point, target_date: str) -> dict:
    """Cea mai apropiata scena de data cerututa, cu cea mai mica acoperire de nori."""
    feats = client.stac_search(["sentinel-2-l2a"], point=point,
                               start=target_date, end=target_date, limit=20)
    if not feats:
        raise CDSEError(f"Nicio scena Sentinel-2 peste {point} la {target_date}")
    feats.sort(key=lambda f: f["properties"].get("eo:cloud_cover") or 100)
    return feats[0]


def get_asset_bytes(client: CDSE, rec: dict, key: str) -> Path:
    assets = rec.get("assets") or {}
    if key not in assets:
        raise CDSEError(f"Asset-ul '{key}' nu exista in scena (disponibile: {', '.join(list(assets)[:10])}...)")
    a = assets[key]
    if not a.get("https"):
        raise CDSEError(f"Asset-ul '{key}' nu are link https (doar s3)")
    suffix = Path((a.get("s3") or a["https"]).split("?")[0]).suffix or ".bin"
    fname = f"{rec['id']}_{key}{suffix}"
    return client.download_asset(a["https"], filename=fname, dest=CACHE, size=a.get("size"))


def utm_window(asset_meta: dict, lat: float, lon: float, box_km: float, img: Image.Image):
    """
    Fereastra de pixeli (left, top, right, bottom) pentru un patrat de box_km in jurul punctului.

    `proj:transform` si `proj:code` sunt metadate la nivel de ASSET (nu de item STAC).
    """
    transform = asset_meta.get("proj:transform")
    code = asset_meta.get("proj:code") or asset_meta.get("proj:epsg")
    if transform is None or code is None:
        raise CDSEError("Asset-ul nu are proj:transform / proj:code")
    if isinstance(code, str) and code.upper().startswith("EPSG:"):
        epsg = int(code.split(":")[1])
    else:
        epsg = int(code)

    # transform = [scale_x, 0, origin_x, 0, -scale_y, origin_y]
    sx, _, ox, _, sy, oy = transform[:6]
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x, y = tr.transform(lon, lat)

    half = box_km * 1000 / 2
    px_l = int(round((x - half - ox) / sx))
    px_r = int(round((x + half - ox) / sx))
    px_t = int(round((y + half - oy) / sy))
    px_b = int(round((y - half - oy) / sy))

    px_l, px_t = max(0, px_l), max(0, px_t)
    px_r, px_b = min(img.size[0], px_r), min(img.size[1], px_b)
    if px_r <= px_l or px_b <= px_t:
        raise CDSEError(f"Fereastra rezultata e invalida ({px_l},{px_t},{px_r},{px_b}) - "
                        f"scena poate sa nu acopere punctul")
    return (px_l, px_t, px_r, px_b), epsg, (sx, sy)


def stretch(a: np.ndarray, lo: float = 2, hi: float = 98) -> np.ndarray:
    """Intinde contrastul pe percentile si intoarce uint8."""
    a = a.astype("float32")
    finite = np.isfinite(a)
    if not finite.any():
        return np.zeros(a.shape, dtype="uint8")
    p_lo, p_hi = np.percentile(a[finite], [lo, hi])
    if p_hi <= p_lo:
        p_hi = p_lo + 1
    return np.clip((a - p_lo) / (p_hi - p_lo) * 255, 0, 255).astype("uint8")


def read_band(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="data scenei, YYYY-MM-DD")
    ap.add_argument("--lat", type=float, default=FIRE[0])
    ap.add_argument("--lon", type=float, default=FIRE[1])
    ap.add_argument("--box-km", type=float, default=22.0, help="latura zonei decupate, in km")
    ap.add_argument("--assets", nargs="+", default=["TCI_60m"])
    ap.add_argument("--composite", action="store_true",
                    help="construieste compozitul false-color din B12/B8A/B04")
    ap.add_argument("--prefix", default=None, help="prefix pentru fisierele de iesire")
    args = ap.parse_args()

    client = CDSE()
    point = (args.lon, args.lat)
    feat = find_scene(client, point, args.date)
    props = feat["properties"]
    rec = normalize_stac(feat)   # asset-urile normalizate: https / s3 / size
    print(f"Scena: {feat['id']}")
    print(f"  data {props.get('datetime')}  nori {props.get('eo:cloud_cover')}%  "
          f"tile {(props.get('grid:code') or '').replace('MGRS-','')}")

    prefix = args.prefix or f"s2_{args.date}"
    bands: dict[str, np.ndarray] = {}
    raw_assets = feat.get("assets") or {}

    for key in args.assets:
        try:
            path = get_asset_bytes(client, rec, key)
        except CDSEError as exc:
            print(f"  [!] {key}: {exc}")
            continue
        img = Image.open(path)
        window, epsg, scale = utm_window(raw_assets[key], args.lat, args.lon, args.box_km, img)
        crop = img.crop(window)
        print(f"  {key:<10} {path.stat().st_size/1e6:>7.1f} MB  fereastra {window}  "
              f"crop {crop.size[0]}x{crop.size[1]} px  (EPSG:{epsg}, {abs(scale[0]):.0f} m/px)")
        arr = np.array(crop)
        bands[key] = arr

        if key.startswith("TCI"):
            rgb = arr[:, :, :3] if arr.ndim == 3 else np.stack([arr] * 3, -1)
            out = OUT / f"{prefix}_{key}_crop.png"
            Image.fromarray(rgb.astype("uint8")).save(out)
            print(f"      -> {out}")

    if args.composite and {"B12_20m", "B8A_20m", "B04_20m"} <= set(bands):
        stack = np.dstack([
            stretch(bands["B12_20m"]),   # R = SWIR2
            stretch(bands["B8A_20m"]),   # G = NIR
            stretch(bands["B04_20m"]),   # B = Rosu
        ])
        out = OUT / f"{prefix}_falscolor_SWIR.png"
        Image.fromarray(stack).save(out)
        print(f"      -> {out}  (compozit SWIR2/NIR/Rosu: zonele arse apar rosu-inchis)")

        # indice de arsura normalizat aproximativ (NBR = NIR - SWIR2) / (NIR + SWIR2)
        nir = bands["B8A_20m"].astype("float32")
        swir = bands["B12_20m"].astype("float32")
        denom = nir + swir
        nbr = np.where(denom > 0, (nir - swir) / denom, np.nan)
        nbr8 = np.clip((nbr + 1) / 2 * 255, 0, 255).astype("uint8")
        out = OUT / f"{prefix}_NBR.png"
        Image.fromarray(nbr8).save(out)
        print(f"      -> {out}  (NBR: negru = suprafata arsa, alb = vegetatie sanatoasa)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
