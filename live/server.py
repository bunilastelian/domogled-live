#!/usr/bin/env python
"""
server.py - serveste dashboard-ul live si starea scrisa de collector.py.

   http://127.0.0.1:8777/            dashboard
   http://127.0.0.1:8777/api/state   starea curenta (JSON)
   http://127.0.0.1:8777/api/health  stare server + varsta datelor

Rulare:  python server.py [--port 8777]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
STATE_FILE = HERE / "state" / "state.json"
PARK_SRC = HERE.parent / "fire" / "cache" / "osm_park_domogled.json"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def ensure_assets() -> None:
    """Copiaza conturul parcului in web/ la prima rulare."""
    WEB.mkdir(parents=True, exist_ok=True)
    dst = WEB / "domogled.geojson"
    if not dst.exists() and PARK_SRC.exists():
        data = json.loads(PARK_SRC.read_text(encoding="utf-8"))
        geom = data[0]["geojson"]
        dst.write_text(json.dumps(geom), encoding="utf-8")
        print(f"[ok] contur parc scris in {dst}")
    (WEB / "img").mkdir(exist_ok=True)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, fmt, *args):  # mai putin zgomot in consola
        if "/api/" in (self.path or ""):
            return
        sys.stderr.write(f"  {self.address_string()} - {fmt % args}\n")

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = (self.path or "/").split("?")[0]

        if path == "/api/state":
            if not STATE_FILE.exists():
                return self._json({"error": "collectorul nu a rulat inca",
                                   "hint": "porneste collector.py", "detections": {}, "stats": None})
            try:
                return self._json(json.loads(STATE_FILE.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                return self._json({"error": "state.json se scrie acum, reincearca"}, 503)

        if path == "/api/health":
            if not STATE_FILE.exists():
                return self._json({"collector": "nu a rulat niciodata"})
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            upd = (st.get("stats") or {}).get("updated")
            age = None
            if upd:
                age = round((datetime.now(timezone.utc)
                             - datetime.fromisoformat(upd)).total_seconds())
            return self._json({"collector": "ok", "cicluri": st.get("cycles"),
                               "ultima_actualizare": upd, "varsta_secunde": age,
                               "detectii": len(st.get("detections", {}))})

        return super().do_GET()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    ensure_assets()
    srv = ThreadingHTTPServer((args.host, args.port), partial(Handler))
    print(f"\n  Dashboard live:  http://{args.host}:{args.port}/")
    print(f"  Stare JSON:      http://{args.host}:{args.port}/api/state")
    print(f"  Sanatate:        http://{args.host}:{args.port}/api/health")
    print("\n  Ctrl+C pentru oprire.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  oprit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
