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
import gzip
import json
import mimetypes
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

# fisierele care merita comprimate (GitHub Pages face asta in productie, deci facem
# si local — altfel testam un comportament nereprezentativ)
COMPRESSIBLE = {".json", ".js", ".html", ".geojson", ".css", ".svg", ".txt", ".map"}

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

    def _send(self, body: bytes, ctype: str, encoding: str | None = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "max-age=60")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static_compressed(self, url_path: str) -> bool:
        """Serveste un fisier static, comprimat cu gzip daca se poate."""
        rel = url_path.lstrip("/") or "index.html"
        f = (WEB / rel).resolve()
        if WEB not in f.parents and f != WEB:            # fara iesire din director
            return False
        if not f.is_file():
            return False
        suffix = f.suffix.lower()
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if "charset" not in ctype and ctype.startswith(("text/", "application/json", "image/svg")):
            ctype += "; charset=utf-8"
        data = f.read_bytes()
        if suffix in COMPRESSIBLE and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            gz = gzip.compress(data, 6)
            if len(gz) < len(data):
                self._send(gz, ctype, "gzip")
                return True
        self._send(data, ctype)
        return True

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

        if self._serve_static_compressed(path):
            return
        self.send_error(404, "Not Found")


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
