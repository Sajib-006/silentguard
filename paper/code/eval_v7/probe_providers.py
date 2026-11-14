#!/usr/bin/env python3
"""Check every provider whose key is present: can we authenticate, list models, and complete?

    python probe_providers.py                 # probe all configured keys
    python probe_providers.py --provider google --model gemini-2.5-flash

Prints, per provider: key present, models visible, and the result of a 5-token completion.
Never prints key material.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request

import providers

# A cheap, widely available model to smoke-test each provider with. Adjusted automatically to
# whatever the provider actually lists if the default is missing.
DEFAULT_MODEL = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "hf": "Qwen/Qwen3.5-9B",
    "vtarc": "DeepSeek-V4.1-Flash",
    "google": "gemini-2.5-flash",
    "xai": "grok-3-mini",
    "nvidia": "nvidia/nemotron-3-super-120b-a12b",
    "groq": "llama-3.3-70b-versatile",
    "cerebras": "llama3.1-8b",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "openrouter": "google/gemini-2.5-flash",
}


def list_models(name: str, cfg: dict, key: str) -> list[str]:
    base = cfg["base"]
    if base is None:
        return []
    req = urllib.request.Request(base.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
        return sorted(m.get("id", "") for m in d.get("data", []))
    except urllib.error.HTTPError as e:
        return [f"<HTTP {e.code}>"]
    except Exception as e:
        return [f"<{e.__class__.__name__}>"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    names = [args.provider] if args.provider else list(providers.PROVIDERS)
    print(f"{'provider':<12} {'key':<6} {'free':<5} {'models':<8} completion")
    print("-" * 76)
    for name in names:
        cfg = providers.PROVIDERS[name]
        key = os.environ.get(cfg["key"], "").strip()
        free = "yes" if cfg.get("free_tier") else "-"
        if not key:
            print(f"{name:<12} {'--':<6} {free:<5} {'-':<8} no key ({cfg['key']} unset)")
            continue
        models = list_models(name, cfg, key) if cfg["base"] else []
        nmod = len(models) if models and not models[0].startswith("<") else 0
        model = args.model or DEFAULT_MODEL.get(name)
        if models and nmod and model not in models:
            # fall back to something the provider actually serves
            model = next((m for m in models if any(t in m.lower() for t in
                                                   ("mini", "flash", "8b", "9b", "small"))), models[0])
        try:
            out = providers.chat(name, model, [{"role": "user", "content": "Reply with exactly: PONG"}],
                                 max_tokens=800)
            txt = (out["text"] or "").strip()[:18]
            status = f"OK  {model}  -> {txt!r}"
        except Exception as e:
            status = f"FAIL {model}: {str(e)[:90]}"
        print(f"{name:<12} {'set':<6} {free:<5} {nmod or '-':<8} {status}")

    print("\nmodels of interest, where visible:")
    for name in names:
        cfg = providers.PROVIDERS[name]
        key = os.environ.get(cfg["key"], "").strip()
        if not key or not cfg["base"]:
            continue
        ms = list_models(name, cfg, key)
        if not ms or ms[0].startswith("<"):
            continue
        hits = [m for m in ms if any(t in m.lower() for t in ("gemini", "grok", "nemotron", "qwen", "llama"))]
        if hits:
            print(f"  {name}: {', '.join(hits[:8])}{' ...' if len(hits) > 8 else ''}")


if __name__ == "__main__":
    main()
