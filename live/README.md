# Domogled Live — monitorizare satelitară în timp aproape real

Dashboard care arată focarele din zona **Parcului Național Domogled – Valea Cernei**:
detecții termice, suprafața arsă estimată, imagini Sentinel-2 proaspete și alerte.

Rulează local (un proces de colectare + un server), dar același cod publică și pe
GitHub Pages fără modificări.

```
live\
├─ collector.py      motorul: interoghează sursele și scrie starea
├─ burn_scar.py      suprafața arsă (dNBR Sentinel-2), recalculată la scenă nouă
├─ server.py         servește dashboard-ul pe http://127.0.0.1:8777
├─ start.ps1         pornește ambele și deschide browserul
├─ stop.ps1          oprește ambele
├─ requirements.txt
├─ web\              dashboard-ul (Leaflet + Chart.js, fără build)
│   ├─ state\state.json   copie pentru modul static
│   └─ img\               imagini Sentinel-2 + overlay-ul de arsură
└─ state\            starea colectată, cache de produse și benzi
```

## Pornire

```powershell
cd D:\copernicus\live
.\start.ps1
```

Se deschid două ferestre (logul colectorului și serverul) și browserul pe
<http://127.0.0.1:8777/>. Oprire: `.\stop.ps1`.

La prima rulare colectorul importă istoricul de 7 zile și descarcă benzile pentru
suprafața arsă (~1 minut). Apoi câte un ciclu la 5 minute.

## Ce arată

- **Hartă** cu detecții NASA FIRMS (VIIRS 375 m, MODIS 1 km) și ESA Sentinel-3 SLSTR FRP.
  Culoare = vechime (roșu < 6 h), mărime = FRP în MW. Conturul parcului, reperele din teren
  (Cascada Cociului, Colțu Pietrii, Domogledul Mare, Băile Herculane), linii de front pe fiecare
  trecere de satelit.
- **Suprafața arsă** (dNBR Sentinel-2), suprapusă pe hartă în trei clase de severitate,
  recalculată automat când apare o scenă nouă.
- **Alerte** la detecții noi, plus notificare în browser (opțional).
- **Grafic** FRP cumulat pe zile, separat NASA și ESA.
- **Imagini Sentinel-2** decupate pe zonă, true color la 60 m.

## Cât de „live" este, de fapt

Limita e a surselor, nu a codului. Măsurat pe 7 octombrie 2026:

| sursă | latență | frecvență |
|---|---|---|
| NASA FIRMS (VIIRS/MODIS) | **~2,6 ore** de la achiziție | fișier orar; 4 treceri/zi peste zonă (00, 02, 10, 12 UTC) |
| ESA Sentinel-3 SLSTR FRP | câteva ore (produs NRT) | 2 treceri/zi |
| ESA Sentinel-2 L2A (imagini, dNBR) | ~1 zi | la 2–5 zile, când nu e nor |

Interfața se reîmprospătează la 60 s, colectorul întreabă la 5 minute, dar
**date noi reale apar de regulă la fiecare ~6 ore**.

## Suprafața arsă — cum se calculează și ce înseamnă

`burn_scar.py` compară scena recentă cu una de referință de dinainte de incendiu:

```
NBR  = (NIR − SWIR2) / (NIR + SWIR2)       benzi B8A și B12, 20 m
dNBR = NBR_referință − NBR_recentă
```

Apoi **scade medianul zonei de fundal** (peste 8 km de focar). Fără corecția asta cifra iese
umflată: între iulie și octombrie 2026 seceta a scăzut NBR în toată regiunea, iar dNBR brut
raportează „ars" pe suprafețe care doar s-au uscat.

Cifre la 4 octombrie (referință 31 iulie):

| clasă dNBR corectat | ha |
|---|---|
| ars ușor (0,10–0,27) | 3.587 |
| moderat-scăzut (0,27–0,44) | 224,6 |
| moderat-ridicat (0,44–0,66) | 12,4 |
| ridicat (≥0,66) | 3,0 |

**Pe hartă se desenează doar de la 0,27 în sus.** Clasa 0,10–0,27 e dominată de zgomot
(uscarea sezonieră), răspândită uniform pe toată fereastra — inclusiv în locuri fără incendiu.
Peste 0,27 semnalul devine coerent spațial și corespunde arsurii reale.

Rezultatul e o estimare proprie, **nu o delimitare oficială**. Două limite cunoscute:
seceta deplasează tot indicele, iar pe 4 octombrie o pană de fum acoperea chiar zona analizată.

## Un singur colector, garantat

Două instanțe care scriu `state.json` se suprascriu reciproc, iar starea pierde câmpuri.
Colectorul ia un lock (`state/collector.lock`); dacă lock-ul e ținut de un proces viu, a doua
instanță refuză cu mesaj clar. Dacă procesul a murit, lock-ul e preluat automat
(verificare de PID, nu doar de timestamp). `--force` ocolește lock-ul.

## Alerte pe Telegram (opțional)

În `D:\copernicus\.env`:

```
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

Fără ele, alertele rămân în consolă și în panoul „Alerte".

## API

| rută | ce dă |
|---|---|
| `/api/state` | toată starea: detecții, statistici, arsură, alerte, imagini, AOI |
| `/api/health` | cicluri, vârsta ultimei actualizări, număr de detecții |

## Publicare pe GitHub Pages

Workflow-ul e deja scris în `.github/workflows/collect.yml` (în rădăcina repo-ului):

1. colectorul rulează la fiecare 15 minute în Actions;
2. comite `live/state/state.json` și `live/web/`;
3. publică `live/web` ca site static.

Secrete necesare în repo (Settings → Secrets → Actions): `CDSE_CLIENT_ID`, `CDSE_CLIENT_SECRET`.

Dashboard-ul citește starea prin `/api/state` când rulează local și cade automat pe
`state/state.json` când e servit ca fișiere statice — deci același cod merge în ambele moduri.

## Setări

În capul `collector.py`:

```python
AOI = {"bbox": [22.30, 44.80, 22.62, 44.98], "center": [44.8982, 22.4785], "zoom": 12}
POLL_SECONDS = 300
IMAGERY_CLOUD_MAX = 60
```

În `burn_scar.py`: `FIRE_START`, `POST_CLOUD_MAX`, `OVERLAY_MIN`, `CLASSES`.

## Date folosite

- NASA FIRMS active fire (fără cheie)
- Copernicus Data Space Ecosystem: OData + STAC (cheile din `D:\copernicus\.env`)
- Produse: `SENTINEL-3/SLSTR/SL_2_FRP`, `SENTINEL-2/MSI/L2A` (TCI, B8A, B12)

Fondurile de hartă (Esri, OpenStreetMap, CARTO) și bibliotecile Leaflet/Chart.js vin din CDN —
necesită internet.
