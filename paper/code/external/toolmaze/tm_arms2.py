#!/usr/bin/env python3
"""ToolMaze study 2: does adding the checklist prompt to a detector help, and does sg > om replicate?

New held-out workflows (none used in the test set or the dev pilot). The frozen tm_arms.py is
imported, not edited: the new arms reuse its exact prompt, checklist, OM receipt and SG view.
  hint, checklist, om, sg      - as in study 1
  omcl                         - checklist prompt + Outcome Monitors receipt
  sgcl                         - checklist prompt + SilentGuard view
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import tm_arms as T

ARMS2 = ["hint", "checklist", "om", "sg", "omcl", "sgcl"]
PROMPT_OF = {"omcl": "checklist", "sgcl": "checklist"}
MONITOR_OF = {"omcl": "om", "sgcl": "sg"}


class Agent2(T.ArmAgent):
    def initialize(self, task_description, tool_definitions):
        base = self.arm
        self.arm = PROMPT_OF.get(base, base)
        try:
            super().initialize(task_description, tool_definitions)
        finally:
            self.arm = base


class Engine2(T.ArmEngine):
    def view(self, tool, args, result):
        base = self.arm
        self.arm = MONITOR_OF.get(base, base)
        try:
            return super().view(tool, args, result)
        finally:
            self.arm = base


def test2_set(seed=20260926, per_c=20, p0_per_c=10):
    """20 new workflows per complexity in P3 and P4 (paired), 10 of them in P0; never in study 1."""
    used = {w for _, w, _ in T.test_set()} | {w for _, w, _ in T.dev_set()}
    rng = random.Random(seed)
    items = []
    for c in ("c1", "c2", "c3", "c4"):
        ids = sorted({p.name.rsplit("_", 1)[0] for p in (T.TM / "data" / "perturbed_tasks" / c).glob("*_P3.json")} - used)
        pick = sorted(rng.sample(ids, per_c))
        for w in pick:
            items += [(c, w, "P3"), (c, w, "P4")]
        items += [(c, w, "P0") for w in pick[:p0_per_c]]
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--arms", default=",".join(ARMS2))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=str(T.AOB / "runs" / "ext_toolmaze2"))
    args = ap.parse_args()
    ros = {k: (p, m) for k, p, m, *_ in T.roster.roster(include_cn=True, include_reasoning=True)}
    provider, model = ros[args.model]
    S = T.roster.SETTINGS
    fold_of, regs = T.build_crossfit(str(T.TM / "data"))
    regs = [T.Registry(r) for r in regs]
    subs = T.build_subs(str(T.TM / "tools"))
    judge = T.JudgeSystem(llm_client=None, template_dir=str(T.TM / "data" / "templates"))
    jobs = [(a, *it) for it in test2_set() for a in args.arms.split(",")]
    random.Random(12).shuffle(jobs)
    out = Path(args.out) / "test2" / args.model
    (out / "traces").mkdir(parents=True, exist_ok=True)
    res_file = out / "results.jsonl"
    done = set()
    if res_file.exists():
        for line in res_file.read_text().splitlines():
            r = json.loads(line)
            if not r.get("provider_error"):
                done.add((r["arm"], r["task_id"], r["mode"]))
    jobs = [j for j in jobs if (j[0], j[2], j[3]) not in done]
    print(f"{args.model}: {len(jobs)} jobs to run ({len(done)} done)", flush=True)
    lock = threading.Lock()

    def one(job):
        arm, c, wid, mode = job
        task = json.load(open(T.TM / "data" / "perturbed_tasks" / c / f"{wid}_{mode}.json"))
        log = []
        agent = Agent2(provider, model, arm, S, log)
        eng = Engine2(task, agent, arm, regs[fold_of[wid]], subs)
        t0 = time.time()
        rounds = eng.run(max_rounds=25)
        trace = {"messages": eng.logger.messages}
        perr = any("provider_error" in x for x in log)
        verdict = judge.judge(task, trace)
        row = {"model": args.model, "provider": provider, "arm": arm, "task_id": wid, "mode": mode,
               "complexity": task["complexity"], "corruption_type": task.get("corruption_type"),
               "pass": bool(verdict.get("pass")), "failure_reason": verdict.get("failure_reason"),
               "rounds": rounds, "n_tool_calls": sum(1 for m in trace["messages"] if m.get("role") == "tool"),
               "monitor_events": eng.events, "llm_calls": agent.n_calls, "nudges": agent.nudges,
               "tok_in": agent.usage["in"], "tok_out": agent.usage["out"], "provider_error": perr,
               "secs": round(time.time() - t0, 1), "temperature": S["temperature"]}
        tf = out / "traces" / f"{arm}__{wid}_{mode}.json"
        if not (perr and tf.exists()):          # never overwrite a trace with a failed rerun
            with open(tf, "w") as f:
                json.dump({"row": row, "trace": trace, "agent_messages": agent.messages, "log": log}, f)
        with lock, open(res_file, "a") as f:
            f.write(json.dumps(row) + "\n")
        return row

    n = 0
    with ThreadPoolExecutor(args.workers) as ex:
        for fu in as_completed([ex.submit(one, j) for j in jobs]):
            n += 1
            try:
                r = fu.result()
                if n % 10 == 0 or n == len(jobs):
                    print(f"[{n}/{len(jobs)}] {r['arm']:<9} {r['task_id']}_{r['mode']} pass={r['pass']} {r['secs']}s", flush=True)
            except Exception:
                traceback.print_exc()
    print("DONE", args.model, flush=True)


if __name__ == "__main__":
    main()
