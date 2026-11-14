#!/usr/bin/env python3
"""tau-bench retail with the silent read faults of Outcome Monitors (Panthi & Abdelfattah 2026):
  status   - `get_order_details` shows an order status outside the known set ("in transit")
  conserv  - `get_order_details` shows a payment amount that breaks net payment = item total
  clean    - no fault (false-alarm cost)
Faults are persistent (every call) and change ONLY the text the agent sees; the database is never
touched, so tau-bench's own state-based reward scores the true outcome.

Same six arms and the same SilentGuard / Outcome-Monitor code as the ToolMaze study.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import threading
import time
import traceback
import types
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
AOB = HERE.parents[2]
TB = AOB / "external" / "tau-bench"
sys.modules.setdefault("litellm", types.SimpleNamespace(completion=None))   # user sim goes through providers.py
sys.path.insert(0, str(TB))
sys.path.insert(0, str(AOB / "code" / "eval_v7"))
sys.path.insert(0, str(AOB / "code" / "external" / "toolmaze"))

from tau_bench.envs.retail.env import MockRetailDomainEnv    # noqa: E402
from tau_bench.envs.user import LLMUserSimulationEnv         # noqa: E402
from tau_bench.types import Action, RESPOND_ACTION_NAME      # noqa: E402
import providers                                             # noqa: E402
import roster                                                # noqa: E402
from tm_arms import (ARMS, CHECKLIST, NUDGE, REVIEWER_SYS, extract_json_objects,   # noqa: E402
                     parse_call, sg_envelope, strip_think)

FAULTS = ["clean", "status", "conserv"]
# free VT ARC model (US origin); was openai/gpt-4.1-mini until OpenAI credit ran out 2026-09-25
USER_PROVIDER, USER_MODEL = "vtarc", "gpt-oss-120b"
BAD_STATUS = "in transit"

# ToolMaze's FAULT HANDLING block, word for word, so the `hint` arm is the same method on both datasets
FAULT_HANDLING = """FAULT HANDLING
Tools may occasionally fail or return unexpected data.
- When evaluating tool outputs, consider whether the returned data is coherent and usable for the next step — fields should be present, values should be semantically reasonable, and types should match what the workflow requires.
- If you determine that a required step cannot be accomplished by any available tool or approach, stop the workflow and clearly report which step failed and why, rather than proceeding with incomplete results."""

FORMAT = """
HOW TO ACT
Each reply is either ONE tool call or ONE message to the user, never both.
- To call a tool, reply with exactly one JSON block and nothing after it:
```json
{"tool": "tool_name", "arguments": {"param1": "value1"}}
```
- To talk to the user, reply with plain text only (no JSON tool block)."""


# ── contracts mined from the public database (orders.json), as Outcome Monitors describe ──
def mine_db_contracts():
    orders = json.load(open(TB / "tau_bench" / "envs" / "retail" / "data" / "orders.json"))
    statuses = sorted({o["status"] for o in orders.values()})
    ttypes = sorted({p["transaction_type"] for o in orders.values() for p in o["payment_history"]})
    prices_pos = all(i["price"] > 0 for o in orders.values() for i in o["items"])
    cons = all(abs(sum(i["price"] for i in o["items"]) - net(o)) < 0.01
               for o in orders.values() if o["status"] != "cancelled")
    return {"status": statuses, "transaction_type": ttypes, "price_pos": prices_pos, "conservation": cons}


def net(o):
    return round(sum(p["amount"] if p["transaction_type"] == "payment" else -p["amount"]
                     for p in o["payment_history"]), 2)


class TauRegistry:
    def __init__(self, c):
        self.c = c

    def check(self, tool, args, y):
        """Returns (code, path, detail, expected, observed)."""
        V = []
        if tool != "get_order_details" or not isinstance(y, dict):
            return V
        if y.get("status") not in self.c["status"]:
            V.append(("unseen_category", "status", "field status has unseen categorical value",
                      self.c["status"], y.get("status")))
        for i, p in enumerate(y.get("payment_history", [])):
            if p.get("transaction_type") not in self.c["transaction_type"]:
                V.append(("unseen_category", f"payment_history[{i}].transaction_type", "unseen transaction type",
                          self.c["transaction_type"], p.get("transaction_type")))
        if self.c["price_pos"]:
            for i, it in enumerate(y.get("items", [])):
                if not (isinstance(it.get("price"), (int, float)) and it["price"] > 0):
                    V.append(("nonpositive_value", f"items[{i}].price", "item price is not positive", "> 0", it.get("price")))
        if self.c["conservation"] and y.get("status") != "cancelled" and y.get("items") is not None:
            tot = round(sum(i.get("price", 0) for i in y["items"]), 2)
            if abs(tot - net(y)) >= 0.01:
                V.append(("affine_relation_broken", "payment_history[*].amount,items[*].price",
                          "net payment differs from item total", tot, net(y)))
        return V


def inject(fault, tool, obs):
    """Corrupt what the agent sees (never the database)."""
    if fault == "clean" or tool != "get_order_details":
        return obs
    try:
        y = json.loads(obs)
    except (json.JSONDecodeError, TypeError):
        return obs
    if fault == "status":
        y["status"] = BAD_STATUS
    elif fault == "conserv" and y.get("payment_history"):
        y["payment_history"][0]["amount"] = round(y["payment_history"][0]["amount"] * 1.15, 2)
    return json.dumps(y)


def om_text(tool, obs, V):
    """Outcome Monitors tau-bench advisory template (their Appendix N), appended to the raw result."""
    lines = [obs, "", "[OUTCOME CONTRACT ADVISORY]",
             f"The observed result from `{tool}` is inconsistent with a known invariant of the retail database."]
    for code, path, _, exp, obsv in V:
        lines.append(f"Violation: {code} at `{path}`; expected {exp}, observed {obsv}.")
    lines.append("This advisory is nonbinding. Treat the result as unreliable and verify or recover using "
                 "the available documented tools and task context before relying on it. All actions remain available.")
    return "\n".join(lines)


def _drop_path(d, path):
    """Remove a field; `a[*].b` removes b from every element of list a; `a[3].b` from element 3."""
    head, _, rest = path.partition(".")
    if "[" in head:
        name, idx = head[:-1].split("[")
        seq = d.get(name) if isinstance(d, dict) else None
        if not isinstance(seq, list):
            return
        targets = seq if idx == "*" else ([seq[int(idx)]] if int(idx) < len(seq) else [])
        for t in targets:
            if rest:
                _drop_path(t, rest)
        return
    if not rest:
        if isinstance(d, dict):
            d.pop(head, None)
    elif isinstance(d, dict) and head in d:
        _drop_path(d[head], rest)


def sg_text(tool, y, V, n_fail):
    """SilentGuard view: same envelope and wording as tm_arms. No other tool returns order details in
    tau retail, so R = [tool]."""
    withheld = []
    for v in V:
        for f in v[1].split(","):
            if f not in withheld:
                withheld.append(f)
    passed = copy.deepcopy(y)
    for f in withheld:
        _drop_path(passed, f)
    return json.dumps(sg_envelope(tool, withheld, passed, [tool], n_fail))


class ProvUser(LLMUserSimulationEnv):
    """tau-bench's LLM user, routed through providers.py (fixed model, temperature 0, all arms)."""

    def __init__(self):
        self.messages, self.model, self.provider, self.total_cost = [], USER_MODEL, USER_PROVIDER, 0.0
        self.tok = Counter()

    def generate_next_message(self, messages):
        text = ""
        for _ in range(3):            # gpt-oss sometimes returns an empty reply; ask again, same prompt
            out = providers.chat(USER_PROVIDER, USER_MODEL, messages, temperature=0.0, max_tokens=1000)
            u = out.get("usage") or {}
            self.tok["in"] += u.get("prompt_tokens", 0) or 0
            self.tok["out"] += u.get("completion_tokens", 0) or 0
            text = strip_think(out["text"])
            if text:
                break
            self.tok["empty"] += 1
        if not text:
            raise RuntimeError("user simulator returned empty text 3 times")
        self.messages.append({"role": "assistant", "content": text})
        return text


