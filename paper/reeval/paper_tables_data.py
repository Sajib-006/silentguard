#!/usr/bin/env python3
"""Step 1 of the paper tables: compute every table number once from raw runs and save it to
reeval/paper_results/table_data.json. Step 2 (reeval/paper_tables_render.py) turns that file into
LaTeX, so table layout can change without recomputing (recomputing takes 15+ minutes on this disk).

Run on the server:  env/bin/python reeval/paper_tables_data.py
It also runs the old paper_results pipeline (macros, figures, numbers.json), so one run updates all.
"""
from __future__ import annotations

import collections
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_results as pr  # noqa: E402  (loads all runs at import)

D = {"models": {"free": pr.FREE, "oai": pr.OAI, "name": pr.NAME}}

# old pipeline first: fills pr.NUM, writes numbers_ext.tex and figures
a, b, apis = pr.write_tables()
pr.write_extra(pr.OUT / "tables_preview.md")
pr.write_strength(pr.OUT / "tables_preview.md")
pr.write_macros()
pr.figures(a, b, apis)
(pr.OUT / "numbers.json").write_text(json.dumps(pr.NUM, indent=1, default=str))

# ── AgentOrchBench, per model ────────────────────────────────────────────────
aob = {}
for arm, _ in pr.AOB_METHODS:
    aob[arm] = {}
    for m in pr.ALL13:
        full = m in pr.FREE
        aob[arm][m] = {
            "done": pr.aob_done(m, arm),
            "wrong": pr.aob_rate(m, arm, pr.FAULTS, "wrong_num", full=full),
            "correct": pr.aob_rate(m, arm, pr.FAULTS, "correct", full=full),
            "unknown": pr.aob_rate(m, arm, pr.FAULTS, "abstain", full=full),
            "clean": pr.aob_rate(m, arm, ["honest"], "correct", full=full),
            "by_fault": {fa: pr.aob_rate(m, arm, [fa], "wrong_num", full=full) for fa in pr.FAULTS},
        }
D["aob"] = aob
D["aob_labels"] = pr.AOB_METHODS
D["faults"] = pr.FAULTS

# ── Retail, per model ────────────────────────────────────────────────────────
ret = {}
for arm, _ in pr.RET_METHODS:
    ret[arm] = {}
    for m in pr.FREE:
        f, c = pr.RET[(m, arm, "fault")], pr.RET[(m, arm, "clean")]
        nf, nc = sum(f.values()), sum(c.values())
        ret[arm][m] = {"wrong": 100 * f["wrong_num"] / nf if nf >= 30 else None,
                       "correct": 100 * f["correct"] / nf if nf >= 30 else None,
                       "unknown": 100 * f["abstain"] / nf if nf >= 30 else None,
                       "clean": 100 * c["correct"] / nc if nc >= 30 else None}
D["retail"] = ret

