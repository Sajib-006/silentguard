#!/usr/bin/env python3
"""tau-bench study 4: SG-P (precision-gated) on the tau retail test tasks NOT used in the running
tau study. Same faults, simulator, settings and harness fix (first-call-only-v1).

Support of each database contract = the number of database records it held on (orders.json):
status / transaction-type domains and conservation are checked on hundreds of orders, so their
rule-of-succession false-alarm estimate 1/(n+2) is far below 1% and SG-P withholds (as `sgv`);
a contract with small support would get the advisory receipt (as `omclr`).
run_episode4 = tau_arms3.run_episode3 with two lines changed: the harness fix, and `sgp` added to
the arms that get the checklist prompt.
"""
from __future__ import annotations

import argparse
import inspect
import json
import random
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolmaze"))
import tau_arms as TA                                     # noqa: E402
import tau_arms3 as T3                                    # noqa: E402
from harness_fix import FIX_TAG, cut_after_first_call    # noqa: E402

EXACT_FA = 0.01
ARMS4 = ["checklist", "om", "omcl", "omclr", "sgv", "sgp"]


def db_support():
    orders = json.load(open(TA.TB / "tau_bench" / "envs" / "retail" / "data" / "orders.json"))
    items = sum(len(o["items"]) for o in orders.values())
    noncanc = sum(1 for o in orders.values() if o["status"] != "cancelled")
    return {"unseen_category": len(orders), "nonpositive_value": items, "affine_relation_broken": noncanc}


SUPPORT = db_support()
_orig_monitor_view = T3.monitor_view


def monitor_view4(arm, env, fault, name, kw, seen, reg, fails, events, persistent):
    if arm != "sgp":
        return _orig_monitor_view(arm, env, fault, name, kw, seen, reg, fails, events, persistent)
    y = T3._parse(seen)
    V = reg.check(name, kw, y)
    if not V:
        return seen
    exact = [SUPPORT.get(v[0], 0) for v in V]
    all_exact = all(1.0 / (n + 2) <= EXACT_FA for n in exact)
    events.append({"tool": name, "precision_gate": "withhold" if all_exact else "advise", "support": exact})
    return _orig_monitor_view("sgv" if all_exact else "omclr", env, fault, name, kw, seen, reg, fails, events, persistent)


T3.monitor_view = monitor_view4

_src = inspect.getsource(T3.run_episode3).replace("def run_episode3(", "def run_episode4(", 1)
_a = '            msgs.append({"role": "assistant", "content": text})\n'
_b = '    if arm in ("checklist", "omcl", "omclr", "sgv"):\n'
_c = '                if arm in ("om", "sg", "omcl", "omclr", "sgv"):\n'
assert _src.count(_a) == 1 and _src.count(_b) == 1 and _src.count(_c) == 1, "run_episode3 changed"
_src = _src.replace(_a, '            msgs.append({"role": "assistant", "content": cut_after_first_call(text) if call else text})\n')
_src = _src.replace(_b, '    if arm in ("checklist", "omcl", "omclr", "sgv", "sgp"):\n')
_src = _src.replace(_c, '                if arm in ("om", "sg", "omcl", "omclr", "sgv", "sgp"):\n')
T3.__dict__["cut_after_first_call"] = cut_after_first_call
exec(compile(_src, "tau_arms4:run_episode4", "exec"), T3.__dict__)
run_episode4 = T3.run_episode4


def task_sample4(seed=20260929, n=50):
    used = set(TA.task_sample()) | TA.PILOT_TOUCHED
    return sorted(random.Random(seed).sample(sorted(set(range(115)) - used), n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--arms", default=",".join(ARMS4))
    ap.add_argument("--faults", default=",".join(TA.FAULTS))
    ap.add_argument("--tasks", default="")
    ap.add_argument("--split", default="test", choices=["test", "dev"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=str(TA.AOB / "runs" / "ext_tau4"))
    args = ap.parse_args()
    ros = {k: (p, m) for k, p, m, *_ in TA.roster.roster(include_cn=True, include_reasoning=True)}
    provider, model = ros[args.model]
    S = TA.roster.SETTINGS
    reg = TA.TauRegistry(TA.mine_db_contracts())
    tasks = [int(t) for t in args.tasks.split(",")] if args.tasks else task_sample4()
    jobs = [(a, f, t) for t in tasks for f in args.faults.split(",") for a in args.arms.split(",")]
    random.Random(16).shuffle(jobs)
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
        ep = run_episode4(provider, model, arm, fault, t, S, reg, split=args.split)
        row = {"model": args.model, "split": args.split, "arm": arm, "fault": fault, "task": t,
               "reward": ep["reward"], "done": ep["done"], "steps": ep["steps"], "usage": ep["usage"],
               "user_tok": ep["user_tok"], "monitor_events": ep["events"], "provider_error": ep["provider_error"],
               "secs": round(time.time() - t0, 1), "user_model": TA.USER_MODEL, "harness": FIX_TAG}
        tf = out / "traces" / f"{arm}__{fault}__{t}.json"
        if not (ep["provider_error"] and tf.exists()):
            with open(tf, "w") as f:
                json.dump({"row": row, "messages": ep["messages"], "actions": ep["actions"]}, f)
        with lock, open(res_file, "a") as f:
            f.write(json.dumps(row) + "\n")
        return row

    n = 0
    with ThreadPoolExecutor(args.workers) as ex:
        for fu in as_completed([ex.submit(one, j) for j in jobs]):
            n += 1
            try:
                r = fu.result()
                print(f"[{n}/{len(jobs)}] {r['arm']:<6} {r['fault']:<7} t{r['task']:<3} reward={r['reward']} "
                      f"err={bool(r['provider_error'])} {r['secs']}s", flush=True)
            except Exception:
                traceback.print_exc()
    print("DONE", args.model, flush=True)


if __name__ == "__main__":
    main()
