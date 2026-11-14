#!/usr/bin/env python3
"""Risk and coverage under a mix of clean and faulted calls (review item; no new runs).
For each method on AgentOrchBench (7 models; 6 for the blocking arms) we take per-model shares of correct, wrong and
unknown answers on clean runs and on faulted runs, and mix them at fault rate p:
    coverage(p) = share of runs with an answer, risk(p) = wrong / (wrong + correct)   (selective risk)
Means over models of each share, then risk from the means.

    python risk_coverage.py > ../reeval/paper_results/risk_coverage.json
"""
import collections
import json
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B / "reeval"))
import paper_results as pr  # noqa: E402

SIX = [m for m in pr.FREE if m != "nemotron-ultra"]
ARMS = ["none", "skeptic", "selfverify", "retry2", "judge_check", "checklist", "reviewer", "om", "advisory", "advisory_hint",
        "repair3", "gate2", "enforce2", "judge_block", "om_block", "judge_block_plain", "judge_advise_plain",
        "advisory_abstain", "advisory_nonum", "advisory_nonum_abstain", "sgp"]
cells = collections.defaultdict(collections.Counter)
for (m, arm, fa, t, s), o in pr.AOB.items():
    cells[(m, arm, "clean" if fa == "honest" else "fault")][o] += 1


def shares(m, arm, grp):
    c = cells[(m, arm, grp)]
    n = sum(c.values())
    if not n:
        return None
    return {"correct": c["correct"] / n, "wrong": c["wrong_num"] / n, "unknown": 1 - (c["correct"] + c["wrong_num"]) / n}


out = {}
for arm in ARMS:
    ms = SIX if arm in ("judge_block", "om_block", "judge_block_plain", "judge_advise_plain", "advisory_abstain", "advisory_nonum_abstain") else pr.FREE
    per = {g: [shares(m, arm, g) for m in ms] for g in ("clean", "fault")}
    if any(x is None for g in per for x in per[g]):
        continue
    mean = {g: {k: sum(x[k] for x in per[g]) / len(per[g]) for k in ("correct", "wrong", "unknown")} for g in per}
    curve = {}
    for p in (0.0, 0.1, 0.25, 0.5, 1.0):
        c = (1 - p) * mean["clean"]["correct"] + p * mean["fault"]["correct"]
        w = (1 - p) * mean["clean"]["wrong"] + p * mean["fault"]["wrong"]
        curve[str(p)] = {"coverage": 100 * (c + w), "risk": 100 * w / (c + w) if c + w else None, "correct": 100 * c,
                         "wrong": 100 * w}
    out[arm] = {"models": len(ms), "clean": mean["clean"], "fault": mean["fault"], "curve": curve}
print(json.dumps(out, indent=1))
