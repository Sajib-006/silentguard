#!/usr/bin/env python3
"""Robustness test for the blind-retry result (2026-09-29).

ToolMaze's one-time fault (P3) corrupts exactly the FIRST call to the victim tool, so a harness that
blindly sends every call twice always gets a clean second result. Here the same P3 fault lasts for the
first TWO calls instead of one; everything else (tasks, faults, judge: retry until a clean result) is
unchanged. Arms: omcl (checklist + Outcome Monitors), blindr (blind retry), sgp (the locked SG-P).
Workflows: the study 3 + study 4 test sets, P3 only. Imports the locked files unchanged.
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

import harness_fix  # noqa: F401
import tm_arms as T
import tm_arms2 as T2
import tm_arms3 as T3
import tm_arms4 as T4
import tm_arms5 as T5

FAULT_CALLS = 2


class Engine6(T5.Engine5):
    def _should_perturb(self, tool_name):
        if self.mode != "P3":
            return super()._should_perturb(tool_name)
        if tool_name not in self.perturbation_map:
            return False
        calls = sum(1 for r in self.context.history if r.tool_name == tool_name)
        return calls < FAULT_CALLS


def items():
    wf = sorted({(c, w) for c, w, _ in T3.test3_set() + T4.test4_set()})
    return [(c, w, "P3") for c, w in wf]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--arms", default="omcl,blindr,sgp")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=str(T.AOB / "runs" / "ext_toolmaze6"))
    args = ap.parse_args()
    ros = {k: (p, m) for k, p, m, *_ in T.roster.roster(include_cn=True, include_reasoning=True)}
    provider, model = ros[args.model]
    S = T.roster.SETTINGS
    fold_of, regs = T.build_crossfit(str(T.TM / "data"))
    regs = [T.Registry(r) for r in regs]
    subs = T.build_subs(str(T.TM / "tools"))
    judge = T.JudgeSystem(llm_client=None, template_dir=str(T.TM / "data" / "templates"))
    jobs = [(a, *it) for it in items() for a in args.arms.split(",")]
    random.Random(18).shuffle(jobs)
    out = Path(args.out) / "p3x2" / args.model
    (out / "traces").mkdir(parents=True, exist_ok=True)
    res_file = out / "results.jsonl"
    done = set()
    if res_file.exists():
        for line in res_file.read_text().splitlines():
            r = json.loads(line)
            if not r.get("provider_error"):
                done.add((r["arm"], r["task_id"], r["mode"]))
    jobs = [j for j in jobs if (j[0], j[2], j[3]) not in done]
    print(f"{args.model}: {len(jobs)} jobs ({len(done)} done)", flush=True)
    lock = threading.Lock()

    def one(job):
        arm, c, wid, mode = job
        task = json.load(open(T.TM / "data" / "perturbed_tasks" / c / f"{wid}_{mode}.json"))
        log = []
        agent = T2.Agent2(provider, model, arm, S, log)
        eng = Engine6(task, agent, arm, regs[fold_of[wid]], subs)
        t0 = time.time()
        rounds = eng.run(max_rounds=25)
        trace = {"messages": eng.logger.messages}
        perr = any("provider_error" in x for x in log)
        verdict = judge.judge(task, trace)
        row = {"model": args.model, "arm": arm, "task_id": wid, "mode": mode, "fault_calls": FAULT_CALLS,
               "pass": bool(verdict.get("pass")), "failure_reason": verdict.get("failure_reason"), "rounds": rounds,
               "n_tool_calls": sum(1 for m in trace["messages"] if m.get("role") == "tool"), "rechecks": eng.rechecks,
               "llm_calls": agent.n_calls, "tok_in": agent.usage["in"], "tok_out": agent.usage["out"],
               "provider_error": perr, "secs": round(time.time() - t0, 1), "harness": harness_fix.FIX_TAG}
        tf = out / "traces" / f"{arm}__{wid}_{mode}.json"
        if not (perr and tf.exists()):
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
                    print(f"[{n}/{len(jobs)}] {r['arm']:<7} {r['task_id']} pass={r['pass']} {r['secs']}s", flush=True)
            except Exception:
                traceback.print_exc()
    print("DONE", args.model, flush=True)


if __name__ == "__main__":
    main()
