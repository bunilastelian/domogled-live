#!/usr/bin/env python3
"""
Harness de analiza pentru monitorul de incendii Domogled.

Ruleaza la fiecare 3 ore (systemd timer) si face:
  1. citeste starea curenta (state.json produs de collector)
  2. compara cu istoricul propriu  -> a evoluat sau nu?
  3. analizeaza vizual scenele satelitare (model cu viziune)
  4. sintetizeaza un raport + un verdict de evolutie
  5. salveaza in memorie (JSONL) si scrie un fisier pentru dashboard
  6. (opțional) trimite alerta in Telegram, doar cand e ceva de semnalat

Rulare:
    python harness.py              # o analiza completa
    python harness.py --no-vision  # doar cifre, fara apeluri de viziune
    python harness.py --dry        # nu scrie nimic, doar afiseaza

De ce un serviciu separat si nu in collector:
  - collector-ul are ciclu de 5 min si trebuie sa rămâna rapid si robust;
    analiza cu LLM dureaza 10-40s si poate eșua fara sa afecteze colectarea.
  - bugetul: 8 rulari/zi * (1-3 imagini) tine costul sub $0.10/zi.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import harness_memory as MEM
import harness_vision as VIS
import harness_context as CTX

ROOT = Path("/home/lili/projects/domogled-live")
STATE = ROOT / "live" / "state" / "state.json"
OUT_JSON = ROOT / "live" / "web" / "state" / "harness.json"
LOG = ROOT / "logs" / "harness.log"
ALERT_STATE = ROOT / "live" / "state" / "harness_alerts.json"

# praguri de alertare. Un foc de 8 zile fluctueaza natural cu +-5% pe 3h;
# sub aceste praguri am face spam fara informatie.
FRP_JUMP_PCT = 20.0        # crestere FRP 24h care merita alerta
DET_JUMP_ABS = 25          # detectii noi in 24h peste media recenta
QUIET_HOURS = 12           # rezumat de liniste la fiecare N ore

TELEGRAM_CHAT = os.getenv("DOMOGLED_TELEGRAM_CHAT", "1284299504")


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_state() -> dict:
    if not STATE.exists():
        raise SystemExit(f"lipseste {STATE} - a rulat collectorul?")
    return json.loads(STATE.read_text(encoding="utf-8"))


def zone_context(zid: str, z: dict) -> str:
    """Datele numerice ale zonei, ca text pentru promptul de viziune.

    Varianta compacta - aici spatiul e pretios pentru ca trimitem si imagini.
    Pentru sinteza folosim harness_context.zone_context (varianta completa).
    """
    return CTX.zone_context_compact(zid, z)


def synthesize(state: dict, cmp: dict, vision: dict, ctx_text: str) -> str:
    """Cererea de sinteza catre model: evolutie + predictie.

    Trimitem DOAR text (fara imagini) - viziunea s-a ocupat deja de imagini
    si ne-a intors descrieri. Asa nu platim de doua ori pentru aceleasi pixeli.
    """
    zones_desc = []
    for zid, z in (state.get("zones") or {}).items():
        v = vision.get(zid) or {}
        blk = [f"===== ZONA {z.get('name') or zid} =====",
               CTX.zone_context(zid, z)]
        d = (cmp.get("zones") or {}).get(zid) or {}
        changes = CTX.comparison_line(zid, d, cmp.get("hours_elapsed"))
        if changes:
            blk.append("SCHIMBARI fata de rularea anterioara:\n  " +
                       "\n  ".join(changes))
        else:
            blk.append("SCHIMBARI: (fara diferenta masurabila)")
        if v.get("current"):
            blk.append(f"ANALIZA VIZUALA a scenei {str(v.get('current_date'))[:10]}:\n  "
                       + v["current"].replace("\n", "\n  "))
        if v.get("comparison"):
            blk.append(f"COMPARATIE INTRE SCENE ({', '.join(v.get('compared') or [])}):\n  "
                       + v["comparison"].replace("\n", "\n  "))
        if v.get("burn_scar"):
            blk.append(f"CICATRICE (citire vizuala dNBR): {v['burn_scar']}")
        if v.get("errors"):
            blk.append(f"ERORI VIZIUNE: {'; '.join(v['errors'])}")
        zones_desc.append("\n".join(blk))

    prompt = f"""Esti analist de incendii de vegetatie pentru Carpatii Meridionali. \
