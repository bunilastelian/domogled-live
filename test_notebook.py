"""
test_notebook.py - verifica analiza.ipynb fara Jupyter.

1. valideaza structura JSON a notebook-ului
2. compileaza fiecare celula de cod (prinde erorile de sintaxa)
3. executa celulele in ordine, in acelasi namespace, ca intr-un kernel

Celula de instalare e sarita (ar instala pachete inutil local).
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NB = Path(__file__).resolve().parent / "analiza.ipynb"
nb = json.loads(NB.read_text(encoding="utf-8"))

# ---------------------------------------------------------------- 1. structura
print("=== 1. structura ===")
assert nb.get("nbformat") == 4, "nbformat trebuie sa fie 4"
celule = nb["cells"]
cod = [c for c in celule if c["cell_type"] == "code"]
md = [c for c in celule if c["cell_type"] == "markdown"]
print(f"  nbformat {nb['nbformat']}.{nb.get('nbformat_minor')}  ·  {len(md)} markdown, {len(cod)} cod")
for c in celule:
    assert "source" in c and isinstance(c["source"], list), "celula fara 'source'"
    if c["cell_type"] == "code":
        assert "outputs" in c and "execution_count" in c, "celula de cod fara outputs/execution_count"
print("  structura valida")

# ---------------------------------------------------------------- 2. compilare
print("\n=== 2. compilare ===")
surse = []
for i, c in enumerate(cod):
    src = "".join(c["source"])
    surse.append(src)
    try:
        compile(src, f"cell_{i}", "exec")
    except SyntaxError as e:
        print(f"  [!] celula {i}: {e}")
        sys.exit(1)
print(f"  toate cele {len(cod)} celule de cod compileaza")

# ------------------------------------------------------------------ 3. rulare
print("\n=== 3. executie ===")

# stub pentru display() din IPython, daca lipseste
ns: dict = {}
try:
    from IPython.display import display  # noqa: F401
except ImportError:
    ns["__builtins__"] = __builtins__
    ns["display"] = lambda *a, **k: print(f"  [display] {type(a[0]).__name__ if a else ''}")

for i, src in enumerate(surse):
    # Celula 0 instaleaza doar ce lipseste; local toate pachetele sunt prezente,
    # deci nu va rula pip. O executam pentru ca defineste si importurile de baza.
    try:
        exec(compile(src, f"cell_{i}", "exec"), ns)
        print(f"  celula {i}: OK")
    except Exception:
        print(f"  [!] celula {i} a eșuat:")
        traceback.print_exc()
        sys.exit(1)

print("\n=== 4. rezultat ===")
focare = ns.get("toate_focarele") or {}
total = sum(len(v) for v in focare.values())
print(f"  focare identificate de notebook: {total}")
for zid, lst in focare.items():
    for f in lst:
        print(f"    {zid}: {f['lat']},{f['lon']} · {f['puncte']} puncte · "
              f"{f['tendinta']} · FRP 24h {f['FRP_24h_MW']} MW")
print("\nnotebook-ul ruleaza cap-coada")
