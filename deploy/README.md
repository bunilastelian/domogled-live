# Pe un VPS gratuit

## Ce mai e gratuit și permanent în 2026

Am verificat, pentru că ofertele se schimbă des — inclusiv pe ascuns. **Oracle și-a redus
la jumătate oferta ARM gratuită** față de cifrele care se citează de obicei („4 nuclee,
24 GB"). Situația de acum:

| furnizor | ce dă | permanent? |
|---|---|---|
| **Oracle Cloud Always Free** | 2 nuclee ARM Ampere, **12 GB RAM**, 200 GB disc, 10 TB trafic/lună | **da** |
| Google Cloud free tier | o instanță e2-micro, într-o regiune din SUA | da, dar foarte mică |
| AWS / Azure | trial pe timp limitat sau credite | **nu** |
| Fly.io, Render, Railway | nivel gratuit eliminat sau cu repaus | nu |

Sursa pentru Oracle: un ghid care declară că a verificat fiecare alocație în documentația
furnizorului pe 11 septembrie 2026, plus relatările despre reducerea pe ascuns a ofertei ARM.

Oracle cere card pentru verificarea identității (nu se facturează) și e cunoscut pentru
erori de capacitate la ARM în regiunile aglomerate — se rezolvă încercând repetat sau
alegând altă regiune.

## Ce ar rezolva un VPS, concret

Nu e vorba de „mai multă putere". Sunt patru lucruri pe care le-am tot ocolit:

1. **Conflictele pe `state.json`.** Acum și GitHub Actions, și orice rulare locală scriu
   același fișier comis în git, iar eu le rezolv manual la fiecare push. Cu o bază de date
   pe VPS, git ține doar codul, iar starea nu mai ajunge niciodată în conflict.
2. **Colectare la 5 minute**, nu la 15 — exact cât permite GitHub Actions și cât se
   reîmprospătează fișierele FIRMS. Sub 5 minute nu aduce nimic nou.
3. **Fără limita de 6 ore per job** și fără întârzierile rulărilor programate de pe GitHub.
4. **Streamlit care nu adoarme**, plus un API real cu bază de date în loc de JSON static.

## Pornire pe Oracle Cloud, pas cu pas

1. Cont pe <https://cloud.oracle.com> → **Always Free**.
2. Creează o instanță: imagine **Ubuntu 24.04**, formă **VM.Standard.A1.Flex**
   (2 OCPU, 12 GB — încadrat în gratuit), adaugă-ți cheia SSH publică.
3. Deschide porturile 80 și 443 în *Security List* al rețelei virtuale.
4. Conectează-te și rulează:

```bash
sudo mkdir -p /opt/domogled && sudo chown $USER /opt/domogled
git clone https://github.com/bunilastelian/domogled-live /opt/domogled
cd /opt/domogled
sudo bash deploy/install.sh
```

5. Pune cheile, cum îți spune scriptul la sfârșit:

```bash
sudo install -m 600 -o domogled -g domogled /dev/null /opt/domogled/.env
sudo nano /opt/domogled/.env     # CDSE_CLIENT_ID si CDSE_CLIENT_SECRET
sudo systemctl start domogled-collector domogled-api
```

6. Pentru HTTPS: pune domeniul în `deploy/Caddyfile`, copiază-l în `/etc/caddy/Caddyfile`
   și dă `sudo systemctl reload caddy`. Caddy ia singur certificatul Let's Encrypt.

## Ce instalează scriptul

- utilizator de sistem `domogled`, fără shell de login;
- venv în `/opt/domogled/.venv` cu dependințele din `live/requirements.txt`
  și, dacă există, din `requirements.txt` (consola Streamlit);
- **`domogled-collector.service`** — colectorul, la 5 minute, `Restart=always`;
- **`domogled-api.service`** — dashboard-ul, ascultă **doar pe 127.0.0.1**;
- **`domogled-streamlit.service`** — consola de analiză, doar local;
- optional Caddy pentru HTTPS automat.

Unitățile systemd au `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp` și
`ReadWritePaths` limitat la directoarele de stare. Codul rămâne al root-ului, citibil;
doar starea e scrisă de serviciu.

## Ce te costă, ca să fie clar

Un VPS nu e „gratuit" la modul absolut: plătești cu **timp**.

- actualizări de securitate pe server (`unattended-upgrades` ajută);
- monitorizare — dacă serviciul cade la 3 noaptea, trebuie să afli;
- backup pentru `live/state/` și `.env`;
- un domeniu, dacă vrei HTTPS (Caddy cere un nume, nu merge pe IP).

## Recomandarea mea, sincer

**Pentru ce face proiectul acum, VPS-ul nu e necesar.** GitHub Actions plus Pages costă
zero, nu cere mentenanță și funcționează deja.

**Are sens dacă** treci la colectare la 5 minute, vrei starea într-o bază de date ca să
scapi de conflictele din git, sau vrei să găzduiești consola Streamlit fără repaus.
Atunci Oracle Always Free e alegerea corectă, iar pachetul din `deploy/` e gata.

O cale de mijloc, care rezolvă cel mai dureros lucru: **mută `live/state/` într-o bază
SQLite și scoate-o din git.** Se poate face și fără VPS, și ar elimina complet conflictele
pe care le tot repar. Spune-mi dacă vrei asta.
