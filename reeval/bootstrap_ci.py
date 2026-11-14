#!/usr/bin/env python3
"""Task-cluster bootstrap for the key contrasts (review item; no new runs). Each resample draws the benchmark's
tasks with replacement (the same draw for every model and arm), recomputes each model's rate and averages over
models; the 95% interval is the 2.5 and 97.5 percentiles of 2,000 resamples of the difference.

    python bootstrap_ci.py > ../reeval/paper_results/bootstrap_ci.json
"""
import collections
import glob
import json
import random
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B / "reeval"))
import paper_results as pr  # noqa: E402

REPS = 2000
rng = random.Random(20261001)


def boot(data, models, a, b, outcome, lower_better):
    """data[(model, arm)][task] -> list of 0/1 for the outcome. Returns mean diff (a - b) and 95% CI."""
    tasks = sorted({t for (m, arm), d in data.items() if arm in (a, b) for t in d})
    def stat(ts):
        diffs = []
        for m in models:
            da, db = data.get((m, a), {}), data.get((m, b), {})
            xa = [v for t in ts for v in da.get(t, [])]
            xb = [v for t in ts for v in db.get(t, [])]
            if not xa or not xb:
                return None
            diffs.append(100 * (sum(xa) / len(xa) - sum(xb) / len(xb)))
        return sum(diffs) / len(diffs)
    point = stat(tasks)
    sims = []
    for _ in range(REPS):
        s = stat([rng.choice(tasks) for _ in tasks])
        if s is not None:
            sims.append(s)
    sims.sort()
    lo, hi = sims[int(0.025 * len(sims))], sims[int(0.975 * len(sims)) - 1]
    better = (hi < 0) if lower_better else (lo > 0)
    return {"diff": point, "lo": lo, "hi": hi, "n_tasks": len(tasks), "excludes_zero": better or (lo > 0 or hi < 0)}


out = {}
# AgentOrchBench: faulted runs, outcome per (model, arm) and task (seeds and fault types pooled per task)
aob = collections.defaultdict(lambda: collections.defaultdict(list))
aobc = collections.defaultdict(lambda: collections.defaultdict(list))
for (m, arm, fa, t, s), o in pr.AOB.items():
    if m not in pr.FREE:
        continue
    if fa in pr.FAULTS:
        aob[(m, arm)][t].append(1 if o == "wrong_num" else 0)
        aobc[(m, arm)][t].append(1 if o == "correct" else 0)
for other in ("none", "selfverify", "judge_check", "checklist", "reviewer", "om", "retry2", "enforce2"):
    out[f"aob_wrong_sgp_vs_{other}"] = boot(aob, pr.FREE, "sgp", other, "wrong", True)
out["aob_correct_sgp_vs_none"] = boot(aobc, pr.FREE, "sgp", "none", "correct", False)
out["aob_correct_sgp_vs_repair3"] = boot(aobc, pr.FREE, "sgp", "repair3", "correct", False)
# ToolMaze studies 3+4 (silent faults P3+P4), task = workflow id
tm = collections.defaultdict(lambda: collections.defaultdict(list))
for (st, m, arm, t, mo), v in pr.TM34.items():
    if mo in ("P3", "P4"):
        tm[(m, arm)][f"{st}:{t}"].append(int(v))
for (m, arm, t, mo), v in pr.TMA.items():
    if arm == "blindr" and mo in ("P3", "P4"):
        tm[(m, arm)][t].append(int(v))
out["tm_success_ours_vs_omcl"] = boot(tm, pr.FREE, "ours", "omcl", "pass", False)
out["tm_success_ours_vs_om"] = boot(tm, pr.FREE, "ours", "om", "pass", False)
out["tm_success_ours_vs_checklist"] = boot(tm, pr.FREE, "ours", "checklist", "pass", False)
# real APIs, wrong documentation, task = api|task
api_w = collections.defaultdict(lambda: collections.defaultdict(list))
api_c = collections.defaultdict(lambda: collections.defaultdict(list))
for d in ("matrix_e3c", "matrix_e3c_sgp", "matrix_e3c_retry", "matrix_e3c_more/*"):
    for f in glob.glob(str(pr.R / d / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r.get("key") in pr.FREE and not r.get("error") and r["docs"] == "G":
                k = f"{r['api']}|{r['task']}"
                api_w[(r["key"], r["arm"])][k].append(1 if r["outcome"] == "wrong_num" else 0)
                api_c[(r["key"], r["arm"])][k].append(1 if r["outcome"] == "correct" else 0)
for other in ("none", "retry2", "schema", "guard"):
    out[f"api_wrong_sgp_vs_{other}"] = boot(api_w, pr.FREE, "sgp", other, "wrong", True)
    out[f"api_correct_sgp_vs_{other}"] = boot(api_c, pr.FREE, "sgp", other, "correct", False)
print(json.dumps(out, indent=1))
