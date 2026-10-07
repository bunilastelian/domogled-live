#!/usr/bin/env python
"""
make_og.py - imaginea de partajare (Open Graph) pentru dashboard.

Din decupajul Sentinel-2 cu pana de fum din 4 octombrie face o imagine 1200x630
cu titlu, pentru ca linkul sa arate ca un articol cand e distribuit.

Atentie la font: fontul bitmap implicit din PIL nu are diacritice romanesti
(ș ț ă â î) si le deseneaza ca patratele goale. Folosim un TrueType real.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "fire" / "out" / "detaliu_10m_6km_2026-10-04.png"
DST = HERE / "web" / "og.jpg"

W, H = 1200, 630

FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\segoeui.ttf"),
    (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"),
    (r"C:\Windows\Fonts\calibrib.ttf", r"C:\Windows\Fonts\calibri.ttf"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for bold_path, reg_path in FONT_CANDIDATES:
        p = Path(bold_path if bold else reg_path)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    # ultima sansa: DejaVu din matplotlib (are diacritice)
    try:
        import matplotlib
        p = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
        return ImageFont.truetype(str(p), size)
    except Exception:  # noqa: BLE001
        print("[!] niciun font TrueType gasit — diacriticele vor aparea greșit")
        return ImageFont.load_default()


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    tr = w / h
    iw, ih = img.size
    if iw / ih > tr:
        nw = int(ih * tr)
        img = img.crop(((iw - nw) // 2, 0, (iw + nw) // 2, ih))
    else:
        nh = int(iw / tr)
        img = img.crop((0, (ih - nh) // 2, iw, (ih + nh) // 2))
    return img.resize((w, h), Image.LANCZOS)


def main() -> int:
    if not SRC.exists():
        print(f"[!] lipseste sursa {SRC}")
        return 1

    with Image.open(SRC) as im:
        img = cover(im.convert("RGB"), W, H)

    band = 210
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # banda de jos, cu degradé simplu in doua trepte
    d.rectangle([0, H - band, W, H], fill=(9, 11, 15, 232))
    d.rectangle([0, H - band, W, H - band + 5], fill=(229, 72, 77, 255))

    f_kicker = font(30, bold=True)
    f_title = font(46, bold=True)
    f_sub = font(25)
    f_url = font(23)

    x = 52
    d.text((x, H - band + 28), "ROMÂNIA ARDE", font=f_kicker, fill=(255, 122, 89, 255))
    d.text((x, H - band + 74), "Parcul Național Domogled – Valea Cernei",
           font=f_title, fill=(243, 248, 252, 255))
    d.text((x, H - band + 136), "Detecții termice NASA și ESA, suprafață arsă, hărți live",
           font=f_sub, fill=(178, 190, 204, 255))
    d.text((x, H - band + 170), "bunilastelian.github.io/domogled-live",
           font=f_url, fill=(122, 190, 255, 255))

    out = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    DST.parent.mkdir(parents=True, exist_ok=True)
    out.save(DST, "JPEG", quality=84, optimize=True)
    print(f"scris: {DST}  ({DST.stat().st_size/1024:.0f} KB, {W}x{H})")
    print(f"font: {font(20).getname()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
