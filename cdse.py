"""
Client minimal pentru Copernicus Data Space Ecosystem (CDSE).

Acopera:
  * autentificare OAuth2 client_credentials (client_id + client_secret din .env)
  * cautare unificata in catalog:
      - STAC  (https://catalogue.dataspace.copernicus.eu/stac)   -> S1, S2, S5P, CCM
      - OData (https://catalogue.dataspace.copernicus.eu/odata/v1) -> orice colectie,
        inclusiv Sentinel-3, care NU exista in STAC-ul CDSE
  * rezolvare nume produs -> UUID (OData cere UUID la descarcare)
  * listare fisiere din interiorul unui produs
  * descarcare produs cu progres

Dependinte: `requests`, `python-dotenv`.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


TOKEN_URL = _env(
    "CDSE_TOKEN_URL",
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
)
ODATA_URL = _env("CDSE_ODATA_URL", "https://catalogue.dataspace.copernicus.eu/odata/v1")
STAC_URL = _env("CDSE_STAC_URL", "https://catalogue.dataspace.copernicus.eu/stac")

_data_dir = _env("COPERNICUS_DATA_DIR", "data")
DATA_DIR = Path(_data_dir)
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR

# ---------------------------------------------------------------------------
# Catalog de colectii.
#   stac  = nume in catalogul STAC (None daca STAC-ul CDSE nu o acopera)
#   odata = nume in catalogul OData
#   match = filtru pe nume, ca sa evitam fisierele auxiliare (AUX/OPER)
# ---------------------------------------------------------------------------
COLLECTIONS: dict[str, dict[str, Any]] = {
    "s1": {
        "stac": "sentinel-1-grd",
        "odata": "SENTINEL-1",
        "match": "GRD",
        "desc": "Sentinel-1 GRD (radar, vede prin nori, zi/noapte)",
    },
    "s2": {
        "stac": "sentinel-2-l2a",
        "odata": "SENTINEL-2",
        "match": "MSIL2A",
        "desc": "Sentinel-2 L2A (optic, 10 m, corectat atmosferic)",
    },
    "s2-l1c": {
        "stac": "sentinel-2-l1c",
        "odata": "SENTINEL-2",
        "match": "MSIL1C",
        "desc": "Sentinel-2 L1C (optic, TOA)",
    },
    "s3": {
        "stac": None,  # STAC-ul CDSE nu expune Sentinel-3
        "odata": "SENTINEL-3",
        "match": None,
        "desc": "Sentinel-3 (ocean + uscat: OLCI, SLSTR, SRAL)",
    },
    "s5p-no2": {
        "stac": "sentinel-5p-l2-no2",
        "odata": "SENTINEL-5P",
        "match": "L2__NO2",
        "prefer": "odata",  # STAC-ul CDSE pentru S5P e in urma; OData are NRTI la zi
        "desc": "Sentinel-5P L2 NO2 (atmosfera)",
    },
    "s5p-ch4": {
        "stac": "sentinel-5p-l2-ch4",
        "odata": "SENTINEL-5P",
        "match": "L2__CH4",
        "prefer": "odata",
        "desc": "Sentinel-5P L2 CH4 (metan)",
    },
    "s5p-co": {
        "stac": "sentinel-5p-l2-co",
        "odata": "SENTINEL-5P",
        "match": "L2__CO",
        "prefer": "odata",
        "desc": "Sentinel-5P L2 CO (monoxid de carbon)",
    },
}

# colectiile folosite de `--all`, in ordinea afisarii
MAIN_COLLECTIONS = ("s1", "s2", "s3", "s5p-no2")

_ALIASES = {
    "sentinel-1": "s1", "sentinel1": "s1", "s-1": "s1",
    "sentinel-2": "s2", "sentinel2": "s2", "s-2": "s2",
    "l2a": "s2", "s2l2a": "s2", "msil2a": "s2",
    "l1c": "s2-l1c", "s2l1c": "s2-l1c",
    "sentinel-3": "s3", "sentinel3": "s3", "s-3": "s3",
    "s5p": "s5p-no2", "sentinel-5p": "s5p-no2", "sentinel5p": "s5p-no2",
    "no2": "s5p-no2", "ch4": "s5p-ch4", "co": "s5p-co",
}

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TILE_RE = re.compile(r"_(T[A-Z0-9]{5})_")


class CDSEError(RuntimeError):
    pass


def resolve_collection(name: str) -> dict[str, Any]:
    """Accepta 's2', 'SENTINEL-2', 'sentinel-2-l2a' ... si intoarce descrierea colectiei."""
    key = name.strip().lower()
    key = _ALIASES.get(key, key)
    if key in COLLECTIONS:
        return {"key": key, **COLLECTIONS[key]}
    for k, v in COLLECTIONS.items():
        if key in (str(v.get("stac") or "").lower(), str(v.get("odata") or "").lower()):
            return {"key": k, **v}
    # colectie STAC necunoscuta: o lasam sa treaca, catalogul decide daca exista
    return {"key": key, "stac": name.strip().lower(), "odata": name.strip().upper(), "match": None,
            "desc": f"colectie necunoscuta '{name}' (passthrough STAC)"}


def human_size(num: float | int | None) -> str:
    if not num:
        return "-"
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} TB"


class CDSE:
    """Sesiune CDSE: token + cautare in catalog + descarcare."""

    def __init__(self, client_id: str | None = None, client_secret: str | None = None, verbose: bool = False):
        self.client_id = (client_id or os.getenv("CDSE_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret or os.getenv("CDSE_CLIENT_SECRET", "")).strip()
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "copernicus-cdse-client/1.0"
        self._token: str | None = None
        self._token_exp: float = 0.0

    # ---------------------------------------------------------------- auth
    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def token(self, force: bool = False) -> str:
        """Token OAuth2 (client_credentials), cu cache pana aproape de expirare."""
        if not self.configured:
            raise CDSEError(
                "Lipsesc CDSE_CLIENT_ID / CDSE_CLIENT_SECRET.\n"
                f"Completeaza-le in {ROOT / '.env'} (model: .env.example)."
            )
        if self._token and not force and time.time() < self._token_exp - 60:
            return self._token

        resp = self.session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise CDSEError(
                f"Autentificare esuata ({resp.status_code}): {resp.text[:400]}\n"
                "Verifica client_id / client_secret in .env (Account -> OAuth clients)."
            )
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_exp = time.time() + float(payload.get("expires_in", 600))
        if self.verbose:
            print(f"[auth] token nou, valid {payload.get('expires_in')}s")
        return self._token

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}

    def _get_following_redirects(self, url: str, headers: dict[str, str], *, timeout: int = 180) -> requests.Response:
        """
        GET cu redirect-uri urmarite MANUAL.

        Motiv: catalogul redirectioneaza descarcarea de pe
        catalogue.dataspace.copernicus.eu pe download.dataspace.copernicus.eu, iar
        `requests` sterge header-ul Authorization cand redirectul schimba hostul
        (`Session.rebuild_auth`). Cu allow_redirects=True primim 401 desi tokenul
        e valid. Urmarind manual, tokenul ajunge si la hostul de download.
        """
        for _ in range(6):
            resp = self.session.get(url, headers=headers, stream=True, timeout=timeout, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                location = urljoin(url, resp.headers["Location"])
                resp.close()
                url = location
                continue
            return resp
        raise CDSEError("Prea multe redirect-uri la descarcare.")

    # ---------------------------------------------------------------- STAC
    def stac_search(
        self,
        collections: Sequence[str],
        *,
        bbox: Sequence[float] | None = None,
        point: tuple[float, float] | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 10,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Cauta in catalogul STAC. `point` = (lon, lat), datele 'YYYY-MM-DD'."""
        body: dict[str, Any] = {
            "collections": list(collections),
            "limit": max(1, min(int(limit), 1000)),
        }
        if bbox is not None:
            body["bbox"] = [float(x) for x in bbox]
        if point is not None:
            body["intersects"] = {"type": "Point", "coordinates": [float(point[0]), float(point[1])]}
        if start or end:
            body["datetime"] = f"{start + 'T00:00:00Z' if start else '..'}/{end + 'T23:59:59Z' if end else '..'}"
        if newest_first:
            body["sortby"] = [{"field": "properties.datetime", "direction": "desc"}]

        headers = {"Content-Type": "application/json"}
        if self.configured:
            headers.update(self.auth_header())

        resp = self.session.post(f"{STAC_URL}/search", json=body, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise CDSEError(f"STAC search a esuat ({resp.status_code}): {resp.text[:400]}")
        features = resp.json().get("features", [])
        if newest_first:
            features.sort(key=lambda f: f.get("properties", {}).get("datetime") or "", reverse=True)
        return features

    # --------------------------------------------------------------- OData
    def odata_products(
        self,
        collection_odata: str,
        *,
        top: int = 10,
        point: tuple[float, float] | None = None,
        bbox: Sequence[float] | None = None,
        start: str | None = None,
        end: str | None = None,
        match: str | None = None,
        newest_first: bool = True,
        extra_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Cauta produse in catalogul OData (Id / S3Path / Online / orice colectie)."""
        filters: list[str] = [f"Collection/Name eq '{collection_odata}'"]
        if match:
            filters.append(f"contains(Name,'{match}')")
        if point is not None:
            lon, lat = float(point[0]), float(point[1])
            filters.append(f"OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon} {lat})')")
        if bbox is not None:
            w, s, e, n = (float(x) for x in bbox)
            poly = f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"
            filters.append(f"OData.CSC.Intersects(area=geography'SRID=4326;{poly}')")
        if start:
            filters.append(f"ContentDate/Start ge {start}T00:00:00.000Z")
        if end:
            filters.append(f"ContentDate/Start le {end}T23:59:59.000Z")
        if extra_filter:
            filters.append(extra_filter)

        params = {
            "$filter": " and ".join(filters),
            "$top": str(int(top)),
            "$orderby": "ContentDate/Start desc" if newest_first else "ContentDate/Start asc",
        }
        resp = self.session.get(f"{ODATA_URL}/Products", params=params, timeout=90)
        if resp.status_code != 200:
            raise CDSEError(f"OData search a esuat ({resp.status_code}): {resp.text[:400]}")
        return resp.json().get("value", [])

    def resolve_product_id(self, name_or_id: str) -> str:
        """Intoarce UUID-ul OData pentru un nume de produs sau un UUID deja dat."""
        if _UUID_RE.match(name_or_id):
            return name_or_id
        stem = name_or_id[:-5] if name_or_id.endswith(".SAFE") else name_or_id
        params = {"$filter": f"startswith(Name,'{stem}')", "$top": "1"}
        resp = self.session.get(f"{ODATA_URL}/Products", params=params, timeout=60)
        if resp.status_code != 200:
            raise CDSEError(f"Rezolvarea numelui a esuat ({resp.status_code}): {resp.text[:300]}")
        values = resp.json().get("value", [])
        if not values:
            raise CDSEError(f"Produsul '{name_or_id}' nu a fost gasit in catalogul OData.")
        return values[0]["Id"]

    def product(self, name_or_id: str) -> dict[str, Any]:
        """Detalii produs (OData), inclusiv Online, ContentLength, S3Path."""
        pid = self.resolve_product_id(name_or_id)
        resp = self.session.get(f"{ODATA_URL}/Products({pid})", timeout=60)
        if resp.status_code != 200:
            raise CDSEError(f"Produsul {name_or_id} nu a putut fi citit ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    def product_nodes(self, name_or_id: str) -> list[dict[str, Any]]:
        """Listeaza fisierele din interiorul unui produs."""
        pid = self.resolve_product_id(name_or_id)
        resp = self.session.get(f"{ODATA_URL}/Products({pid})/Nodes", timeout=60)
        if resp.status_code != 200:
            raise CDSEError(f"Listarea nodurilor a esuat ({resp.status_code}): {resp.text[:300]}")
        return resp.json().get("result", [])

    # ------------------------------------------------------------ unificat
    def search(
        self,
        collection: dict[str, Any] | str,
        *,
        limit: int = 10,
        point: tuple[float, float] | None = None,
        bbox: Sequence[float] | None = None,
        start: str | None = None,
        end: str | None = None,
        cloud_max: float | None = None,
        prefer: str = "auto",
    ) -> list[dict[str, Any]]:
        """
        Cauta in colectiunea data si intoarce inregistrari normalizate.

        `prefer`: 'auto' (STAC daca exista, altfel OData), 'stac', 'odata'.
        """
        coll = resolve_collection(collection) if isinstance(collection, str) else collection
        source = prefer
        if source == "auto":
            # unele colectii sunt mai la zi in OData decat in STAC (vezi 'prefer')
            source = coll.get("prefer") or ("stac" if coll.get("stac") else "odata")

        if source == "stac" and not coll.get("stac"):
            raise CDSEError(f"Colectiunea '{coll['key']}' nu exista in STAC-ul CDSE. Foloseste OData.")

        # cerem mai multe daca filtram pe nori, ca sa ramana `limit` rezultate
        fetch = min(1000, limit * 5) if cloud_max is not None else limit

        if source == "stac":
            feats = self.stac_search(
                [coll["stac"]], bbox=bbox, point=point, start=start, end=end, limit=fetch
            )
            records = [normalize_stac(f, coll["key"]) for f in feats]
        else:
            prods = self.odata_products(
                coll["odata"], top=fetch, point=point, bbox=bbox,
                start=start, end=end, match=coll.get("match"),
            )
            records = [normalize_odata(p, coll["key"]) for p in prods]

        if cloud_max is not None:
            records = [r for r in records if r["cloud_cover"] is None or r["cloud_cover"] <= cloud_max]
        return records[:limit]

    # ----------------------------------------------------------- descarcare
    def _stream_to_file(
        self, resp: requests.Response, target: Path, size: int | None, chunk: int
    ) -> Path:
        """Scrie raspunsul intr-un `.part`, cu progres, apoi il redenumeste."""
        if resp.status_code != 200:
            if resp.status_code in (401, 403):
                raise CDSEError(
                    f"Acces refuzat ({resp.status_code}) pentru {target.name}. Verifica cheile din .env "
                    "si daca ai acceptat termenii licentei CDSE in cont."
                )
            raise CDSEError(f"Descarcare esuata ({resp.status_code}): {resp.text[:300]}")

        part = target.with_name(target.name + ".part")
        total = int(resp.headers.get("Content-Length") or size or 0)
        done = 0
        t0 = time.time()
        try:
            with open(part, "wb") as fh:
                for block in resp.iter_content(chunk_size=chunk):
                    if not block:
                        continue
                    fh.write(block)
                    done += len(block)
                    speed = done / max(time.time() - t0, 1e-6)
                    if total:
                        print(
                            f"\r  {target.name[:46]:<46} {done * 100 / total:5.1f}%  "
                            f"{human_size(done)}/{human_size(total)}  {human_size(speed)}/s",
                            end="", flush=True,
                        )
                    else:
                        print(f"\r  {target.name[:46]:<46} {human_size(done)}  {human_size(speed)}/s",
                              end="", flush=True)
            print()
        finally:
            resp.close()

        if total and part.stat().st_size != total:
            raise CDSEError(
                f"Transfer incomplet pentru {target.name}: {human_size(part.stat().st_size)} din "
                f"{human_size(total)}. Fisierul partial a ramas la {part} - reia descarcarea."
            )
        part.replace(target)
        return target

    def download_product(
        self,
        name_or_id: str,
        *,
        dest: Path | str | None = None,
        overwrite: bool = False,
        chunk: int = 1 << 20,
    ) -> Path:
        """Descarca un produs intreg (ZIP) prin OData `$value`. Necesita cont (token)."""
        pid = self.resolve_product_id(name_or_id)
        info = self.product(pid)
        name = info.get("Name") or name_or_id
        size = info.get("ContentLength")
        if info.get("Online") is False:
            raise CDSEError(
                f"Produsul {name} este arhivat (nu e online) si nu poate fi descarcat direct. "
                "Se poate reactiva din interfata CDSE sau se descarca prin S3."
            )

        out_dir = Path(dest) if dest else DATA_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (name if name.lower().endswith(".zip") else f"{name}.zip")
        if target.exists() and not overwrite and (size is None or target.stat().st_size == size):
            print(f"[skip] exista deja: {target.name} ({human_size(target.stat().st_size)})")
            return target

        resp = self._get_following_redirects(f"{ODATA_URL}/Products({pid})/$value", self.auth_header())
        return self._stream_to_file(resp, target, size, chunk)

    def download_asset(
        self,
        url: str,
        *,
        filename: str | None = None,
        dest: Path | str | None = None,
        size: int | None = None,
        overwrite: bool = False,
        chunk: int = 1 << 20,
    ) -> Path:
        """
        Descarca un singur asset (ex. o banda dintr-o scena S2) dat fiind URL-ul https
        din STAC (`asset['https']`). Mult mai mic decat produsul intreg.
        """
        out_dir = Path(dest) if dest else DATA_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        if not filename:
            filename = url.split("?")[0].rstrip("/").split("/")[-1] or "asset.bin"
        target = out_dir / filename
        if target.exists() and not overwrite and (size is None or target.stat().st_size == size):
            print(f"[skip] exista deja: {target.name} ({human_size(target.stat().st_size)})")
            return target
        resp = self._get_following_redirects(url, self.auth_header())
        return self._stream_to_file(resp, target, size, chunk)


# --------------------------------------------------------------- normalizare
def _platform_from_name(name: str) -> str | None:
    prefix = name.split("_")[0]
    return prefix.lower() if prefix.upper().startswith(("S1", "S2", "S3", "S5P")) else None


def normalize_stac(feature: dict[str, Any], collection_key: str | None = None) -> dict[str, Any]:
    """Item STAC -> inregistrare normalizata."""
    props = feature.get("properties", {}) or {}
    fid = feature.get("id") or ""
    tile = props.get("grid:code") or props.get("s2:mgrs_tile")
    if tile and str(tile).startswith("MGRS-"):
        tile = str(tile)[5:]

    # asset-uri: 'https' = link direct descarcabil, 's3' = calea din bucketul eodata
    assets: dict[str, dict[str, Any]] = {}
    for key, a in (feature.get("assets") or {}).items():
        href = a.get("href") or ""
        assets[key] = {
            "https": ((a.get("alternate") or {}).get("https") or {}).get("href"),
            "s3": href if href.startswith("s3://") else None,
            "size": a.get("file:size"),
            "title": a.get("title"),
        }

    return {
        "id": fid,
        "name": fid,
        "datetime": props.get("datetime"),
        "end": props.get("end_datetime"),
        "cloud_cover": props.get("eo:cloud_cover"),
        "platform": props.get("platform") or _platform_from_name(fid),
        "tile": tile,
        "size": (assets.get("Product") or {}).get("size") or props.get("file:size"),
        "online": None,
        "s3_path": None,
        "assets": assets,
        "collection": collection_key or feature.get("collection"),
        "source": "stac",
    }


def normalize_odata(product: dict[str, Any], collection_key: str | None = None) -> dict[str, Any]:
    """Produs OData -> inregistrare normalizata."""
    name = product.get("Name") or ""
    stem = name[:-5] if name.endswith(".SAFE") else name
    cd = product.get("ContentDate") or {}
    m = _TILE_RE.search(name)
    return {
        "id": product.get("Id"),
        "name": stem,
        "datetime": cd.get("Start"),
        "end": cd.get("End"),
        "cloud_cover": None,
        "platform": _platform_from_name(name),
        "tile": m.group(1) if m else None,
        "size": product.get("ContentLength"),
        "online": product.get("Online"),
        "s3_path": product.get("S3Path"),
        "assets": {},
        "collection": collection_key or (product.get("Collection") or {}).get("Name"),
        "source": "odata",
    }


def print_table(records: Iterable[dict[str, Any]]) -> None:
    rows = list(records)
    if not rows:
        print("(niciun rezultat)")
        return
    print(f"{'#':>2}  {'data/ora (UTC)':<19} {'nori':>6}  {'platforma':<9} {'tile':<7} {'marime':>9}  id")
    for i, r in enumerate(rows, 1):
        dt = (r.get("datetime") or "")[:19].replace("T", " ")
        cc = r.get("cloud_cover")
        cloud = f"{cc:.1f}%" if isinstance(cc, (int, float)) else "-"
        print(
            f"{i:>2}  {dt:<19} {cloud:>6}  {(r.get('platform') or '-'):<9} "
            f"{(r.get('tile') or '-'):<7} {human_size(r.get('size')):>9}  {r.get('name') or r.get('id')}"
        )
