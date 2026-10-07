#!/usr/bin/env python
"""
check_secrets.py - garda anti-secrete, inainte de commit.

Verifica daca valorile sensibile din .env apar in vreun fisier care ar putea ajunge
in git. Afiseaza DOAR numele fisierelor, niciodata valoarea.

Se uita doar la variabilele al caror nume arata a secret (SECRET, TOKEN, KEY, _ID),
mai putin cele care sunt evident publice (…_URL, …_ENDPOINT, …_DIR, …_REGION).

Rulare:  python check_secrets.py
Cod de iesire: 0 = curat, 1 = secret gasit in afara .env
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"

SENSIBIL = re.compile(r"(SECRET|TOKEN|KEY|PASSWORD|PASSWD|APIKEY)|(_ID)$", re.I)
PUBLIC = re.compile(r"(_URL|_ENDPOINT|_DIR|_REGION|_PATH)$", re.I)

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
             "data", "cache", "out"}          # 'out' = rezultate regenerate
SKIP_SUFFIX = {".zip", ".nc", ".jp2", ".tif", ".tiff", ".png", ".jpg", ".jpeg",
               ".gif", ".exe", ".dll", ".pyc", ".pdf", ".woff", ".woff2"}
MAX_SIZE = 3_000_000


def secrets_from_env() -> list[tuple[str, str]]:
    if not ENV.exists():
        print(f"[!] nu exista {ENV} — nimic de verificat")
        return []
    out = []
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = (x.strip() for x in line.split("=", 1))
        if len(val) < 12 or PUBLIC.search(key) or not SENSIBIL.search(key):
            continue
        out.append((key, val))
    return out


def is_ignored(rel: str) -> bool:
    r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", rel],
                       capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    secrets = secrets_from_env()
    if not secrets:
        return 0
    print(f"valori sensibile de verificat: {len(secrets)} ({', '.join(k for k, _ in secrets)})")

    env_ignored = is_ignored(".env")
    print(f".env ignorat de git: {'DA' if env_ignored else 'NU — PROBLEMA!'}\n")

    hits: list[tuple[str, str]] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in SKIP_SUFFIX:
                continue
            try:
                if p.stat().st_size > MAX_SIZE:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for name, value in secrets:
                if value in text:
                    hits.append((str(p.relative_to(ROOT)).replace("\\", "/"), name))

    print(f"fisiere text scanate: {scanned}")
    inafara = [(p, n) for p, n in hits if p != ".env"]
    if not inafara:
        print("REZULTAT: curat — nicio secretă în afara .env")
    else:
        print("REZULTAT: ATENȚIE, secrete gasite in:")
        for path, name in inafara:
            print(f"   {path}   ({name})")

    if not env_ignored or inafara:
        print("\nNU comite pana nu rezolvi ce e mai sus.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
