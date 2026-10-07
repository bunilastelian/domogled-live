# Rulează pe GitHub, nu local

Trei lucruri diferite se confundă des. Le separ, cu ce se poate și ce nu.

## 1. Producția rulează deja pe GitHub (nu are nevoie de nimic local)

Colectorul rulează în **GitHub Actions** la fiecare 15 minute, cu `pip install -r live/requirements.txt`
la fiecare rulare. Nu folosește și nu are nevoie de venv-ul de pe PC-ul tău.

Dashboard-ul e pe **GitHub Pages**, care servește fișiere statice.

Deci, pentru producție, **PC-ul poate fi oprit** — așa a și fost testat: am oprit colectorul local,
iar site-ul s-a actualizat singur în continuare.

Cost: **0**. Pe repository public, minutele de Actions sunt gratuite.

## 2. Dezvoltarea se poate muta complet în cloud: GitHub Codespaces

Dacă vrei să scrii cod și să rulezi `collector.py` / `server.py` fără să instalezi nimic local,
**Codespaces** îți dă un VS Code complet în browser, cu terminal și Python 3.12.

Planul gratuit pentru cont personal: **120 de ore-nucleu pe lună** și 15 GB stocare.
Cu `hostRequirements: cpus: 2` din `.devcontainer/devcontainer.json`, asta înseamnă
**~60 de ore de lucru pe lună**. Pentru un proiect ca acesta, e mult.

Pornire: pe pagina repo-ului → **Code** → **Codespaces** → **Create codespace on main**.
Mediul se configurează singur din `.devcontainer/`, inclusiv portul 8777.

În terminalul din Codespaces, exact ca local:

```bash
python collector.py --once      # un ciclu de colectare
python server.py --port 8777    # dashboard, deschis automat în previzualizare
python audit.py                 # auditul site-ului
python fires.py                 # gruparea focarelor
```

### Cheile, în Codespaces

Nu pui `.env` în repo. Le adaugi o singură dată ca **Codespaces secrets**:
GitHub → Settings → **Secrets and variables** → **Codespaces** → New repository secret,
cu numele `CDSE_CLIENT_ID` și `CDSE_CLIENT_SECRET`.

`cdse.py` citește întâi variabilele de mediu, deci merge fără `.env`.

## 3. Ce NU se poate pe GitHub

**Un server care rulează permanent.** Un job de Actions trăiește cel mult **6 ore** și
mașina e efemeră. Deci `server.py` nu poate sta pornit pe GitHub — dar nici nu e nevoie,
pentru că Pages servește starea ca fișier static.

Un Codespace se **oprește singur după 30 de minute de inactivitate** (implicit). Deci
Codespaces e pentru dezvoltare și rulări manuale, nu pentru producție 24/7.

## 4. Google Colab: bun pentru analiză, NU pentru monitorizare

Colab pare soluția gratuită pentru „rulat în cloud", dar nu e potrivit pentru un serviciu
care trebuie să meargă încontinuu. Google o spune explicit în
[FAQ-ul lor](https://research.google.com/colaboratory/faq.html), citat textual:

> „Runtimes will time out if you are idle."

> „In the version of Colab that is free of charge notebooks can run for at most 12 hours"

> „Virtual machines are deleted when idle for a while, and have a maximum lifetime enforced by the Colab service."

> „Colab does not publish these limits, in part because they can vary over time."

În plus, **execuția în fundal e o funcție plătită** — FAQ-ul o listează la „additional
capabilities" ale planurilor superioare.

Deci un colector care trebuie să ruleze la fiecare 5 minute nu poate sta pe Colab.
Nici serverul, nici Streamlit.

### Dar pentru analiză, Colab e chiar bun

`analiza.ipynb` din rădăcina repo-ului rulează în Colab **fără instalare locală și fără
nicio cheie API**: citește datele publice ale monitorului și reproduce cifrele.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/bunilastelian/domogled-live/blob/main/analiza.ipynb)

De ce contează: notebook-ul poate fi deschis și rulat de **oricine** — jurnalist, ONG,
funcționar — ca să verifice singur cifrele, nu să le creadă pe cuvânt. Pentru un subiect
aflat în atenția publică, asta valorează mai mult decât încă un loc unde rulează cod.

Verificat cu `test_notebook.py`, care compilează și execută toate cele 8 celule: reproduce
ambele focare, cu 378 de puncte fiecare, tendințele și reperele.

## Limitele, pe scurt

| | limită | sursa |
|---|---|---|
| durata unui job Actions | 6 ore | docs GitHub |
| interval minim pentru `schedule` | 5 minute | docs GitHub |
| rulări programate | pot întârzia la încărcare mare (ex. la începutul fiecărei ore) | docs GitHub |
| minute Actions, repo public | **gratuite** | docs GitHub |
| minute Actions, repo privat | 2.000/lună pe planul gratuit | docs GitHub |
| Codespaces, cont personal gratuit | 120 ore-nucleu/lună, 15 GB | docs GitHub |
| Codespace inactiv | se oprește după 30 min (implicit) | docs GitHub |

## Rularea manuală din browser

Nu-ți trebuie nici măcar Codespaces pentru o rulare: pe pagina repo-ului →
**Actions** → **colectare live Domogled** → **Run workflow**. Colectorul rulează pe
runner-ul GitHub și publică rezultatul pe Pages.