def run_episode(provider, model, arm, fault, task_index, S, reg, max_steps=30, split="test"):
    env = MockRetailDomainEnv(user_strategy="human", task_split=split, task_index=task_index)
    env.user = ProvUser()
    obs = env.reset(task_index).observation
    role = env.wiki if arm == "vanilla" else env.wiki + "\n\n" + FAULT_HANDLING
    if arm == "checklist":
        role += "\n" + CHECKLIST
    tdoc = "TOOLS (JSON schemas)\n" + json.dumps([t["function"] for t in env.tools_info], indent=1)
    msgs = [{"role": "system", "content": f"{role}\n\n{tdoc}\n{FORMAT}"}, {"role": "user", "content": obs}]
    usage, log, events, fails = Counter(), [], [], Counter()

    def chat(m):
        out = providers.chat(provider, model, m, temperature=S["temperature"], max_tokens=S["max_tokens"])
        u = out.get("usage") or {}
        usage["in"] += u.get("prompt_tokens", 0) or 0
        usage["out"] += u.get("completion_tokens", 0) or 0
        usage["calls"] += 1
        return strip_think(out["text"])

    reward, done, steps = 0.0, False, 0
    try:
        for steps in range(1, max_steps + 1):
            text = chat(msgs)
            for _ in range(2):
                if text.strip():
                    break
                usage["nudges"] += 1
                text = chat(msgs + [{"role": "user", "content": NUDGE}])
            call = parse_call(text)
            if arm == "reviewer" and call:
                for _ in range(5):
                    convo = "\n".join(f"[{m['role']}] {m['content']}" for m in msgs[1:])[-12000:]
                    rv = chat([{"role": "system", "content": REVIEWER_SYS},
                               {"role": "user", "content": f"POLICY AND TOOLS:\n{role}\n\n{tdoc}\n\nCONVERSATION SO FAR:\n{convo}\n\nPROVISIONAL RESPONSE:\n{text}"}])
                    objs = extract_json_objects(rv)
                    v = objs[0] if objs else {"error": False}
                    log.append({"review": bool(v.get("error")), "message": str(v.get("message", ""))[:300]})
                    if not v.get("error"):
                        break
                    msgs += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"[REVIEWER FEEDBACK] Your provisional response was not executed. {v.get('message', '')}\nRevise your response."}]
                    text = chat(msgs)
                    call = parse_call(text)
                    if not call:
                        break
            msgs.append({"role": "assistant", "content": text})
            if call:
                name, kw = call
                r = env.step(Action(name=name, kwargs=kw))
                seen = inject(fault, name, r.observation)
                view = seen
                if arm in ("om", "sg"):
                    try:
                        y = json.loads(seen)
                    except (json.JSONDecodeError, TypeError):
                        y = None
                    V = reg.check(name, kw, y)
                    if V:
                        fails[name] += 1
                        events.append({"tool": name, "codes": [v[0] for v in V], "n": fails[name]})
                        view = om_text(name, seen, V) if arm == "om" else sg_text(name, y, V, fails[name])
                msgs.append({"role": "user", "content": f"Tool `{name}` returned:\n{view}"})
            else:
                r = env.step(Action(name=RESPOND_ACTION_NAME, kwargs={"content": text}))
                msgs.append({"role": "user", "content": r.observation})
            if r.done:
                reward, done = r.reward, True
                break
        perr = None
    except Exception as e:
        perr = f"{type(e).__name__}: {str(e)[:300]}"
    if not done and perr is None:
        reward = env.calculate_reward().reward      # step cap reached: score the state as it stands
    return {"reward": reward, "done": done, "steps": steps, "usage": dict(usage), "user_tok": dict(env.user.tok),
            "events": events, "reviews": log, "provider_error": perr, "messages": msgs,
            "actions": [a.model_dump() for a in env.actions]}