Analizezi monitorul automat al Parcului National Domogled - Valea Cernei si al zonei Portile de Fier.

DATA ANALIZEI: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

ISTORIC (ultimele rulari ale tale, cel mai vechi primul):
{ctx_text}

DATE CURENTE:
{chr(10).join(zones_desc)}

Scrie un raport structurat, in ROMANA, maxim 350 de cuvinte:

EVOLUTIE: A evoluat focul fata de istoricul de mai sus, sau e stationar? \
Spune explicit: ESCALADARE / STAGNARE / SCADERE. Justifica cu cifre (delta FRP, \
delta detectii, focare noi/stinse).

CE ARATA IMAGINILE: Ce confirma sau infirma analiza vizuala fata de cifre. \
Daca imaginea contrazice senzorii (ex: multi detectori dar fara fum vizibil), \
spune asta - e o informatie importanta, nu un conflict de ascuns.

RISC: Urmatoarele 24-48h. Vant, umiditate, precipitatii, uscaciune. \
In ce directie s-ar putea extinde? Ce zone/localitati sunt expuse?

PREDICTIE: Estimeaza pentru urmatoarele 24h: FRP-ul va creste, scade sau stagna? \
Da o probabilitate estimata si spune clar ca e o estimare bazata pe trend, \
NU un model fizic de propagare a focului.

SEMNALE: Orice anomalie - detectii fara focar, focar fara detectii noi, \
date lipsa, imagini cu nori care ascund zona.

