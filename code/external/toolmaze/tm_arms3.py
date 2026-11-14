#!/usr/bin/env python3
"""ToolMaze study 3: SG-V (verify-then-release) vs the strongest published methods.

SG-V = checklist prompt + the shared detector + three harness rules:
  1. Re-check: when a result fails a check, the harness calls the same tool once more (through the
     benchmark's own fault layer; the call is logged like any other tool call). If the new result
     passes, only that verified result is released.
  2. Persistent fault (fails again): values that fail are withheld; the agent is told to redo the
     step with a named substitute tool, or, if none exists, to stop if the task needs the values.
  3. No more re-checks on a tool once it is known to be persistently failing.
Control `omclr` = checklist + Outcome Monitors receipt + the SAME re-check (rule 1), so the effect of
rule 1 alone is visible. Imports tm_arms / tm_arms2 unchanged.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import tm_arms as T
import tm_arms2 as T2

ARMS3 = ["checklist", "om", "omcl", "omclr", "sgv"]
T2.PROMPT_OF.update({"omclr": "checklist", "sgv": "checklist"})
T2.MONITOR_OF.update({"omclr": "om", "sgv": "sg"})
RECHECK_ARMS = {"omclr", "sgv"}


def withhold(result, V):
    fields = []
    for _, p, _ in V:
        for f in p.split(","):
            if f not in fields:
                fields.append(f)
    kept = copy.deepcopy(result) if isinstance(result, dict) else {}
    for f in fields:
        T._drop(kept, f)
    return fields, kept


def sgv_message(tool, fields, others, stop_phrase):
    """Shared with tau_arms3 so the method text is the same on both benchmarks (only the
    benchmark's own stop format differs)."""
    head = (f"`{tool}` failed verification twice (the harness already called it again). "
            f"Withheld values: {', '.join(fields)}; they cannot be verified with `{tool}`.")
    if others:
        return head + (f" Redo this step with {' or '.join(f'`{o}`' for o in others)} instead, and do not use "
                       f"values from this result in later steps.")
    return head + (f" No other tool can do this step. If the task needs these values, stop: {stop_phrase}. "
                   f"Otherwise continue with the steps that do not need them, and never guess them.")


TM_STOP = "reply FINAL ANSWER: Task aborted: the tool returned data that failed verification"


def sgv_view(tool, result, V, R, stop_phrase=TM_STOP):
    fields, kept = withhold(result, V)
    others = [r for r in R if r != tool]
    return {"verification": "FAILED_TWICE", "withheld_fields": fields,
            "message": sgv_message(tool, fields, others, stop_phrase),
            "result_without_withheld_fields": kept, "recovery_tools": R}


def rechecked_view(result2):
    return {"verification": "PASSED_ON_RECHECK",
            "message": "The first result failed verification; the harness called the tool again and this "
                       "result passed the checks.",
            "result": result2}


class Engine3(T2.Engine2):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.persistent = set()
        self.rechecks = 0

    def _R(self, tool):
        visible = set(self.agent.visible)
        return [tool] + [s for s in self.subs.get(tool, []) if s in visible and s != tool]

    def run(self, max_rounds=25):
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
            try:
                result, pstat = self._intercept_tool_call(tool, args)
            except Exception as e:
                result, pstat = {"error": f"tool execution failed: {str(e)[:200]}"}, "clean"
            self.logger.log_round(round_num=rnd, agent_action=ad, tool_result=result, perturbation_status=pstat)
            if self.arm not in RECHECK_ARMS:
                view = self.view(tool, args, result)
            else:
                view = self.view3(tool, args, result, rnd)
            self.agent.receive_tool_result(tool, view)
        return max_rounds

    def view3(self, tool, args, result, rnd):
        if isinstance(result, dict) and "error" in result:
            # already a loud error: sgv passes it through; omclr keeps Outcome Monitors' own handling
            return self.view(tool, args, result) if self.arm == "omclr" else result
        V = self.registry.check(tool, args, result)
        if not V:
            return result
        R = self._R(tool)
        self.events.append({"tool": tool, "codes": [c for c, _, _ in V], "n": 1})
        if tool not in self.persistent:                 # rule 1: one re-check through the fault layer
            self.rechecks += 1
            try:
                result2, pstat2 = self._intercept_tool_call(tool, args)
            except Exception as e:
                result2, pstat2 = {"error": f"tool execution failed: {str(e)[:200]}"}, "clean"
            self.logger.log_round(round_num=rnd, agent_action={
                "type": "tool_call", "tool_name": tool, "arguments": args,
                "content": "[harness re-check]", "thought": None}, tool_result=result2, perturbation_status=pstat2)
            V2 = self.registry.check(tool, args, result2)
            if not V2 and not (isinstance(result2, dict) and "error" in result2):
                self.events.append({"tool": tool, "codes": [], "recheck": "passed"})
                return rechecked_view(result2)
            self.persistent.add(tool)
            self.events.append({"tool": tool, "codes": [c for c, _, _ in V2], "recheck": "failed"})
            result, V = result2, (V2 or V)
        if self.arm == "omclr":
            return T.om_view(tool, result, V, R)
        return sgv_view(tool, result, V, R)


def unused_ids(c):
    used = ({w for _, w, _ in T.test_set()} | {w for _, w, _ in T.dev_set()} |
            {w for _, w, _ in T2.test2_set()})
    return sorted({p.name.rsplit("_", 1)[0] for p in (T.TM / "data" / "perturbed_tasks" / c).glob("*_P3.json")} - used)


def dev3_set(seed=20260927, per_c=6):
    rng = random.Random(seed)
    items = []
    for c in ("c1", "c2", "c3", "c4"):
        for w in sorted(rng.sample(unused_ids(c), per_c)):
            items += [(c, w, "P3"), (c, w, "P4"), (c, w, "P0")]
    return items


def test3_set(seed=20260928, per_c=20, p0_per_c=10):
    dev = {w for _, w, _ in dev3_set()}
    rng = random.Random(seed)
    items = []
    for c in ("c1", "c2", "c3", "c4"):
        pick = sorted(rng.sample([w for w in unused_ids(c) if w not in dev], per_c))
        for w in pick:
            items += [(c, w, "P3"), (c, w, "P4")]
        items += [(c, w, "P0") for w in pick[:p0_per_c]]
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--split", choices=["dev3", "test3"], required=True)
    ap.add_argument("--arms", default=",".join(ARMS3))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=str(T.AOB / "runs" / "ext_toolmaze3"))
    args = ap.parse_args()
    ros = {k: (p, m) for k, p, m, *_ in T.roster.roster(include_cn=True, include_reasoning=True)}
    provider, model = ros[args.model]
    S = T.roster.SETTINGS
    fold_of, regs = T.build_crossfit(str(T.TM / "data"))
    regs = [T.Registry(r) for r in regs]
    subs = T.build_subs(str(T.TM / "tools"))
    judge = T.JudgeSystem(llm_client=None, template_dir=str(T.TM / "data" / "templates"))
    items = dev3_set() if args.split == "dev3" else test3_set()
    jobs = [(a, *it) for it in items for a in args.arms.split(",")]
    random.Random(13).shuffle(jobs)
    out = Path(args.out) / args.split / args.model
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
        agent = T2.Agent2(provider, model, arm, S, log)
        eng = Engine3(task, agent, arm, regs[fold_of[wid]], subs)
        t0 = time.time()
        rounds = eng.run(max_rounds=25)
        trace = {"messages": eng.logger.messages}
        perr = any("provider_error" in x for x in log)
        verdict = judge.judge(task, trace)
        row = {"model": args.model, "provider": provider, "arm": arm, "task_id": wid, "mode": mode,
               "complexity": task["complexity"], "corruption_type": task.get("corruption_type"),
               "pass": bool(verdict.get("pass")), "failure_reason": verdict.get("failure_reason"),
               "rounds": rounds, "n_tool_calls": sum(1 for m in trace["messages"] if m.get("role") == "tool"),
               "rechecks": eng.rechecks, "monitor_events": eng.events, "llm_calls": agent.n_calls,
               "nudges": agent.nudges, "tok_in": agent.usage["in"], "tok_out": agent.usage["out"],
               "provider_error": perr, "secs": round(time.time() - t0, 1), "temperature": S["temperature"]}
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
    print("DONE", args.model, flush=True)


if __name__ == "__main__":
    main()
