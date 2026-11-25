"""Phase 2 analysis (P2-01 ctl5, P2-02 mix5). Run on the server: env/bin/python reeval/phase2_analysis.py
Reads raw runs, grades with the paper's strict grader, checks the sanity gates and the pre-registered predictions
(reeval/PHASE2_PLAN.md), and writes reeval/paper_results/phase2_stats.json. Nothing here is tuned to results.
"""
import collections
import glob
import json
import math
import random
import sys
from pathlib import Path

A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A / "code" / "eval_v7"))
from strict_grade import strict_outcome  # noqa: E402

SEEN = ["drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]
BLIND = ["blind_analyzed_text_match", "blind_null_negation_drop", "blind_partial_shard_timeout",
         "blind_tag_join_fanout", "blind_keyset_cursor_ties", "blind_lossy_bitmap_no_recheck"]
ALLF = ["honest"] + BLIND + SEEN           # blind names first so "blind_..." is matched before a seen prefix
MODELS = ["gpt-oss-120b", "phi-4", "gemma3-27b", "llama33-70b"]
VLLM = ["phi-4", "gemma3-27b", "llama33-70b"]
ARMS = ["none", "advisory_nonum_abstain", "advisory_num_abstain", "enforce2", "sgp"]
OLD_DIRS = ("matrix_final5", "matrix_sgp5", "matrix_permit5")     # earlier runs (paper)
NEW_DIRS = ("matrix_ctl5", "matrix_mix5", "phase2_arc")           # Phase 2 runs


def load(dirs):
    out = {}
    for d in dirs:
        for f in glob.glob(str(A / "runs" / d / "*.jsonl")):
            for line in open(f):
                r = json.loads(line)
                if "-f5-" not in r.get("tag", ""):
                    continue
                head, rest = r["tag"].split("-f5-", 1)
                model = head.split("-", 1)[1]
                fault = next((x for x in ALLF if rest.startswith(x + "-")), None)
                if fault is None or model not in MODELS:
                    continue
                arm = rest[len(fault) + 1:]
                if arm in ARMS:
                    out[(model, arm, fault, r["task_id"], r["seed"])] = strict_outcome(r)
    return out


OLD, NEW = load(OLD_DIRS), load(NEW_DIRS)
# one table per model: vLLM models use only Phase 2 runs; gpt-oss uses Phase 2 runs plus its earlier seen and clean runs
RUN = dict(NEW)
for k, v in OLD.items():
    if k[0] == "gpt-oss-120b" and k not in RUN:
        RUN[k] = v


def rate(model, arm, faults, outcome, src=None):
    src = RUN if src is None else src
    c = collections.Counter(o for (m, a, f, t, s), o in src.items() if m == model and a == arm and f in faults)
    n = sum(c.values())
    return (100 * c[outcome] / n if n else None), n


def paired(arm_a, arm_b, faults, models):
    pairs = []
    for (m, a, f, t, s), o in RUN.items():
        if a == arm_a and f in faults and m in models and (m, arm_b, f, t, s) in RUN:
            pairs.append((t, m, o == "wrong_num", RUN[(m, arm_b, f, t, s)] == "wrong_num"))
    if not pairs:
        return None
    b = sum(1 for _, _, x, y in pairs if x and not y); c = sum(1 for _, _, x, y in pairs if y and not x)
    by = collections.defaultdict(list)
    for t, _, x, y in pairs:
        by[t].append(int(x) - int(y))
    tasks = sorted(by); rng = random.Random(0); boots = []
    for _ in range(2000):
        v = [d for t in (rng.choice(tasks) for _ in tasks) for d in by[t]]
        boots.append(100 * sum(v) / len(v))
    boots.sort()
    n = b + c
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n) if n else 1.0
    wins = sum(1 for m in models if (lambda pm: pm and sum(x for x, _ in pm) < sum(y for _, y in pm))(
        [(x, y) for _, mm, x, y in pairs if mm == m]))
    return {"n_pairs": len(pairs), "diff_a_minus_b": round(100 * (sum(x for _, _, x, _ in pairs) - sum(y for _, _, _, y in pairs)) / len(pairs), 1),
            "ci95": [round(boots[49], 1), round(boots[1949], 1)], "mcnemar_p": p, "a_lower_on_models": wins}


res = {"models_done": {}, "sanity": {}, "table": {}, "predictions": {}}
done = []
for m in MODELS:
    counts = {a: {"blind": rate(m, a, BLIND, "wrong_num")[1], "seen": rate(m, a, SEEN, "wrong_num")[1],
                  "clean": rate(m, a, ["honest"], "correct")[1]} for a in ARMS}
    res["models_done"][m] = counts
    full = all(counts[a]["blind"] >= 0.95 * 432 for a in ("none", "advisory_nonum_abstain", "enforce2", "sgp")) \
        and counts["advisory_num_abstain"]["seen"] >= 0.95 * 432
    if full:
        done.append(m)
res["complete_models"] = done