# ── Real APIs, agents with wrong / right docs, per model ─────────────────────
cell = collections.defaultdict(collections.Counter)
for d in ("matrix_e3c", "matrix_e3c_sgp", "matrix_e3c_retry", "matrix_e3c_more/*", "matrix_e3c_typed/*"):
    for f in glob.glob(str(pr.R / d / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r.get("key") in pr.ALL13 and not r.get("error"):
                cell[(r["key"], r["arm"], r["docs"])][r["outcome"]] += 1
api = {}
for arm in ("none", "guard", "schema", "retry2", "sgp", "typed", "typed_sgp"):
    api[arm] = {}
    for m in pr.ALL13:
        g, ok = cell[(m, arm, "G")], cell[(m, arm, "A")]
        ng, na = sum(g.values()), sum(ok.values())
        api[arm][m] = {"wrong": 100 * g["wrong_num"] / ng if ng >= 10 else None,
                       "correct": 100 * g["correct"] / ng if ng >= 10 else None,
                       "unknown": 100 * g["abstain"] / ng if ng >= 10 else None,
                       "clean": 100 * ok["correct"] / na if na >= 10 else None, "n": ng}
D["realapi"] = api
bycell = collections.defaultdict(collections.Counter)
for d in ("matrix_e3c", "matrix_e3c_sgp", "matrix_e3c_retry", "matrix_e3c_more/*", "matrix_e3c_typed/*"):
    for f in glob.glob(str(pr.R / d / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r.get("key") in pr.FREE and not r.get("error") and r["docs"] == "G":
                bycell[(r["api"], r["arm"])][r["outcome"]] += 1
ORIG4 = {"ncbi", "uniprot", "europepmc", "gbif"}   # the APIs the pre-registered predictions were written for
cell4 = collections.defaultdict(collections.Counter)
for d in ("matrix_e3c", "matrix_e3c_sgp", "matrix_e3c_retry"):
    for f in glob.glob(str(pr.R / d / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            if r.get("key") in pr.ALL13 and not r.get("error") and r["api"] in ORIG4:
                cell4[(r["key"], r["arm"], r["docs"])][r["outcome"]] += 1
D["realapi4"] = {}
for arm in ("none", "guard", "schema", "retry2", "sgp"):
    D["realapi4"][arm] = {}
    for m in pr.ALL13:
        g = cell4[(m, arm, "G")]
        ng = sum(g.values())
        D["realapi4"][arm][m] = {"wrong": 100 * g["wrong_num"] / ng if ng >= 10 else None,
                                 "correct": 100 * g["correct"] / ng if ng >= 10 else None}
D["realapi_by_api"] = {f"{a}|{arm}": dict(c) for (a, arm), c in bycell.items()}

# ── ToolMaze, per model ──────────────────────────────────────────────────────
tm = {}
for arm, _ in pr.TM_METHODS:
    tm[arm] = {}
    for m in pr.FREE:
        tm[arm][m] = {"p3": pr.tm34(m, arm, ["P3"], 60), "p4": pr.tm34(m, arm, ["P4"], 60),
                      "silent": pr.tm34(m, arm, ["P3", "P4"], 120), "clean": pr.tm34(m, arm, ["P0"], 30),
                      "loud": None if arm == "blindr" else pr.tm34(m, arm, ["P1", "P2"], 120)}
D["toolmaze"] = tm
D["toolmaze_labels"] = pr.TM_METHODS
D["toolmaze1"] = {m: {"om": pr.tm_rate(pr.TM1, m, "om", ["P3", "P4"], key_has_study=False),
                      "sg": pr.tm_rate(pr.TM1, m, "sg", ["P3", "P4"], key_has_study=False)} for m in pr.ALL13}
D["toolmaze_twocall"] = {m: {a_: pr.tm6(m, a_) for a_ in ("ours", "blindr", "omcl")} for m in pr.FREE}

# ── the rest, as the old pipeline computed it ────────────────────────────────
D["realapi_probe"] = {"apis": apis, "fire": {k: list(v) for k, v in pr.t3()[1].items()}}
D["controls"] = pr.NUM.get("controls")
D["cost"] = [(pr.plain(l), v) for l, v in pr.t5_cost()]
D["training"] = [(fam, lab, [x[0] for x in r0], [x[0] if x[1] else None for x in r7]) for fam, lab, r0, r7 in pr.t6_training()]
rows, vbw = pr.oracle_blind()
D["oracle"] = {"rows": [(pr.FNICE.get(f, f), n, fired, none, enf, rep) for f, n, fired, none, enf, rep in rows], "vbw": vbw}
D["risk"] = pr.risk_table()
D["repeated_actions"] = pr.NUM.get("repeated_actions")
D["pooled"] = pr.NUM.get("pooled_stats")
D["rec"] = pr.NUM.get("rec_repair_vs_checked")
try:
    D["rec"] = [(l, v) for l, v in pr.rec_table()]
except Exception as e:  # record tasks are optional for the tables
    D["rec_error"] = str(e)

(pr.OUT / "table_data.json").write_text(json.dumps(D, indent=1, default=str))
print("TABLE_DATA_DONE", pr.OUT / "table_data.json")
