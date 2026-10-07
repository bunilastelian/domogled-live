"""
streamlit_app.py - consola de analiza pentru incendiile monitorizate.

Citeste aceeasi stare pe care o scrie collector.py si o serveste interactiv:
harta, focare, tabel de detectii cu filtre si descarcare, grafic de intensitate,
meteo si indice de pericol.

Rulare locala:
    streamlit run streamlit_app.py

Gazduire gratuita: Streamlit Community Cloud, deployat din acest repository.
Are nevoie de requirements.txt in radacina repo-ului.
"""

from __future__ import annotations

import json
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "live" / "state" / "state.json"
WEB = ROOT / "live" / "web"

st.set_page_config(page_title="Incendii Copernicus · analiză", page_icon="🔥", layout="wide")


# ------------------------------------------------------------------ incarcare
@st.cache_data(ttl=60, show_spinner=False)
def load_state(path: str, mtime: float) -> dict:
    """mtime intra in cheia de cache, ca sa se reincarce cand colectorul scrie."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def state_mtime() -> float:
    return STATE.stat().st_mtime if STATE.exists() else 0.0


if not STATE.exists():
    st.error(f"Nu găsesc starea la `{STATE}`. Rulează întâi `python live/collector.py --once`.")
    st.stop()

data = load_state(str(STATE), state_mtime())
zones = data.get("zones") or {}
zone_list = data.get("zone_list") or []

if not zones:
    st.error("Starea nu conține nicio zonă. Rulează colectorul.")
    st.stop()


# --------------------------------------------------------------------- antet
head_l, head_r = st.columns([3, 1])
with head_l:
    st.title("🔥 Incendii de vegetație · analiză")
with head_r:
    st.caption("date Copernicus + NASA FIRMS")
    if st.button("↻ Reîncarcă datele", width='stretch'):
        st.cache_data.clear()
        st.rerun()

c1, c2, c3, c4 = st.columns(4)
with c1:
    zone_id = st.selectbox("Zona", [z["id"] for z in zone_list],
                           format_func=lambda i: next(z["name"] for z in zone_list if z["id"] == i))
with c2:
    hours = st.select_slider("Interval", options=[6, 12, 24, 72, 168, 0],
                             value=72, format_func=lambda h: "tot istoricul" if h == 0 else f"{h} h")
with c3:
    frp_min = st.slider("FRP minim (MW)", 0.0, 50.0, 0.0, 0.5)
with c4:
    srcs = st.multiselect("Surse", ["NASA", "ESA"], default=["NASA", "ESA"])

z = zones.get(zone_id) or {}
aoi = z.get("aoi") or {}
stats = z.get("stats") or {}
fires = z.get("fires") or []
weather = z.get("weather") or {}
burn = z.get("burn") or {}

# --------------------------------------------------------------- pregatire date
rows = []
for d in (z.get("detections") or {}).values():
    if d.get("src") not in srcs:
        continue
    if (d.get("frp") or 0) < frp_min:
        continue
    t = pd.to_datetime(f"{d['date']} {str(d.get('time', '0000')).zfill(4)[:2]}:{str(d.get('time','0000')).zfill(4)[2:4]}",
                       utc=True, errors="coerce")
    if pd.isna(t):
        continue
    if hours:
        if (pd.Timestamp.now(tz="UTC") - t).total_seconds() / 3600 > hours:
            continue
    rows.append({
        "timp_utc": t,
        "sursa": d.get("src"),
        "senzor": d.get("sensor"),
        "lat": d.get("lat"),
        "lon": d.get("lon"),
        "frp_mw": d.get("frp"),
        "incredere_pct": d.get("confidence"),
        "zi_noapte": "zi" if d.get("daynight") == "D" else "noapte",
        "temp_brilianta_k": d.get("bt_k"),
    })

df = pd.DataFrame(rows).sort_values("timp_utc", ascending=False) if rows else pd.DataFrame()


# -------------------------------------------------------------------- KPI
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Detecții în selecție", len(df))
k2.metric("FRP maxim", f"{(df['frp_mw'].max() if len(df) else 0):.1f} MW")
k3.metric("FRP cumulat", f"{(df['frp_mw'].sum() if len(df) else 0):.0f} MW")
k4.metric("Focare identificate", len(fires))
k5.metric("Suprafață arsă (dNBR)", f"{burn.get('ha_moderat_plus', '–')} ha")

if stats.get("updated"):
    st.caption(f"Ultima colectare: {stats['updated']} · {data.get('cycles', '?')} cicluri · "
               f"zonă: {aoi.get('name', zone_id)} · bbox {aoi.get('bbox')}")


# ------------------------------------------------------------------ fileuri
left, right = st.columns([2, 1])

with left:
    st.subheader("Hartă")
    center = aoi.get("center") or [44.8982, 22.4785]
    m = folium.Map(location=center, zoom_start=aoi.get("zoom", 12), tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satelit").add_to(m)
    folium.TileLayer("OpenStreetMap", name="Hartă").add_to(m)

    park_file = ((next((x for x in zone_list if x["id"] == zone_id), {}) or {}).get("park") or {}).get("geojson")
    if park_file and (WEB / park_file).exists():
        folium.GeoJson(json.loads((WEB / park_file).read_text(encoding="utf-8")),
                       name="Parc", style_function=lambda _f: {"color": "#3b82f6", "weight": 2,
                                                               "fill": False, "dashArray": "6 4"}).add_to(m)

    if burn.get("png") and (WEB / "img" / burn["png"]).exists() and burn.get("bounds"):
        b = burn["bounds"]
        folium.raster_layers.ImageOverlay(
            image=str(WEB / "img" / burn["png"]),
            bounds=[[b[0][0], b[0][1]], [b[1][0], b[1][1]]],
            opacity=0.75, name=f"Arsură {burn.get('post_date', '')}").add_to(m)

    if len(df):
        for _, r in df.iterrows():
            h = (pd.Timestamp.now(tz="UTC") - r["timp_utc"]).total_seconds() / 3600
            colour = "#e5484d" if h <= 6 else "#f76b15" if h <= 24 else "#f5d90a" if h <= 72 else "#6b7684"
            folium.CircleMarker(
                [r["lat"], r["lon"]], radius=max(3, min(20, 3 + (r["frp_mw"] or 0) ** 0.5 * 1.6)),
                color="#22d3ee" if r["sursa"] == "ESA" else "#ffffff", weight=1.5,
                fill=True, fillColor=colour, fillOpacity=0.6,
                popup=folium.Popup(
                    f"<b>{r['senzor']}</b><br>FRP {r['frp_mw']:.2f} MW<br>"
                    f"încredere {r['incredere_pct']}%<br>{r['timp_utc']:%Y-%m-%d %H:%M}Z<br>"
                    f"{r['lat']:.4f}, {r['lon']:.4f}", max_width=220)).add_to(m)

    for f in fires[:3]:
        folium.Marker([f["lat"], f["lon"]], tooltip=f"Focar #{f['rank']} · {f['trend']}",
                      icon=folium.Icon(color="red" if f["rank"] == 1 else "orange", icon="fire")).add_to(m)

    if weather.get("downwind_deg") is not None and fires:
        folium.Marker([fires[0]["lat"], fires[0]["lon"]],
                      tooltip=f"Pană spre {weather.get('downwind_compass')}").add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    st_folium(m, height=520, width='stretch', returned_objects=[])

with right:
    st.subheader("Meteo și pericol")
    d = weather.get("danger") or {}
    if d:
        st.markdown(f"**Pericol {d.get('class', '?')}** · {d.get('name', '')} {d.get('index', '')}")
    w1, w2 = st.columns(2)
    w1.metric("Temperatură", f"{weather.get('temp_c', '–')} °C")
    w2.metric("Umiditate", f"{weather.get('humidity_pct', '–')} %")
    w1.metric("Vânt", f"{weather.get('wind_kmh', '–')} km/h")
    w2.metric("Rafale", f"{weather.get('gust_kmh', '–')} km/h")
    w1.metric("Precipitații 48 h", f"{weather.get('precip_next48_mm', '–')} mm")
    w2.metric("VPD", f"{weather.get('vpd_kpa', '–')} kPa")
    st.caption(f"Vânt din **{weather.get('wind_from_compass', '?')}** "
               f"({weather.get('wind_from_deg', '?')}°) → pană spre "
               f"**{weather.get('downwind_compass', '?')}** ({weather.get('downwind_deg', '?')}°)")
    t = weather.get("terrain") or {}
    if t:
        st.caption(f"Relief {t.get('elevation_m')} m, pantă {t.get('slope_pct')}%, "
                   f"{'pe vârf/creastă — panta nu determină propagarea' if t.get('on_summit') else 'pantă spre ' + str(t.get('upslope_compass'))}")

    st.subheader("Focare")
    if fires:
        st.dataframe(pd.DataFrame([{
            "#": f["rank"], "lat": f["lat"], "lon": f["lon"], "puncte": f["points"],
            "zile": f["days"], "FRP 24h": f["frp_last24"], "tendință": f["trend"],
            "km": f["extent_km"], "parc": f["in_park"], "reper": f["nearest_place"],
        } for f in fires]), hide_index=True, width='stretch')
    else:
        st.info("Niciun focar în intervalul selectat.")

# -------------------------------------------------------------------- grafice
st.subheader("Intensitate în timp")
if len(df):
    g = (df.set_index("timp_utc").groupby(pd.Grouper(freq="h"))["frp_mw"]
         .agg(["sum", "max", "count"]).rename(columns={"sum": "FRP cumulat (MW)",
                                                       "max": "FRP maxim (MW)",
                                                       "count": "detecții"}))
    st.line_chart(g, height=260)
else:
    st.info("Niciun rezultat pentru filtrele curente.")

days = stats.get("days") or []
if days:
    dd = pd.DataFrame(days)
    st.bar_chart(dd.set_index("date")[["frp_nasa", "frp_esa"]], height=220)

# --------------------------------------------------------------------- tabel
st.subheader("Detecții")
if len(df):
    st.dataframe(df, hide_index=True, width='stretch', height=320)
    st.download_button("⬇ Descarcă CSV", df.to_csv(index=False).encode("utf-8"),
                       file_name=f"detectii_{zone_id}.csv", mime="text/csv")
    st.caption("Numărul de puncte de detecție nu este numărul de incendii: un focar produce "
               "zeci de puncte la treceri succesive ale satelitului.")
else:
    st.info("Niciun rezultat.")

with st.expander("Metodă și surse"):
    st.markdown("""
- **NASA FIRMS** — VIIRS 375 m (Suomi-NPP, NOAA-20) și MODIS 1 km, fișiere publice.
- **ESA Sentinel-3 SLSTR L2 FRP** — Fire Radiative Power, prin Copernicus Data Space Ecosystem.
- **ESA Sentinel-2 L2A** — benzi B8A/B12 la 20 m pentru dNBR.
- **Meteo** — Open-Meteo (gratuit, fără cheie).
- **Indicele de pericol** — Angström: `I = (H/20) + (27-T)/10`; mai mic = mai periculos.
- **Suprafața arsă** — dNBR corectat cu medianul zonei de fundal; **estimare proprie, nu delimitare oficială**.
""")
