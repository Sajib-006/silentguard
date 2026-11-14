#!/usr/bin/env python3
"""ToolMaze external test: SilentGuard vs five published methods, same models, same tasks.

The ToolMaze sandbox injects the faults and the ToolMaze judge scores the run, both unmodified.
Methods only change what the agent SEES: the judge always receives the raw tool result
(as in Outcome Monitors: "the injector changes only the text observed by the model").

Arms: vanilla | hint | checklist | reviewer | om | sg      (see reeval/EXTERNAL_PLAN.md)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
AOB = HERE.parents[2]                                  # .../AgentOrchBench
TM = AOB / "external" / "ToolMaze"
sys.path.insert(0, str(TM))
sys.path.insert(0, str(AOB / "code" / "eval_v7"))
sys.path.insert(0, str(HERE))

from evaluation.core.sandbox import ExecutionEngine          # noqa: E402
from evaluation.core.judge import JudgeSystem                # noqa: E402
from evaluation.agents.base_agent import BaseAgent, AgentAction, TokenUsage  # noqa: E402
import providers                                             # noqa: E402
import roster                                                # noqa: E402
from om_contracts import build_crossfit, Registry, build_subs  # noqa: E402

ARMS = ["vanilla", "hint", "checklist", "reviewer", "om", "sg"]

# ── prompts ──────────────────────────────────────────────────────────────────
# verbatim from ToolMaze evaluation/agents/openai_agent.py (pinned checkout ef0798a)
STANDARD = """You are an autonomous AI assistant that executes tool-based workflows.

TASK COMPLETION
- The user query may describe multiple sub-tasks. You must complete ALL of them — do NOT skip any sub-tasks.
- Every fact, data retrieval, or state change must come from a tool call. Never substitute a tool result with internal knowledge, and never report a step as completed unless you actually called the corresponding tool and received its result in this conversation.

FINAL ANSWER FORMAT
- Be CONCISE and DIRECT. No emojis, markdown formatting (**, ✅, etc.), or bullet points.
- State only essential facts and requested results (e.g., confirmation codes). Omit internal technical details."""

FAULT_AWARE = """You are an autonomous AI assistant that executes tool-based workflows. Tools may occasionally fail or return unexpected data.

TASK COMPLETION
- The user query may describe multiple sub-tasks. You must complete ALL of them — do NOT skip any sub-tasks.
- Every fact, data retrieval, or state change must come from a tool call. Never substitute a tool result with internal knowledge, and never report a step as completed unless you actually called the corresponding tool and received its result in this conversation.
- Sub-tasks may be accomplishable through different tool combinations.

FAULT HANDLING
- When evaluating tool outputs, consider whether the returned data is coherent and usable for the next step — fields should be present, values should be semantically reasonable, and types should match what the workflow requires.
- If you determine that a required step cannot be accomplished by any available tool or approach, stop the workflow and clearly report which step failed and why, rather than proceeding with incomplete results.