# sanity gates for vLLM models: new no-guard numbers against the earlier ones for the same model
for m in VLLM:
    new_w, n1 = rate(m, "none", SEEN, "wrong_num", NEW); old_w, _ = rate(m, "none", SEEN, "wrong_num", OLD)
    new_c, n2 = rate(m, "none", ["honest"], "correct", NEW); old_c, _ = rate(m, "none", ["honest"], "correct", OLD)
    if n1:
        res["sanity"][m] = {"none_seen_wrong_new": new_w, "none_seen_wrong_old": old_w, "none_clean_new": new_c,
                            "none_clean_old": old_c, "n_seen": n1, "n_clean": n2,
                            "pass": (old_w is not None and abs(new_w - old_w) <= 10 and old_c is not None
                                     and new_c is not None and abs(new_c - old_c) <= 10)}

for m in MODELS:
    res["table"][m] = {}
    for a in ARMS:
        row = {}
        for name, fs in (("blind", BLIND), ("seen", SEEN), ("all12", BLIND + SEEN)):
            for o, lab in (("wrong_num", "wrong"), ("correct", "correct"), ("abstain", "unknown"), ("no_answer", "no_answer")):
                row[f"{name}_{lab}"] = rate(m, a, fs, o)[0]
        row["clean_correct"] = rate(m, a, ["honest"], "correct")[0]
        res["table"][m][a] = row


def mean(arm, key, models):
    v = [res["table"][m][arm][key] for m in models if res["table"][m][arm][key] is not None]
    return sum(v) / len(v) if v else None


def fmt(x):
    return None if x is None else round(x, 1)


M = done
P = res["predictions"]
P["models_used"] = M
if M:
    none_b, sgp_b = mean("none", "blind_wrong", M), mean("sgp", "blind_wrong", M)
    P["M1"] = {"none_blind_wrong": fmt(none_b), "held": none_b >= 50}
    P["M2"] = {"sgp_blind_wrong": fmt(sgp_b), "below_none_on": sum(res["table"][m]["sgp"]["blind_wrong"] < res["table"][m]["none"]["blind_wrong"] for m in M),
               "held": 15 <= sgp_b <= 35 and all(res["table"][m]["sgp"]["blind_wrong"] < res["table"][m]["none"]["blind_wrong"] for m in M)}
    p3 = paired("sgp", "advisory_nonum_abstain", BLIND, M); P["M3"] = dict(p3, held=p3["ci95"][1] < 0)
    n12, s12 = mean("none", "all12_wrong", M), mean("sgp", "all12_wrong", M)
    P["M4"] = {"none_all12_wrong": fmt(n12), "sgp_all12_wrong": fmt(s12), "held": s12 <= n12 / 3}
    nc, sc = mean("none", "blind_correct", M), mean("sgp", "blind_correct", M)
    P["M5"] = {"none_blind_correct": fmt(nc), "sgp_blind_correct": fmt(sc), "held": nc - sc >= 10}
    p6 = paired("enforce2", "sgp", BLIND, M); P["M6"] = dict(p6, held=p6["ci95"][0] > 0)
    z = mean("advisory_num_abstain", "seen_wrong", M)
    P["Z1"] = {"ctl_seen_wrong": fmt(z), "held": z >= 5}
    pz = paired("sgp", "advisory_num_abstain", SEEN, M)
    P["Z2"] = dict(pz, held=pz["ci95"][1] < 0 and pz["a_lower_on_models"] >= min(3, len(M)))
    zs = [res["table"][m]["advisory_num_abstain"]["seen_wrong"] for m in M]
    P["Z3"] = {"by_model": {m: fmt(v) for m, v in zs and zip(M, zs)}, "range": fmt(max(zs) - min(zs)), "held": max(zs) - min(zs) >= 10}
    P["kill_rule_sgp_not_below_none_models"] = sum(res["table"][m]["sgp"]["blind_wrong"] >= res["table"][m]["none"]["blind_wrong"] for m in M)
    res["by_blind_fault"] = {f: {a: fmt(sum(rate(m, a, [f], "wrong_num")[0] for m in M) / len(M)) for a in ("none", "advisory_nonum_abstain", "enforce2", "sgp")} for f in BLIND}
    res["means"] = {a: {k: fmt(mean(a, k, M)) for k in ("blind_wrong", "blind_correct", "blind_unknown", "seen_wrong", "seen_correct", "all12_wrong", "all12_correct", "clean_correct")} for a in ARMS}

(A / "reeval/paper_results/phase2_stats.json").write_text(json.dumps(res, indent=1, default=str))
print("complete models:", done)
print("SANITY:", json.dumps({m: {k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()} for m, s in res["sanity"].items()}))
for m in MODELS:
    if m in done or res["models_done"][m]["none"]["blind"]:
        print("==", m, {a: res["models_done"][m][a] for a in ARMS})
        for a in ARMS:
            r = res["table"][m][a]
            print(f"   {a:24s} blind wrong/corr/unk {fmt(r['blind_wrong'])}/{fmt(r['blind_correct'])}/{fmt(r['blind_unknown'])} | seen wrong {fmt(r['seen_wrong'])} | clean {fmt(r['clean_correct'])}")
if M:
    print("MEANS over", M)
    for a, v in res["means"].items():
        print("  ", a, v)
    print("BY BLIND FAULT (wrong %):")
    for f, v in res["by_blind_fault"].items():
        print("  ", f, v)
    for k in ("M1", "M2", "M3", "M4", "M5", "M6", "Z1", "Z2", "Z3"):
        print(k, P[k])
    print("kill rule count:", P["kill_rule_sgp_not_below_none_models"])
