#!/usr/bin/env python3
"""
Descarca decupaje true-color Sentinel-2 pentru istoricul recent.

DE CE NU Process API: am incercat Sentinel Hub Process API
(sh.dataspace.copernicus.eu/api/v1/process) si returneaza imagini GOALE
(negru, 0 pixeli) indiferent de bbox/evalscript. Contul are client_credentials
pentru CATALOG, nu neaparat pentru procesare. Deci folosim metoda care merge
in proiectul asta: descarcam asset-ul TCI_60m (True Color Image, gata
procesat de ESA) si il decupam local cu PIL.

TCI_60m e un JPEG2000 de ~10000x10000 px cu 3 benzi (R,G,B deja corectate
atmosferic). Decupam fereastra zonei si salvam JPEG mic.

Output: live/web/img/s2hist_{zona}_{data}.jpg + index actualizat
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from PIL import Image  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

IDX = ROOT / "live" / "web" / "state" / "s2_history.json"
CACHE = ROOT / "live" / "state" / "s2_tci"
IMG_DIR = ROOT / "live" / "web" / "img"
STATE = ROOT / "live" / "state" / "state.json"

OUT_SIZE = 640          # latura decupajului final


def crop_tci(tci_path: Path, bbox: list, out_path: Path,
             scene_bbox: list | None = None) -> bool:
    """Decupeaza fereastra AOI din TCI si salveaza JPEG.

    IMPORTANT: NU decupam din centrul scenei. O scena S2 acopera 110x110 km,
    iar zona noastra e un colt al ei - decupand din centru luam alta zona
    (sau nodata/umbra, cum s-a intamplat prima data: 90% pixeli negri).

    Corect: folosim scene_bbox (din STAC) ca sa transformam coordonatele
    geografice ale AOI in pixeli in imaginea TCI.
    """
    if not scene_bbox:
        return False
    try:
        with Image.open(tci_path) as im:
            W, H = im.size
            sw, ss, se, sn = scene_bbox          # bbox scena (WGS84)
            w, s, e, n = bbox                    # bbox zona noastra

            # clampam AOI la scena (poate iesi partial in afara)
            w2, s2 = max(w, sw), max(s, ss)
            e2, n2 = min(e, se), min(n, sn)
            if e2 <= w2 or n2 <= s2:
                print("      AOI in afara scenei")
                return False

            def px(lon, lat):
                x = (lon - sw) / (se - sw) * W
                y = (sn - lat) / (sn - ss) * H
                return x, y

            x0, y1 = px(w2, s2)
            x1, y0 = px(e2, n2)

            # marja: includem context in jurul zonei (latura x2), dar nu
            # iesim din imagine
            cw, ch = x1 - x0, y1 - y0
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            half_w, half_h = cw * 0.9, ch * 0.9      # 1.8x latura zonei
            left = max(0, int(cx - half_w))
            right = min(W, int(cx + half_w))
            top = max(0, int(cy - half_h))
            bottom = min(H, int(cy + half_h))
            if right - left < 32 or bottom - top < 32:
                print(f"      decupaj prea mic ({right-left}x{bottom-top})")
                return False

            cropped = im.crop((left, top, right, bottom)).convert("RGB")
            cropped.thumbnail((OUT_SIZE, OUT_SIZE), Image.Resampling.LANCZOS)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            cropped.save(out_path, "JPEG", quality=84, optimize=True)

            # verificam ca nu e imagine goala (tot negru = nodata)
            import numpy as _np
            arr = _np.asarray(cropped)
            if float(arr.std()) < 4:
                print(f"      imagine uniforma (std={arr.std():.1f}) - nodata?")
                out_path.unlink(missing_ok=True)
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"      crop esuat: {type(exc).__name__}: {str(exc)[:90]}")
        return False


def main() -> int:
    import cdse
    from cdse import CDSE, CDSEError, normalize_stac

    st = json.loads(STATE.read_text(encoding="utf-8"))
    hist = json.loads(IDX.read_text(encoding="utf-8")) if IDX.exists() else {}
    client = CDSE()
    if not client.configured:
        print("lipsesc cheile CDSE", file=sys.stderr)
        return 1

    CACHE.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    new = 0

    for cfg in (st.get("zone_list") or []):
        zid, name, bbox = cfg.get("id"), cfg.get("name"), cfg.get("bbox")
        if not (zid and bbox):
            continue
        scenes = hist.get(zid) or []
        # doar scenele din ultimele 30 de zile care au deja id STAC
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        recent = [s for s in scenes if s["date"] >= cutoff]
        print(f"\n=== {name}: {len(recent)} scene de la {cutoff} ===")

        for s in recent:
            outp = IMG_DIR / f"s2hist_{zid}_{s['date'].replace('-','')}.jpg"
            s["file"] = outp.name
            if outp.exists() and outp.stat().st_size > 3000:
                print(f"   {s['date']}  deja pe disc ({outp.stat().st_size//1024} KB)")
                continue

            # asset-ul TCI_60m vine din STAC (nu din OData product(), care nu
            # contine lista de assets). Caut dupa id in catalogul STAC.
            asset = None
            scene_bbox = None
            try:
                import requests
                r = requests.post(
                    "https://stac.dataspace.copernicus.eu/v1/search",
                    json={"collections": ["sentinel-2-l2a"], "ids": [s["id"]]},
                    headers={"Authorization": f"Bearer {client.token()}"},
                    timeout=90)
                if r.status_code == 200 and r.json().get("features"):
                    feat = r.json()["features"][0]
                    scene_bbox = feat.get("bbox")
                    rec = normalize_stac(feat)
                    asset = (rec.get("assets") or {}).get("TCI_60m")
            except Exception as exc:  # noqa: BLE001
                print(f"   {s['date']}  STAC esuat: {type(exc).__name__}: {str(exc)[:90]}")

            if not asset or not asset.get("https"):
                print(f"   {s['date']}  fara asset TCI_60m")
                s["file"] = None
                continue

            cache_p = CACHE / f"{s['id']}_TCI.jp2"
            try:
                if not cache_p.exists():
                    client.download_asset(asset["https"], filename=cache_p.name,
                                          dest=CACHE, size=asset.get("size"))
                if crop_tci(cache_p, bbox, outp, scene_bbox):
                    new += 1
                    print(f"   {s['date']}  OK {outp.stat().st_size//1024} KB "
                          f"(nori={s['cloud']:.1f}%)")
                else:
                    s["file"] = None
            except CDSEError as exc:
                print(f"   {s['date']}  descarcare esuata: {str(exc)[:90]}")
                s["file"] = None
            time.sleep(0.6)

        hist[zid] = scenes

    IDX.write_text(json.dumps(hist, indent=2, ensure_ascii=False))
    tot = sum(f.stat().st_size for f in IMG_DIR.glob("s2hist_*.jpg")) / 1048576
    cache_mb = sum(f.stat().st_size for f in CACHE.glob("*.jp2")) / 1048576
    print(f"\n{new} imagini noi | imagini: {tot:.1f} MB | cache TCI: {cache_mb:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
