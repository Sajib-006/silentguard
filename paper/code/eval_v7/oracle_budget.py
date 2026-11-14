#!/usr/bin/env python3
"""Harness alone, no model (free): how many check positions are needed, and does a check-aware API
get through?  One verify_all call gives the identity result at EVERY position, so the detection rate
of any check budget b (check b positions chosen at random) is computed exactly over all b-subsets,
with no sampling. 'fixed-last' is the original single check (last condition only), which the
consistent_lie fault was written to pass.

Policy for the outcome cells is the SilentGuard answer rule with no model in the loop: a detected
count is withheld (unknown), an undetected one is released (correct if it equals the truth).

    python oracle_budget.py --tasks <adv_d7_v5.jsonl> --worker 11 --out <dir>
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import d7_harness_v3 as v3  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--tasks", required=True)
ap.add_argument("--worker", type=int, default=11)
ap.add_argument("--faults", default="off,drop_last,cap_total,or_semantics,drop_field,ignore_negation,off_by_one,prefix,consistent_lie")
ap.add_argument("--out", required=True)
ap.add_argument("--key", default="bench-catalog-key")
a = ap.parse_args()
base = f"http://127.0.0.1:{8000 + a.worker * 10 + 3}"
S = base + "/search"
H = {"X-Api-Key": a.key, "Content-Type": "application/json"}
tasks = [json.loads(l) for l in open(a.tasks)]
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)

rows = []
try:
    for fault in a.faults.split(","):
        r = requests.post(base + "/set_sfr_mode", json={"mode": fault, "catalog": "large", "projection": "count_only"}, timeout=30)
        r.raise_for_status()
        requests.post(base + "/reset", timeout=30).raise_for_status()
        for t in tasks:
            F = [{"mode": "path", "path": c["field"], "value": c["value"]} for c in t["conds"]]
            truth = t["counts"]["correct"]
            n = requests.post(S, headers=H, data=json.dumps({"json_metadata_filters": F, "limit": 50}), timeout=30).json()["total_count"]
            v = v3.verify_all(S, H, F, n, timeout=30)
            k = len(F)
            fails = [i in v.get("failed_at", []) for i in range(k)] if v["ran"] else None
            rows.append({"fault": fault, "task": t["task_id"], "k": k, "truth": truth, "api": n, "ran": v["ran"],
                         "fails": fails})
finally:
    requests.post(base + "/set_sfr_mode", json={"mode": "off", "catalog": "small", "projection": "full"}, timeout=30)

(out / "oracle_budget_rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def strategy_stats(rs, strat):
    """mean detection prob, mean extra calls, and expected outcome shares under the answer rule"""
    det, calls, ok, unk, wrong = 0.0, 0.0, 0.0, 0.0, 0.0
    for r in rs:
        k, fails = r["k"], r["fails"]
        if fails is None:                       # check could not run: count released as is
            p, c = 0.0, 0.0
        elif strat == "fixed-last":
            p, c = float(fails[-1]), 2.0
        elif strat == "all":
            p, c = float(any(fails)), 2.0 * k
        elif strat == "sequential":             # random order, stop at the first failure
            p = float(any(fails))
            perms = list(itertools.permutations(range(k)))
            tot = 0
            for order in perms:
                used = 0
                for i in order:
                    used += 1
                    if fails[i]:
                        break
                tot += 2 * used
            c = tot / len(perms)
        else:                                   # random-b: b positions chosen at random
            b = min(int(strat.split("-")[1]), k)
            subs = list(itertools.combinations(range(k), b))
            p = sum(any(fails[i] for i in s) for s in subs) / len(subs)
            c = 2.0 * b
        det += p
        calls += c
        right = r["api"] == r["truth"]
        unk += p
        ok += (1 - p) * right
        wrong += (1 - p) * (not right)
    n = len(rs)
    return {"detect": 100 * det / n, "extra_calls": calls / n, "correct": 100 * ok / n, "unknown": 100 * unk / n,
            "wrong": 100 * wrong / n, "n": n}


STRATS = ["none", "fixed-last", "random-1", "random-2", "sequential", "all"]
summary = {}
for fault in a.faults.split(","):
    rs = [r for r in rows if r["fault"] == fault]
    summary[fault] = {}
    for s in STRATS:
        if s == "none":
            right = sum(r["api"] == r["truth"] for r in rs)
            summary[fault][s] = {"detect": 0.0, "extra_calls": 0.0, "correct": 100 * right / len(rs), "unknown": 0.0,
                                 "wrong": 100 * (len(rs) - right) / len(rs), "n": len(rs)}
        else:
            summary[fault][s] = strategy_stats(rs, s)
(out / "oracle_budget_summary.json").write_text(json.dumps(summary, indent=1))
for fault, d in summary.items():
    print(fault)
    for s, v in d.items():
        print(f"   {s:<12} detect {v['detect']:5.1f}  calls {v['extra_calls']:4.1f}  correct {v['correct']:5.1f}  "
              f"unknown {v['unknown']:5.1f}  wrong {v['wrong']:5.1f}")
print("ORACLE_BUDGET_DONE")
