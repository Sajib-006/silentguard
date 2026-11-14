#!/usr/bin/env python3
"""tau-bench study 3: SG-V and the checklist mixes on the SAME 50 test tasks and faults as the
running tau study (same simulator, same settings). New arms only; tau_arms.py is imported unchanged.
  omcl  - checklist prompt + Outcome Monitors advisory
  omclr - omcl + one harness re-check on a flagged read (control for SG-V rule 1)
  sgv   - checklist prompt + SG-V (re-check, release only verified, withhold failed values after a
          second failure; text shared with tm_arms3.sgv_message)
run_episode3 is tau_arms.run_episode with only the prompt line and the monitor block changed.
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import tau_arms as TA
from tau_arms import (Action, CHECKLIST, FAULT_HANDLING, FORMAT, MockRetailDomainEnv, NUDGE, ProvUser,   # noqa: F401
                      RESPOND_ACTION_NAME, REVIEWER_SYS, extract_json_objects, inject, om_text, parse_call,
                      providers, sg_text, strip_think, _drop_path)
import sys
sys.path.insert(0, str(TA.AOB / "code" / "external" / "toolmaze"))
from tm_arms3 import sgv_message                                                          # noqa: E402

ARMS3 = ["omcl", "omclr", "sgv"]
TAU_STOP = "tell the user this step cannot be completed because the order data failed verification"


def _parse(s):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def monitor_view(arm, env, fault, name, kw, seen, reg, fails, events, persistent):
    y = _parse(seen)
    V = reg.check(name, kw, y)
    if not V:
        return seen
    fails[name] += 1
    events.append({"tool": name, "codes": [v[0] for v in V], "n": fails[name]})
    if arm == "om" or arm == "omcl":
        return om_text(name, seen, V)
    if arm == "sg":
        return sg_text(name, y, V, fails[name])
    if name not in persistent:                    # rule 1: one re-check, same fault layer as the agent sees
        r2 = env.step(Action(name=name, kwargs=kw))
        seen2 = inject(fault, name, r2.observation)
        y2 = _parse(seen2)
        V2 = reg.check(name, kw, y2)
        if not V2:
            events.append({"tool": name, "codes": [], "recheck": "passed"})
            return json.dumps({"verification": "PASSED_ON_RECHECK",
                               "message": "The first result failed verification; the harness called the tool "
                                          "again and this result passed the checks.", "result": y2 if y2 is not None else seen2})
        persistent.add(name)
        events.append({"tool": name, "codes": [v[0] for v in V2], "recheck": "failed"})
        seen, y, V = seen2, y2, V2
    if arm == "omclr":
        return om_text(name, seen, V)
    fields = []
    for v in V:
        for f in v[1].split(","):
            if f not in fields:
                fields.append(f)
    import copy
    kept = copy.deepcopy(y)
    for f in fields:
        _drop_path(kept, f)
    return json.dumps({"verification": "FAILED_TWICE", "withheld_fields": fields,
                       "message": sgv_message(name, fields, [], TAU_STOP),
                       "result_without_withheld_fields": kept, "recovery_tools": [name]})


def run_episode3(provider, model, arm, fault, task_index, S, reg, max_steps=30, split="test"):
    env = MockRetailDomainEnv(user_strategy="human", task_split=split, task_index=task_index)
    env.user = ProvUser()
    obs = env.reset(task_index).observation
    role = env.wiki if arm == "vanilla" else env.wiki + "\n\n" + FAULT_HANDLING
    if arm in ("checklist", "omcl", "omclr", "sgv"):
        role += "\n" + CHECKLIST
    tdoc = "TOOLS (JSON schemas)\n" + json.dumps([t["function"] for t in env.tools_info], indent=1)
    msgs = [{"role": "system", "content": f"{role}\n\n{tdoc}\n{FORMAT}"}, {"role": "user", "content": obs}]
    usage, log, events, fails, persistent = Counter(), [], [], Counter(), set()

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
                if arm in ("om", "sg", "omcl", "omclr", "sgv"):
                    view = monitor_view(arm, env, fault, name, kw, seen, reg, fails, events, persistent)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--arms", default=",".join(ARMS3))
    ap.add_argument("--faults", default=",".join(TA.FAULTS))
    ap.add_argument("--split", default="test", choices=["test", "dev"])
    ap.add_argument("--tasks", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=str(TA.AOB / "runs" / "ext_tau"))
    args = ap.parse_args()
    ros = {k: (p, m) for k, p, m, *_ in TA.roster.roster(include_cn=True, include_reasoning=True)}
    provider, model = ros[args.model]
    S = TA.roster.SETTINGS
    reg = TA.TauRegistry(TA.mine_db_contracts())
    tasks = [int(t) for t in args.tasks.split(",")] if args.tasks else TA.task_sample()
    jobs = [(a, f, t) for t in tasks for f in args.faults.split(",") for a in args.arms.split(",")]
    random.Random(14).shuffle(jobs)
    out = Path(args.out) / args.split / args.model
    (out / "traces").mkdir(parents=True, exist_ok=True)
    res_file = out / "results.jsonl"          # same file as the running tau study: arms are new, keys never collide
    done = set()
    if res_file.exists():
        for line in res_file.read_text().splitlines():
            r = json.loads(line)
            if not r.get("provider_error"):
                done.add((r["arm"], r["fault"], r["task"]))
    jobs = [j for j in jobs if j not in done]
    print(f"{args.model}: {len(jobs)} jobs ({len(done)} done in file)", flush=True)
    lock = threading.Lock()

    def one(job):
        arm, fault, t = job
        t0 = time.time()
        ep = run_episode3(provider, model, arm, fault, t, S, reg, split=args.split)
        row = {"model": args.model, "split": args.split, "arm": arm, "fault": fault, "task": t,
               "reward": ep["reward"], "done": ep["done"], "steps": ep["steps"], "usage": ep["usage"],
               "user_tok": ep["user_tok"], "monitor_events": ep["events"], "review_rejects": 0,
               "provider_error": ep["provider_error"], "secs": round(time.time() - t0, 1),
               "user_model": TA.USER_MODEL}
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
