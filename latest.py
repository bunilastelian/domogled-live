#!/usr/bin/env python
"""
latest.py - cele mai recente date din Copernicus Data Space Ecosystem (CDSE).

Exemple:
  python latest.py                          # ultimele 10 scene Sentinel-2 L2A (global)
  python latest.py -c s1 -n 5               # ultimele 5 Sentinel-1 GRD
  python latest.py -c s3 -n 5               # Sentinel-3 (prin OData)
  python latest.py -c s2 --lat 44.43 --lon 26.10 --days 20 --cloud 20
  python latest.py --all -n 3               # ce e mai nou, pe fiecare colectiune
  python latest.py -c s2 -n 1 --download 1  # descarca cea mai noua scena
  python latest.py --check                  # verifica doar cheile din .env
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# consola Windows poate fi cp1252; fortam UTF-8 ca diacriticele sa nu se strice
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from cdse import (  # noqa: E402
    CDSE,
    CDSEError,
    COLLECTIONS,
    DATA_DIR,
    MAIN_COLLECTIONS,
    human_size,
    print_table,
    resolve_collection,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    cols = "\n".join(
        f"  {k:<8} {v['desc']}  [{'OData' if (v.get('prefer') == 'odata' or not v.get('stac')) else 'STAC+OData'}]"
        for k, v in COLLECTIONS.items()
    )
    p = argparse.ArgumentParser(
        description="Cele mai recente date Copernicus (CDSE)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Colectii disponibile:\n{cols}",
    )
    p.add_argument("-c", "--collection", default="s2", help="colectiunea (implicit: s2)")
    p.add_argument("--all", action="store_true", help="cate o cautare pentru fiecare colectiune principala")
    p.add_argument("-n", "--limit", type=int, default=10, help="cate rezultate (implicit 10)")
    p.add_argument("--days", type=int, default=30, help="fereastra in urma, in zile (implicit 30)")
    p.add_argument("--start", help="data de inceput YYYY-MM-DD (suprascrie --days)")
    p.add_argument("--end", help="data de sfarsit YYYY-MM-DD (implicit azi)")
    p.add_argument("--lat", type=float, help="latitudine (punct de interes)")
    p.add_argument("--lon", type=float, help="longitudine (punct de interes)")
    p.add_argument("--bbox", help="W,S,E,N  (ex: 20.0,43.0,30.0,48.0)")
    p.add_argument("--cloud", type=float, help="acoperire maxima cu nori, %% (doar colectii optice)")
    p.add_argument("--source", choices=["auto", "stac", "odata"], default="auto",
                   help="de unde sa caute (implicit auto)")
    p.add_argument("--download", type=int, metavar="N", help="descarca cele mai noi N produse (ZIP intreg)")
    p.add_argument("--band", metavar="NUME", action="append", default=None,
                   help="descarca doar aceasta banda/asset din cele mai noi produse (ex: --band B04_10m, "
                        "repetabil; doar pentru sursa STAC)")
    p.add_argument("--list-bands", action="store_true", help="arata benzile/asset-urile disponibile si iese")
    p.add_argument("--out", help=f"folder de descarcare (implicit {DATA_DIR})")
    p.add_argument("--overwrite", action="store_true", help="suprascrie fisierele existente")
    p.add_argument("--json", action="store_true", help="afiseaza rezultatul ca JSON")
    p.add_argument("--check", action="store_true", help="verifica autentificarea si iese")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def time_window(args: argparse.Namespace) -> tuple[str, str]:
    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end) - timedelta(days=args.days)).isoformat()
    return start, end


def spatial(args: argparse.Namespace) -> tuple[tuple[float, float] | None, list[float] | None]:
    point = None
    if args.lat is not None and args.lon is not None:
        point = (args.lon, args.lat)  # STAC/OData vor (lon, lat)
    elif (args.lat is None) != (args.lon is None):
        raise SystemExit("--lat si --lon se folosesc impreuna.")

    bbox = None
    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            raise SystemExit("--bbox trebuie sa aiba 4 valori: W,S,E,N")
        bbox = parts

    if point and bbox:
        raise SystemExit("Foloseste fie --lat/--lon, fie --bbox, nu amandoua.")
    return point, bbox


def describe_aoi(point, bbox) -> str:
    if point:
        return f"punct lat={point[1]:.4f} lon={point[0]:.4f}"
    if bbox:
        return f"bbox W,S,E,N={bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    return "global"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = CDSE(verbose=args.verbose)

    if args.check:
        print(f"client_id     : {'setat' if client.client_id else 'LIPSA'}")
        print(f"client_secret : {'setat' if client.client_secret else 'LIPSA'}")
        if not client.configured:
            print(f"\nCompleteaza CDSE_CLIENT_ID si CDSE_CLIENT_SECRET in {Path(__file__).parent / '.env'}")
            return 2
        try:
            client.token()
        except CDSEError as exc:
            print(f"\nESEC: {exc}")
            return 1
        print("\nOK: autentificare reusita, token obtinut.")
        return 0

    start, end = time_window(args)
    point, bbox = spatial(args)
    keys = list(MAIN_COLLECTIONS) if args.all else [args.collection]

    results: dict[str, list] = {}
    records: list[dict] = []
    failures = 0

    for key in keys:
        try:
            coll = resolve_collection(key)
        except Exception as exc:  # noqa: BLE001
            print(f"[err] colectiunea '{key}': {exc}", file=sys.stderr)
            failures += 1
            continue
        try:
            found = client.search(
                coll, limit=args.limit, point=point, bbox=bbox,
                start=start, end=end, cloud_max=args.cloud, prefer=args.source,
            )
        except CDSEError as exc:
            print(f"[err] {coll['key']}: {exc}", file=sys.stderr)
            failures += 1
            continue

        results[coll["key"]] = found
        if not args.all:
            records = found

        if not args.json:
            if args.all:
                print(f"\n=== {coll['key']} - {coll['desc']}  [{found[0]['source'] if found else 'n/a'}] ===")
            else:
                src = found[0]["source"] if found else "n/a"
                print(f"{coll['desc']}")
                print(f"sursa catalog: {src}  |  zona: {describe_aoi(point, bbox)}  |  interval: {start} .. {end}"
                      + (f"  |  nori <= {args.cloud}%" if args.cloud is not None else ""))
            print_table(found)

    if args.json:
        print(json.dumps(results if args.all else records, indent=2, ensure_ascii=False, default=str))
        return 1 if failures and not any(results.values()) else 0

    newest = records if not args.all else [r for k in MAIN_COLLECTIONS for r in results.get(k, [])]

    if args.list_bands:
        if not newest:
            print("\nNiciun produs pentru care sa listez benzi.")
            return 1
        for rec in newest:
            assets = rec.get("assets") or {}
            print(f"\n{rec['name']}  ({len(assets)} asset-uri)")
            if not assets:
                print("   (sursa", rec["source"], "nu intoarce lista de asset-uri; foloseste o colectiune STAC)")
                continue
            for key, a in assets.items():
                link = "https" if a.get("https") else "doar-s3"
                print(f"   {key:<18} {human_size(a.get('size')):>9}  {link}")
        return 0

    if args.band:
        if not newest:
            print("\nNimic de descarcat.")
            return 1
        if not client.configured:
            print("\n[!] Descarcarea cere chei in .env. Ruleaza intai: python latest.py --check")
            return 2
        out = Path(args.out) if args.out else DATA_DIR
        count = args.download or 1
        picks = newest[:count]
        print(f"\nDescarc benzile {args.band} pentru {len(picks)} produs(e) in {out} ...")
        total = 0
        for rec in picks:
            assets = rec.get("assets") or {}
            for band in args.band:
                key = band if band in assets else next((k for k in assets if k.lower() == band.lower()), None)
                if key is None:
                    key = next((k for k in assets if band.lower() in k.lower()), None)
                if key is None:
                    print(f"[err] {rec['name']}: nu exista asset-ul '{band}'. Vezi --list-bands.")
                    continue
                asset = assets[key]
                if not asset.get("https"):
                    print(f"[err] {rec['name']}/{key}: fara link https (doar s3) - descarca prin S3.")
                    continue
                # URL-ul https se termina in `/$value`, deci extensia o luam din calea s3
                suffix = Path((asset.get("s3") or asset["https"]).split("?")[0]).suffix or ""
                filename = f"{rec['name']}_{key}{suffix}"
                try:
                    path = client.download_asset(
                        asset["https"], filename=filename, dest=out,
                        size=asset.get("size"), overwrite=args.overwrite,
                    )
                    total += path.stat().st_size
                except CDSEError as exc:
                    print(f"[err] {filename}: {exc}")
        print(f"\nGata. Total descarcat: {human_size(total)}")
        return 1 if failures and not any(results.values()) else 0

    if args.download:
        if not newest:
            print("\nNimic de descarcat.")
            return 1
        if not client.configured:
            print("\n[!] Descarcarea cere chei in .env. Ruleaza intai: python latest.py --check")
            return 2
        out = Path(args.out) if args.out else DATA_DIR
        print(f"\nDescarc {min(args.download, len(newest))} produs(e) intregi (ZIP) in {out} ...")
        total = 0
        for rec in newest[: args.download]:
            try:
                path = client.download_product(rec["id"], dest=out, overwrite=args.overwrite)
                total += path.stat().st_size
            except CDSEError as exc:
                print(f"[err] {rec['name']}: {exc}")
        print(f"\nGata. Total descarcat: {human_size(total)}")

    return 1 if failures and not any(results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
