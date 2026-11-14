#!/usr/bin/env python3
"""Model-level inference for the response comparison (replaces GEE p-values; review R2 W3).

With 13 models the unit of inference is the model. For each arm vs `none`:
  * per-model wrong-number rate, pooled over the three detectable faults (complete models only);
  * paired difference per model; how many of 13 models improve;
  * exact sign-flip permutation test on the mean paired difference (all 2^13 sign patterns);
  * 95% interval for the mean difference by resampling models (cluster bootstrap, 20k draws);
  * Mantel-Haenszel odds ratio of a wrong answer, stratified by model.
Writes reeval/modellevel.json, read by paper_numbers.py.
"""
from __future__ import annotations
import collections, glob, itertools, json, math, random, re, sys
from pathlib import Path

A = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(A / "code/eval_v7"))
import outcomes                                    # noqa: E402
from strict_grade import strict_outcome            # noqa: E402

FAM = r"(?P<fam>drop_last|cap_total|or_semantics)"
SRC = [("matrix_faults", rf"-f-{FAM}-(?P<arm>\w+)$"), ("matrix_baselines", rf"-b-{FAM}-(?P<arm>\w+)$"),
       ("matrix_gate2v", rf"-g2-{FAM}-(?P<arm>gate2)$")]


def main(out=A / "reeval/modellevel.json", expect=180):
    cnt = collections.defaultdict(collections.Counter)   # (model, arm, fam) -> counts
    for d, rx in SRC:
        for f in glob.glob(str(A / "runs" / d / "*.jsonl")):
            m = re.search(rx, Path(f).name.split("__")[0])
            if not m:
                continue
            for line in open(f):
                r = json.loads(line)
                if not outcomes.scoreable(r):
                    continue
                k = (r["model"].split("/")[-1], m.group("arm"), m.group("fam"))
                cnt[k]["n"] += 1; cnt[k]["wrong"] += strict_outcome(r) == "wrong_num"
    models = sorted({k[0] for k in cnt})
    arms = sorted({k[1] for k in cnt} - {"none"})

    def rate(mod, arm):
        cs = [cnt[(mod, arm, f)] for f in ("drop_last", "cap_total", "or_semantics")]
        if any(c["n"] < 0.97 * expect for c in cs):
            return None, None, None
        n = sum(c["n"] for c in cs); w = sum(c["wrong"] for c in cs)
        return w / n, w, n

    res = {}
    for arm in arms:
        rows = []
        for mod in models:
            a, wa, na = rate(mod, arm); b, wb, nb = rate(mod, "none")
            if a is not None and b is not None:
                rows.append((mod, a, b, wa, na, wb, nb))
        if len(rows) < 13:
            res[arm] = {"complete_models": len(rows)}
            continue
        d = [r[1] - r[2] for r in rows]
        mean = sum(d) / len(d)
        # exact sign-flip permutation p (two-sided)
        ge = sum(1 for signs in itertools.product((1, -1), repeat=len(d))
                 if abs(sum(s * x for s, x in zip(signs, d)) / len(d)) >= abs(mean) - 1e-12)
        p_perm = ge / 2 ** len(d)
        rng = random.Random(0); boots = []
        for _ in range(20000):
            s = [d[rng.randrange(len(d))] for _ in d]; boots.append(sum(s) / len(s))
        boots.sort()
        # Mantel-Haenszel OR (wrong | arm vs none), strata = models
        num = den = 0.0
        for _, _, _, wa, na, wb, nb in rows:
            N = na + nb
            num += wa * (nb - wb) / N; den += wb * (na - wa) / N
        res[arm] = {"complete_models": len(rows), "mean_diff": mean,
                    "ci95": [boots[500], boots[19499]], "models_better": sum(x < 0 for x in d),
                    "models_worse": sum(x > 0 for x in d), "p_signflip": p_perm,
                    "mh_or": num / den if den else None,
                    "per_model": {r[0]: {"arm": r[1], "none": r[2]} for r in rows}}
    # Holm across the complete arms
    comp = sorted((v["p_signflip"], a) for a, v in res.items() if "p_signflip" in v)
    k = len(comp); running = 0
    for i, (p, a) in enumerate(comp):
        running = max(running, min(1.0, (k - i) * p)); res[a]["p_holm"] = running
    Path(out).write_text(json.dumps(res, indent=1))
    for a in sorted(res):
        v = res[a]
        if "p_signflip" in v:
            print(f"{a:<9} diff {v['mean_diff']:+.3f} [{v['ci95'][0]:+.3f},{v['ci95'][1]:+.3f}]  "
                  f"better {v['models_better']}/13  p_perm {v['p_signflip']:.4f}  p_holm {v['p_holm']:.4f}  MH-OR {v['mh_or']:.2f}")
        else:
            print(f"{a:<9} incomplete ({v['complete_models']}/13 models)")


if __name__ == "__main__":
    main()
