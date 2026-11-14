"""The canonical model roster and the canonical run settings.

Every experiment imports SETTINGS from here rather than passing its own flags, so a fairness
question has one answer in one place. If a model needs a deviation, it is recorded in
`DEVIATIONS` and printed in the results, never applied silently.

Origin is tracked because the paper sits under a pharma affiliation whose policy on
Chinese-origin weights is unresolved: TIER_PRIMARY is safe under any ruling, and the optional
cohort can be added or dropped without re-running anything else.
"""
from __future__ import annotations

# ── identical settings for every model, every experiment ────────────────────
SETTINGS = {
    "temperature": 0.0,      # anthropic exposes none; recorded as null (see DEVIATIONS)
    "max_tokens": 2000,
    "max_turns": 16,
    "abort_no_progress": 4,  # measured: no scoring change, ~40% fewer input tokens
    "g_enforce": "block",    # canonical Condition G: escape impossible by construction
    "seeds": 3,
    "prompt_view_chars": 15000,
}

DEVIATIONS = {
    "openai/gpt-5.6-*": "The API rejects temperature=0 for GPT-5.6 (HTTP 400: only the default is supported), so these models sample at the provider default. Audit 2026-09-16: every other roster model runs at 0.0 as requested. Extra sampling variance is covered by the same 3 seeds; each run row records temperature_effective.",
    "anthropic/*": "Anthropic Messages API exposes no temperature parameter; temperature is "
                   "recorded as null rather than 0.0. All other settings identical.",
    "microsoft/phi-4": "Weak instruction-follower: frequently ignores the ANSWER: format. "
                       "Kept for capability spread; its low scores are partly format failure.",
}

# (key, provider, model_id, origin, params_hint)
# Every entry below was verified on real HD100 tasks, not just a ping: a provider that answers
# "PONG" can still fail the benchmark protocol (see EXCLUDED).
TIER_PRIMARY = [
    # US / EU / CA weights — usable regardless of the pending policy answer
    ("llama4-scout",     "hf",     "meta-llama/Llama-4-Scout-17B-16E-Instruct",        "US (Meta)",      "17B-16E"),
    ("llama4-maverick",  "hf",     "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8","US (Meta)",      "17B-128E"),
    ("llama33-70b",      "hf",     "meta-llama/Llama-3.3-70B-Instruct",                "US (Meta)",      "70B"),
    # served by VT ARC, not HF: the HF route rejects this model's plain-text output as a
    # malformed tool call (see EXCLUDED/gpt-oss-20b). Same weights, working transport.
    ("gpt-oss-120b",     "vtarc",  "gpt-oss-120b",                                     "US (OpenAI)",    "120B"),
    ("nemotron-ultra",   "hf",     "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",    "US (NVIDIA)",    "550B-A55B"),
    ("gemma3-27b",       "hf",     "google/gemma-3-27b-it",                            "US (Google)",    "27B"),
    ("phi-4",            "hf",     "microsoft/phi-4",                                  "US (Microsoft)", "14B"),
    # proprietary, keys already held
    # frontier closed models (added 2026-09-16; same settings, same full experiment suite)
    ("gpt-5.6-sol",      "openai", "gpt-5.6-sol",                                      "US (OpenAI)",    "—"),
    ("gpt-5.6-terra",    "openai", "gpt-5.6-terra",                                    "US (OpenAI)",    "—"),
    ("gpt-5.6-luna",     "openai", "gpt-5.6-luna",                                     "US (OpenAI)",    "—"),
    ("gpt-4o-mini",      "openai", "gpt-4o-mini",                                      "US (OpenAI)",    "—"),
    ("gpt-4o",           "openai", "gpt-4o",                                           "US (OpenAI)",    "—"),
    ("gpt-4.1",          "openai", "gpt-4.1",                                          "US (OpenAI)",    "—"),
    ("haiku-4.5",        "anthropic", "claude-haiku-4-5-20251001",                     "US (Anthropic)", "—"),
    ("sonnet-5",         "anthropic", "claude-sonnet-5",                               "US (Anthropic)", "—"),
]

# Verified NOT usable, with the reason. Kept visible so the roster's composition is a recorded
# decision rather than an unexplained gap — a reviewer will ask why Cohere is absent.
EXCLUDED = {
    "openai/gpt-oss-20b via hf":
        "HTTP 400 'Tool choice is none, but model called a tool' on every task: the HF route "
        "parses the model's plain-text RUN: line as a malformed tool call. gpt-oss-120b via "
        "vtarc is unaffected and is in the roster.",
    "CohereLabs/command-a-reasoning-08-2025":
        "HTTP 422 NO_VALID_RESPONSE_GENERATED on every task with the real prompt, though short "
        "pings succeed; hangs when retried at higher max_tokens.",
    "CohereLabs/command-a-plus-05-2026-bf16":
        "HTTP 429 rate-limited on the real prompt after full retry backoff; not viable for a sweep.",
    "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16":
        "HTTP 400: not served by any provider behind the HF router.",
}

# Optional appendix cohort. Independent of TIER_PRIMARY: adding or dropping it forces no other re-run.
TIER_OPTIONAL_CN = [
    ("deepseek-v41",     "vtarc", "DeepSeek-V4.1-Flash", "CN (DeepSeek)", "—"),
    ("glm-53",           "vtarc", "GLM-5.3",             "CN (Zhipu)",    "—"),
    ("kimi-k3",          "vtarc", "Kimi-K3",             "CN (Moonshot)", "—"),
    ("qwen35-9b",        "hf",    "Qwen/Qwen3.5-9B",     "CN (Alibaba)",  "9B"),
]

# Same weights, different reasoning budget — a within-model ablation for the gap study.
REASONING_PAIRS = [
    ("gpt-oss-120b-low",  "vtarc", "gpt-oss-120b-thinking-low",  "US (OpenAI)", "120B"),
    ("gpt-oss-120b-high", "vtarc", "gpt-oss-120b-thinking-high", "US (OpenAI)", "120B"),
]

# Slower than ~30 s/run on the pilot: fewer seeds, or give them their own worker.
SLOW = {"kimi-k3"}


def roster(include_cn: bool = False, include_reasoning: bool = False) -> list[tuple]:
    out = list(TIER_PRIMARY)
    if include_cn:
        out += TIER_OPTIONAL_CN
    if include_reasoning:
        out += REASONING_PAIRS
    return out


def as_args(settings: dict | None = None) -> list[str]:
    """SETTINGS rendered as runner.py flags, so no experiment can drift."""
    s = {**SETTINGS, **(settings or {})}
    return ["--temperature", str(s["temperature"]),
            "--max-tokens", str(s["max_tokens"]),
            "--max-turns", str(s["max_turns"]),
            "--abort-no-progress", str(s["abort_no_progress"])]


if __name__ == "__main__":
    print("canonical settings:", SETTINGS)
    print("\nPRIMARY roster (safe under any policy ruling):")
    for k, p, m, o, sz in TIER_PRIMARY:
        print(f"  {k:<18} {p:<10} {o:<16} {sz:<10} {m}")
    print("\nOPTIONAL CN cohort (pending policy):")
    for k, p, m, o, sz in TIER_OPTIONAL_CN:
        print(f"  {k:<18} {p:<10} {o:<16} {sz:<10} {m}")
    print("\nreasoning pairs:", [k for k, *_ in REASONING_PAIRS])
    print("\ndeviations:")
    for k, v in DEVIATIONS.items():
        print(f"  {k}: {v}")
    print("\nexcluded (verified unusable):")
    for k, v in EXCLUDED.items():
        print(f"  {k}\n      {v}")
