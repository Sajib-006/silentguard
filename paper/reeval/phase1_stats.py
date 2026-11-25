"""Phase 1 analyses from existing logs (no new runs). Writes reeval/paper_results/phase1_stats.json.

1. funnel: warning arm (full check, no numbers), per model: fault -> check fired -> agent still reports a number
2. paired: run-level paired tests, SilentGuard against other arms (exact McNemar, task-clustered bootstrap)
3. false_alarm: SilentGuard and no guard on faulted runs where the fault does not change the true count
4. natural: real-API study, right documentation, no guard: runs with a parameter the API does not document
5. retail_clean: outcome breakdown on fault-free retail tasks
"""
import collections
import glob
import json
import math
import random
import re
import sys
from pathlib import Path

A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A / "code" / "eval_v7"))
sys.path.insert(0, str(A / "code" / "e3"))
from strict_grade import strict_outcome  # noqa: E402

FAULTS = ["drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]
FREE = ["llama4-scout", "llama4-maverick", "llama33-70b", "gpt-oss-120b", "nemotron-ultra", "gemma3-27b", "phi-4"]
MATS = ("matrix_final5", "matrix_sgp5", "matrix_pubd5", "matrix_retry5", "matrix_block5", "matrix_block5p",
        "matrix_cross5", "matrix_permit5")
out = {}

# ── load AgentOrchBench runs ────────────────────────────────────────────────
RUN = {}
for d in MATS:
    for f in glob.glob(str(A / "runs" / d / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            head, rest = r["tag"].split("-f5-", 1)
            model = head.split("-", 1)[1]
            fault = next(x for x in ["honest"] + FAULTS if rest.startswith(x + "-"))
            arm = rest[len(fault) + 1:]
            if model in FREE:
                RUN[(model, arm, fault, r["task_id"], r["seed"])] = r
TASK = {json.loads(l)["task_id"]: json.loads(l) for l in open(A / "code/benchmark/tasks/adv_d7_v5.jsonl")}


def changes(task, fault):
    return bool(TASK[task]["fault_sim"][fault]["changes_answer"])


# ── 1. funnel on the warning arm ────────────────────────────────────────────
MENTION = re.compile(r"harness|check|warning|not reliable|unreliable|inconsisten|not consistent|cannot be (trusted|determined)", re.I)
fun = {}
for arm in ("advisory_nonum", "advisory_nonum_abstain"):
    fun[arm] = {}
    for m in FREE:
        c = collections.Counter(); conf_w, conf_clean = [], []
        for (mm, a, fa, t, s), r in RUN.items():
            if mm != m or a != arm:
                continue
            o = strict_outcome(r)
            if fa == "honest":
                if o == "correct" and isinstance(r.get("confidence"), (int, float)):
                    conf_clean.append(r["confidence"])
                continue
            c["faulted"] += 1
            if (r.get("d7_harness_failures") or 0) > 0:
                c["fired"] += 1
                c["fired_" + o] += 1
                if MENTION.search(r.get("final_text") or ""):
                    c["mentions"] += 1
                    c["mentions_" + o] += 1
                if o == "wrong_num" and isinstance(r.get("confidence"), (int, float)):
                    conf_w.append(r["confidence"])
        if c["faulted"]:
            fun[arm][m] = dict(c, conf_wrong_after_flag=(sum(conf_w) / len(conf_w) if conf_w else None),
                               conf_clean_correct=(sum(conf_clean) / len(conf_clean) if conf_clean else None))
out["funnel"] = fun


# ── 2. paired run-level tests ───────────────────────────────────────────────
def mcnemar(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, p)


def paired(arm_a, arm_b, faults=FAULTS, models=FREE):
    pairs = []
    for (m, a, fa, t, s), r in RUN.items():
        if a != arm_a or fa not in faults or m not in models:
            continue
        r2 = RUN.get((m, arm_b, fa, t, s))
        if r2 is None:
            continue
        pairs.append((t, m, strict_outcome(r) == "wrong_num", strict_outcome(r2) == "wrong_num"))
    if not pairs:
        return None
    b = sum(1 for _, _, x, y in pairs if x and not y)   # a wrong, b right
    c = sum(1 for _, _, x, y in pairs if y and not x)
    diff = 100 * (sum(x for _, _, x, _ in pairs) - sum(y for _, _, _, y in pairs)) / len(pairs)
    by_task = collections.defaultdict(list)
    for t, _, x, y in pairs:
        by_task[t].append(int(x) - int(y))
    tasks = sorted(by_task); rng = random.Random(0); boots = []
    for _ in range(2000):
        vals = [v for t in (rng.choice(tasks) for _ in tasks) for v in by_task[t]]
        boots.append(100 * sum(vals) / len(vals))
    boots.sort()
    per_model = {}
    for m in models:
        pm = [(x, y) for _, mm, x, y in pairs if mm == m]
        if pm:
            per_model[m] = round(100 * (sum(x for x, _ in pm) - sum(y for _, y in pm)) / len(pm), 1)
    return {"n_pairs": len(pairs), "models": len(per_model), "a_wrong_only": b, "b_wrong_only": c,
            "diff_points": round(diff, 1), "ci95_task_bootstrap": [round(boots[49], 1), round(boots[1949], 1)],
            "mcnemar_p": mcnemar(b, c), "per_model_diff": per_model}


out["paired"] = {f"sgp_vs_{b}": paired("sgp", b) for b in
                 ("none", "advisory_nonum", "advisory_nonum_abstain", "enforce2", "gate2", "repair3", "judge_block",
                  "judge_check", "checklist", "reviewer", "om", "retry2", "selfverify")}
out["paired"]["warning_vs_none"] = paired("advisory_nonum", "none")
out["paired"]["warning_abstain_vs_warning"] = paired("advisory_nonum_abstain", "advisory_nonum")

# ── 3. false alarms: faulted runs where the fault does not change the count ──
fa_out = {}
for arm in ("none", "sgp", "enforce2", "advisory_nonum"):
    per = {}
    tot = collections.Counter()
    for m in FREE:
        c = collections.Counter()
        for (mm, a, fa, t, s), r in RUN.items():
            if mm == m and a == arm and fa in FAULTS and not changes(t, fa):
                c[strict_outcome(r)] += 1; c["n"] += 1; c["fault_" + fa] += 1
        if c["n"]:
            per[m] = dict(c); tot.update(c)
    fa_out[arm] = {"per_model": per, "pooled": dict(tot),
                   "pooled_pct": {k: round(100 * v / tot["n"], 1) for k, v in tot.items()
                                  if k in ("correct", "wrong_num", "abstain", "no_answer")} if tot["n"] else {}}
    # and the affected runs, for the >80.6% question
    tot2 = collections.Counter()
    for (mm, a, fa, t, s), r in RUN.items():
        if a == arm and fa in FAULTS and changes(t, fa):
            tot2[strict_outcome(r)] += 1; tot2["n"] += 1
    fa_out[arm]["affected_pooled_pct"] = {k: round(100 * v / tot2["n"], 1) for k, v in tot2.items() if k != "n"} if tot2["n"] else {}
    fa_out[arm]["affected_n"] = tot2["n"]
out["false_alarm"] = fa_out

# ── 4. natural rate on real APIs: right docs, no guard ───────────────────────
import e3c_real_agents as e3c  # noqa: E402
try:
    import e3c_more
    SPECS = dict(e3c.API); SPECS.update(getattr(e3c_more, "API", {}) or getattr(e3c_more, "MORE", {}))
except Exception as e:  # noqa: BLE001
    SPECS = dict(e3c.API); out["natural_note"] = f"e3c_more specs not loaded: {e}"
nat = collections.defaultdict(collections.Counter)
files = glob.glob(str(A / "runs/matrix_e3c/*.jsonl")) + glob.glob(str(A / "runs/matrix_e3c_more/*/*.jsonl"))
for f in files:
    for line in open(f):
        r = json.loads(line)
        if r.get("docs") != "A" or r.get("arm") != "none" or r.get("key") not in FREE:
            continue
        spec = SPECS.get(r["api"])
        if not spec:
            continue
        c = nat[r["api"]]
        c["runs"] += 1
        unk = any(p not in spec["allowed"] for call in (r.get("calls") or []) for p in (call.get("params") or []))
        c["runs_with_undocumented_param"] += unk
        c[r.get("outcome", "?")] += 1
        if unk:
            c["undoc_" + str(r.get("outcome"))] += 1
tot = collections.Counter()
for c in nat.values():
    tot.update(c)
out["natural"] = {"per_api": {k: dict(v) for k, v in nat.items()}, "pooled": dict(tot)}

# ── 5. retail clean accuracy ────────────────────────────────────────────────
ret = collections.defaultdict(collections.Counter)
errs = collections.Counter()
for d in ("matrix_retail5", "matrix_sgp_r5"):
    for f in glob.glob(str(A / "runs" / d / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            head, rest = r["tag"].split("-r5-", 1)
            model = head.split("-", 1)[1]
            if not rest.startswith("honest-") or model not in FREE:
                continue
            arm = rest[len("honest-"):]
            if arm in ("none", "sgp"):
                o = strict_outcome(r)
                ret[(model, arm)][o] += 1
                ret[(model, arm)]["turns_sum"] += r.get("n_turns") or 0
                if o != "correct" and arm == "none":
                    errs[(model, o, (r.get("error") or "")[:40], bool(r.get("aborted_no_progress")))] += 1
out["retail_clean"] = {f"{m}|{a}": dict(c) for (m, a), c in sorted(ret.items())}
out["retail_clean_errors_none"] = [[list(k), v] for k, v in errs.most_common(25)]

(A / "reeval/paper_results/phase1_stats.json").write_text(json.dumps(out, indent=1, default=str))
print("FUNNEL advisory_nonum")
for m, c in out["funnel"]["advisory_nonum"].items():
    print(" ", m, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.items()})
print("PAIRED")
for k, v in out["paired"].items():
    if v:
        print(" ", k, {x: v[x] for x in ("n_pairs", "models", "diff_points", "ci95_task_bootstrap", "mcnemar_p")})
print("FALSE ALARM (fault does not change the count)")
for arm, v in out["false_alarm"].items():
    print(" ", arm, v["pooled"].get("n"), v["pooled_pct"], "| affected:", v["affected_n"], v["affected_pooled_pct"])
print("NATURAL", out["natural"]["pooled"], out.get("natural_note", ""))
for k, v in out["natural"]["per_api"].items():
    print(" ", k, v)
print("RETAIL CLEAN")
for k, v in out["retail_clean"].items():
    print(" ", k, v)
for k, v in out["retail_clean_errors_none"][:12]:
    print("   ", v, k)
