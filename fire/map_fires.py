#!/usr/bin/env python
"""
map_fires.py - harta focarelor din regiunea Domogled - Valea Cernei.

Panoul 1: regiunea larga - toate detectiile FIRMS, ariile protejate din OSM, orasele.
Panoul 2: zoom pe incendiul din Parcul National Domogled - Valea Cernei, cu
          detectiile colorate pe zile si marcaj pentru Cascada Cociului.

Rulare: python map_fires.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
CACHE = HERE / "cache"
OUT.mkdir(exist_ok=True)

# repere geografice reale (OSM / Nominatim)
PLACES = {
    "Băile Herculane": (44.8785, 22.4144),
    "Orșova": (44.7235, 22.3983),
    "Drobeta-Turnu Severin": (44.6258, 22.6532),
    "Cerna-Sat": (45.1285, 22.6858),
    "Cascada Cociului": (44.91838, 22.46485),
}
# focarul principal (cluster 1 din FIRMS)
MAIN_FIRE = (44.8982, 22.4785)
# poligonul Parcului National Domogled - Valea Cernei, pentru zoom
DOMOGLED_BOX = (22.30, 44.82, 22.68, 45.02)


def load_park(name: str):
    p = CACHE / name
    if not p.exists():
        return None
    geom = json.loads(p.read_text(encoding="utf-8"))[0]["geojson"]
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return polys


def plot_park(ax, polys, color, label=None, lw=1.2):
    if not polys:
        return
    for poly in polys:
        for ring in poly[:1]:
            xs = [pt[0] for pt in ring]
            ys = [pt[1] for pt in ring]
            ax.plot(xs, ys, color=color, lw=lw, alpha=0.85, zorder=1)


def main() -> int:
    det_path = OUT / "detections.csv"
    if not det_path.exists():
        print("Lipseste out/detections.csv - ruleaza intai fire_analysis.py")
        return 1
    with open(det_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
        r["frp_mw"] = float(r["frp_mw"]) if r["frp_mw"] else 0.0

    domogled = load_park("osm_park_domogled.json")
    portile = load_park("osm_park_portiledefier.json")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 10), facecolor="white")

    # ---------------- panoul 1: regiunea
    if portile:
        plot_park(ax1, portile, "#2e7d32", "Parcul Natural Porțile de Fier")
    if domogled:
        plot_park(ax1, domogled, "#1565c0", "Parcul Național Domogled-Valea Cernei")

    days = sorted({r["date"] for r in rows})
    cmap = plt.get_cmap("autumn_r", len(days))
    day_color = {d: cmap(i) for i, d in enumerate(days)}

    # dimensiunea punctului ~ FRP; noaptea = contur negru
    for r in rows:
        ax1.scatter(r["lon"], r["lat"], s=8 + r["frp_mw"] * 3.2, c=[day_color[r["date"]]],
                    alpha=0.72, linewidths=0.5,
                    edgecolors="black" if r["daynight"] == "N" else "none", zorder=3)

    ax1.set_title("Detecții termice NASA FIRMS (30 sep – 7 oct 2026)\n"
                  "mărimea punctului = FRP (MW); contur negru = detecție nocturnă",
                  fontsize=13)
    ax1.set_xlabel("longitudine (°E)")
    ax1.set_ylabel("latitudine (°N)")
    ax1.set_xlim(21.7, 23.3)
    ax1.set_ylim(44.3, 45.6)
    ax1.grid(alpha=0.25, ls=":")
    ax1.set_aspect(1 / 0.72)

    # marcaje orase pe panoul 1
    for name, (lat, lon) in PLACES.items():
        marker = "*" if "Cociului" in name else "s"
        ax1.plot(lon, lat, marker, color="#d81b60" if marker == "*" else "black",
                 markersize=10 if marker == "*" else 5, zorder=5)
        ax1.annotate(name, (lon, lat), textcoords="offset points", xytext=(6, 4),
                     fontsize=8.5, color="#333333", zorder=6)

    legend = [Patch(facecolor=day_color[d], label=d) for d in days]
    legend.append(Line2D([], [], color="#1565c0", label="P.N. Domogled-Valea Cernei"))
    legend.append(Line2D([], [], color="#2e7d32", label="P.N. Porțile de Fier"))
    ax1.legend(handles=legend, loc="upper left", fontsize=8, ncol=2, framealpha=0.9)

    ax1.annotate("incendiul din Parcul Național\nDomogled (271 detecții, 8 zile)",
                 xy=MAIN_FIRE, xytext=(22.62, 44.98), fontsize=9.5, color="#b71c1c",
                 arrowprops=dict(arrowstyle="->", color="#b71c1c", lw=1.4), zorder=7)

    # ---------------- panoul 2: zoom pe Domogled
    if domogled:
        plot_park(ax2, domogled, "#1565c0")

    zooms = [r for r in rows if DOMOGLED_BOX[0] <= r["lon"] <= DOMOGLED_BOX[2]
             and DOMOGLED_BOX[1] <= r["lat"] <= DOMOGLED_BOX[3]]
    for r in zooms:
        ax2.scatter(r["lon"], r["lat"], s=25 + r["frp_mw"] * 5, c=[day_color[r["date"]]],
                    alpha=0.8, linewidths=0.6,
                    edgecolors="black" if r["daynight"] == "N" else "none", zorder=3)

    for name, (lat, lon) in PLACES.items():
        if not (DOMOGLED_BOX[0] - 0.05 <= lon <= DOMOGLED_BOX[2] + 0.05
                and DOMOGLED_BOX[1] - 0.05 <= lat <= DOMOGLED_BOX[3] + 0.05):
            continue
        marker = "*" if "Cociului" in name else "s"
        ax2.plot(lon, lat, marker, color="#d81b60" if marker == "*" else "black",
                 markersize=16 if marker == "*" else 6, zorder=5)
        ax2.annotate(name, (lon, lat), textcoords="offset points", xytext=(8, 5),
                     fontsize=10, color="#222222", zorder=6)

    # distanta focar -> cascada
    ax2.plot([MAIN_FIRE[1], PLACES["Cascada Cociului"][1]],
             [MAIN_FIRE[0], PLACES["Cascada Cociului"][0]],
             "--", color="#6a1b9a", lw=1.3, zorder=4)
    ax2.annotate("2,5 km", ((MAIN_FIRE[1] + PLACES["Cascada Cociului"][1]) / 2,
                            (MAIN_FIRE[0] + PLACES["Cascada Cociului"][0]) / 2),
                 textcoords="offset points", xytext=(6, -14), fontsize=10,
                 color="#6a1b9a", fontweight="bold", zorder=6)

    ax2.plot(MAIN_FIRE[1], MAIN_FIRE[0], "x", color="#b71c1c", markersize=13,
             markeredgewidth=2.5, zorder=6)
    ax2.annotate(f"focar principal\n{MAIN_FIRE[0]:.4f} N, {MAIN_FIRE[1]:.4f} E",
                 MAIN_FIRE, textcoords="offset points", xytext=(10, -22), fontsize=9.5,
                 color="#b71c1c", zorder=6)

    ax2.set_title("Zoom: incendiul din Parcul Național Domogled-Valea Cernei\n"
                  f"{len(zooms)} detecții în 8 zile consecutive", fontsize=13)
    ax2.set_xlabel("longitudine (°E)")
    ax2.set_ylabel("latitudine (°N)")
    ax2.set_xlim(DOMOGLED_BOX[0], DOMOGLED_BOX[2])
    ax2.set_ylim(DOMOGLED_BOX[1], DOMOGLED_BOX[3])
    ax2.grid(alpha=0.25, ls=":")
    ax2.set_aspect(1 / 0.72)

    fig.suptitle("Incendiile de vegetație din zona Domogled – Valea Cernei / Băile Herculane\n"
                 "sursa detecțiilor: NASA FIRMS (VIIRS 375 m + MODIS 1 km), 30.09–07.10.2026",
                 fontsize=15, y=0.98)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out = OUT / "harta_focare.png"
    fig.savefig(out, dpi=115)
    print(f"Scris: {out}")

    # statistica pe zone
    print(f"\nDetectii in cadru zoom: {len(zooms)}")
    inpark = 0
    if domogled:
        print("(vezi fire_analysis.py pentru testul punct-in-poligon)")
    print(f"\nTotal detectii regiune: {len(rows)}")
    by_day = defaultdict(int)
    for r in rows:
        by_day[r["date"]] += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
