#!/usr/bin/env python3
"""Sensitivity of SilentGuard's precision gate to the 1% cutoff, from the gate decisions already logged
in the ToolMaze runs (no new runs). A learned check with support n counts as exact when
1/(n+2) <= tau. For each tau we report how many logged gate decisions would change from warn to
withhold (or back) compared with tau = 1%. If none change, every ToolMaze result is the same at that tau.
On AgentOrchBench and real APIs every check is exact (n = infinity), so tau has no effect there.

    python gate_sensitivity.py <runs dir>   (reads ext_toolmaze4/test4/*/traces/sgp__*.json)
"""
import collections
import glob
import json
import sys

R = sys.argv[1]
TAUS = [0.001, 0.005, 0.01, 0.02, 0.05, 0.10, 0.20]
dec, sup = [], collections.Counter()
runs_with_gate = 0
for f in glob.glob(f"{R}/ext_toolmaze4/test4/*/traces/sgp__*.json"):
    try:
        row = json.load(open(f))["row"]
    except (OSError, ValueError, KeyError):
        continue
    if row.get("provider_error"):
        continue
    g = row.get("gate_log") or []
    runs_with_gate += bool(g)
    for e in g:
        dec.append(e["support"])
        sup[e["support"]] += 1
out = {"decisions": len(dec), "runs_with_gate": runs_with_gate, "support_counts": dict(sorted(sup.items())),
       "max_support": max(dec) if dec else None}
base = [1 / (n + 2) <= 0.01 for n in dec]
out["by_tau"] = {}
for tau in TAUS:
    ex = [1 / (n + 2) <= tau for n in dec]
    out["by_tau"][str(tau)] = {"exact_share": 100 * sum(ex) / len(ex) if ex else None,
                               "changed_vs_1pct": sum(a != b for a, b in zip(ex, base)),
                               "min_support_for_exact": max(0, round(1 / tau - 2))}
print(json.dumps(out, indent=1))