FINAL ANSWER FORMAT
- Be CONCISE and DIRECT. No emojis, markdown formatting (**, ✅, etc.), or bullet points.
- If SUCCESSFUL: State only essential facts and requested results (e.g., confirmation codes). Omit internal technical details.
- If HALTED: State clearly that the task was aborted and the reason (e.g., "Task aborted: the pricing tool returned corrupted data after retry and no other alternative approaches exist"). Do not apologize excessively."""

# Sun et al. 2024 (Tools Fail) checklist, moved from calculator outputs to tool records.
CHECKLIST = """
RESULT CHECKLIST
Before you use any tool result, check it against this list:
1. Presence and type: every field you need is present and has the right type.
2. Agreement with your request: values that repeat your request (IDs, names, dates, codes) match what you asked for.
3. Plausibility: numbers and categories are sensible (for example, no negative prices or impossible values).
4. Consistency: the result does not contradict itself and is about what you asked.
If any check fails, treat the result as incorrect: retry the tool, use another tool that can do the same step, or stop and report which step failed."""

TOOL_FORMAT = """
HOW TO CALL A TOOL
To call a tool, reply with exactly one JSON block in this format and nothing after it:
```json
{"tool": "tool_name", "arguments": {"param1": "value1"}}
```
Call one tool at a time and wait for its result. When the task is finished (or halted), reply with "FINAL ANSWER:" followed by your answer, and no JSON tool block."""

NUDGE = "Your last reply was empty. Reply with exactly one JSON tool block, or with your final answer."
NUDGE_ACT = ("Your last reply had no tool call and no final answer. Reply with exactly one JSON tool block, "
             "or start your reply with FINAL ANSWER: if the task is finished or halted.")

REVIEWER_SYS = """You are a reviewer agent. A primary assistant is carrying out a user's task with tools. Before its provisional tool call is executed, decide whether the call is correct.
Check the call against the user request, the tool documentation, and the whole conversation so far, including earlier tool results. Look for: a wrong tool, wrong or made-up arguments, arguments copied from a tool result that is wrong or inconsistent, skipped or repeated steps, and continuing when the workflow should stop.
Tool-only responses are complete. Do NOT MARK TOOL-ONLY RESPONSES AS INCOMPLETE.
Reply with JSON only: {"reasoning": "<detailed evaluation>", "message": "<brief feedback for the assistant>", "error": true or false}"""


def tools_text(tool_defs):
    lines = ["AVAILABLE TOOLS"]
    for i, td in enumerate(tool_defs, 1):
        mcp = td.get("paradigms", {}).get("mcp", {})
        spec = td.get("paradigms", {}).get("function_call", {}).get("spec", {})
        if mcp.get("prompt_signature") and mcp.get("prompt_description"):
            lines.append(f"{i}. `{mcp['prompt_signature']}`: {mcp['prompt_description']}")
        else:
            props = spec.get("parameters", {}).get("properties", {})
            sig = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in props.items())
            lines.append(f"{i}. `{spec.get('name', td.get('name'))}({sig}) -> dict`: {spec.get('description', '')}")
    return "\n".join(lines)


def extract_json_objects(text):
    """All balanced top-level {...} spans that parse as JSON dicts (fixes the nested-brace
    truncation in ToolMaze's MCP regex)."""
    out, i = [], 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return out
        depth, j, in_str, esc = 0, i, False, False
        while j < len(text):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            return out
        try:
            obj = json.loads(text[i:j + 1])
            if isinstance(obj, dict):
                out.append(obj)
                i = j + 1
                continue
        except json.JSONDecodeError:
            pass
        i += 1


def parse_call(text):
    for obj in extract_json_objects(text or ""):
        if "tool" in obj and isinstance(obj.get("arguments", {}), dict):
            return obj["tool"], obj.get("arguments", {}) or {}
    return None


def strip_think(text):
    return re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()


# ── agent ────────────────────────────────────────────────────────────────────
class ArmAgent(BaseAgent):
    def __init__(self, provider, model, arm, settings, log):
        self.provider, self.model, self.arm, self.s, self.log = provider, model, arm, settings, log
        self.messages, self.usage = [], Counter()
        self.n_calls = 0
        self.nudges = 0

    def _chat(self, messages):
        out = providers.chat(self.provider, self.model, messages,
                             temperature=self.s["temperature"], max_tokens=self.s["max_tokens"])
        u = out.get("usage") or {}
        self.usage["in"] += u.get("prompt_tokens", 0) or 0
        self.usage["out"] += u.get("completion_tokens", 0) or 0
        self.n_calls += 1
        return strip_think(out["text"])

    def initialize(self, task_description, tool_definitions):
        self.task = task_description
        self.tdoc = tools_text(tool_definitions)
        self.visible = [td.get("name") or td.get("paradigms", {}).get("function_call", {}).get("spec", {}).get("name")
                        for td in tool_definitions]
        role = STANDARD if self.arm == "vanilla" else FAULT_AWARE
        if self.arm == "checklist":
            role += "\n" + CHECKLIST
        self.messages = [{"role": "system", "content": f"{role}\n\n{self.tdoc}\n{TOOL_FORMAT}"}]

    def _review(self, provisional):
        convo = "\n".join(f"[{m['role']}] {m['content']}" for m in self.messages[1:])[-12000:]
        msgs = [{"role": "system", "content": REVIEWER_SYS},
                {"role": "user", "content": f"USER QUERY:\n{self.task}\n\n{self.tdoc}\n\nCONVERSATION SO FAR:\n{convo}\n\nPROVISIONAL RESPONSE:\n{provisional}"}]
        verdict = self._chat(msgs)
        objs = extract_json_objects(verdict)
        v = objs[0] if objs else {"error": False, "message": ""}
        self.log.append({"review": v.get("error"), "message": str(v.get("message", ""))[:400]})
        return bool(v.get("error")), str(v.get("message", ""))

    def step(self, user_message=None):
        if user_message:
            self.messages.append({"role": "user", "content": user_message})
        try:
            text = self._chat(self.messages)
            for _ in range(2):                          # same nudge as the main harness (runner.py)
                if text.strip():
                    break
                self.nudges += 1
                text = self._chat(self.messages + [{"role": "user", "content": NUDGE}])
            call = parse_call(text)
            for _ in range(2):                          # narration without an action: ask once more
                if call or not text.strip() or text.lstrip().upper().startswith("FINAL ANSWER"):
                    break
                self.nudges += 1
                self.messages.append({"role": "assistant", "content": text})
                self.messages.append({"role": "user", "content": NUDGE_ACT})
                text = self._chat(self.messages)
                call = parse_call(text)
            if self.arm == "reviewer" and call:
                for _ in range(5):                      # Reinforced Agent progressive feedback r5
                    bad, fb = self._review(text)
                    if not bad:
                        break
                    self.messages.append({"role": "assistant", "content": text})
                    self.messages.append({"role": "user", "content": f"[REVIEWER FEEDBACK] Your provisional response was not executed. {fb}\nRevise your response."})
                    text = self._chat(self.messages)
                    call = parse_call(text)
                    if not call:
                        break
        except Exception as e:                           # provider failure: recorded, not scored as a model answer
            self.log.append({"provider_error": str(e)[:300]})
            return AgentAction(type="final_answer", content=f"__PROVIDER_ERROR__ {e}")
        self.messages.append({"role": "assistant", "content": text})
        if call:
            return AgentAction(type="tool_call", tool_name=call[0], arguments=call[1],
                               thought=text[:2000])
        return AgentAction(type="final_answer", content=text)

    def receive_tool_result(self, tool_name, result, tool_call_index=0):
        self.messages.append({"role": "user", "content":
                              f"Tool `{tool_name}` returned:\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```"})

    def get_total_tokens(self):
        return self.usage["in"] + self.usage["out"]

    def get_conversation_history(self):
        return list(self.messages)

    def reset(self):
        self.messages, self.usage = [], Counter()

    def get_token_usage(self):
        return TokenUsage(input_tokens=self.usage["in"], output_tokens=self.usage["out"])


# ── monitor views ────────────────────────────────────────────────────────────
def om_view(tool, result, V, R):
    """Outcome Monitors receipt, schema v1 envelope (their Appendix N)."""
    return {"tool_result": result,
            "outcome_contract": {"status": "inconsistent",
                                 "violations": [{"code": c, "detail": d} for c, _, d in V],
                                 "admissible_recovery_tools": R}}


def _drop(d, path):
    parts = path.split(".")
    for p in parts[:-1]:
        if not isinstance(d, dict) or p not in d:
            return
        d = d[p]
    if isinstance(d, dict):
        d.pop(parts[-1], None)


def sg_message(tool, withheld, others, n_fail):
    """Shared by ToolMaze and tau-bench so the method text is identical on both."""
    alt = f" or one of: {', '.join(others)}" if others else ""
    if n_fail < 2:
        return (f"Values from `{tool}` failed verification and were withheld: {', '.join(withheld)}. "
                f"Do not guess them. The other fields passed the checks. If your task needs the withheld "
                f"values, call `{tool}` again{alt} to get verified ones; otherwise continue.")
    return (f"Values from `{tool}` failed verification again and were withheld: {', '.join(withheld)}. "
            f"They cannot be verified with `{tool}`." + (f" Use one of: {', '.join(others)}." if others else "")
            + " If your task needs them and no verified result can be obtained, stop and report that this "
              "step failed; never guess them. Otherwise continue.")


def sg_envelope(tool, withheld, passed, R, n_fail):
    others = [r for r in R if r != tool]
    return {"verification": "FAILED", "withheld_fields": withheld,
            "message": sg_message(tool, withheld, others, n_fail),
            "result_without_withheld_fields": passed, "recovery_tools": R}


def sg_view(tool, result, V, R, n_fail):
    """SilentGuard: binding at the value level. Every field that fails a check is withheld, so it can
    never be read, reused, or repeated; the agent is told which values are missing and what to do if
    it needs them (retry / switch, then stop after 2 failures). Explicit errors pass unchanged."""
    import copy
    if isinstance(result, dict) and "error" in result:
        return result                 # already a loud failure: nothing silent to guard
    withheld = []
    for c, p, _ in V:
        for f in p.split(","):
            if f not in withheld:
                withheld.append(f)
    passed = copy.deepcopy(result) if isinstance(result, dict) else {}
    for f in withheld:
        _drop(passed, f)
    return sg_envelope(tool, withheld, passed, R, n_fail)


class ArmEngine(ExecutionEngine):
    """ExecutionEngine.run with one change: the agent receives view(result); the logger (and
    so the judge) receives the raw result."""

    def __init__(self, task, agent, arm, registry, subs):
        super().__init__(task, agent, tools_dir=str(TM / "tools"))
        self.arm, self.registry, self.subs = arm, registry, subs
        self.fail_count = Counter()
        self.events = []

    def view(self, tool, args, result):
        if self.arm not in ("om", "sg"):
            return result
        V = self.registry.check(tool, args or {}, result)
        if not V or (self.arm == "sg" and isinstance(result, dict) and "error" in result):
            return result
        visible = set(self.agent.visible)
        R = [tool] + [s for s in self.subs.get(tool, []) if s in visible and s != tool]
        self.fail_count[tool] += 1
        self.events.append({"tool": tool, "codes": [c for c, _, _ in V], "n": self.fail_count[tool]})
        if self.arm == "om":
            return om_view(tool, result, V, R)
        return sg_view(tool, result, V, R, self.fail_count[tool])

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
            try:
                result, pstat = self._intercept_tool_call(action.tool_name, action.arguments or {})
            except Exception as e:                   # unknown tool / bad arguments -> tool error
                result, pstat = {"error": f"tool execution failed: {str(e)[:200]}"}, "clean"
            self.logger.log_round(round_num=rnd, agent_action=ad, tool_result=result, perturbation_status=pstat)
            self.agent.receive_tool_result(action.tool_name, self.view(action.tool_name, action.arguments or {}, result))
        return max_rounds


# ── test set ─────────────────────────────────────────────────────────────────
def test_set(seed=20260925, per_c=20, p0_per_c=10):
    """Fixed before any run: per complexity, 20 workflows run in P3 and P4 (paired), 10 of them in P0."""
    rng = random.Random(seed)
    items = []
    for c in ("c1", "c2", "c3", "c4"):
        ids = sorted({p.name.rsplit("_", 1)[0] for p in (TM / "data" / "perturbed_tasks" / c).glob("*_P3.json")})
        pick = sorted(rng.sample(ids, per_c))
        for w in pick:
            items += [(c, w, "P3"), (c, w, "P4")]
        items += [(c, w, "P0") for w in pick[:p0_per_c]]
    return items


def dev_set(n=6, seed=7):
    test_ids = {w for _, w, _ in test_set()}
    rng = random.Random(seed)
    items = []
    for c in ("c1", "c2", "c3", "c4"):
        ids = sorted({p.name.rsplit("_", 1)[0] for p in (TM / "data" / "perturbed_tasks" / c).glob("*_P3.json")} - test_ids)
        for w in rng.sample(ids, n // 2):
            items += [(c, w, "P3"), (c, w, "P4")]
    return items


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="roster key")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--split", default="test", choices=["test", "dev"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(AOB / "runs" / "ext_toolmaze"))
    args = ap.parse_args()

    ros = {k: (p, m) for k, p, m, *_ in roster.roster(include_cn=True, include_reasoning=True)}
    provider, model = ros[args.model]
    S = roster.SETTINGS
    fold_of, regs = build_crossfit(str(TM / "data"))
    regs = [Registry(r) for r in regs]
    subs = build_subs(str(TM / "tools"))
    judge = JudgeSystem(llm_client=None, template_dir=str(TM / "data" / "templates"))

    items = test_set() if args.split == "test" else dev_set()
    arms = args.arms.split(",")
    jobs = [(a, *it) for it in items for a in arms]
    random.Random(11).shuffle(jobs)                    # interleave arms so drift hits all arms alike
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
                done.add((r["arm"], r["task_id"], r["mode"]))
    jobs = [j for j in jobs if (j[0], j[2], j[3]) not in done]
    print(f"{args.model}: {len(jobs)} jobs to run ({len(done)} done)", flush=True)
    lock = threading.Lock()

    def one(job):
        arm, c, wid, mode = job
        task = json.load(open(TM / "data" / "perturbed_tasks" / c / f"{wid}_{mode}.json"))
        log = []
        agent = ArmAgent(provider, model, arm, S, log)
        eng = ArmEngine(task, agent, arm, regs[fold_of[wid]], subs)
        t0 = time.time()
        rounds = eng.run(max_rounds=25)
        trace = {"messages": eng.logger.messages}
        perr = any("provider_error" in x for x in log)
        verdict = judge.judge(task, trace)
        row = {"model": args.model, "provider": provider, "arm": arm, "task_id": wid, "mode": mode,
               "complexity": task["complexity"], "corruption_type": task.get("corruption_type"),
               "pass": bool(verdict.get("pass")), "failure_reason": verdict.get("failure_reason"),
               "rounds": rounds, "n_tool_calls": sum(1 for m in trace["messages"] if m.get("role") == "tool"),
               "monitor_events": eng.events, "reviews": sum(1 for x in log if "review" in x),
               "review_rejects": sum(1 for x in log if x.get("review")),
               "llm_calls": agent.n_calls, "nudges": agent.nudges, "tok_in": agent.usage["in"], "tok_out": agent.usage["out"],
               "provider_error": perr, "secs": round(time.time() - t0, 1),
               "temperature": S["temperature"]}
        with open(out / "traces" / f"{arm}__{wid}_{mode}.json", "w") as f:
            json.dump({"row": row, "trace": trace, "agent_messages": agent.messages, "log": log}, f)
        with lock, open(res_file, "a") as f:
            f.write(json.dumps(row) + "\n")
        return row

    n = 0
    with ThreadPoolExecutor(args.workers) as ex:
        futs = [ex.submit(one, j) for j in jobs]
        for fu in as_completed(futs):
            n += 1
            try:
                r = fu.result()
                if n % 10 == 0 or n == len(jobs):
                    print(f"[{n}/{len(jobs)}] {r['arm']:<9} {r['task_id']}_{r['mode']} pass={r['pass']} "
                          f"tok={r['tok_in']}/{r['tok_out']} {r['secs']}s", flush=True)
            except Exception:
                traceback.print_exc()
    print("DONE", args.model, flush=True)


if __name__ == "__main__":
    main()
