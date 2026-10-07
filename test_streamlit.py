"""test_streamlit.py - ruleaza aplicatia headless cu AppTest si raporteaza exceptiile.

Streamlit randeaza prin websocket, deci un simplu request HTTP nu dovedeste ca
scriptul ruleaza. AppTest il executa in proces si expune exceptiile.
"""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

at = AppTest.from_file(str(HERE / "streamlit_app.py"), default_timeout=90)
at.run()

print(f"exceptii: {len(at.exception)}")
for e in at.exception:
    print("  [!]", e.value)
    if getattr(e, "stack_trace", None):
        print("     ", str(e.stack_trace).splitlines()[-3:])

print(f"\nmetrici randate: {len(at.metric)}")
for m in at.metric:
    print(f"  {m.label}: {m.value}")

print(f"\nselectoare: {len(at.selectbox)}  slidere: {len(at.slider)}  "
      f"multiselect: {len(at.multiselect)}  butoane: {len(at.button)}")
print(f"dataframe-uri: {len(at.dataframe)}  markdown: {len(at.markdown)}")
print(f"subheader-e: {[s.value for s in at.subheader]}")
print(f"erori afisate in pagina: {len(at.error)}")
for e in at.error:
    print("   ", e.value)
print(f"avertismente: {len(at.warning)}")

# incercam si interactiunea: schimbarea zonei
if len(at.selectbox):
    opts = at.selectbox[0].options
    print(f"\nzone disponibile in selector: {opts}")
    if len(opts) > 1:
        at.selectbox[0].set_value(opts[1]).run()
        print(f"dupa schimbarea zonei -> exceptii: {len(at.exception)}")
        for m in at.metric[:5]:
            print(f"  {m.label}: {m.value}")

sys.exit(1 if at.exception else 0)
