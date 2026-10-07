# Copernicus Data Space Ecosystem (CDSE) — acces si date recente

Client Python minimal pentru [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/):
autentificare OAuth2, cautare in catalog (STAC + OData), descarcare de produse si de benzi
individuale, previzualizare PNG.

## Analiza incendiilor

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/bunilastelian/domogled-live/blob/main/analiza.ipynb)

`analiza.ipynb` reproduce, fara instalare locala si fara chei API, cifrele despre incendiile
din Parcul National Domogled – Valea Cernei si din Parcul Natural Portile de Fier: cate focare
distincte sunt (nu cate puncte de detectie), intensitatea in timp, unde anume, suprafata arsa
estimata din Sentinel-2 si directia de propagare.

Monitorul care alimenteaza notebook-ul ruleaza la 15 minute:
<https://bunilastelian.github.io/domogled-live/>

## Instalare

Mediul virtual e deja creat in `.venv` (Python 3.12, `requests`, `python-dotenv`, `pillow`).

```
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe requests python-dotenv pillow
```

## Chei

Pune `client_id` si `client_secret` in `.env` (fisier ignorat de git, model in `.env.example`).
Se genereaza din <https://eportal.dataspace.copernicus.eu/> → login → **Account → OAuth clients**.

Verificare:

```
.venv\Scripts\python.exe latest.py --check
```

## Utilizare

```powershell
# cele mai noi 10 produse Sentinel-2 L2A, global (fara zona)
.venv\Scripts\python.exe latest.py

# alta colectiune
.venv\Scripts\python.exe latest.py -c s1 -n 5
.venv\Scripts\python.exe latest.py -c s3 -n 5          # Sentinel-3 (doar prin OData)
.venv\Scripts\python.exe latest.py -c s5p-no2 -n 5     # atmosfera

# zona + fereastra de timp + filtru de nori (doar colectii optice)
.venv\Scripts\python.exe latest.py -c s2 --lat 44.43 --lon 26.10 --days 20 --cloud 20

# ce e mai nou, pe fiecare colectiune
.venv\Scripts\python.exe latest.py --all -n 3

# descarcare
.venv\Scripts\python.exe latest.py -c s3 -n 1 --download 1            # produs intreg (ZIP)
.venv\Scripts\python.exe latest.py -c s2 --lat 44.43 --lon 26.10 `
    --days 20 --cloud 20 -n 1 --band B04_10m                          # doar o banda
.venv\Scripts\python.exe latest.py -c s2 -n 1 --list-bands            # ce benzi exista

# previzualizare: descarca TCI (true color) si scrie PNG + montaj
.venv\Scripts\python.exe preview.py --lat 44.43 --lon 26.10 --days 20 --cloud 20 -n 3
```

## Colectii

| alias | descriere | catalog |
|---|---|---|
| `s1` | Sentinel-1 GRD (radar, vede prin nori) | STAC + OData |
| `s2` | Sentinel-2 L2A (optic, 10 m, corectat atmosferic) | STAC + OData |
| `s2-l1c` | Sentinel-2 L1C (TOA) | STAC + OData |
| `s3` | Sentinel-3 (OLCI / SLSTR / SRAL) | **doar OData** |
| `s5p-no2`, `s5p-ch4`, `s5p-co` | Sentinel-5P (atmosfera) | OData (STAC e in urma) |

## Ce am aflat despre API (capcane reale)

1. **STAC-ul CDSE e incomplet.** `/stac/collections` intoarce doar 10 colectii (CCM + CLMS),
   dar cautarea accepta si `sentinel-1-grd`, `sentinel-2-l2a`, `sentinel-5p-l2-*`.
   Lista reala validata prin probare:

   ```
   sentinel-1-grd  sentinel-1-slc  sentinel-2-l1c  sentinel-2-l2a
   sentinel-5p-l2-no2  sentinel-5p-l2-o3-tcl  sentinel-5p-l2-aer-ai
   sentinel-5p-l2-co   sentinel-5p-l2-ch4  ccm-optical  ccm-sar
   ```

   **Sentinel-3, Copernicus DEM si Landsat NU exista in STAC-ul CDSE** → se interogheaza prin OData.

2. **STAC-ul pentru Sentinel-5P e in urma.** La data testarii, STAC returna ultimul produs
   OFFL din 28 sep, in timp ce OData avea NRTI pana in ziua curenta. De aceea colectiile
   `s5p-*` au `"prefer": "odata"` in `cdse.py` si ocolesc STAC-ul.

3. **Descarcarea prin `$value` da 401 daca folosesti `allow_redirects=True`.**
   Catalogul redirectioneaza (301) de pe `catalogue.dataspace.copernicus.eu` pe
   `download.dataspace.copernicus.eu`, iar `requests.Session.rebuild_auth` **sterge header-ul
   `Authorization`** cand redirectul schimba hostul. Solutia (implementata in
   `CDSE._get_following_redirects`): urmarim redirect-urile manual si reatasam token-ul.

4. **OData cere UUID, STAC da numele produsului.** Itemul STAC are `id` =
   `S2B_MSIL2A_..._T35TMK_...`, iar OData are `Name` = acelasi nume **+ `.SAFE`**.
   `CDSE.resolve_product_id()` face conversia prin `startswith(Name, ...)`.

5. **Marimi reale:** o scena Sentinel-2 L2A = **~1.07 GB** (ZIP), Sentinel-1 GRD = **~1.13 GB**.
   Pentru o singura banda foloseste `--band` (ex. `TCI_60m` = 3.5 MB, `B04_10m` = 113 MB).

6. **Token si produse arhivate.** Tokenul e valabil ~600 s; `CDSE.token()` il reimprospateaza
   automat. Produsele arhivate (`Online: false`) nu se pot descarca direct — se reactiveaza din
   interfata CDSE sau se iau prin S3 (`s3://eodata/<S3Path>`, endpoint `eodata.dataspace.copernicus.eu`).

## Fisiere

| fisier | rol |
|---|---|
| `cdse.py` | biblioteca: auth, cautare STAC/OData, rezolvare nume→UUID, descarcare |
| `latest.py` | CLI: cele mai recente produse, filtre, descarcare |
| `preview.py` | CLI: descarca TCI si scrie PNG + montaj |
| `.env` | cheile (nu se comite) |
| `data/` | descarcari (nu se comit) |

## Pas urmator posibil

* descarcare in masa prin S3 (`boto3`, credentiale din acelasi OAuth client) — mult mai rapid decat HTTPS;
* decupare pe zona de interes inainte de descarcare (Sentinel Hub Processing API / `sentinelhub-py`),
  ca sa nu iei 1 GB pentru 10 km²;
* filtrare pe `Attributes` OData (ex. `cloudCover` server-side) pentru colectii mari.
