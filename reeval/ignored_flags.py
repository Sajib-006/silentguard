#!/usr/bin/env python3
"""What do agents write when they report a count the harness flagged? (review round 2, no new runs)
AgentOrchBench final5, 7 free models, faulted runs where the detector flagged a result and the final answer was
wrong. For each advice-only arm: share of final answers that mention the flag at all, the stated confidence, and
the share stated with confidence 0.9 or more. Clean correct runs of the same arm are the reference.

    python ignored_flags.py > ../reeval/paper_results/ignored_flags.json
"""
import collections
import glob
import json
import re
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
MENTION = re.compile(r"suspect|warning|fail|inconsisten|not reliable|unreliable|could not (be )?verif|cannot (be )?verif|"
                     r"unverif|flag|harness|check|caution|may be (wrong|incorrect)|discrepan", re.I)


def conf(r):
    try:
        return float(str(r.get("confidence")).strip())
    except (TypeError, ValueError):
        return None


def flagged(arm, r):
    if arm in ("advisory", "advisory_hint"):
        return (r.get("d7_harness_failures") or 0) > 0
    if arm == "judge_check":
        return (r.get("judge_suspect") or 0) > 0
    return False


agg = collections.defaultdict(lambda: collections.defaultdict(list))
for f in glob.glob(str(B / "runs" / "matrix_final5" / "*.jsonl")):
    for line in open(f):
        r = json.loads(line)
        m = KEY.get(r["model"], r["model"])
        if m not in FREE:
            continue
        rest = r["tag"].split("-f5-", 1)[1]
        fault = next((x for x in ["honest"] + FAULTS if rest.startswith(x + "-")), None)
        arm = rest[len(fault) + 1:] if fault else None
        if arm not in ("advisory", "advisory_hint", "judge_check"):
            continue
        o = strict_outcome(r)
        text = (r.get("final_text") or "") + " " + (r.get("answer") or "")
        if fault == "honest" and o == "correct":
            agg[arm]["clean_conf"].append(conf(r))
        elif fault != "honest" and flagged(arm, r) and o == "wrong_num":
            agg[arm]["mention"].append(bool(MENTION.search(text)))
            agg[arm]["conf"].append(conf(r))
out = {}
for arm, d in agg.items():
    cs = [c for c in d["conf"] if c is not None]
    cc = [c for c in d["clean_conf"] if c is not None]
    out[arm] = {"n_flagged_wrong": len(d["mention"]),
                "mention_pct": 100 * sum(d["mention"]) / len(d["mention"]) if d["mention"] else None,
                "conf_mean": sum(cs) / len(cs) if cs else None,
                "conf_high_pct": 100 * sum(c >= 0.9 for c in cs) / len(cs) if cs else None,
                "clean_conf_mean": sum(cc) / len(cc) if cc else None}
print(json.dumps(out, indent=1))