Fii concret si onest. Nu inventa date care nu sunt mai sus. Daca ceva lipseste, scrie ca lipseste."""

    return _llm_text(prompt, 1400)


def _llm_text(prompt: str, max_tokens: int) -> str:
    """Apel text pur catre deepseek-flash (fara imagini)."""
    key = None
    for line in (Path.home() / ".hermes" / ".env").read_text().splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            key = line.split("=", 1)[1].strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY lipseste")

    body = {"model": os.getenv("DOMOGLED_TEXT_MODEL", "deepseek-flash"),
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    return (d["choices"][0]["message"].get("content") or "").strip()


def verdict(cmp: dict) -> str:
    """Verdict mecanic de evolutie, calculat din cifre (nu de la LLM).

    Avem nevoie de un verdict determinist pentru a decide alertarea:
    nu putem baza o alerta pe textul generat, care poate varia intre rulari.
    """
    esc = stag = scad = 0
    for d in (cmp.get("zones") or {}).values():
        dfrp = d.get("last24_frp")
        if d.get("new_fires"):
            esc += 1
        if dfrp is None:
            continue
        if dfrp > FRP_JUMP_PCT / 100 * max(1, abs(d.get("last24_frp") or 1)) or dfrp > 15:
            esc += 1
        elif dfrp < -15:
            scad += 1
        else:
            stag += 1

    if cmp.get("first_run"):
        return "PRIMA_RULARE"
    if esc and not scad:
        return "ESCALADARE"
    if scad and not esc:
        return "SCADERE"
    if esc and scad:
        return "MIXT"
    return "STAGNARE"


def should_alert(state: dict, cmp: dict, vd: str) -> tuple[bool, str]:
    """Decide daca merită sa trimitem alerta. Scopul: zero spam."""
    prev = {}
    if ALERT_STATE.exists():
        try:
            prev = json.loads(ALERT_STATE.read_text())
        except json.JSONDecodeError:
            prev = {}

    last_ts = prev.get("last_alert_ts")
    quiet_since = prev.get("last_any_ts")

    # alerta imediata pe escaladare
    if vd == "ESCALADARE":
        return True, "escaladare"

    # focar nou
    for d in (cmp.get("zones") or {}).values():
        if d.get("new_fires"):
            return True, "focar nou"

    # rezumat de liniste: daca n-am vorbit de QUIET_HOURS, spunem cum sta
    if quiet_since:
        try:
            hrs = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(quiet_since)).total_seconds() / 3600
            if hrs >= QUIET_HOURS:
                return True, f"rezumat la {hrs:.0f}h"
        except ValueError:
            pass
    return False, ""


def send_telegram(text: str) -> bool:
    """Trimite in Telegram. Folosim botul deja configurat pentru Hermes."""
    env = {}
    p = Path.home() / ".hermes" / ".env"
    for line in p.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    tok = env.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        log("[!] TELEGRAM_BOT_TOKEN lipseste - nu trimit alerta")
        return False
    body = json.dumps({"chat_id": TELEGRAM_CHAT, "text": text,
                       "parse_mode": "Markdown",
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:  # noqa: BLE001
        log(f"[!] telegram: {type(e).__name__}: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-vision", action="store_true", help="fara apeluri de viziune")
    ap.add_argument("--dry", action="store_true", help="nu scrie nimic")
    ap.add_argument("--force-alert", action="store_true", help="trimite alerta mereu")
    args = ap.parse_args()

    log("--- harness pornit ---")
    state = read_state()

    cmp = MEM.compare(state)
    ctx_text = MEM.context_text(state)
    log(f"istoric: {len(MEM.load_history())} snapshot-uri | "
        f"comparatie: {cmp.get('hours_elapsed')}h")

    vision: dict = {}
    if not args.no_vision:
        for zid, z in (state.get("zones") or {}).items():
            try:
                vision[zid] = VIS.analyze_zone(zid, z, zone_context(zid, z))
                errs = vision[zid].get("errors") or []
                log(f"viziune {zid}: ok={vision[zid].get('ok')} erori={len(errs)}")
                for e in errs:
                    log(f"    [!] {e}")
            except Exception as e:  # noqa: BLE001
                log(f"[!] viziune {zid} a eșuat: {type(e).__name__}: {e}")
                vision[zid] = {"ok": False, "errors": [str(e)[:200]]}

    try:
        report = synthesize(state, cmp, vision, ctx_text)
    except Exception as e:  # noqa: BLE001
        log(f"[!] sinteza a eșuat: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        report = ""

    vd = verdict(cmp)
    log(f"verdict mecanic: {vd}")

    result = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": vd,
        "comparison": cmp,
        "vision": vision,
        "report": report,
        "state_updated": state.get("updated"),
    }

    if args.dry:
        print(json.dumps(result, ensure_ascii=False, indent=2)[:6000])
        return 0

    snap = MEM.snapshot(state, {"verdict": vd, "report": report,
                                "vision_ok": {k: v.get("ok") for k, v in vision.items()}})
    MEM.deliver(snap, snap["analysis"])
    log(f"memorie: scris snapshot {snap['ts']}")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"scris {OUT_JSON}")

    do, why = should_alert(state, cmp, vd)
    if args.force_alert:
        do, why = True, "fortat"
    if do and report:
        txt = _alert_text(result)
        ok = send_telegram(txt)
        log(f"alerta ({why}): {'trimisa' if ok else 'EȘUATA'}")
        ALERT_STATE.write_text(json.dumps({
            "last_alert_ts": result["generated"] if ok else (json.loads(
                ALERT_STATE.read_text()).get("last_alert_ts") if ALERT_STATE.exists() else None),
            "last_any_ts": result["generated"],
            "reason": why,
        }, indent=2), encoding="utf-8")
    else:
        log(f"fara alerta ({why or 'nimic semnificativ'})")
        prev = {}
        if ALERT_STATE.exists():
            try:
                prev = json.loads(ALERT_STATE.read_text())
            except json.JSONDecodeError:
                prev = {}
        prev["last_any_ts"] = result["generated"]
        ALERT_STATE.write_text(json.dumps(prev, indent=2), encoding="utf-8")

    log("--- harness terminat ---")
    return 0


def _alert_text(result: dict) -> str:
    """Mesajul scurt de Telegram. Reportul complet e pe dashboard."""
    vd = result["verdict"]
    emoji = {"ESCALADARE": "🔴", "SCADERE": "🟢", "STAGNARE": "⚪",
             "MIXT": "🟡", "PRIMA_RULARE": "🔵"}.get(vd, "⚪")

    head = f"{emoji} *Domogled — {vd}*\n"
    lines = []
    for zid, d in (result.get("comparison") or {}).get("zones", {}).items():
        parts = []
        if d.get("last24_frp") is not None:
            parts.append(f"FRP24h {d['last24_frp']:+.0f} MW")
        if d.get("last24_count") is not None:
            parts.append(f"det24h {d['last24_count']:+.0f}")
        for nf in (d.get("new_fires") or []):
            parts.append(f"FOCAR NOU {nf.get('lat')},{nf.get('lon')}")
        if parts:
            lines.append(f"• {zid}: " + ", ".join(parts))

    body = (result.get("report") or "")[:900]
    return head + "\n".join(lines) + "\n\n" + body


if __name__ == "__main__":
    raise SystemExit(main())
