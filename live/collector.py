#!/usr/bin/env python
"""
collector.py - motorul de colectare live pentru incendiul Domogled - Valea Cernei.

Ruleaza in bucla si actualizeaza stare/datele pe care le serveste dashboard-ul:

  1. detections  - NASA FIRMS (VIIRS 375 m Suomi-NPP + NOAA-20, MODIS 1 km), la fiecare ciclu
  2. frp         - ESA Sentinel-3 SLSTR FRP, produse noi descarcate cand apar (2 MB fiecare)
  3. imagery     - Sentinel-2: cand apare o scena noua peste zona, se decupeaza TCI la 60 m
  4. alerts      - detectii noi fata de ciclul anterior -> consola, dashboard, Telegram (optional)

Starea se scrie in  state/  si e citita de  server.py.

Rulare:
   python collector.py                 # bucla continua, ciclu la 5 minute
   python collector.py --once          # un singur ciclu (pentru test)
   python collector.py --interval 120  # alt interval
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from PIL import Image
from pyproj import Transformer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # pentru cdse.py
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from cdse import CDSE, CDSEError, normalize_stac  # noqa: E402

# ----------------------------------------------------------------- configurare
# Zonele vin din zones.json — adaugi o zona noua acolo, fara sa schimbi codul.
ZONES_FILE = HERE / "zones.json"
ZONE_CFG = json.loads(ZONES_FILE.read_text(encoding="utf-8"))
ZONES: list[dict] = ZONE_CFG["zones"]
ZONE: dict = next((z for z in ZONES if z["id"] == ZONE_CFG.get("default_zone")), ZONES[0])

AOI = {
    "name": ZONE["name"],
    "bbox": ZONE["bbox"],                     # W, S, E, N
    "center": ZONE["center"],
    "zoom": ZONE.get("zoom", 12),
    "park_file": (ZONE.get("park") or {}).get("geojson", ""),
}
POLL_SECONDS = 300
FRP_LOOKBACK_HOURS = 6            # cat de des cautam produse FRP noi
IMAGERY_CLOUD_MAX = 60            # % acoperire cu nori acceptata pentru preview
UA = {"User-Agent": "copernicus-domogled-live/2.0"}


def set_zone(zone: dict) -> None:
    """Leaga modulele de zona care se proceseaza acum.

    Colectorul merge secvential prin zone, intr-un singur fir, deci reasignarea
    variabilelor de modul e sigura si evita sa ducem `zone` prin toate functiile.
    Fisierele de iesire primesc prefixul zonei, ca doua zone sa nu se calce.
    """
    global ZONE
    ZONE = zone
    AOI.update({
        "name": zone["name"],
        "bbox": zone["bbox"],
        "center": zone["center"],
        "zoom": zone.get("zoom", 12),
        "park_file": (zone.get("park") or {}).get("geojson", ""),
    })


def zone_image(name: str) -> Path:
    """Nume de fisier unic pe zona (doua zone pot partaja aceeasi scena Sentinel-2)."""
    return IMG / f"{ZONE['id']}_{name}"


def zone_burn_cache() -> Path:
    return STATE / "burn" / ZONE["id"]

STATE = HERE / "state"
WEB = HERE / "web"
IMG = WEB / "img"
STATE.mkdir(exist_ok=True)
IMG.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE / "state.json"

FIRMS_FEEDS = {
    "VIIRS-SNPP-375m": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Europe_{win}.csv",
    "VIIRS-NOAA20-375m": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_{win}.csv",
    "MODIS-1km": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Europe_{win}.csv",
}
FIRMS_SEED = "7d"     # la prima rulare luam si istoricul de 7 zile


# ------------------------------------------------------------------ utilitare
def dist_km(a, b) -> float:
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def in_aoi(lat: float, lon: float) -> bool:
    w, s, e, n = AOI["bbox"]
    return w <= lon <= e and s <= lat <= n


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("[!] state.json corupt, pornesc de la zero")
    return {"version": 2, "cycles": 0, "zones": {}}


def save_state(state: dict) -> None:
    """Scrie starea atomic, in doua locuri:
       state/state.json        - sursa de adevar, citita de server.py
       web/state/state.json    - copie pentru modul static (GitHub Pages)
    """
    blob = json.dumps(state, ensure_ascii=False, indent=1)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(blob, encoding="utf-8")
    tmp.replace(STATE_FILE)

    static = WEB / "state" / "state.json"
    static.parent.mkdir(parents=True, exist_ok=True)
    stmp = static.with_suffix(".tmp")
    stmp.write_text(blob, encoding="utf-8")
    stmp.replace(static)


def http_get(url: str, timeout: int = 90) -> str:
    """GET cu retry. Un timeout izolat nu trebuie sa coste un ciclu intreg:
    NASA FIRMS si CDSE au momente de indisponibilitate scurta, iar fara retry
    pierdem fereastra de 24h pana la urmatorul ciclu (5 min mai tarziu)."""
    req = urllib.request.Request(url, headers=UA)
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))   # 2s, apoi 4s
    raise last  # type: ignore[misc]


# --------------------------------------------------------------------- lock
# Doar un colector are voie sa scrie state.json. Fara asta, doua instante
# (ex. una pornita de start.ps1 si una rulata manual cu --once) se suprascriu
# reciproc, iar starea pierde campuri scrise de cealalta.
LOCK = STATE / "collector.lock"


def _pid_alive(pid: int) -> bool:
    """Verifica daca un PID mai traieste, fara sa il atinga.

    Pe Windows NU se foloseste os.kill(pid, 0): acolo orice semnal in afara de
    CTRL_C_EVENT/CTRL_BREAK_EVENT omoara procesul tinta.
    """
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def acquire_lock(interval: int, force: bool = False) -> None:
    """Ia lock-ul daca nu-l are deja un colector viu."""
    stale_after = max(600, 2 * interval + 120)
    if LOCK.exists() and not force:
        try:
            info = json.loads(LOCK.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            info = {}
        pid = int(info.get("pid") or 0)
        age = time.time() - float(info.get("ts") or 0)
        # lock-ul e valid doar daca procesul traieste SI a dat semnal recent
        if _pid_alive(pid) and age < stale_after:
            raise SystemExit(
                f"\n  Alt colector ruleaza deja (PID {pid}, ultimul semnal acum {age:.0f}s).\n"
                f"  Opreste-l cu  .\\stop.ps1  sau ruleaza cu  --force  daca esti sigur.\n")
        motiv = "procesul nu mai traieste" if not _pid_alive(pid) else f"fara semnal de {age:.0f}s"
        log(f"   lock vechi preluat (PID {pid}: {motiv})")
    LOCK.write_text(json.dumps({"pid": os.getpid(), "ts": time.time(),
                                "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}),
                    encoding="utf-8")


def touch_lock() -> None:
    try:
        LOCK.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")
    except OSError:
        pass


def release_lock() -> None:
    LOCK.unlink(missing_ok=True)


# ------------------------------------------------------------------- 1. FIRMS
def fetch_firms(window: str) -> list[dict]:
    out = []
    for sensor, tpl in FIRMS_FEEDS.items():
        url = tpl.format(win=window)
        try:
            txt = http_get(url)
        except Exception as exc:  # noqa: BLE001
            log(f"   [!] FIRMS {sensor} ({window}): {type(exc).__name__}: {exc}")
            continue
        n = 0
        for d in csv.DictReader(io.StringIO(txt)):
            try:
                lat, lon = float(d["latitude"]), float(d["longitude"])
            except (KeyError, ValueError):
                continue
            if not in_aoi(lat, lon):
                continue
            try:
                frp = float(d.get("frp") or 0)
            except ValueError:
                frp = 0.0
            conf = str(d.get("confidence") or "")
            if conf.isdigit():
                conf_pct = int(conf)
            else:
                conf_pct = {"h": 90, "n": 60, "l": 30}.get(conf[:1].lower(), 40)
            out.append({
                "src": "NASA",
                "sensor": sensor,
                "lat": round(lat, 5), "lon": round(lon, 5),
                "date": d["acq_date"], "time": d["acq_time"].zfill(4),
                "frp": round(frp, 2),
                "confidence": conf_pct,
                "daynight": d.get("daynight", ""),
                "satellite": d.get("satellite", ""),
            })
            n += 1
        log(f"   FIRMS {sensor:<18} {window}  -> {n:>4} detectii in zona")
    return out


def key_of(d: dict) -> str:
    return f"{d['src']}|{d['sensor']}|{d['date']}|{d['time']}|{d['lat']:.4f}|{d['lon']:.4f}"


# --------------------------------------------------------------- 2. ESA FRP
def fetch_esa_frp(client: CDSE, state: dict, lookback_days: int = 2) -> list[dict]:
    """Descarca produsele SL_2_FRP noi care acopera zona si extrage detectiile."""
    done = set(state.get("frp_products", []))
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    try:
        prods = client.odata_products(
            "SENTINEL-3", top=60,
            point=((AOI["bbox"][0] + AOI["bbox"][2]) / 2, (AOI["bbox"][1] + AOI["bbox"][3]) / 2),
            start=start.isoformat(), end=end.isoformat(), match="SL_2_FRP",
        )
    except CDSEError as exc:
        log(f"   [!] ESA FRP: {exc}")
        return []

    # varianta NRT (~2 MB) in locul celei reprocesate NT (~75 MB)
    nrt = [p for p in prods if "_O_NR" in (p.get("Name") or "")]
    fresh = [p for p in (nrt or prods) if p["Name"] not in done]
    if not fresh:
        log(f"   ESA FRP: {len(prods)} produse, niciunul nou")
        return []

    import zipfile
    rows: list[dict] = []
    schemes = {"MWIR1km-standard": "FRP_MWIR1km_standard.csv",
               "MWIR1km-alternative": "FRP_MWIR1km_alternative.csv",
               "SWIR500m": "FRP_SWIR500m.csv"}
    for p in fresh:
        try:
            zp = client.download_product(p["Id"], dest=STATE / "frp")
            with zipfile.ZipFile(zp) as z:
                for scheme, member in schemes.items():
                    name = next((n for n in z.namelist() if n.endswith(member)), None)
                    if not name:
                        continue
                    text = z.read(name).decode("utf-8", "replace")
                    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
                    for d in csv.DictReader(io.StringIO("\n".join(lines))):
                        try:
                            lat, lon = float(d["lat(deg)"]), float(d["lon(deg)"])
                        except (KeyError, ValueError):
                            continue
                        if not in_aoi(lat, lon):
                            continue
                        def num(k):
                            try:
                                return float(d.get(k) or 0)
                            except ValueError:
                                return 0.0
                        rows.append({
                            "src": "ESA", "sensor": f"SLSTR {scheme}",
                            "lat": round(lat, 5), "lon": round(lon, 5),
                            "date": d.get("day"), "time": (d.get("time") or "0000").replace(":", "")[:4],
                            "frp": round(num("FRP(MW)"), 2),
                            "confidence": round(num("confidence(%)") or num("SWIR_SAA_confidence(%)")),
                            "daynight": d.get("D/N", ""),
                            "satellite": d.get("satellite", "S3"),
                            "bt_k": round(num("MWIR_BT(K)"), 1),
                        })
            done.add(p["Name"])
            log(f"   ESA FRP {p['Name'][:44]} -> produs nou procesat")
        except CDSEError as exc:
            log(f"   [!] ESA FRP {p['Name'][:40]}: {exc}")
    state["frp_products"] = sorted(done)[-400:]
    return rows


# --------------------------------------------------------------- 3. imagistica
def fetch_imagery(client: CDSE, state: dict) -> int:
    """Cand apare o scena Sentinel-2 noua peste zona, decupeaza TCI la 60 m."""
    try:
        feats = client.stac_search(["sentinel-2-l2a"],
                                   point=(AOI["center"][1], AOI["center"][0]),
                                   start=(datetime.now(timezone.utc).date() - timedelta(days=12)).isoformat(),
                                   limit=30)
    except CDSEError as exc:
        log(f"   [!] imagistica: {exc}")
        return 0

    added = 0
    for f in sorted(feats, key=lambda x: x["properties"].get("datetime") or "", reverse=True)[:4]:
        rec = normalize_stac(f)
        cloud = f["properties"].get("eo:cloud_cover")
        if cloud is not None and cloud > IMAGERY_CLOUD_MAX:
            continue
        if rec["id"] in state["imagery"]:
            continue
        asset = rec["assets"].get("TCI_60m")
        if not asset or not asset.get("https"):
            continue
        try:
            raw = client.download_asset(asset["https"], filename=f"{rec['id']}_TCI_60m.jp2",
                                        dest=STATE / "img", size=asset.get("size"))
        except CDSEError as exc:
            log(f"   [!] imagistica {rec['id'][:34]}: {exc}")
            continue

        # decupam zona de interes folosind georeferentierea din STAC
        #   x = ox + col*sx  (sx > 0)      y = oy + row*sy  (sy < 0)
        #   deci: nord (y mare) -> rand mic (sus);  sud (y mic) -> rand mare (jos)
        a = f["assets"]["TCI_60m"]
        sx, _, ox, _, sy, oy = a["proj:transform"][:6]
        epsg = int(str(a["proj:code"]).split(":")[1])
        conv = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        w, s, e, n = AOI["bbox"]
        x1, y1 = conv.transform(w, s)
        x2, y2 = conv.transform(e, n)

        with Image.open(raw) as im:
            W, H = im.size
            left = max(0, int((min(x1, x2) - ox) / sx))
            right = min(W, int((max(x1, x2) - ox) / sx))
            top = max(0, int((max(y1, y2) - oy) / sy))
            bottom = min(H, int((min(y1, y2) - oy) / sy))
            if right <= left or bottom <= top:
                log(f"   [!] imagistica {rec['id'][:34]}: fereastra invalida "
                    f"({left},{top},{right},{bottom}) in {W}x{H}")
                continue
            crop = im.crop((left, top, right, bottom)).convert("RGB")

        out = zone_image(f"s2_{rec['id']}.png")
        crop.save(out)
        state["imagery"][rec["id"]] = {
            "file": out.name,
            "datetime": f["properties"].get("datetime"),
            "cloud": cloud,
            "size": list(crop.size),
            "scene": rec["id"],
            "collection": "Sentinel-2 L2A (true color, 60 m)",
        }
        added += 1
        log(f"   imagistica noua: {rec['id'][:46]} (nori {cloud}%) -> {out.name}")

    # pastram doar ultimele 12 imagini pe disc
    for sid in list(state["imagery"])[12:]:
        old = IMG / state["imagery"][sid]["file"]
        old.unlink(missing_ok=True)
        del state["imagery"][sid]
    return added


# ------------------------------------------------------------------- 4. alerte
def send_telegram(text: str) -> bool:
    import os
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return False
    try:
        data = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception as exc:  # noqa: BLE001
        log(f"   [!] Telegram: {type(exc).__name__}: {exc}")
        return False


def raise_alerts(state: dict, new_dets: list[dict]) -> None:
    if not new_dets:
        return
    strong = [d for d in new_dets if d["frp"] >= 5]
    dets = strong or new_dets
    d = max(dets, key=lambda x: x["frp"])
    ce = "focar puternic" if d["frp"] >= 20 else ("focar moderat" if d["frp"] >= 5 else "detecție slabă")
    text = (f"🔥 {ZONE['name']}: {len(new_dets)} detecție/detecții noi\n"
            f"cea mai puternică: {d['frp']:.1f} MW ({ce})\n"
            f"la {d['lat']:.4f}, {d['lon']:.4f} — {d['date']} {d['time']} UTC\n"
            f"sursa: {d['src']} {d['sensor']}")
    log("   ALERTA: " + text.replace("\n", " | "))
    sent = send_telegram(text)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(new_dets),
        "max_frp": d["frp"],
        "lat": d["lat"], "lon": d["lon"],
        "when": f"{d['date']} {d['time']}Z",
        "source": f"{d['src']} {d['sensor']}",
        "telegram": sent,
        "label": f"{len(new_dets)} detecții noi · max {d['frp']:.1f} MW · {d['date']} {d['time']}Z",
    }
    state["alerts"] = ([entry] + state.get("alerts", []))[:60]


# ------------------------------------------------------------ 5. suprafata arsa
def update_burn(client: CDSE, state: dict) -> None:
    """
    Recalculeaza cicatricea de incendiu (dNBR) si scrie web/img/arsura.png.

    Se executa doar cand apare o scena Sentinel-2 noua peste zona — descarcarea
    benzilor B8A/B12 la 20 m inseamna ~120 MB, deci nu o facem la fiecare ciclu.
    """
    import burn_scar

    center = tuple(AOI["center"])
    post = burn_scar.pick_recent(client, center)
    current = (state.get("burn") or {}).get("post_id")
    if current == post["id"]:
        log("   arsura: nicio scena noua, nu recalculez")
        return

    log(f"   arsura: scena noua {post['id'][:44]} (nori {post['properties'].get('eo:cloud_cover')}%) "
        f"— recalculez dNBR (descarc ~120 MB)")
    info = burn_scar.compute(client, center, zone_image("arsura.png"), zone_burn_cache(), post=post)
    state["burn"] = info
    log(f"   arsura: {info['ha_moderat_plus']} ha peste pragul moderat, "
        f"{info['ha_total']} ha peste pragul slab (scena {info['post_date']})")


# ------------------------------------------------- 6. focare si meteo
def load_park_geojson() -> dict | None:
    p = WEB / (AOI.get("park_file") or "")
    if not p or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def update_fires(state: dict, dets: dict) -> None:
    """Grupeaza detectiile in focare distincte, cu tendinta pe 24 h."""
    import fires as fires_mod
    found = fires_mod.find_fires(dets, ZONE, load_park_geojson())
    state["fires"] = found
    if found:
        f = found[0]
        log(f"   focare: {len(found)} · principalul la {f['lat']:.4f},{f['lon']:.4f} "
            f"({f['points']} puncte, {f['days']} zile, FRP 24h {f['frp_last24']} MW, {f['trend']}, "
            f"{f['nearest_place']} {f['nearest_km']} km)")
    else:
        log("   focare: niciunul cu suficiente detectii")


def update_weather(state: dict) -> None:
    """Meteo curent, indice de pericol de incendiu, vant si directie de propagare."""
    from weather import zone_weather
    w = zone_weather(tuple(AOI["center"]))
    state["weather"] = w
    d = w.get("danger") or {}
    log(f"   meteo: {w.get('temp_c')}°C, {w.get('humidity_pct')}% RH, vant {w.get('wind_kmh')} km/h "
        f"din {w.get('wind_from_compass')} -> pana spre {w.get('downwind_compass')}; "
        f"pericol {d.get('class')} (Angström {d.get('index')}); "
        f"precipitatii 48h: {w.get('precip_next48_mm')} mm")


# ------------------------------------------------------------------- statistici
def compute_stats(dets: dict) -> dict:
    rows = list(dets.values())
    now = datetime.now(timezone.utc)
    by_day: dict[str, dict] = {}
    for r in rows:
        day = r["date"]
        b = by_day.setdefault(day, {"date": day, "count": 0, "frp": 0.0, "max": 0.0,
                                    "nasa": 0, "esa": 0, "frp_nasa": 0.0, "frp_esa": 0.0})
        is_nasa = r["src"] == "NASA"
        b["count"] += 1
        b["frp"] += r["frp"]
        b["max"] = max(b["max"], r["frp"])
        b["nasa" if is_nasa else "esa"] += 1
        b["frp_nasa" if is_nasa else "frp_esa"] += r["frp"]
    days = sorted(by_day.values(), key=lambda x: x["date"])
    for b in days:
        b["frp"] = round(b["frp"], 1)
        b["max"] = round(b["max"], 1)
        b["frp_nasa"] = round(b["frp_nasa"], 1)
        b["frp_esa"] = round(b["frp_esa"], 1)

    last24 = [r for r in rows
              if (now - datetime.strptime(f"{r['date']} {r['time'][:4]}", "%Y-%m-%d %H%M")
                  .replace(tzinfo=timezone.utc)) <= timedelta(hours=24)]
    latest = max(rows, key=lambda r: (r["date"], r["time"])) if rows else None
    return {
        "total": len(rows),
        "nasa": sum(1 for r in rows if r["src"] == "NASA"),
        "esa": sum(1 for r in rows if r["src"] == "ESA"),
        "last24_count": len(last24),
        "last24_frp": round(sum(r["frp"] for r in last24), 1),
        "last24_max": round(max((r["frp"] for r in last24), default=0), 1),
        "days": days,
        "latest": latest,
        "updated": now.isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------- ciclu
def cycle_zone(state: dict, client: CDSE, first: bool) -> None:
    """Un ciclu pentru o singura zona. `state` e subarborele zonei."""
    dets: dict = state["detections"]
    before = set(dets)

    log(f"ciclul incepe — {ZONE['name']}")
    # 1. FIRMS
    window = FIRMS_SEED if first else "24h"
    for d in fetch_firms(window):
        dets.setdefault(key_of(d), {**d, "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    # 2. ESA FRP (doar daca avem chei)
    if client.configured:
        # la prima rulare luam 7 zile de istoric, apoi doar ce e nou in ultimele 2 zile
        for d in fetch_esa_frp(client, state, lookback_days=7 if first else 2):
            dets.setdefault(key_of(d), {**d, "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    else:
        log("   ESA FRP: sar peste (lipsesc cheile CDSE din .env)")

    new_keys = [k for k in dets if k not in before]
    new_dets = [dets[k] for k in new_keys]
    log(f"   total detectii in zona: {len(dets)}  ({len(new_dets)} noi)")

    # 3. imagistica
    if client.configured:
        try:
            fetch_imagery(client, state)
        except Exception as exc:  # noqa: BLE001
            log(f"   [!] imagistica: {type(exc).__name__}: {exc}")
        try:
            update_burn(client, state)
        except Exception as exc:  # noqa: BLE001
            log(f"   [!] arsura: {type(exc).__name__}: {exc}")

    # 4. alerte (nu alertam la prima rulare - ar fi sute de detectii istorice deodata)
    if not first and new_dets:
        raise_alerts(state, new_dets)
    elif first:
        log(f"   prima rulare: import istoric, fara alerte ({len(new_dets)} detectii incarcate)")

    # 5. focare distincte
    try:
        update_fires(state, dets)
    except Exception as exc:  # noqa: BLE001
        log(f"   [!] focare: {type(exc).__name__}: {exc}")

    # 6. meteo si pericol de incendiu
    try:
        update_weather(state)
    except Exception as exc:  # noqa: BLE001
        log(f"   [!] meteo: {type(exc).__name__}: {exc}")

    state["cycles"] = state.get("cycles", 0) + 1
    state["stats"] = compute_stats(dets)
    state["aoi"] = dict(AOI)
    state["id"] = ZONE["id"]
    state["name"] = ZONE["name"]
    state["short"] = ZONE.get("short") or ZONE["name"]
    s = state["stats"]
    log(f"   gata: {s['total']} detectii, {s['last24_count']} in 24h, FRP 24h {s['last24_frp']} MW")


# ------------------------------------------------------------------ retentie
# Fara curatare, state.json creste la infinit: fiecare detectie rămâne pe
# vecie, fisierul se rescrie la 5 minute si se publica pe Pages. Dupa o luna
# ajunge la cativa MB, deci il incarcam degeaba pe telefon la fiecare refresh.
#
# Pastram RETENTION_DAYS zile de istoric (implicit 21). Un foc activ are
# zeci-sute de detectii pe zi, deci 21 de zile acopera cu varf orice incendiu
# real si tot lasa graficul FRP pe ultimele 3 saptamani.
#
# Atentie: nu taiem din focarele active — o detectie veche care face parte
# dintr-un foc inca aprins se pastreaza, altfel pierdem numaratoarea corecta.
RETENTION_DAYS = int(os.getenv("DOMOGLED_RETENTION_DAYS", "21"))


def prune_detections(state: dict) -> int:
    """Scoate detectiile mai vechi de RETENTION_DAYS. Returneaza cate a sters."""
    dets = state.get("detections") or {}
    if not dets:
        return 0

    # limitele focarelor active: orice detectie care contribuie la un foc
    # raportat acum rămâne, oricat de veche ar fi.
    active_first = set()
    for f in (state.get("fires") or []):
        first = (f.get("first") or "")[:10]
        if first:
            active_first.add(first)

    # data de referinta = acum (UTC), nu ultima detectie — altfel un import
    # de istoric vechi ar pastra tot pentru ca "acum" ar fi tot vechi.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).date()

    drop = []
    for key, d in dets.items():
        dtxt = (d.get("date") or "")[:10]
        if not dtxt:
            continue
        try:
            dday = date.fromisoformat(dtxt)
        except ValueError:
            continue
        if dday >= cutoff:
            continue
        # pastreaza daca apartine unui foc activ
        if dtxt in active_first:
            continue
        drop.append(key)

    for key in drop:
        dets.pop(key, None)
    return len(drop)


def cycle(state: dict, client: CDSE, first: bool) -> None:
    """Un ciclu pentru toate zonele, apoi o singura scriere de stare."""
    zones = state.setdefault("zones", {})
    state["zone_list"] = [{k: z.get(k) for k in ("id", "name", "short", "bbox", "center",
                                                 "zoom", "places", "park")}
                          for z in ZONES]
    state["default_zone"] = ZONE_CFG.get("default_zone") or ZONES[0]["id"]
    for z in ZONES:
        set_zone(z)
        zs = zones.setdefault(z["id"], {"detections": {}, "alerts": [], "imagery": {},
                                        "frp_products": [], "cycles": 0})
        empty = not zs["detections"]
        try:
            cycle_zone(zs, client, first or empty)
        except Exception:  # noqa: BLE001
            log(f"[!] zona {z['id']} a eșuat:\n" + traceback.format_exc())
        save_state(state)          # scriem dupa fiecare zona, ca un eșec sa nu piarda restul
        # curatam istoricul prea vechi (vezi prune_detections)
        try:
            n = prune_detections(zs)
            if n:
                log(f"   retentie: sterse {n} detectii mai vechi de {RETENTION_DAYS} zile")
        except Exception as exc:  # noqa: BLE001
            log(f"   [!] retentie: {type(exc).__name__}: {exc}")
    state["cycles"] = state.get("cycles", 0) + 1
    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)

    # predictia urmatoarelor treceri satelit (countdown pe dashboard).
    # O rulam aici, dupa ce state.json e la zi, ca predictorul sa vada
    # ultimele produse FRP catalogate.
    try:
        import sat_passes
        sat_passes.main()
    except Exception as exc:  # noqa: BLE001
        log(f"   [!] predictie treceri: {type(exc).__name__}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=POLL_SECONDS, help="secunde intre cicluri")
    ap.add_argument("--once", action="store_true", help="un singur ciclu")
    ap.add_argument("--force", action="store_true", help="ignora lock-ul altui colector")
    args = ap.parse_args()

    acquire_lock(args.interval, args.force)
    state = load_state()
    client = CDSE()
    first = not any((state.get("zones") or {}).get(z["id"], {}).get("detections") for z in ZONES)
    n_det = sum(len((state.get("zones") or {}).get(z["id"], {}).get("detections") or {}) for z in ZONES)
    log(f"pornire collector — {len(ZONES)} zone {[z['id'] for z in ZONES]}, "
        f"{n_det} detectii in stare, chei CDSE: {'da' if client.configured else 'NU'}")

    try:
        while True:
            t0 = time.time()
            try:
                cycle(state, client, first)
            except Exception:  # noqa: BLE001
                log("[!] ciclu eșuat:\n" + traceback.format_exc())
            first = False
            touch_lock()
            if args.once:
                break
            wait = max(30, args.interval - (time.time() - t0))
            log(f"astept {wait:.0f}s pana la urmatorul ciclu\n")
            time.sleep(wait)
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
