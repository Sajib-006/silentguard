#!/usr/bin/env python3
"""Run the experiment matrix across parallel workers with identical settings everywhere.

Builds the full (model x condition x domain x seed) job list from roster.py, assigns jobs to
worker stacks, and runs them with a fixed concurrency. Every job invokes runner.py with flags
rendered from roster.SETTINGS, so no experiment can quietly use different settings from another.
Resumable: runner.py skips (task, seed) rows already present, so re-invoking picks up where it
stopped.

    python run_matrix.py --stage leaderboard --workers 1,2,3 --retail-workers 5,6 --dry-run
    python run_matrix.py --stage leaderboard --workers 1,2,3 --retail-workers 5,6
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import roster

HERE = Path(__file__).resolve().parent
CODE = HERE.parent
ROOT = CODE.parent
PY = str(ROOT / "env" / "bin" / "python")
RUNNER = str(HERE / "runner.py")
TASKS = {"adv": str(CODE / "benchmark" / "tasks" / "adv_d7_v1.jsonl"),
         "adv2": str(CODE / "benchmark" / "tasks" / "adv_d7_v2.jsonl"),
         "adv3": str(CODE / "benchmark" / "tasks" / "adv_d7_v3.jsonl"),
         "retailadv": str(CODE / "benchmark" / "tasks" / "adv_retail_v1.jsonl"),
         "adv4": str(CODE / "benchmark" / "tasks" / "adv_d7_v4.jsonl"),
         "adv5": str(CODE / "benchmark" / "tasks" / "adv_d7_v5.jsonl"),
         "rec": str(CODE / "benchmark" / "tasks" / "rec_v1.jsonl"),
         "bio": str(CODE / "benchmark" / "tasks" / "hd100_v7.jsonl"),
         "retail": str(CODE / "benchmark" / "tasks" / "retail_v1.jsonl")}

STAGES = {
    # the main leaderboard tables: correct docs vs wrong docs, both domains
    "leaderboard": {"conditions": ["A", "G"], "domains": ["bio", "retail"], "seeds": None},
    # parametric baseline — must be ~0 now that the answer leak is fixed
    "parametric":  {"conditions": ["F"], "domains": ["bio", "retail"], "seeds": 1},
    # same as G but escape recorded instead of blocked, to quantify documentation-following
    "escape":      {"conditions": ["G"], "domains": ["bio"], "seeds": 1, "g_enforce": "record"},
    # SilentGuard: 5 cells over the ADV conjunction suite (reeval/RESULTS_SILENTGUARD.md).
    # records vs count-only response, honest vs drop_last adversary, guard off vs harness guard.
    "silentguard": {"conditions": ["A"], "domains": ["bio"], "seeds": 3,
                    "task_file": "adv",
                    "cells": [
                        ("c1-records-adv",      ["--projection", "full",       "--sfr-mode", "drop_last"]),
                        ("c2-countonly-adv",    ["--projection", "count_only", "--sfr-mode", "drop_last"]),
                        ("c3-countonly-adv-g",  ["--projection", "count_only", "--sfr-mode", "drop_last", "--d7-harness"]),
                        ("c4-countonly-hon-g",  ["--projection", "count_only", "--sfr-mode", "off",       "--d7-harness"]),
                        ("c5-records-hon",      ["--projection", "full",       "--sfr-mode", "off"]),
                    ]},
}
STAGES["remediation"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 3, "task_file": "adv",
    # count-only + harness detector; each remediation under the adversary and on an honest API.
    # The advisory arm is silentguard c3/c4 and is not repeated.
    "cells": [
        ("r-error-adv",    ["--projection", "count_only", "--sfr-mode", "drop_last", "--d7-harness", "--d7-remediation", "error"]),
        ("r-error-hon",    ["--projection", "count_only", "--sfr-mode", "off",       "--d7-harness", "--d7-remediation", "error"]),
        ("r-withhold-adv", ["--projection", "count_only", "--sfr-mode", "drop_last", "--d7-harness", "--d7-remediation", "withhold"]),
        ("r-withhold-hon", ["--projection", "count_only", "--sfr-mode", "off",       "--d7-harness", "--d7-remediation", "withhold"]),
        ("r-gate-adv",     ["--projection", "count_only", "--sfr-mode", "drop_last", "--d7-harness", "--d7-remediation", "gate"]),
        ("r-gate-hon",     ["--projection", "count_only", "--sfr-mode", "off",       "--d7-harness", "--d7-remediation", "gate"]),
        ("r-errnonum-adv", ["--projection", "count_only", "--sfr-mode", "drop_last", "--d7-harness", "--d7-remediation", "error_nonum"]),
        ("r-errnonum-hon", ["--projection", "count_only", "--sfr-mode", "off",       "--d7-harness", "--d7-remediation", "error_nonum"]),
    ]}
# E4: four silent-fault families x six harness responses, plus the honest API under each
# response. Same exact detector everywhere; only the fault and the remediation change.
FAULTS = ["drop_last", "cap_total", "or_semantics", "prefix"]
ARMS = {"none": [], "advisory": ["--d7-harness"],
        "error": ["--d7-harness", "--d7-remediation", "error"],
        "errnonum": ["--d7-harness", "--d7-remediation", "error_nonum"],
        "withhold": ["--d7-harness", "--d7-remediation", "withhold"],
        "gate": ["--d7-harness", "--d7-remediation", "gate"]}
STAGES["faults"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 3, "task_file": "adv2",
    "cells": [(f"f-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                  "--sfr-mode", fam] + a) for fam in FAULTS for arm, a in ARMS.items()]
            + [(f"f-honest-{arm}", ["--catalog", "large", "--projection", "count_only",
                                    "--sfr-mode", "off"] + a) for arm, a in ARMS.items()]}
# E4b: literature baselines + strict gate, same suite/catalog/projection as `faults`
B_ARMS = {"skeptic": ["--baseline", "skeptic"], "requery": ["--baseline", "requery"],
          "judge": ["--baseline", "judge"],
          "enforce": ["--d7-harness", "--d7-remediation", "enforce"]}
STAGES["baselines"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 3, "task_file": "adv2",
    "cells": [(f"b-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only", "--sfr-mode",
                                  "off" if fam == "honest" else fam] + a)
              for fam in ["drop_last", "cap_total", "or_semantics", "honest"] for arm, a in B_ARMS.items()]}
# gate2 validation: the ONE harness edit being tested, on the design suite (adv2) and on a
# fresh held-out suite (adv3) that no harness-design decision has ever looked at.
STAGES["gate2v"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 3, "task_file": "adv2",
    "cells": [(f"g2-{fam}-gate2", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam,
                                   "--d7-harness", "--d7-remediation", "gate2"])
              for fam in ["drop_last", "cap_total", "or_semantics", "honest"]]}
STAGES["gate2h"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 3, "task_file": "adv3",
    "cells": [(f"h-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                  "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in ["drop_last", "cap_total", "or_semantics", "honest"]
              for arm, a in (("none", []),
                             ("gate", ["--d7-harness", "--d7-remediation", "gate"]),
                             ("gate2", ["--d7-harness", "--d7-remediation", "gate2"]))]}
# Held-out validation of the full arm ladder on adv3 (none/gate/gate2 come from stage gate2h).
HO_ARMS = {
    "advisory": ["--d7-harness"],
    "error":    ["--d7-harness", "--d7-remediation", "error"],
    "errnonum": ["--d7-harness", "--d7-remediation", "error_nonum"],
    "withhold": ["--d7-harness", "--d7-remediation", "withhold"],
    "enforce":  ["--d7-harness", "--d7-remediation", "enforce"],
    "skeptic":  ["--baseline", "skeptic"],
    "requery":  ["--baseline", "requery"],
    "judge":    ["--baseline", "judge"],
}
STAGES["heldout"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 3, "task_file": "adv3",
    "cells": [(f"h-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                  "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in ["drop_last", "cap_total", "or_semantics", "honest"]
              for arm, a in HO_ARMS.items()]}
# Cross-domain: the ladder's key rungs on the retail orders API, same faults, same settings.
RET_ARMS = {"none": [], "advisory": ["--d7-harness"],
            "withhold": ["--d7-harness", "--d7-remediation", "withhold"],
            "gate2":    ["--d7-harness", "--d7-remediation", "gate2"],
            "enforce":  ["--d7-harness", "--d7-remediation", "enforce"],
            "repair":   ["--d7-harness", "--d7-remediation", "repair"]}
STAGES["retailfaults"] = {
    "conditions": ["A"], "domains": ["retail"], "seeds": 3, "task_file": "retailadv",
    "cells": [(f"r-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                  "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in ["drop_last", "cap_total", "or_semantics", "honest"]
              for arm, a in RET_ARMS.items()]}
# E7: repair-then-verify, on a FOURTH suite (adv4) built after the method was frozen, over the
# known faults plus three fault families the method never saw (ignore_negation, off_by_one,
# and the adaptive consistent_lie).
REP_FAULTS = ["drop_last", "cap_total", "or_semantics", "prefix",
              "ignore_negation", "off_by_one", "consistent_lie", "honest"]
REP_ARMS = {"none": [], "enforce": ["--d7-harness", "--d7-remediation", "enforce"],
            "repair": ["--d7-harness", "--d7-remediation", "repair"]}
STAGES["repair"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 3, "task_file": "adv4",
    "cells": [(f"rp-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in REP_FAULTS for arm, a in REP_ARMS.items()]}
# EDIT 4 final stage on the fifth suite: every arm interleaved, fresh `none`, pinned endpoints.
F5_FAULTS = ["honest", "drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]
F5_ARMS = {"none": [], "skeptic": ["--baseline", "skeptic"], "judge_check": ["--baseline", "judge_check"],
           "selfverify": ["--d7", "5"], "advisory": ["--d7-harness"],
           "advisory_hint": ["--d7-harness", "--d7-remediation", "advisory_hint"],
           "advisory_nonum": ["--d7-harness", "--d7-remediation", "advisory_nonum"],
           "gate2": ["--d7-harness", "--d7-remediation", "gate2"],
           "enforce2": ["--d7-harness", "--d7-remediation", "enforce2"],
           "repair": ["--d7-harness", "--d7-remediation", "repair"],
           "repair3": ["--d7-harness", "--d7-remediation", "repair3"]}
STAGES["final5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in F5_FAULTS for arm, a in F5_ARMS.items()]}
# Experiment C (2026-09-28): the locked final method SG-P on the final5 fault set, free models only.
STAGES["sgp5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-sgp", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam,
                                   "--d7-harness", "--d7-remediation", "sgp"])
              for fam in F5_FAULTS]}
# EDIT 4 second domain: retail orders API, same new arms, ignored field = Status.
R5_FAULTS = ["honest", "drop_last", "cap_total", "or_semantics", "drop_field"]
R5_ARMS = {"none": [], "advisory": ["--d7-harness"],
           "advisory_hint": ["--d7-harness", "--d7-remediation", "advisory_hint"],
           "enforce2": ["--d7-harness", "--d7-remediation", "enforce2"],
           "repair3": ["--d7-harness", "--d7-remediation", "repair3"]}
STAGES["retail5"] = {
    "conditions": ["A"], "domains": ["retail"], "seeds": 2, "task_file": "retailadv",
    "cells": [(f"r5-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in R5_FAULTS for arm, a in R5_ARMS.items()]}
# 2026-09-28: the locked SG-P on the retail domain (second domain of AgentOrchBench), free models.
STAGES["sgp_r5"] = {
    "conditions": ["A"], "domains": ["retail"], "seeds": 2, "task_file": "retailadv",
    "cells": [(f"r5-{fam}-sgp", ["--catalog", "large", "--projection", "count_only",
                                 "--sfr-mode", "off" if fam == "honest" else fam,
                                 "--d7-harness", "--d7-remediation", "sgp"])
              for fam in R5_FAULTS]}
# Experiment D (2026-09-28): published methods inside AgentOrchBench, final5 faults, free models.
PUBD_ARMS = {"checklist": ["--baseline", "checklist"], "reviewer": ["--baseline", "reviewer"],
             "om": ["--baseline", "om"]}
STAGES["pubd5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in F5_FAULTS for arm, a in PUBD_ARMS.items()]}
# 2026-09-29: retry controls on AgentOrchBench (final5 faults, free models): blind retry (retry2, second
# response released) and re-query with both counts shown (requery).
STAGES["retry5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam, "--baseline", arm])
              for fam in F5_FAULTS for arm in ("retry2", "requery")]}
# E9: record-level tasks (list the IDs), records returned; count-blind and blind faults included.
REC_FAULTS = ["honest", "drop_last", "drop_field", "or_semantics", "prefix", "consistent_lie", "cap_total",
              "blind_analyzed_text_match", "blind_tag_join_fanout", "blind_partial_shard_timeout",
              "blind_lossy_bitmap_no_recheck", "blind_keyset_cursor_ties"]
REC_ARMS_M = {"none": [], "rec_enforce": ["--d7-harness", "--d7-remediation", "rec_enforce"],
              "rec_repair": ["--d7-harness", "--d7-remediation", "rec_repair"]}
STAGES["rec9"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 1, "task_file": "rec",
    "cells": [(f"e9-{fam}-{arm}", ["--catalog", "large", "--projection", "full",
                                   "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in REC_FAULTS for arm, a in REC_ARMS_M.items()]}
# 2026-10-01: the locked SG-P on list answers (review item), same tasks and faults as rec9
STAGES["rec9_sgp"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 1, "task_file": "rec",
    "cells": [(f"e9-{fam}-rec_sgp", ["--catalog", "large", "--projection", "full",
                                     "--sfr-mode", "off" if fam == "honest" else fam,
                                     "--d7-harness", "--d7-remediation", "rec_sgp"])
              for fam in REC_FAULTS]}
# 2026-10-01 (review): the LLM judge and Outcome Monitors with the same blocking power as SilentGuard
STAGES["block5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam, "--baseline", arm])
              for fam in F5_FAULTS for arm in ("judge_block", "om_block")]}
# 2026-10-01 (review follow-up): the judge with blocking but without the harness's consistency evidence
STAGES["block5p"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-judge_block_plain", ["--catalog", "large", "--projection", "count_only",
                                               "--sfr-mode", "off" if fam == "honest" else fam,
                                               "--baseline", "judge_block_plain"])
              for fam in F5_FAULTS]}
# 2026-10-02 (review round 2): the crossed 2x2 with one judge, plus warning with permission to abstain
STAGES["cross5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in F5_FAULTS for arm, a in (("judge_advise_plain", ["--baseline", "judge_advise_plain"]),
                                                  ("advisory_abstain", ["--d7-harness", "--baseline", "skeptic"]))]}
# 2026-10-04 (review round 3): warning from the full (every-position) check plus permission to abstain
STAGES["permit5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-advisory_nonum_abstain", ["--catalog", "large", "--projection", "count_only",
                                                    "--sfr-mode", "off" if fam == "honest" else fam,
                                                    "--d7-harness", "--d7-remediation", "advisory_nonum",
                                                    "--baseline", "skeptic"])
              for fam in F5_FAULTS]}
# every model must go through every one of these, with identical settings
FULL_SUITE = ["leaderboard", "parametric", "escape", "silentguard", "remediation"]


def jobs_for(stage: str, models: list[tuple], seeds: int | None) -> list[dict]:
    cfg = STAGES[stage]
    n_seeds = cfg["seeds"] or seeds or roster.SETTINGS["seeds"]
    out = []
    cells = cfg.get("cells") or [(None, [])]
    for (key, prov, model, origin, _sz), cond, dom, (cell, extra) in itertools.product(
            models, cfg["conditions"], cfg["domains"], cells):
        # identical seeds for every model — no per-model reductions (fair benchmark)
        out.append({"key": key, "provider": prov, "model": model, "condition": cond,
                    "domain": dom, "seeds": n_seeds, "stage": stage, "cell": cell,
                    "extra": list(extra), "task_file": cfg.get("task_file", dom),
                    "g_enforce": cfg.get("g_enforce", roster.SETTINGS["g_enforce"])})
    return out


def cmd_for(job: dict, worker: int, out_dir: str) -> list[str]:
    c = [PY, RUNNER,
         "--provider", job["provider"], "--model", job["model"],
         "--domain", job["domain"], "--condition", job["condition"],
         "--task-file", TASKS[job.get("task_file", job["domain"])],
         "--tasks", "all", "--seeds", str(job["seeds"]),
         "--worker", str(worker),
         "--out", out_dir,
         "--tag", f'{job["stage"]}-{job["key"]}' + (f'-{job["cell"]}' if job.get("cell") else "")]
    c += roster.as_args()
    c += job.get("extra", [])
    if job["condition"] == "F":
        c += ["--no-reset"]
    else:
        c += ["--g-enforce", job["g_enforce"]]
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=sorted(STAGES) + ["full"])
    ap.add_argument("--workers", default="1,2,3", help="worker stacks serving the bio domain")
    ap.add_argument("--retail-workers", default="5", help="worker stacks serving the retail domain")
    ap.add_argument("--include-cn", action="store_true", help="add the Chinese-origin cohort")
    ap.add_argument("--include-reasoning", action="store_true")
    ap.add_argument("--only", default=None, help="comma list of roster keys")
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--shuffle", type=int, default=None,
                    help="shuffle job order with this seed, so arms and models interleave in time")
    args = ap.parse_args()

    models = roster.roster(args.include_cn, args.include_reasoning)
    if args.only:
        want = {k.strip() for k in args.only.split(",")}
        models = [m for m in models if m[0] in want]
        missing = want - {m[0] for m in models}
        if missing:
            sys.exit(f"unknown roster keys: {sorted(missing)}")

    out_dir = args.out or str(ROOT / "runs" / ("matrix_" + args.stage))
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stages = FULL_SUITE if args.stage == "full" else [args.stage]
    jobs = [j for st in stages for j in jobs_for(st, models, args.seeds)]
    if args.shuffle is not None:
        import random as _rnd
        _rnd.Random(args.shuffle).shuffle(jobs)
    bio_w = [int(x) for x in args.workers.split(",") if x.strip()]
    ret_w = [int(x) for x in args.retail_workers.split(",") if x.strip()]

    # one worker stack serves one job at a time: runs on the same stack reset each other
    queues: dict[int, list[dict]] = {w: [] for w in set(bio_w) | set(ret_w)}
    rr = {"bio": itertools.cycle(bio_w), "retail": itertools.cycle(ret_w)}
    for j in jobs:
        queues[next(rr["bio" if j["domain"] == "bio" else "retail"])].append(j)

    print(f"stage={args.stage}  models={len(models)}  jobs={len(jobs)}  out={out_dir}")
    print(f"settings: {roster.SETTINGS}")
    for w, q in sorted(queues.items()):
        est = sum(j["seeds"] for j in q)
        print(f"  worker {w}: {len(q)} jobs ({est} task-sweeps)")
    if args.dry_run:
        print("\nfirst 3 commands:")
        for w, q in sorted(queues.items()):
            for j in q[:1]:
                print("  " + " ".join(cmd_for(j, w, out_dir)))
        return

    Path(out_dir, "matrix_manifest.json").write_text(json.dumps(
        {"stage": args.stage, "settings": roster.SETTINGS, "deviations": roster.DEVIATIONS,
         "models": [{"key": k, "provider": p, "model": m, "origin": o} for k, p, m, o, _ in models],
         "jobs": jobs, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1))

    lock = threading.Lock()
    done = {"n": 0}
    total = len(jobs)

    def worker_thread(w: int, q: list[dict]):
        for j in q:
            log = Path(out_dir) / (f'{j["stage"]}-{j["key"]}-{j["condition"]}-{j["domain"]}'
                                   + (f'-{j["cell"]}' if j.get('cell') else '') + '.log')
            with open(log, "a") as fh:
                subprocess.run(cmd_for(j, w, out_dir), stdout=fh, stderr=subprocess.STDOUT)
            with lock:
                done["n"] += 1
                print(f'[{done["n"]}/{total}] w{w} {j["key"]} {j["condition"]}/{j["domain"]} done',
                      flush=True)

    threads = [threading.Thread(target=worker_thread, args=(w, q), daemon=False)
               for w, q in queues.items() if q]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"\nstage {args.stage} complete in {(time.time()-t0)/60:.0f} min -> {out_dir}")


if __name__ == "__main__":
    main()
