#!/usr/bin/env python3
"""Summary of the wrong-value-format study (runs/matrix_e3i_badformat) -> reeval/paper_results/e3i_badformat_summary.json"""
import collections, glob, json
FREE = ["llama4-scout", "llama4-maverick", "llama33-70b", "gpt-oss-120b", "nemotron-ultra", "gemma3-27b", "phi-4"]
c = collections.defaultdict(collections.Counter); errs = 0
for f in glob.glob("runs/matrix_e3i_badformat/*/*.jsonl"):
    for line in open(f):
        r = json.loads(line)
        if r.get("error"):
            errs += 1; continue
        c[(r["key"], r["arm"], r["docs"])][r["outcome"]] += 1
out = {"errors": errs, "per_model": {}, "mean": {}, "n_models": {}}
for arm, docs in (("none", "G"), ("schema", "G"), ("sgp", "G"), ("none", "A")):
    per = {}
    for m in FREE:
        x = c[(m, arm, docs)]; n = sum(x.values())
        if n >= 5:                      # a model counts only if all 5 tasks finished without a provider error
            per[m] = {"n": n, "wrong": 100 * x["wrong_num"] / n, "correct": 100 * x["correct"] / n, "unknown": 100 * x["abstain"] / n,
                      "no_answer": 100 * x["no_answer"] / n}
    key = f"{arm}|{docs}"
    out["per_model"][key] = per; out["n_models"][key] = len(per); out.setdefault("models", {})[key] = sorted(per)
    out["mean"][key] = {k: sum(v[k] for v in per.values()) / len(per) for k in ("wrong", "correct", "unknown", "no_answer")} if per else None
print(json.dumps(out, indent=1))
