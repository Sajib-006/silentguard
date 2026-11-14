"""Unified chat client for the v7 harness.

Every provider is called through its official API. Returns the same shape for all:
    {"text": str, "model_id": str, "usage": {...}, "finish": str, "raw_reasoning": str}
No IDE agents, no CLI wrappers. Keys come from code/.env or the environment.
"""
from __future__ import annotations

import json, os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # .../code
ENV_FILE = ROOT / ".env"


def load_env() -> None:
    """Load KEY=VALUE lines from code/.env without overriding real env vars."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

# Every entry but `anthropic` speaks the OpenAI chat-completions protocol, so one code path
# serves them all. Free-tier providers are listed first because they are the default plan:
# OpenRouter is only needed for a model none of the others serve.
PROVIDERS = {
    # paid, keys already in place
    "openai":    {"base": None, "key": "OPENAI_API_KEY"},
    "anthropic": {"base": None, "key": "ANTHROPIC_API_KEY"},
    # free / org-billed
    "hf":        {"base": "https://router.huggingface.co/v1", "key": "HF_TOKEN"},
    # VT ARC institutional endpoint — free, OpenAI-compatible. Serves GLM-5.3, Kimi-K3,
    # gpt-oss-120b and DeepSeek-V4.1-Flash, each with -thinking-low/high/max variants, which
    # gives a within-model reasoning ablation at no cost.
    # locally served models (E6): base and fine-tuned, same OpenAI wire format so the evaluation
    # path is byte-identical to the API providers.
    "local_base": {"base": "http://127.0.0.1:9100/v1", "key": "CLAUDE_CLI_DUMMY", "free_tier": True},
    "local_sft":  {"base": "http://127.0.0.1:9101/v1", "key": "CLAUDE_CLI_DUMMY", "free_tier": True},
    "local_sft2": {"base": "http://127.0.0.1:9102/v1", "key": "CLAUDE_CLI_DUMMY", "free_tier": True},
    "local_rl":   {"base": "http://127.0.0.1:9103/v1", "key": "CLAUDE_CLI_DUMMY", "free_tier": True},
    "local_m_base": {"base": "http://127.0.0.1:9104/v1", "key": "CLAUDE_CLI_DUMMY", "free_tier": True},
    "local_m_sft":  {"base": "http://127.0.0.1:9105/v1", "key": "CLAUDE_CLI_DUMMY", "free_tier": True},
    "local_m_sftr": {"base": "http://127.0.0.1:9106/v1", "key": "CLAUDE_CLI_DUMMY", "free_tier": True},
    # Claude Code CLI over a reverse SSH tunnel to a machine where it is logged in.
    # NOT comparable with the API providers: the CLI keeps its tool schemas in the model context
    # even with the system prompt replaced and tool execution gated. Report separately, labelled.
    "claude_cli": {"base": "http://127.0.0.1:9999/v1", "key": "CLAUDE_CLI_DUMMY",
                   "free_tier": True, "not_comparable": True},
    # Claude on Google Vertex AI — clean Messages API, comparable with other API providers.
    "vertex":    {"base": None, "key": "VERTEX_PROJECT_ID"},
    "vtarc":     {"base": "https://llm-api.arc.vt.edu/api/v1", "key": "VTARC_API_KEY",
                  "free_tier": True},
    "google":    {"base": "https://generativelanguage.googleapis.com/v1beta/openai",
                  "key": "GOOGLE_API_KEY", "free_tier": True},     # AI Studio: Gemini
    "xai":       {"base": "https://api.x.ai/v1", "key": "XAI_API_KEY"},           # Grok
    "nvidia":    {"base": "https://integrate.api.nvidia.com/v1",
                  "key": "NVIDIA_API_KEY", "free_tier": True},     # NIM dev tier: Nemotron etc.
    "groq":      {"base": "https://api.groq.com/openai/v1",
                  "key": "GROQ_API_KEY", "free_tier": True},       # fast open-weight
    "cerebras":  {"base": "https://api.cerebras.ai/v1",
                  "key": "CEREBRAS_API_KEY", "free_tier": True},
    "together":  {"base": "https://api.together.xyz/v1", "key": "TOGETHER_API_KEY"},
    # last resort, paid per token
    "openrouter": {"base": "https://openrouter.ai/api/v1", "key": "OPENROUTER_API_KEY"},
}

_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _key(provider: str) -> str:
    name = PROVIDERS[provider]["key"]
    val = os.environ.get(name, "").strip()
    if not val:
        raise SystemExit(f"missing {name} (put it in {ENV_FILE})")
    return val


HF_PIN = {"meta-llama/Llama-3.3-70B-Instruct": "scaleway"}   # returns id llama-3.3-70b-instruct (majority of our runs)
# per-run override, e.g. HF_PIN_JSON='{"meta-llama/Llama-3.3-70B-Instruct": "novita"}' (added 2026-09-30)
HF_PIN.update(json.loads(os.environ.get("HF_PIN_JSON", "{}")))



# Hard spending cap for HF Inference Providers (added 2026-09-30 after a large org bill).
# Every process adds its cost to one locked ledger; once HF_BUDGET_USD is reached, all HF calls
# stop with an error. Prices are the highest live provider price per 1M tokens (in, out), so the
# ledger over-counts rather than under-counts.
HF_PRICE = {
    "meta-llama/Llama-4-Scout-17B-16E-Instruct": (0.18, 0.59),
    "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8": (0.27, 0.85),
    "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16": (1.0, 5.0),
    "meta-llama/Llama-3.3-70B-Instruct": (1.04, 1.04),
    "google/gemma-3-27b-it": (0.08, 0.16),
    "microsoft/phi-4": (0.07, 0.14),
}
# price of the pinned backend when a run pins a cheaper one, e.g. HF_PRICE_JSON='{"<model>": [0.135, 0.4]}'
HF_PRICE.update({k: tuple(v) for k, v in json.loads(os.environ.get("HF_PRICE_JSON", "{}")).items()})
HF_BUDGET_USD = float(os.environ.get("HF_BUDGET_USD", "0") or 0)
_HF_LEDGER = os.environ.get("HF_LEDGER", str(Path(__file__).resolve().parents[2] / "runs" / "hf_spend_ledger.json"))


class HFBudgetExceeded(RuntimeError):
    pass


def _hf_ledger(update=None) -> dict:
    import fcntl, json as _json
    with open(_HF_LEDGER + ".lock", "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(_HF_LEDGER) as f:
                d = _json.load(f)
        except (OSError, ValueError):
            d = {"usd": 0.0, "tok_in": 0, "tok_out": 0, "calls": 0, "by_model": {}}
        if update:
            update(d)
            with open(_HF_LEDGER + ".tmp", "w") as f:
                _json.dump(d, f, indent=1)
            os.replace(_HF_LEDGER + ".tmp", _HF_LEDGER)
        return d


def _hf_budget_check() -> None:
    if not os.environ.get("HF_BILL_SELF"):
        return
    if HF_BUDGET_USD <= 0:
        raise HFBudgetExceeded("HF_BILL_SELF is set but HF_BUDGET_USD is not; refusing to call HF")
    d = _hf_ledger()
    if d["usd"] >= HF_BUDGET_USD:
        raise HFBudgetExceeded(f"HF budget reached: ${d['usd']:.2f} of ${HF_BUDGET_USD:.2f}")


def _hf_budget_add(model: str, usage: dict) -> None:
    if not os.environ.get("HF_BILL_SELF"):
        return
    pi, po = HF_PRICE.get(model, (5.0, 15.0))
    ti, to = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    cost = (ti * pi + to * po) / 1e6

    def up(d):
        d["usd"] += cost; d["tok_in"] += ti; d["tok_out"] += to; d["calls"] += 1
        m = d["by_model"].setdefault(model, {"usd": 0.0, "calls": 0})
        m["usd"] += cost; m["calls"] += 1
    _hf_ledger(up)


# Hard spending cap for OpenAI (added 2026-10-05). Active when OPENAI_BUDGET_USD is set: every process adds its
# cost to one locked ledger and all OpenAI calls stop once the cap is reached. List prices per 1M tokens (in, out).
OAI_PRICE = {"gpt-5.6-sol": (5.0, 30.0), "gpt-5.6-terra": (2.0, 12.0), "gpt-5.6-luna": (0.2, 1.2)}
OAI_BUDGET_USD = float(os.environ.get("OPENAI_BUDGET_USD", "0") or 0)
_OAI_LEDGER = os.environ.get("OPENAI_LEDGER", str(Path(__file__).resolve().parents[2] / "runs" / "openai_spend_ledger.json"))


def _oai_ledger(update=None) -> dict:
    import fcntl, json as _json
    with open(_OAI_LEDGER + ".lock", "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(_OAI_LEDGER) as f:
                d = _json.load(f)
        except (OSError, ValueError):
            d = {"usd": 0.0, "tok_in": 0, "tok_out": 0, "calls": 0, "by_model": {}}
        if update:
            update(d)
            with open(_OAI_LEDGER + ".tmp", "w") as f:
                _json.dump(d, f, indent=1)
            os.replace(_OAI_LEDGER + ".tmp", _OAI_LEDGER)
        return d


def _oai_budget_check() -> None:
    if OAI_BUDGET_USD <= 0:
        return
    d = _oai_ledger()
    if d["usd"] >= OAI_BUDGET_USD:
        raise HFBudgetExceeded(f"OpenAI budget reached: ${d['usd']:.2f} of ${OAI_BUDGET_USD:.2f}")


def _oai_budget_add(model: str, usage: dict) -> None:
    if OAI_BUDGET_USD <= 0:
        return
    pi, po = OAI_PRICE.get(model, (5.0, 30.0))
    ti, to = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    cost = (ti * pi + to * po) / 1e6

    def up(d):
        d["usd"] += cost; d["tok_in"] += ti; d["tok_out"] += to; d["calls"] += 1
        m = d["by_model"].setdefault(model, {"usd": 0.0, "calls": 0, "tok_in": 0, "tok_out": 0})
        m["usd"] += cost; m["calls"] += 1; m["tok_in"] += ti; m["tok_out"] += to
    _oai_ledger(up)

# VT ARC allows 200 requests per minute for the whole account. Every process on this machine
# (agents, the tau user simulator, judges) shares one budget through a locked timestamp file, so
# the sum stays under VTARC_RPM no matter how many runs are going. Added 2026-09-28.
VTARC_RPM = int(os.environ.get("VTARC_RPM", "60"))
_VTARC_STATE = "/tmp/aob_vtarc_rate.json"


def _vtarc_slot() -> None:
    import fcntl, json as _json
    while True:
        with open(_VTARC_STATE + ".lock", "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                with open(_VTARC_STATE) as f:
                    ts = _json.load(f)
            except (OSError, ValueError):
                ts = []
            now = time.time()
            ts = [t for t in ts if now - t < 60.0]
            if len(ts) < VTARC_RPM:
                ts.append(now)
                with open(_VTARC_STATE + ".tmp", "w") as f:
                    _json.dump(ts, f)
                os.replace(_VTARC_STATE + ".tmp", _VTARC_STATE)
                return
            wait = 60.0 - (now - min(ts)) + 0.05
        time.sleep(max(0.05, wait))

def _openai_compat(provider: str, model: str, messages: list[dict], temperature: float | None,
                   max_tokens: int, seed: int | None) -> dict:
    from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError

    cfg = PROVIDERS[provider]
    headers = {}
    # HF_BILL_SELF=1 bills the token owner's own account instead of the org (added 2026-09-30)
    if provider == "hf" and os.environ.get("HF_BILL_TO") and not os.environ.get("HF_BILL_SELF"):
        headers["X-HF-Bill-To"] = os.environ["HF_BILL_TO"]
    client = OpenAI(api_key=_key(provider), base_url=cfg["base"], default_headers=headers or None,
                    timeout=180, max_retries=0)

    body: dict = {"model": model, "messages": messages}
    if provider == "hf" and model in HF_PIN:
        # the HF router spreads some models over several backends and the pool changes over time
        # (Llama-3.3 was served as "-Instruct-Turbo" by one of them for ~1k rows). Pin one backend;
        # the row keeps the plain model name and records the id the backend returned.
        body["model"] = f"{model}:{HF_PIN[model]}"
    # GPT-5.x rejects max_tokens; older models reject max_completion_tokens.
    body["max_completion_tokens" if provider == "openai" else "max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    if seed is not None and provider in ("openai", "groq", "together"):
        body["seed"] = seed
    if provider == "hf":
        # ask thinking-mode models to answer directly; providers that ignore this still work
        body["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    last = None
    for attempt in range(1, 9):
        try:
            if provider == "vtarc":
                _vtarc_slot()
            if provider == "hf":
                _hf_budget_check()
            if provider == "openai":
                _oai_budget_check()
            resp = client.chat.completions.create(**body)
            msg = resp.choices[0].message
            text = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""
            if not text and reasoning:
                # thinking model that spent the whole budget reasoning: surface it so the loop can react
                text = ""
            usage = resp.usage.model_dump() if resp.usage else {}
            if provider == "hf":
                _hf_budget_add(model, usage)
            if provider == "openai":
                _oai_budget_add(model, usage)
            return {"text": text, "model_id": resp.model or model, "usage": usage,
                    "finish": resp.choices[0].finish_reason or "", "raw_reasoning": reasoning[:2000],
                    # None = the provider's own default was used (param rejected or never sent)
                    "temperature_used": body.get("temperature")}
        except APIStatusError as e:
            last = f"HTTP {e.status_code}: {str(e)[:300]}"
            msg_l = str(e).lower()
            # parameter-compat fallbacks, retried immediately
            if e.status_code == 400 and "temperature" in msg_l and "temperature" in body:
                body.pop("temperature"); continue
            if e.status_code == 400 and "max_tokens" in msg_l and "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens"); continue
            if e.status_code == 400 and "max_completion_tokens" in msg_l and "max_completion_tokens" in body:
                body["max_tokens"] = body.pop("max_completion_tokens"); continue
            if e.status_code == 400 and "seed" in msg_l and "seed" in body:
                body.pop("seed"); continue
            if e.status_code == 400 and "chat_template_kwargs" in msg_l and "extra_body" in body:
                body.pop("extra_body"); continue
            if e.status_code not in _RETRY_STATUS:
                raise
        except (APIConnectionError, APITimeoutError) as e:
            last = f"conn: {e}"
        time.sleep(min(120, 5 * attempt * attempt))
    raise RuntimeError(f"{provider}/{model} failed after retries: {last}")


def _anthropic_params() -> set[str]:
    import inspect
    import anthropic
    return set(inspect.signature(anthropic.resources.messages.Messages.create).parameters)


def supports_temperature(provider: str) -> bool:
    return provider not in ("anthropic", "vertex") or "temperature" in _anthropic_params()


def _anthropic(model: str, messages: list[dict], temperature: float | None,
               max_tokens: int) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=_key("anthropic"), timeout=180, max_retries=0)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]
    kw = {"model": model, "max_tokens": max_tokens, "messages": turns}
    if system:
        kw["system"] = system
    # anthropic SDK >= 1.x (Claude 5 API) exposes no sampling temperature; the runner records
    # temperature=None for this provider so the provenance is honest.
    if temperature is not None and "temperature" in _anthropic_params():
        kw["temperature"] = temperature
    last = None
    for attempt in range(1, 9):
        try:
            resp = client.messages.create(**kw)
            text = "".join(getattr(b, "text", "") for b in resp.content)
            usage = {"prompt_tokens": resp.usage.input_tokens, "completion_tokens": resp.usage.output_tokens,
                     "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens}
            return {"text": text, "model_id": resp.model, "usage": usage,
                    "finish": resp.stop_reason or "", "raw_reasoning": "",
                    "temperature_used": kw.get("temperature")}
        except anthropic.APIStatusError as e:
            last = f"HTTP {e.status_code}: {str(e)[:300]}"
            if e.status_code not in _RETRY_STATUS:
                raise
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            last = f"conn: {e}"
        time.sleep(min(120, 5 * attempt * attempt))
    raise RuntimeError(f"anthropic/{model} failed after retries: {last}")


def _anthropic_vertex(model: str, messages: list[dict], temperature, max_tokens: int) -> dict:
    """Claude via Vertex. Identical request construction to _anthropic, different transport."""
    import anthropic
    from anthropic import AnthropicVertex

    client = AnthropicVertex(project_id=_key("vertex"),
                             region=os.environ.get("VERTEX_REGION", "us-east5"),
                             timeout=180, max_retries=0)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]
    kw = {"model": model, "max_tokens": max_tokens, "messages": turns}
    if system:
        kw["system"] = system
    # same rule as the direct Anthropic path: only send temperature if the SDK accepts it
    if temperature is not None and "temperature" in _anthropic_params():
        kw["temperature"] = temperature
    last = None
    for attempt in range(1, 9):
        try:
            resp = client.messages.create(**kw)
            text = "".join(getattr(b, "text", "") for b in resp.content)
            usage = {"prompt_tokens": resp.usage.input_tokens,
                     "completion_tokens": resp.usage.output_tokens,
                     "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens}
            return {"text": text, "model_id": resp.model, "usage": usage,
                    "finish": resp.stop_reason or "", "raw_reasoning": "",
                    "temperature_used": kw.get("temperature")}
        except anthropic.APIStatusError as e:
            last = f"HTTP {e.status_code}: {str(e)[:300]}"
            if e.status_code not in _RETRY_STATUS:
                raise
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            last = f"conn: {e}"
        time.sleep(min(120, 5 * attempt * attempt))
    raise RuntimeError(f"vertex/{model} failed after retries: {last}")


def chat(provider: str, model: str, messages: list[dict], temperature: float | None = 0.0,
         max_tokens: int = 2000, seed: int | None = None) -> dict:
    if provider not in PROVIDERS:
        raise SystemExit(f"unknown provider {provider}; choose from {sorted(PROVIDERS)}")
    if provider == "anthropic":
        return _anthropic(model, messages, temperature, max_tokens)
    if provider == "vertex":
        return _anthropic_vertex(model, messages, temperature, max_tokens)
    return _openai_compat(provider, model, messages, temperature, max_tokens, seed)


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True)
    ap.add_argument("--model", required=True)
    args = ap.parse_args()
    out = chat(args.provider, args.model, [{"role": "user", "content": "Reply with exactly: PONG"}], max_tokens=20)
    print(json.dumps(out, indent=1)[:600])
