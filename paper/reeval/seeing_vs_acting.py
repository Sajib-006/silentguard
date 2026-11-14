#!/usr/bin/env python3
"""Seeing is not acting (review item, no new runs): when a detector flags a faulted result and only tells the
model (warning, judge verdict, Outcome Monitors advisory), how often does the agent still report a wrong count?
And how often does each detector flag faulted vs clean runs? AgentOrchBench final5, 7 free models.

    python seeing_vs_acting.py > ../reeval/paper_results/seeing_vs_acting.json
"""
import collections
import glob
import json
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B / "reeval"))
sys.path.insert(0, str(B / "code" / "eval_v7"))
from strict_grade import strict_outcome  # noqa: E402
import roster  # noqa: E402

KEY = {m: k for k, p, m, *_ in roster.roster(True, True)}
FREE = ["llama4-scout", "llama4-maverick", "llama33-70b", "gpt-oss-120b", "nemotron-ultra", "gemma3-27b", "phi-4"]
FAULTS = ["drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]


def flagged(arm, r):
    if arm in ("advisory", "advisory_hint"):
        return (r.get("d7_harness_failures") or 0) > 0
    if arm == "judge_check":
        return (r.get("judge_suspect") or 0) > 0
    if arm == "om":
        return (r.get("om_receipts") or 0) > 0
    return None


cells = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
for d in ("matrix_final5", "matrix_pubd5"):
    for f in glob.glob(str(B / "runs" / d / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            m = KEY.get(r["model"], r["model"])
            if m not in FREE:
                continue
            rest = r["tag"].split("-f5-", 1)[1]
            fault = next((x for x in ["honest"] + FAULTS if rest.startswith(x + "-")), None)
            if fault is None:
                continue
            arm = rest[len(fault) + 1:]
            fl = flagged(arm, r)
            if fl is None:
                continue
            o = strict_outcome(r)
            grp = "clean" if fault == "honest" else "fault"
            c = cells[arm][m]
            c[(grp, "n")] += 1
            c[(grp, "flagged")] += fl
            if fl and grp == "fault":
                c[("flag_fault", o)] += 1
out = {}
for arm, per in cells.items():
    rows = {}
    for m in FREE:
        c = per.get(m)
        if not c:
            continue
        nf, nc = c[("fault", "n")], c[("clean", "n")]
        fl = c[("fault", "flagged")]
        rows[m] = {"flag_rate_fault": 100 * fl / nf if nf else None,
                   "flag_rate_clean": 100 * c[("clean", "flagged")] / nc if nc else None,
                   "wrong_after_flag": 100 * c[("flag_fault", "wrong_num")] / fl if fl else None,
                   "n_flagged": fl}
    def mean(k):
        v = [x[k] for x in rows.values() if x[k] is not None]
        return sum(v) / len(v) if v else None
    out[arm] = {"per_model": rows, "flag_rate_fault": mean("flag_rate_fault"), "flag_rate_clean": mean("flag_rate_clean"),
                "wrong_after_flag": mean("wrong_after_flag"), "models": len(rows)}
print(json.dumps(out, indent=1))
