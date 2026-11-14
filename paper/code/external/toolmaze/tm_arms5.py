#!/usr/bin/env python3
"""ToolMaze confirmatory experiments A and B for the locked final method SG-P (reeval/METHOD_FINAL.md).

A. Blind-retry control ("is it just a retry?"): arm `blindr` = checklist prompt + Outcome Monitors
   receipt + the harness calls EVERY tool twice and releases the second result, with no check
   deciding when. Compared with `sgp`, which re-calls only when a check fails and releases only a
   verified result. Workflows: study 3 + study 4 test sets, P3/P4/P0.
B. Loud faults: ToolMaze P1 (explicit error once) and P2 (explicit error every time) on the same
   workflows, arms checklist, om, omcl, sgp: does SG-P hurt when failures are already visible?
Imports the locked files unchanged; harness fix on.
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

T2.PROMPT_OF.update({"blindr": "checklist"})
T2.MONITOR_OF.update({"blindr": "om"})


class Engine5(T4.Engine4):
    def run(self, max_rounds=25):
        if self.arm != "blindr":
            return super().run(max_rounds)
        self.agent.initialize(self.task_description, self.tool_definitions)
        self.logger.log_user_message(self.task_description)
        for rnd in range(1, max_rounds + 1):
            action = self.agent.step(user_message=self.task_description if rnd == 1 else None)
            ad = {"type": action.type, "tool_name": action.tool_name, "arguments": action.arguments,
                  "content": action.content, "thought": action.thought}
            if action.type == "final_answer":
                self.logger.log_round(round_num=rnd, agent_action=ad, tool_result=None, perturbation_status="n/a")
                return rnd
            tool, args = action.tool_name, action.arguments or {}
            results = []
            for i in range(2):                               # every call is made twice, no check decides
                try:
                    res, pst = self._intercept_tool_call(tool, args)
                except Exception as e:
                    res, pst = {"error": f"tool execution failed: {str(e)[:200]}"}, "clean"
                self.logger.log_round(round_num=rnd, agent_action=ad if i == 0 else {
                    "type": "tool_call", "tool_name": tool, "arguments": args,
                    "content": "[harness blind retry]", "thought": None}, tool_result=res, perturbation_status=pst)
                results.append(res)
            self.rechecks += 1
            self.agent.receive_tool_result(tool, self.view(tool, args, results[1]))
        return max_rounds


def items_A():
    out = []
    for it in T3.test3_set() + T4.test4_set():
        out.append(it)
    return out


def items_B():
    wf = sorted({(c, w) for c, w, _ in T3.test3_set() + T4.test4_set()})
    return [(c, w, m) for c, w in wf for m in ("P1", "P2")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--exp", choices=["A", "B"], required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=str(T.AOB / "runs" / "ext_toolmaze5"))
    args = ap.parse_args()
    ros = {k: (p, m) for k, p, m, *_ in T.roster.roster(include_cn=True, include_reasoning=True)}
    provider, model = ros[args.model]
    S = T.roster.SETTINGS
    fold_of, regs = T.build_crossfit(str(T.TM / "data"))
    regs = [T.Registry(r) for r in regs]
    subs = T.build_subs(str(T.TM / "tools"))
    judge = T.JudgeSystem(llm_client=None, template_dir=str(T.TM / "data" / "templates"))
    if args.exp == "A":
        jobs = [("blindr", *it) for it in items_A()]
    else:
        jobs = [(a, *it) for it in items_B() for a in ("checklist", "om", "omcl", "sgp")]
    random.Random(17).shuffle(jobs)
    out = Path(args.out) / f"exp{args.exp}" / args.model
    (out / "traces").mkdir(parents=True, exist_ok=True)
    res_file = out / "results.jsonl"
    done = set()
    if res_file.exists():
        for line in res_file.read_text().splitlines():
            r = json.loads(line)
            if not r.get("provider_error"):
                done.add((r["arm"], r["task_id"], r["mode"]))
    jobs = [j for j in jobs if (j[0], j[2], j[3]) not in done]
    print(f"{args.model} exp{args.exp}: {len(jobs)} jobs to run ({len(done)} done)", flush=True)
    lock = threading.Lock()

    def one(job):
        arm, c, wid, mode = job
        task = json.load(open(T.TM / "data" / "perturbed_tasks" / c / f"{wid}_{mode}.json"))
        log = []
        agent = T2.Agent2(provider, model, arm, S, log)
        eng = Engine5(task, agent, arm, regs[fold_of[wid]], subs)
        t0 = time.time()
        rounds = eng.run(max_rounds=25)
        trace = {"messages": eng.logger.messages}
        perr = any("provider_error" in x for x in log)
        verdict = judge.judge(task, trace)
        row = {"model": args.model, "provider": provider, "arm": arm, "task_id": wid, "mode": mode,
               "complexity": task["complexity"], "corruption_type": task.get("corruption_type"),
               "pass": bool(verdict.get("pass")), "failure_reason": verdict.get("failure_reason"),
               "rounds": rounds, "n_tool_calls": sum(1 for m in trace["messages"] if m.get("role") == "tool"),
               "rechecks": eng.rechecks, "gate_log": eng.gate_log, "monitor_events": eng.events,
               "llm_calls": agent.n_calls, "nudges": agent.nudges, "tok_in": agent.usage["in"],
               "tok_out": agent.usage["out"], "provider_error": perr, "secs": round(time.time() - t0, 1),
               "temperature": S["temperature"], "harness": harness_fix.FIX_TAG, "exp": args.exp}
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
                    print(f"[{n}/{len(jobs)}] {r['arm']:<9} {r['task_id']}_{r['mode']} pass={r['pass']} {r['secs']}s", flush=True)
            except Exception:
                traceback.print_exc()
    print("DONE", args.model, args.exp, flush=True)


if __name__ == "__main__":
    main()