PILOT_TOUCHED = {5, 17}     # test tasks seen in the first plumbing pilot (old SG text): never in the test sample


def task_sample(seed=20260718, n=50):
    return sorted(random.Random(seed).sample(sorted(set(range(115)) - PILOT_TOUCHED), n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--faults", default=",".join(FAULTS))
    ap.add_argument("--tasks", default="", help="comma list of task indices (default: fixed 50-task sample)")
    ap.add_argument("--split", default="test", choices=["test", "dev"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(AOB / "runs" / "ext_tau"))
    args = ap.parse_args()

    ros = {k: (p, m) for k, p, m, *_ in roster.roster(include_cn=True, include_reasoning=True)}
    provider, model = ros[args.model]
    S = roster.SETTINGS
    reg = TauRegistry(mine_db_contracts())
    tasks = [int(t) for t in args.tasks.split(",")] if args.tasks else task_sample()
    jobs = [(a, f, t) for t in tasks for f in args.faults.split(",") for a in args.arms.split(",")]
    random.Random(11).shuffle(jobs)
    if args.limit:
        jobs = jobs[:args.limit]
    out = Path(args.out) / args.split / args.model
    (out / "traces").mkdir(parents=True, exist_ok=True)
    res_file = out / "results.jsonl"
    done = set()
    if res_file.exists():
        for line in res_file.read_text().splitlines():
            r = json.loads(line)
            if not r.get("provider_error"):
                done.add((r["arm"], r["fault"], r["task"]))
    jobs = [j for j in jobs if j not in done]
    print(f"{args.model}: {len(jobs)} jobs ({len(done)} done)", flush=True)
    lock = threading.Lock()

    def one(job):
        arm, fault, t = job
        t0 = time.time()
        ep = run_episode(provider, model, arm, fault, t, S, reg, split=args.split)
        row = {"model": args.model, "split": args.split, "arm": arm, "fault": fault, "task": t, "reward": ep["reward"],
               "done": ep["done"], "steps": ep["steps"], "usage": ep["usage"], "user_tok": ep["user_tok"],
               "monitor_events": ep["events"], "review_rejects": sum(1 for x in ep["reviews"] if x["review"]),
               "provider_error": ep["provider_error"], "secs": round(time.time() - t0, 1),
               "user_model": USER_MODEL}
        with open(out / "traces" / f"{arm}__{fault}__{t}.json", "w") as f:
            json.dump({"row": row, "messages": ep["messages"], "actions": ep["actions"], "reviews": ep["reviews"]}, f)
        with lock, open(res_file, "a") as f:
            f.write(json.dumps(row) + "\n")
        return row

    n = 0
    with ThreadPoolExecutor(args.workers) as ex:
        for fu in as_completed([ex.submit(one, j) for j in jobs]):
            n += 1
            try:
                r = fu.result()
                print(f"[{n}/{len(jobs)}] {r['arm']:<9} {r['fault']:<7} t{r['task']:<3} reward={r['reward']} "
                      f"steps={r['steps']} err={bool(r['provider_error'])} {r['secs']}s", flush=True)
            except Exception:
                traceback.print_exc()
    print("DONE", args.model, flush=True)


if __name__ == "__main__":
    main()
