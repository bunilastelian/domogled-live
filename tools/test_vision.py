#!/usr/bin/env python3
"""Test: ce modele de pe Token Plan accepta imagini (viziune)."""
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
KEY = os.environ["DASHSCOPE_API_KEY"]

img = Path("/home/lili/projects/domogled-live/live/web/img/"
           "s2_S2A_MSIL2A_20261004T093141_N0513_R136_T34TFQ_20261004T143711.png")
b64 = base64.b64encode(img.read_bytes()).decode()
print(f"imagine: {img.stat().st_size} bytes -> {len(b64)} base64\n")

MODELS = ["qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "glm-5.3",
          "deepseek-v4.1-flash", "auto"]


def ask(model: str) -> str:
    body = {
        "model": model,
        "max_tokens": 120,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Ce vezi in imaginea satelitara? Raspunde in maxim 2 propozitii."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
        return "DA  -> " + d["choices"][0]["message"]["content"][:140].replace("\n", " ")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()[:200]
        try:
            msg = json.loads(raw).get("error", {}).get("message", raw)
        except Exception:
            msg = raw
        return f"NU  ({e.code}) {msg[:100]}"
    except Exception as e:  # noqa: BLE001
        return f"ERR {type(e).__name__}: {str(e)[:80]}"


for m in MODELS:
    print(f"{m:22} {ask(m)}", flush=True)
