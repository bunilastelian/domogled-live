#!/usr/bin/env python
"""
preview.py - descarca imaginea true-color (TCI) a celor mai recente scene si
o transforma in PNG, ca sa vezi datele, nu doar sa le listezi.

Exemple:
  python preview.py                                  # ultimele 3 scene S2, globale
  python preview.py --lat 44.43 --lon 26.10 --days 20 --cloud 20 -n 3
  python preview.py -c s2 --lat 44.43 --lon 26.10 --band TCI_10m -n 1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from PIL import Image, ImageDraw  # noqa: E402

from cdse import CDSE, CDSEError, DATA_DIR, human_size, print_table, resolve_collection  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Previzualizare PNG a celor mai recente scene Copernicus")
    p.add_argument("-c", "--collection", default="s2", help="colectiunea (implicit s2)")
    p.add_argument("-n", "--limit", type=int, default=3, help="cate scene (implicit 3)")
    p.add_argument("--days", type=int, default=30, help="fereastra in urma, in zile")
    p.add_argument("--start", help="data de inceput YYYY-MM-DD")
    p.add_argument("--end", help="data de sfarsit YYYY-MM-DD")
    p.add_argument("--lat", type=float, help="latitudine")
    p.add_argument("--lon", type=float, help="longitudine")
    p.add_argument("--bbox", help="W,S,E,N")
    p.add_argument("--cloud", type=float, help="acoperire maxima cu nori, %%")
    p.add_argument("--band", default="TCI_60m", help="asset-ul de descarcat (implicit TCI_60m)")
    p.add_argument("--out", help=f"folder de iesire (implicit {DATA_DIR})")
    p.add_argument("--max-side", type=int, default=700, help="latura maxima a PNG-ului per scena")
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def to_png(jp2: Path, png: Path, max_side: int) -> Image.Image:
    """JP2 -> PNG (8 biti), scalat. TCI e deja o imagine vizuala, deci nu cere stretch."""
    with Image.open(jp2) as im:
        img = im.convert("RGB") if im.mode not in ("RGB", "L") else im.copy()
    if img.size[0] > max_side or img.size[1] > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    img.save(png)
    return img


def montage(images: list[tuple[str, Image.Image]], gap: int = 8, label_h: int = 18) -> Image.Image:
    if not images:
        raise ValueError("nimic de montat")
    # normalizam inaltimile ca sa arate ca o banda de film
    target_h = min(im.size[1] for _, im in images)
    resized = []
    for label, im in images:
        w = max(1, round(im.size[0] * target_h / im.size[1]))
        resized.append((label, im.resize((w, target_h), Image.LANCZOS)))
    width = sum(im.size[0] for _, im in resized) + gap * (len(resized) - 1)
    canvas = Image.new("RGB", (width, target_h + label_h), (18, 18, 18))
    draw = ImageDraw.Draw(canvas)
    x = 0
    for label, im in resized:
        canvas.paste(im, (x, label_h))
        draw.text((x + 4, 3), label[:44], fill=(235, 235, 235))
        x += im.size[0] + gap
    return canvas


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = CDSE()

    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end) - timedelta(days=args.days)).isoformat()

    point = (args.lon, args.lat) if (args.lat is not None and args.lon is not None) else None
    bbox = [float(x) for x in args.bbox.split(",")] if args.bbox else None

    coll = resolve_collection(args.collection)
    out = Path(args.out) if args.out else DATA_DIR
    out.mkdir(parents=True, exist_ok=True)

    try:
        recs = client.search(coll, limit=args.limit, point=point, bbox=bbox,
                             start=start, end=end, cloud_max=args.cloud)
    except CDSEError as exc:
        print(f"Eroare: {exc}", file=sys.stderr)
        return 1

    if not recs:
        print("Niciun produs gasit pentru criteriile date.")
        return 1

    if not args.json:
        print(f"{coll['desc']}  |  interval: {start} .. {end}"
              + (f"  |  nori <= {args.cloud}%" if args.cloud is not None else ""))
        print_table(recs)
        print()

    results = []
    for rec in recs:
        assets = rec.get("assets") or {}
        asset = assets.get(args.band)
        if asset is None:
            asset = next((a for k, a in assets.items() if k.lower() == args.band.lower()), None)
        if asset is None or not asset.get("https"):
            print(f"[skip] {rec['name']}: nu am gasit asset-ul '{args.band}' cu link https "
                  f"(disponibile: {', '.join(list(assets)[:6])}...)")
            continue

        suffix = Path((asset.get("s3") or asset["https"]).split("?")[0]).suffix or ".jp2"
        raw = out / f"{rec['name']}_{args.band}{suffix}"
        try:
            raw = client.download_asset(asset["https"], filename=raw.name, dest=out, size=asset.get("size"))
        except CDSEError as exc:
            print(f"[err] {rec['name']}: {exc}")
            continue

        png = out / f"{rec['name']}_{args.band}.png"
        try:
            img = to_png(raw, png, args.max_side)
        except Exception as exc:  # noqa: BLE001
            print(f"[err] conversie {raw.name}: {type(exc).__name__}: {exc}")
            continue

        label = f"{(rec.get('datetime') or '')[:10]}  nori {rec.get('cloud_cover')}%  {rec.get('tile') or ''}"
        results.append({"scene": rec["name"], "png": str(png), "datetime": rec.get("datetime"),
                        "cloud_cover": rec.get("cloud_cover"), "label": label, "image": img})
        print(f"  -> {png}  ({human_size(png.stat().st_size)}, {img.size[0]}x{img.size[1]})")

    if results:
        strip = out / f"preview_{coll['key']}.png"
        montage([(r["label"], r["image"]) for r in results]).save(strip)
        print(f"\nMontaj: {strip}")

    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != 'image'} for r in results],
                         indent=2, ensure_ascii=False, default=str))
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
