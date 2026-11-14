"""Offline detector audit, no model calls. Mirrors Outcome Monitors' 'C3/C4 cross-fit audit'
(their numbers: 79.6% implicit recall, 1.39% clean false-positive rate per tool outcome).

Fault outcomes = every is_perturbed step in P3/P4 task files; clean outcomes = every success
step in P0 files. Each workflow is checked with the registry mined without its own fold.
"""
import json, glob, sys, collections
from om_contracts import build_crossfit, Registry

root = sys.argv[1] if len(sys.argv) > 1 else "."
fold_of, regs = build_crossfit(f"{root}/data")
R = [Registry(r) for r in regs]


def steps_of(d):
    if "valid_paths" in d:
        return [s for p in d["valid_paths"] for s in p.get("execution_trace", [])]
    return d.get("execution_trace", [])


res = collections.defaultdict(lambda: [0, 0, 0])   # key -> [n, flagged, covered]
bytype = collections.defaultdict(lambda: [0, 0])
codes = collections.Counter()
for f in sorted(glob.glob(f"{root}/data/perturbed_tasks/c*/*.json")):
    d = json.load(open(f))
    m, c = d["perturbation_mode"], d["complexity"]
    if m not in ("P0", "P3", "P4"):
        continue
    reg = R[fold_of[d["task_id"]]]
    seen = set()
    for s in steps_of(d):
        key = (s["tool_name"], json.dumps(s.get("arguments", {}), sort_keys=True), json.dumps(s.get("output"), sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        if m == "P0" and s.get("status") != "success":
            continue
        if m in ("P3", "P4") and not s.get("is_perturbed"):
            continue
        V = reg.check(s["tool_name"], s.get("arguments", {}), s.get("output"))
        cov = s["tool_name"] in reg.reg
        grp = "clean" if m == "P0" else "fault"
        for k in [(grp, "all"), (grp, c), (grp, "C3C4" if c in ("C3", "C4") else "C1C2")]:
            res[k][0] += 1; res[k][1] += bool(V); res[k][2] += cov
        if m != "P0":
            bytype[d.get("corruption_type")][0] += 1; bytype[d.get("corruption_type")][1] += bool(V)
        for v in V:
            codes[(grp, v[0])] += 1

print("group      subset  n      flagged  rate    coverage")
for k in sorted(res):
    n, fl, cv = res[k]
    print(f"{k[0]:<10} {k[1]:<7} {n:<6} {fl:<8} {fl/n:6.1%}  {cv/n:6.1%}")
print("\nrecall by corruption type (all complexities)")
for t, (n, fl) in sorted(bytype.items(), key=lambda x: -x[1][1] / x[1][0]):
    print(f"  {t:<28} {n:<5} {fl/n:6.1%}")
print("\nfired codes")
for k, v in sorted(codes.items()):
    print(" ", k, v)
