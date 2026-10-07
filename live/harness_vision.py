#!/usr/bin/env python3
"""
Analiza vizuala a imaginilor satelitare, prin model cu viziune.

Folosim `deepseek-flash` (api.deepseek.com) pentru ca e singurul model
disponibil care accepta imagini — verificat: Token Plan da 403 pe toate
modelele, iar DeepSeek direct accepta `image_url` cu base64.

CRUCIAL: trimitem `thinking: {"type": "disabled"}`. Fara el, modelul
consuma tot bugetul de tokeni pe reasoning_content si intoarce content gol
(testat: 600 tokeni de reasoning, raspuns final zero). Cu thinking oprit
raspunde in ~5 secunde, direct.

Imaginile sunt PNG-uri mici (300-420 px), deci base64-ul e ~400 KB — se
incadreaza lejer. Daca vreodata cresim rezolutia, trebuie redimensionat
inainte (limita practica ~5 MB per imagine la DeepSeek).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.deepseek.com/chat/completions"
MODEL = os.getenv("DOMOGLED_VISION_MODEL", "deepseek-flash")
IMG_DIR = Path("/home/lili/projects/domogled-live/live/web/img")
TIMEOUT = 300


def _key() -> str:
    k = os.getenv("DEEPSEEK_API_KEY")
    if k:
        return k
    # fallback: citește direct din .env daca nu e in mediu (rulare manuala)
    p = Path.home() / ".hermes" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("DEEPSEEK_API_KEY lipseste")


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _call(prompt: str, images: list[Path], max_tokens: int = 1200) -> str:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for p in images:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{_b64(p)}"}})

    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},   # vezi docstring
        "messages": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_key()}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"DeepSeek HTTP {e.code}: {e.read().decode()[:300]}") from e

    msg = d["choices"][0]["message"]
    out = (msg.get("content") or "").strip()
    if not out and msg.get("reasoning_content"):
        # daca totusi a consumat pe reasoning, salvam ce a produs
        out = msg["reasoning_content"].strip()
    return out


PROMPT_SINGLE = """Esti analist de teledetectie. Prima imagine e o scena Sentinel-2 \
true color a zonei {zone} (Parcul National Domogled - Valea Cernei / Portile de Fier, Carpatii Meridionali).

Date numerice confirmate de senzori in zona: {ctx}

Descrie CONCRET ce vezi in imagine, in maxim 120 de cuvinte:
1. FUM - se vede? dens, subtire, dispersat? in ce directie se duce (foloseste puncte cardinale)?
2. CICATRICE DE ARDERE - se distinge o zona arsa (maro-inchis, cenusiu, negru) fata de padure verde?
3. VEGETATIE - padure densa, pajiste, stancarie, zone uscate? cat la suta e verde?
4. RELIEF - vai, creste, platou?

Daca imaginea e acoperita de nori, spune clar asta si nu inventa. Fii onest: \
daca nu poti distinge ceva, scrie "nu se distinge". Nu inventa detalii."""

PROMPT_COMPARE = """Esti analist de teledetectie. Ai 2 imagini Sentinel-2 true color \
ale aceleiasi zone ({zone}), la date diferite:
- IMAGINEA 1: {date1}
- IMAGINEA 2: {date2}

Compara-le si spune doar ce s-a SCHIMBAT, in maxim 100 de cuvinte:
1. Fumul a crescut, a scazut, sau s-a mutat?
2. Cicatricea de ardere s-a extins? In ce directie?
3. Vegetatia arata mai uscata sau la fel?

Daca diferentele sunt doar de nori/iluminare si nu de incendiu, spune clar \
"fara schimbare relevanta". Nu inventa diferente care nu se vad."""


def _scene_images(zone_state: dict, limit: int = 3) -> list[tuple[Path, str]]:
    """Scenele S2 disponibile pentru o zona, cea mai noua prima."""
    im = zone_state.get("imagery") or {}
    scenes = []
    for v in im.values():
        f = v.get("file")
        if not f:
            continue
        p = IMG_DIR / f
        if p.exists():
            scenes.append((p, v.get("datetime") or "?"))
    scenes.sort(key=lambda x: x[1], reverse=True)
    return scenes[:limit]


def analyze_zone(zone_id: str, zone_state: dict, ctx: str) -> dict:
    """Analiza vizuala pentru o zona: scena curenta + comparatie cu anterioara."""
    out: dict = {"zone": zone_id, "ok": False, "errors": []}

    scenes = _scene_images(zone_state, limit=3)
    if not scenes:
        out["errors"].append("nicio imagine S2 pe disc")
        return out

    name = zone_state.get("name") or zone_id

    # 1. scena cea mai recenta
    try:
        out["current"] = _call(
            PROMPT_SINGLE.format(zone=name, ctx=ctx), [scenes[0][0]], 700)
        out["current_scene"] = scenes[0][0].name
        out["current_date"] = scenes[0][1]
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"scena curenta: {type(e).__name__}: {str(e)[:150]}")

    # 2. comparatie cu o scena din ALTA zi.
    # Capcana: senzorii S2A/S2B/S2C produc scene in aceeasi zi (survoluri
    # diferite). Compararea lor da "nimic schimbat" si irosim tokeni degeaba.
    # Alegem prima scena cu o zi calendaristica diferita.
    day0 = (scenes[0][1] or "")[:10]
    older = next(((p, d) for p, d in scenes[1:] if (d or "")[:10] != day0), None)
    if older:
        try:
            a, b = scenes[0], older
            out["comparison"] = _call(
                PROMPT_COMPARE.format(zone=name,
                                      date1=a[1][:10], date2=b[1][:10]),
                [b[0], a[0]], 600)
            out["compared"] = [b[0].name, a[0].name]
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"comparatie: {type(e).__name__}: {str(e)[:150]}")

    # 3. cicatricea de ardere (imaginea dNBR), daca exista
    burn = IMG_DIR / f"{zone_id}_arsura.png"
    if burn.exists():
        try:
            out["burn_scar"] = _call(
                f"Imaginea e o harta de severitate a arderii (dNBR) pentru {name}. "
                "Zonele rosii/inchise = ars sever, verzi = nears. Estimativ cat "
                "la suta din imagine arata ca ars? Raspunde in maxim 40 de cuvinte, "
                "doar cu estimarea si unde e localizata zona arsa.",
                [burn], 300)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"dNBR: {type(e).__name__}: {str(e)[:150]}")

    return out
