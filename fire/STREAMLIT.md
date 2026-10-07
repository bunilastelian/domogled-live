# Streamlit — se poate, dar e altă arhitectură

Răspuns scurt: **da, se poate găzdui gratuit**, dar Streamlit înlocuiește doar *partea de vizualizare*,
nu și colectorul. Și nu poate rula pe GitHub Actions, pentru că e un server care trebuie să stea pornit.

## Ce am construit

`streamlit_app.py` în rădăcina repo-ului — o consolă de analiză care citește **aceeași stare**
pe care o scrie colectorul. Nu dublează date, nu are nevoie de colectorul local.

Ce oferă, peste dashboard-ul static:

- selectoare pentru zonă, interval (6 h … tot istoricul), FRP minim, sursă;
- hartă folium cu detecții, contur de parc și suprapunere de arsură;
- tabel de detecții filtrabil, cu **descărcare CSV**;
- grafic de intensitate orar și pe zile;
- panou meteo și indice de pericol.

Rulare locală:

```bash
streamlit run streamlit_app.py
```

Testare fără browser (framework-ul oficial `AppTest`, care scoate excepțiile):

```bash
python test_streamlit.py
```

## Găzduire gratuită: Streamlit Community Cloud

Se conectează la contul GitHub și rulează direct din repo. `requirements.txt` din rădăcină
e citit automat — de aceea l-am adăugat.

Pași: <https://share.streamlit.io/> → **New app** → alege repo-ul `domogled-live` →
main file `streamlit_app.py` → Deploy.

### Limitele, cu ce am putut verifica și ce nu

| | valoare | cât de sigur |
|---|---|---|
| minute Actions, repo public | gratuite | verificat în documentația GitHub |
| durata unui job Actions | 6 ore | verificat în documentația GitHub |
| Codespaces, cont gratuit | 120 ore-nucleu/lună | verificat în documentația GitHub |
| Streamlit Cloud, repaus la inactivitate | se spune ~12 h, apoi necesită reboot | **neverificat** — pagina lor de limite se randează în JavaScript și n-am putut citi textul |

Nu am pus cifre pe care nu le-am putut confirma. Verifică-le pe
<https://docs.streamlit.io/deploy/streamlit-community-cloud/status-and-limitations> înainte
să te bazezi pe ele.

## De ce NU înlocuiește dashboard-ul actual

| | static (GitHub Pages) | Streamlit |
|---|---|---|
| cost | 0 | 0, dar cu limite de resurse |
| disponibilitate | mereu, servit din CDN | **adoarme** după inactivitate |
| viteză la prima încărcare | ~73 KB | mai greu: pornește un proces Python per vizitator |
| interactivitate | filtre | mult mai bogată: tabele, CSV, drill-down |
| dependențe | 2 (Leaflet) | Streamlit + folium + pandas |
| colectare | Actions, la 15 min | **nu colectează** — doar citește |

Concluzia: **nu înlocui**, ci **adaugă**. Dashboard-ul static rămâne interfața publică
(mereu disponibilă, ușoară, fără repaus), iar Streamlit e consola de analiză pentru cineva
care vrea să filtreze, să descarce date și să exploreze.

## Capcana pe care trebuie s-o eviți

**Nu pune colectorul înăuntrul aplicației Streamlit.** S-ar executa doar când cineva deschide
pagina, deci datele ar fi colectate aleatoriu, iar la repaus nu s-ar colecta deloc.
Colectorul rămâne în GitHub Actions, iar Streamlit doar citește `live/state/state.json`.

## Dependențe

`requirements.txt` (rădăcină) — pentru Streamlit.
`live/requirements.txt` — pentru colector. Sunt separate intenționat: CI-ul nu are nevoie
să instaleze Streamlit, iar Streamlit nu are nevoie de pyproj sau netCDF.
