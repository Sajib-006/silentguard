#!/usr/bin/env python3
"""Mine Outcome Monitors contracts for AgentOrchBench from CLEAN responses (experiment D).

Replays, on a clean API copy (no fault, same catalog and projection as final5), every request that the
7 free models sent in the final5 honest/no-guard runs, then learns contracts with the same miner as the
ToolMaze port (om_contracts.learn), 5-fold by task: a task is only checked with contracts learned from
other tasks. Writes reeval/om_aob_registry.json.
"""
import glob, json, random, sys
from collections import defaultdict
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "external" / "toolmaze"))
import runner, executor
from om_contracts import learn
A = HERE.parents[1]
W = 13; PB = 8000 + W * 10
lock = runner._worker_lock(W)
samples = defaultdict(list)
rows = []
for f in glob.glob(str(A / "runs" / "matrix_final5" / "*honest-none*.jsonl")):
    for line in open(f):
        r = json.loads(line)
        if r.get("calls"):
            rows.append((r["task_id"], [c["cmd"] for c in r["calls"] if c.get("cmd")]))
print("clean runs with calls:", len(rows))
seen = set()
for task, cmds in rows:
    runner.reset_apis(PB, "bio")
    runner.set_sfr_mode("off", PB, "bio", projection="count_only", catalog="large")
    for cmd in cmds:
        rec = executor.execute(cmd, "A", port_base=PB, domain="bio")
        if rec.get("status") != 200:
            continue
        try:
            j = json.loads(rec["full_body"])
            p = executor.parse_curl(cmd)
        except Exception:
            continue
        if not isinstance(j, dict):
            continue
        key = (task, p["method"], p["path"], json.dumps(executor.om_args(p), sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        samples[task].append((f"{p['method']} {p['path']}", executor.om_args(p), j))
tasks = sorted(samples)
rng = random.Random(20260928); order = tasks[:]; rng.shuffle(order)
fold_of = {t: i % 5 for i, t in enumerate(order)}
regs = [learn([s for t in tasks if fold_of[t] != k for s in samples[t]]) for k in range(5)]
json.dump({"fold_of": fold_of, "regs": regs, "n_samples": sum(len(v) for v in samples.values()),
           "n_tasks": len(tasks)}, open(A / "reeval" / "om_aob_registry.json", "w"))
print("tasks", len(tasks), "samples", sum(len(v) for v in samples.values()), "tools per fold", [len(r) for r in regs])
