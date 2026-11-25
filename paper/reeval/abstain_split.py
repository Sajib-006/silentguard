"""Who abstains under SilentGuard: the agent or the harness? And SilentGuard vs LLM judge + blocking.

Reads the raw AgentOrchBench runs (matrix_sgp5, matrix_block5), grades with the paper's strict
grader, and writes reeval/paper_results/abstain_split.json. Read-only on the runs.
"""
import collections
import glob
import itertools
import json
import sys
from pathlib import Path

A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A / "code" / "eval_v7"))
from strict_grade import strict_outcome  # noqa: E402

FAULTS = ["drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]


def rows(matrix, arm):
    out = collections.defaultdict(list)
    for f in glob.glob(str(A / "runs" / matrix / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            rest = r["tag"].split("-f5-", 1)[1]
            fault = next(x for x in ["honest"] + FAULTS if rest.startswith(x + "-"))
            if rest[len(fault) + 1:] != arm:
                continue
            model = r["tag"].split("-f5-")[0].split("-", 1)[1]
            out[(model, fault)].append(r)
    return out


def split(rs):
    c = collections.Counter()
    for r in rs:
        o = strict_outcome(r)
        harness = bool(r.get("enforced_abstain")) or "(harness:" in (r.get("answer") or "")
        if o == "abstain":
            o = "unknown_harness" if harness else "unknown_agent"
        c[o] += 1
        c["any_gate_rejection"] += (r.get("gate_rejections") or 0) > 0
        c["n"] += 1
    return c


def signflip(d):
    obs = abs(sum(d))
    return sum(1 for s in itertools.product([1, -1], repeat=len(d))
               if abs(sum(a * b for a, b in zip(s, d))) >= obs - 1e-12) / 2 ** len(d)


sgp = rows("matrix_sgp5", "sgp")
jb = rows("matrix_block5", "judge_block")
models = sorted({m for m, _ in sgp})
res = {"per_model": {}, "pooled_faulted": collections.Counter(), "pooled_clean": collections.Counter()}
diffs = []
for m in models:
    fc = sum((split(sgp[(m, fa)]) for fa in FAULTS), collections.Counter())
    cc = split(sgp[(m, "honest")])
    jf = sum((split(jb[(m, fa)]) for fa in FAULTS), collections.Counter())
    res["pooled_faulted"].update(fc)
    res["pooled_clean"].update(cc)
    pm = {k: 100 * fc[k] / fc["n"] for k in ("correct", "wrong_num", "unknown_harness", "unknown_agent",
                                             "no_answer", "any_gate_rejection")} if fc["n"] else {}
    pm["n_faulted"] = fc["n"]
    pm["clean_correct"] = 100 * cc["correct"] / cc["n"] if cc["n"] else None
    pm["clean_unknown_harness"] = 100 * cc["unknown_harness"] / cc["n"] if cc["n"] else None
    pm["judge_block_wrong"] = 100 * jf["wrong_num"] / jf["n"] if jf["n"] else None
    pm["judge_block_n"] = jf["n"]
    res["per_model"][m] = pm
    if pm.get("judge_block_wrong") is not None and fc["n"]:
        diffs.append(pm["wrong_num"] - pm["judge_block_wrong"])
pf = res["pooled_faulted"]
res["pooled_faulted_pct"] = {k: round(100 * pf[k] / pf["n"], 1) for k in pf if k != "n"}
res["pooled_faulted_pct"]["n"] = pf["n"]
pc = res["pooled_clean"]
res["pooled_clean_pct"] = {k: round(100 * pc[k] / pc["n"], 1) for k in pc if k != "n"}
res["pooled_clean_pct"]["n"] = pc["n"]
res["sgp_vs_judge_block"] = {"models": len(diffs), "sgp_fewer_wrong": sum(d < 0 for d in diffs),
                             "mean_diff": round(sum(diffs) / len(diffs), 2) if diffs else None,
                             "signflip_p": signflip(diffs) if diffs else None}
res["pooled_faulted"] = dict(pf)
res["pooled_clean"] = dict(pc)
out = A / "reeval" / "paper_results" / "abstain_split.json"
out.write_text(json.dumps(res, indent=1))
print(json.dumps({k: res[k] for k in ("pooled_faulted_pct", "pooled_clean_pct", "sgp_vs_judge_block")}, indent=1))
for m, pm in res["per_model"].items():
    print(m, {k: (round(v, 1) if isinstance(v, float) else v) for k, v in pm.items()})
