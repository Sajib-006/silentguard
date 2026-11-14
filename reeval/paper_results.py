#!/usr/bin/env python3
"""Every results table and figure for the paper, generated only from raw run files.

Run on the server:  env/bin/python reeval/paper_results.py
Writes reeval/paper_results/: T1..T4 .tex, tables_preview.md, F1..F5 .pdf/.png, numbers.json.
A cell whose runs are not finished prints "pend." (never a partial number presented as final).
Grading: AgentOrchBench uses reeval/strict_grade.py; ToolMaze uses its own judge (row["pass"]).
"""
from __future__ import annotations

import collections
import glob
import itertools
import json
import re
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
R = B / "runs"
OUT = B / "reeval" / "paper_results"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(B / "code" / "eval_v7"))
sys.path.insert(0, str(B / "reeval"))
from strict_grade import strict_outcome  # noqa: E402
import roster                             # noqa: E402

FREE = ["llama4-scout", "llama4-maverick", "llama33-70b", "gpt-oss-120b", "nemotron-ultra", "gemma3-27b", "phi-4"]
OAI = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-4.1", "gpt-4o", "gpt-4o-mini"]
ALL13 = FREE + OAI
NAME = {"llama4-scout": "Llama-4-Scout", "llama4-maverick": "Llama-4-Maverick", "llama33-70b": "Llama-3.3-70B",
        "gpt-oss-120b": "gpt-oss-120b", "nemotron-ultra": "Nemotron-3-Ultra", "gemma3-27b": "Gemma-3-27B",
        "phi-4": "Phi-4", "gpt-5.6-sol": "GPT-5.6-Sol", "gpt-5.6-terra": "GPT-5.6-Terra",
        "gpt-5.6-luna": "GPT-5.6-Luna", "gpt-4.1": "GPT-4.1", "gpt-4o": "GPT-4o", "gpt-4o-mini": "GPT-4o-mini"}
KEY = {m: k for k, p, m, *_ in roster.roster(True, True)}
FAULTS = ["drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]
FNAME = {"drop_last": "Drop last", "cap_total": "Cap total", "or_semantics": "AND$\\to$OR",
         "drop_field": "Ignore field", "ignore_negation": "Ignore NOT", "off_by_one": "Off by one"}
PER_CELL = 72              # AgentOrchBench: 36 tasks x 2 seeds per (model, arm, fault)
NUM = {}                   # every number the text may cite


def plain(s):
    for a, b in (("\\textbf{", ""), ("}", ""), ("\\&", "&"), ("$\\to$", "->"), ("\\%", "%"), ("$\\times$", "x")):
        s = s.replace(a, b)
    return s


def pct(x):
    return "pend." if x is None else f"{x:.1f}"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def signflip(d):
    d = [x for x in d if x is not None]
    if not d:
        return None
    obs = abs(sum(d))
    return sum(1 for s in itertools.product([1, -1], repeat=len(d))
               if abs(sum(a * b for a, b in zip(s, d))) >= obs - 1e-12) / 2 ** len(d)


# ── AgentOrchBench ───────────────────────────────────────────────────────────
def load_aob():
    out = {}
    for d in ("matrix_final5", "matrix_sgp5", "matrix_pubd5", "matrix_retry5", "matrix_block5", "matrix_block5p", "matrix_cross5", "matrix_permit5"):
        for f in glob.glob(str(R / d / "*.jsonl")):
            for line in open(f):
                r = json.loads(line)
                m = KEY.get(r["model"], r["model"])
                rest = r["tag"].split("-f5-", 1)[1]
                fault = next(x for x in ["honest"] + FAULTS if rest.startswith(x + "-"))
                arm = rest[len(fault) + 1:]
                out[(m, arm, fault, r["task_id"], r["seed"])] = strict_outcome(r)
    return out


AOB = load_aob()
AOB_CELL = collections.defaultdict(collections.Counter)
for (m, a, fa, t, s), o in AOB.items():
    AOB_CELL[(m, a, fa)][o] += 1


def aob_rate(m, arm, faults, outcome, full=True):
    c = collections.Counter()
    for fa in faults:
        c.update(AOB_CELL[(m, arm, fa)])
    n = sum(c.values())
    if n == 0 or (full and n < 0.9 * PER_CELL * len(faults)):
        return None
    return 100 * c[outcome] / n


def aob_done(m, arm):
    n = sum(sum(AOB_CELL[(m, arm, fa)].values()) for fa in ["honest"] + FAULTS)
    return n / (PER_CELL * 7)


AOB_METHODS = [("none", "No guard"), ("skeptic", "Caution prompt"), ("judge_check", "LLM judge"), ("checklist", "Checklist [Sun et al. 2024]"),
               ("reviewer", "Reviewer [Ta et al. 2026]"), ("om", "Outcome Monitors [Panthi \\& Abdelfattah 2026]"),
               ("retry2", "Blind retry (control)"), ("requery", "Re-query, both counts shown"),
               ("selfverify", "Self-verification"), ("advisory", "Warning only"),
               ("advisory_hint", "Warning + recovery tip"), ("repair3", "Auto-repair"),
               ("gate2", "Answer gate"), ("enforce2", "Enforce"), ("judge_block", "LLM judge + blocking"),
               ("om_block", "Outcome Monitors + blocking"), ("judge_block_plain", "LLM judge without check results + blocking"), ("judge_advise_plain", "LLM judge without check results"),
               ("advisory_abstain", "Warning (last-condition check) + permission to abstain"),
               ("advisory_nonum", "Warning (full check)"), ("advisory_nonum_abstain", "Warning (full check) + permission to abstain"),
               ("sgp", "\\textbf{SilentGuard (ours)}")]


def t1a():
    rows, js = [], {}
    for arm, label in AOB_METHODS:
        per_f = [mean([aob_rate(m, arm, [fa], "wrong_num") for m in FREE]) if all(
            aob_rate(m, arm, [fa], "wrong_num") is not None for m in FREE) else None for fa in FAULTS]
        full = all(aob_rate(m, arm, FAULTS, "wrong_num") is not None for m in FREE)
        w = mean([aob_rate(m, arm, FAULTS, "wrong_num") for m in FREE]) if full else None
        c = mean([aob_rate(m, arm, FAULTS, "correct") for m in FREE]) if full else None
        ab = mean([aob_rate(m, arm, FAULTS, "abstain") for m in FREE]) if full else None
        cl_ok = all(aob_rate(m, arm, ["honest"], "correct") is not None for m in FREE)
        cl = mean([aob_rate(m, arm, ["honest"], "correct") for m in FREE]) if cl_ok else None
        rows.append((label, per_f, w, c, ab, cl))
        js[arm] = {"wrong_by_fault": dict(zip(FAULTS, per_f)), "wrong": w, "correct": c, "abstain": ab, "clean_correct": cl}
    NUM["aob"] = js
    return rows


R5 = ["drop_last", "cap_total", "or_semantics", "drop_field"]


def load_retail():
    cell = collections.defaultdict(collections.Counter)
    for d in ("matrix_retail5", "matrix_sgp_r5"):
        for f in glob.glob(str(R / d / "*.jsonl")):
            for line in open(f):
                r = json.loads(line)
                m = KEY.get(r["model"], r["model"])
                rest = r["tag"].split("-r5-", 1)[1]
                fault = next(x for x in ["honest"] + R5 if rest.startswith(x + "-"))
                cell[(m, rest[len(fault) + 1:], "clean" if fault == "honest" else "fault")][strict_outcome(r)] += 1
    return cell


RET = load_retail()
RET_METHODS = [("none", "No guard"), ("advisory", "Warning only"), ("advisory_hint", "Warning + recovery tip"),
               ("repair3", "Auto-repair"), ("enforce2", "Enforce"), ("sgp", "\\textbf{SilentGuard (ours)}")]


def t1_retail():
    rows = []
    for arm, label in RET_METHODS:
        vals = []
        for o, c in (("wrong_num", "fault"), ("correct", "fault"), ("abstain", "fault"), ("correct", "clean")):
            per = [100 * RET[(m, arm, c)][o] / sum(RET[(m, arm, c)].values()) if sum(RET[(m, arm, c)].values()) >= 30 else None
                   for m in FREE]
            vals.append(mean(per) if all(v is not None for v in per) else None)
        rows.append((label, vals))
    NUM["retail"] = {a: v for (a, _), (_, v) in zip(RET_METHODS, rows)}
    return rows


def t1_realapi():
    cell = collections.defaultdict(collections.Counter)
    for d in ("matrix_e3c", "matrix_e3c_sgp", "matrix_e3c_retry", "matrix_e3c_more/*"):
        for f in glob.glob(str(R / d / "*.jsonl")):
            for line in open(f):
                r = json.loads(line)
                if r.get("key") in FREE and not r.get("error"):
                    cell[(r["key"], r["arm"], r["docs"])][r["outcome"]] += 1
    rows = []
    for arm, label in (("none", "No guard"), ("guard", "Result check (ours v1)"), ("schema", "Spec check"),
                       ("retry2", "Blind retry (control)"), ("sgp", "\\textbf{SilentGuard (ours)}")):
        vals = []
        for o, docs in (("wrong_num", "G"), ("correct", "G"), ("abstain", "G"), ("correct", "A")):
            per = [100 * cell[(m, arm, docs)][o] / sum(cell[(m, arm, docs)].values()) if sum(cell[(m, arm, docs)].values()) >= 10 else None
                   for m in FREE]
            vals.append(mean(per) if all(v is not None for v in per) else None)
        rows.append((label, vals))
    NUM["realapi_free7"] = {l: v for l, v in rows}
    return rows


# ── ToolMaze ────────────────────────────────────────────────────────────────
def load_tm(root, arm_map=None):
    out = {}
    for f in glob.glob(str(R / root / "*" / "results.jsonl")):
        m = f.split("/")[-2]
        for line in open(f):
            r = json.loads(line)
            if r.get("provider_error"):
                continue
            arm = (arm_map or {}).get(r["arm"], r["arm"])
            out[(m, arm, r["task_id"], r["mode"])] = float(r["pass"])
    return out


TM34 = {}
TM34.update({("s3",) + k: v for k, v in load_tm("ext_toolmaze3/test3", {"omclr": "ours"}).items()})
TM34.update({("s4",) + k: v for k, v in load_tm("ext_toolmaze4/test4", {"sgp": "ours"}).items()
             if k[1] != "omclr"})
TMA = load_tm("ext_toolmaze5/expA")           # blind retry, same workflows as s3+s4
TMB = load_tm("ext_toolmaze5/expB", {"sgp": "ours"})
TM1 = load_tm("ext_toolmaze/test")
TM6 = load_tm("ext_toolmaze6/p3x2", {"sgp": "ours"})   # one-time fault lasting two calls instead of one


def tm6(m, arm, minn=120):
    ys = [v for (mm, a, t, mo), v in TM6.items() if mm == m and a == arm]
    return 100 * sum(ys) / len(ys) if len(ys) >= minn else None


def tm_rate(D, m, arm, modes, key_has_study=True, minn=30):
    ys = [v for k, v in D.items() if (k[1] if key_has_study else k[0]) == m
          and (k[2] if key_has_study else k[1]) == arm and (k[4] if key_has_study else k[3]) in modes]
    return 100 * sum(ys) / len(ys) if len(ys) >= minn else None


def tm34(m, arm, modes, minn=30):
    if arm == "blindr":
        ys = [v for (mm, a, t, mo), v in TMA.items() if mm == m and a == arm and mo in modes]
    elif modes[0] in ("P1", "P2"):
        ys = [v for (mm, a, t, mo), v in TMB.items() if mm == m and a == arm and mo in modes]
    else:
        ys = [v for (st, mm, a, t, mo), v in TM34.items() if mm == m and a == arm and mo in modes]
    return 100 * sum(ys) / len(ys) if len(ys) >= minn else None


TM_METHODS = [("checklist", "Checklist [Sun et al. 2024]"), ("om", "Outcome Monitors [Panthi \\& Abdelfattah 2026]"),
              ("omcl", "Checklist + Outcome Monitors"), ("blindr", "Blind retry (control)"),
              ("ours", "\\textbf{SilentGuard (ours)}")]


def t1b():
    rows, js = [], {}
    for arm, label in TM_METHODS:
        cols = []
        for modes, minn in ((["P3"], 60), (["P4"], 60), (["P3", "P4"], 120), (["P0"], 30), (["P1", "P2"], 120)):
            if arm == "blindr" and modes[0] in ("P1", "P2"):
                cols.append(None)
                continue
            if arm in ("blindr",) and False:
                pass
            vals = [tm34(m, arm, modes, minn) for m in FREE]
            cols.append(mean(vals) if all(v is not None for v in vals) else None)
        rows.append((label, cols))
        js[arm] = dict(zip(["p3", "p4", "silent", "clean", "loud"], cols))
    NUM["toolmaze34"] = js
    return rows


# ── Real APIs ───────────────────────────────────────────────────────────────
def t3():
    apis = []
    for line in open(B / "reeval" / "e3_real_apis_20260925.txt"):
        m = re.match(r"^(.+?)\s+base=(\d+)\s+doc=(\d+|None)\s+\|\s+(.+?)(?:\s+\|\s+partition.*)?$", line.strip())
        if not m:
            continue
        name, base, doc, params = m.group(1).strip(), int(m.group(2)), (None if m.group(3) == "None" else int(m.group(3))), m.group(4)
        st = dict(p.split("=", 1) for p in [x.strip() for x in params.split(",")] if "=" in p)
        silent = any(v == "SILENT" for v in st.values())
        kind = "silent" if silent else ("rejected" if all(v == "rejected" for v in st.values()) else "mixed")
        apis.append({"api": name, "base": base, "doc": doc, "behaviour": kind,
                     "inflation": base / doc if silent and doc else None})
    fire = {}
    for line in open(B / "reeval" / "e3b_value_swap.txt"):
        m = re.match(r"^(.+?)\s+base=\d+\s+(.*)$", line.strip())
        if m:
            parts = [p.strip() for p in m.group(2).split("|")]
            fire[m.group(1).strip()] = (sum(1 for p in parts if p.endswith("=FIRE")),
                                        sum(1 for p in parts if p.startswith("w:") and not p.endswith("=n/a")))
    e3c = {}
    txt = open(B / "reeval" / "e3c_results.txt").read()
    for m in re.finditer(r"docs (\w)\s+(\w+)\s+n=(\d+)\s+wrong ([\d.]+)\s+abstain ([\d.]+)\s+correct ([\d.]+)", txt):
        e3c[(m.group(1), m.group(2))] = {"n": int(m.group(3)), "wrong": 100 * float(m.group(4)),
                                         "abstain": 100 * float(m.group(5)), "correct": 100 * float(m.group(6))}
    better = re.findall(r"(\d+)/13", txt)
    NUM["real_apis"] = {"n_apis": len(apis), "n_silent": sum(a["behaviour"] == "silent" for a in apis),
                        "max_inflation": max(a["inflation"] or 0 for a in apis), "e3c": {f"{k[0]}_{k[1]}": v for k, v in e3c.items()},
                        "value_swap_fired": sum(v[0] for v in fire.values())}
    return apis, fire, e3c, better


# ── Tables ──────────────────────────────────────────────────────────────────
def write_tables():
    md = ["# Results tables (generated by reeval/paper_results.py; do not edit by hand)\n"]
    a = t1a()
    b = t1b()
    # T1 LaTeX
    L = ["\\begin{table*}[t]\\centering\\small", "\\setlength{\\tabcolsep}{4pt}",
         "\\begin{tabular}{l" + "c" * 6 + "|cccc}\\toprule",
         "\\multicolumn{11}{l}{\\textit{(a) AgentOrchBench: count tasks, silent API faults, 7 models. Wrong answers (\\%, $\\downarrow$) per fault type.}}\\\\",
         "Method & " + " & ".join(FNAME[f] for f in FAULTS) + " & Wrong$\\downarrow$ & Correct & Says unknown & Clean$\\uparrow$\\\\\\midrule"]
    md.append("## Table 1a. AgentOrchBench (7 models), % of faulted runs\n")
    md.append("| Method | " + " | ".join(FNAME[f].replace("$\\to$", "->") for f in FAULTS) + " | Wrong | Correct | Says unknown | Clean correct |")
    md.append("|" + "---|" * 11)
    for label, per_f, w, c, ab, cl in a:
        L.append(f"{label} & " + " & ".join(pct(x) for x in per_f) + f" & {pct(w)} & {pct(c)} & {pct(ab)} & {pct(cl)}\\\\")
        md.append(f"| {plain(label)} | " + " | ".join(pct(x) for x in per_f) + f" | {pct(w)} | {pct(c)} | {pct(ab)} | {pct(cl)} |")
    for title, rows_, mdtitle in (
            ("(b) AgentOrchBench, retail domain (4 fault types). Wrong, correct and says-unknown: \\% of faulted runs; clean: \\% correct with no fault.", t1_retail(), "Table 1b. AgentOrchBench retail domain (7 models)"),
            ("(c) Real public APIs, agents given subtly wrong documentation (4 APIs). Clean: \\% correct with the right documentation.", t1_realapi(), "Table 1c. Real public APIs, wrong docs (7 models)")):
        L += ["\\midrule", f"\\multicolumn{{11}}{{l}}{{\\textit{{{title}}}}}\\\\",
              "Method & \\multicolumn{6}{c}{} & Wrong$\\downarrow$ & Correct & Says unknown & Clean$\\uparrow$\\\\\\midrule"]
        md.append(f"\n## {mdtitle}\n")
        md.append("| Method | Wrong | Correct | Says unknown | Clean correct |\n|---|---|---|---|---|")
        for label, v in rows_:
            L.append(f"{label} & \\multicolumn{{6}}{{c}}{{}} & " + " & ".join(pct(x) for x in v) + "\\\\")
            md.append(f"| {plain(label)} | " + " | ".join(pct(x) for x in v) + " |")
    L += ["\\midrule",
          "\\multicolumn{11}{l}{\\textit{(d) ToolMaze [Zhu et al. 2026], fresh workflows (studies 3+4, 180 workflows), 7 models. Task success (\\%, $\\uparrow$).}}\\\\",
          "Method & \\multicolumn{2}{c}{One-time fault} & \\multicolumn{2}{c}{Lasting fault} & \\multicolumn{2}{c}{Silent (all)} & Clean & \\multicolumn{3}{c}{Loud faults}\\\\\\midrule"]
    md.append("\n## Table 1d. ToolMaze, fresh workflows (studies 3+4), 7 models, % task success\n")
    md.append("| Method | One-time fault (P3) | Lasting fault (P4) | Silent (P3+P4) | Clean (P0) | Loud faults (P1+P2) |")
    md.append("|---|---|---|---|---|---|")
    for label, cols in b:
        L.append(f"{label} & \\multicolumn{{2}}{{c}}{{{pct(cols[0])}}} & \\multicolumn{{2}}{{c}}{{{pct(cols[1])}}} & "
                 f"\\multicolumn{{2}}{{c}}{{{pct(cols[2])}}} & {pct(cols[3])} & \\multicolumn{{3}}{{c}}{{{pct(cols[4]) if cols[4] is not None or 'blind' not in label.lower() else '--'}}}\\\\")
        md.append(f"| {plain(label)} | " + " | ".join(
            ("--" if (i == 4 and "blind" in label.lower()) else pct(x)) for i, x in enumerate(cols)) + " |")
    L += ["\\bottomrule\\end{tabular}",
          "\\caption{Main results. (a) On AgentOrchBench every agent must report a count while the API silently "
          "returns a wrong one. Without a guard, agents report a wrong number most of the time; prompts, an LLM judge "
          "and self-verification help very little; only checks inside the harness that pass on checked values alone remove "
          "wrong answers. Says unknown = the agent answers that the count cannot be found. Clean = success on the same tasks with no fault. (b) On ToolMaze, built by others with its "
          "own judge, SilentGuard is the best method on silent faults. All methods use the same models and settings.}",
          "\\label{tab:main}", "\\end{table*}"]
    (OUT / "T1_main.tex").write_text("\n".join(L) + "\n")

    # T2 per model
    md.append("\n## Table 2. Per model\n")
    md.append("| Model | AOB wrong, no guard | AOB wrong, SilentGuard | AOB clean, SilentGuard | ToolMaze-1 OM | ToolMaze-1 ours v1 | ToolMaze-3+4 Checklist+OM | ToolMaze-3+4 SilentGuard |")
    md.append("|---|---|---|---|---|---|---|---|")
    L2 = ["\\begin{table*}[t]\\centering\\small", "\\begin{tabular}{lccc|cc|cc}\\toprule",
          " & \\multicolumn{3}{c|}{AgentOrchBench} & \\multicolumn{2}{c|}{ToolMaze study 1} & \\multicolumn{2}{c}{ToolMaze studies 3+4}\\\\",
          "Model & Wrong, no guard$\\downarrow$ & Wrong, SilentGuard$\\downarrow$ & Clean, SilentGuard$\\uparrow$ & Outcome Mon. & Ours (v1) & Checklist+OM & SilentGuard\\\\\\midrule"]
    d_aob, d_tm, d_tm1 = [], [], []
    for m in ALL13:
        f1 = aob_done(m, "none")
        wn = aob_rate(m, "none", FAULTS, "wrong_num", full=False)
        ws = aob_rate(m, "sgp", FAULTS, "wrong_num")
        cs = aob_rate(m, "sgp", ["honest"], "correct")
        om1 = tm_rate(TM1, m, "om", ["P3", "P4"], key_has_study=False)
        sg1 = tm_rate(TM1, m, "sg", ["P3", "P4"], key_has_study=False)
        c34 = tm34(m, "omcl", ["P3", "P4"], 120)
        o34 = tm34(m, "ours", ["P3", "P4"], 120)
        if wn is not None and ws is not None:
            d_aob.append(ws - wn)
        if c34 is not None and o34 is not None:
            d_tm.append(o34 - c34)
        if om1 is not None and sg1 is not None:
            d_tm1.append(sg1 - om1)
        name = NAME[m] + ("" if f1 >= 0.97 else f" ({100 * f1:.0f}\\%)")
        L2.append(f"{name} & {pct(wn)} & {pct(ws)} & {pct(cs)} & {pct(om1)} & {pct(sg1)} & {pct(c34)} & {pct(o34)}\\\\")
        md.append(f"| {plain(name)} | {pct(wn)} | {pct(ws)} | {pct(cs)} | {pct(om1)} | {pct(sg1)} | {pct(c34)} | {pct(o34)} |")
    summ = (f"Ours better on & \\multicolumn{{3}}{{c|}}{{{sum(x < 0 for x in d_aob)}/{len(d_aob)} models, p={signflip(d_aob) or float('nan'):.3f}}} & "
            f"\\multicolumn{{2}}{{c|}}{{{sum(x > 0 for x in d_tm1)}/{len(d_tm1)}, p={signflip(d_tm1) or float('nan'):.3f}}} & "
            f"\\multicolumn{{2}}{{c}}{{{sum(x > 0 for x in d_tm)}/{len(d_tm)}, p={signflip(d_tm) or float('nan'):.3f}}}\\\\")
    L2 += ["\\midrule", summ, "\\bottomrule\\end{tabular}",
           "\\caption{Per-model results. A model with fewer than all of its runs finished shows the finished share in "
           "brackets. p: exact sign-flip test over models. ToolMaze study 1 compares our first version with Outcome "
           "Monitors on 13 models; studies 3+4 compare the final SilentGuard with the best mix of published methods.}",
           "\\label{tab:permodel}", "\\end{table*}"]
    md.append(f"\nOurs better: AOB {sum(x < 0 for x in d_aob)}/{len(d_aob)} (p={signflip(d_aob)}), ToolMaze-1 "
              f"{sum(x > 0 for x in d_tm1)}/{len(d_tm1)} (p={signflip(d_tm1)}), ToolMaze-3+4 {sum(x > 0 for x in d_tm)}/{len(d_tm)} (p={signflip(d_tm)})")
    NUM["permodel"] = {"aob_better": [sum(x < 0 for x in d_aob), len(d_aob), signflip(d_aob)],
                       "tm1_better": [sum(x > 0 for x in d_tm1), len(d_tm1), signflip(d_tm1)],
                       "tm34_better": [sum(x > 0 for x in d_tm), len(d_tm), signflip(d_tm)]}
    (OUT / "T2_permodel.tex").write_text("\n".join(L2) + "\n")

    # T3 real APIs
    apis, fire, e3c, better = t3()
    md.append("\n## Table 3. Real public APIs\n")
    md.append("| API | Wrong filter is... | Returned count | Correct count | Overcount | Value-swap check |")
    md.append("|---|---|---|---|---|---|")
    L3 = ["\\begin{table}[t]\\centering\\small", "\\begin{tabular}{lcrrrc}\\toprule",
          "API & Wrong filter & Returned & Correct & Overcount & Check\\\\\\midrule"]
    for a_ in apis:
        fk = next((v for k, v in fire.items() if k.startswith(a_["api"][:12])), None)
        chk = "--" if not fk or fk[1] == 0 else f"{fk[0]}/{fk[1]}"
        infl = f"{a_['inflation']:.0f}$\\times$" if a_["inflation"] else "--"
        L3.append(f"{a_['api']} & {a_['behaviour']} & {a_['base']:,} & {a_['doc'] if a_['doc'] is None else format(a_['doc'], ',')} & {infl} & {chk}\\\\".replace("None", "--"))
        md.append(f"| {a_['api']} | {a_['behaviour']} | {a_['base']:,} | {'--' if a_['doc'] is None else format(a_['doc'], ',')} | {plain(infl)} | {chk} |")
    L3.append("\\midrule\\multicolumn{6}{l}{\\textit{Agents on these APIs with subtly wrong documentation (13 models, 260 runs per arm)}}\\\\")
    L3.append("Arm & \\multicolumn{2}{c}{Wrong$\\downarrow$} & Says unknown & \\multicolumn{2}{c}{Correct$\\uparrow$}\\\\")
    md.append("\n| Agents, wrong docs (13 models) | Wrong | Says unknown | Correct |\n|---|---|---|---|")
    for k, label in (("none", "No guard"), ("guard", "Result check (ours v1)"), ("schema", "Request-vs-spec check (SilentGuard exact check)")):
        v = e3c.get(("G", k))
        if v:
            L3.append(f"{label} & \\multicolumn{{2}}{{c}}{{{v['wrong']:.0f}}} & {v['abstain']:.0f} & \\multicolumn{{2}}{{c}}{{{v['correct']:.0f}}}\\\\")
            md.append(f"| {label} | {v['wrong']:.0f} | {v['abstain']:.0f} | {v['correct']:.0f} |")
    L3 += ["\\bottomrule\\end{tabular}",
           "\\caption{Silent failures in real public APIs. \\emph{silent}: an unknown or misspelled filter is ignored with "
           "HTTP 200, so the API returns the unfiltered count. Overcount = returned count / correct count. Check: wrong filters "
           "caught by the value-swap check. Bottom: agents given subtly wrong documentation; every model has fewer "
           "wrong answers with either check.}", "\\label{tab:realapis}", "\\end{table}"]
    (OUT / "T3_realapis.tex").write_text("\n".join(L3) + "\n")

    # T4 controls
    md.append("\n## Table 4. Ablations and controls (7 models)\n")
    L4 = ["\\begin{table}[t]\\centering\\small", "\\begin{tabular}{llcc}\\toprule", "Setting & Arm & Measure & Value\\\\\\midrule"]
    rows4 = []
    for arm, lab in (("ours", "SilentGuard (re-check only when a check fails)"), ("blindr", "Blind retry (every call twice)"), ("omcl", "No re-check (Checklist + OM)")):
        rows4.append(("Re-check vs. blind retry (ToolMaze P3+P4)", lab, "silent success", mean([tm34(m, arm, ["P3", "P4"], 120) for m in FREE]) if all(tm34(m, arm, ["P3", "P4"], 120) is not None for m in FREE) else None))
    s3w = mean([tm_rate(load_tm("ext_toolmaze3/test3"), m, "sgv", ["P3", "P4"], key_has_study=False) for m in FREE])
    s3a = mean([tm_rate(load_tm("ext_toolmaze3/test3"), m, "omclr", ["P3", "P4"], key_has_study=False) for m in FREE])
    rows4 += [("Withhold vs. warn, learned checks (ToolMaze)", "Warn (SilentGuard choice)", "silent success", s3a),
              ("", "Withhold", "silent success", s3w)]
    for arm, lab in (("advisory", "Warn"), ("enforce2", "Enforce (withhold)"), ("sgp", "SilentGuard choice")):
        full = all(aob_rate(m, arm, FAULTS, "wrong_num") is not None for m in FREE)
        rows4.append(("Withhold vs. warn, exact checks (AgentOrchBench)" if arm == "advisory" else "", lab, "wrong answers",
                      mean([aob_rate(m, arm, FAULTS, "wrong_num") for m in FREE]) if full else None))
    for arm, lab in (("omcl", "Checklist + OM"), ("ours", "SilentGuard")):
        rows4.append(("Loud faults (ToolMaze P1/P2)" if arm == "omcl" else "", lab, "success",
                      mean([tm34(m, arm, ["P1", "P2"], 120) for m in FREE]) if all(tm34(m, arm, ["P1", "P2"], 120) is not None for m in FREE) else None))
    p3x2 = {}
    for arm, lab in (("ours", "SilentGuard"), ("blindr", "Blind retry"), ("omcl", "No re-check (Checklist + OM)")):
        vals = [tm6(m, arm) for m in FREE]
        v = mean(vals) if all(x is not None for x in vals) else None
        p3x2[arm] = v
        rows4.append(("One-time fault lasting two calls (ToolMaze P3)" if arm == "ours" else "", lab, "success", v))
    d6 = [tm6(m, "ours") - tm6(m, "blindr") for m in FREE if tm6(m, "ours") is not None and tm6(m, "blindr") is not None]
    d6o = [tm6(m, "ours") - tm6(m, "omcl") for m in FREE if tm6(m, "ours") is not None and tm6(m, "omcl") is not None]
    NUM["p3x2"] = {"rates": p3x2, "n_models": len(d6),
                   "ours_minus_blindr": [mean(d6) if d6 else None, sum(x > 0 for x in d6), signflip(d6) if d6 else None],
                   "ours_minus_omcl": [mean(d6o) if d6o else None, sum(x > 0 for x in d6o), signflip(d6o) if d6o else None],
                   "per_model": {m: [tm6(m, a) for a in ("ours", "blindr", "omcl")] for m in FREE}}
    md.append("| Setting | Arm | Measure | Value |\n|---|---|---|---|")
    for q, lab, meas, v in rows4:
        L4.append(f"{q} & {lab} & {meas} & {pct(v)}\\\\")
        md.append(f"| {q} | {lab} | {meas} | {pct(v)} |")
    L4 += ["\\bottomrule\\end{tabular}", "\\caption{Controls. The right response depends on how exact the check is "
           "(withhold for exact checks, warn for learned ones), which is the rule SilentGuard applies; SilentGuard does not hurt when "
           "failures are already loud. Blind retry sends every call twice and releases the second result, so it clears a "
           "fault that ends within two calls, but it cannot help when the fault persists.}", "\\label{tab:controls}", "\\end{table}"]
    (OUT / "T4_controls.tex").write_text("\n".join(L4) + "\n")
    NUM["controls"] = {f"{i}": [q, lab, v] for i, (q, lab, meas, v) in enumerate(rows4)}
    (OUT / "tables_preview.md").write_text("\n".join(md) + "\n")
    return a, b, apis


# ── Cost, training, pooled statistics ─────────────────────────────────────────
def t5_cost():
    """Cost per faulted run on AgentOrchBench (7 models): agent tool calls, LLM tokens, wall time,
    harness checks run, extra judge/reviewer calls. Rows are read raw (not graded)."""
    agg = collections.defaultdict(lambda: collections.defaultdict(list))
    for d in ("matrix_final5", "matrix_sgp5", "matrix_pubd5", "matrix_retry5"):
        for f in glob.glob(str(R / d / "*.jsonl")):
            for line in open(f):
                r = json.loads(line)
                m = KEY.get(r["model"], r["model"])
                if m not in FREE or "-f5-honest-" in r["tag"]:
                    continue
                arm = r["tag"].split("-f5-", 1)[1].split("-", 1)[1]
                u = r.get("usage") or {}
                agg[arm][m].append((r.get("n_calls", 0) or 0,
                                    (u.get("prompt_tokens", 0) or 0) + (u.get("completion_tokens", 0) or 0),
                                    r.get("wall_s", 0) or 0, r.get("d7_harness_ran", 0) or 0,
                                    (r.get("reviews", 0) or 0) + (r.get("judge_calls", 0) or 0)))
    rows = []
    for arm, label in AOB_METHODS:
        if not all(agg[arm][m] for m in FREE):
            rows.append((label, [None] * 5))
            continue
        cols = [mean([sum(x[i] for x in agg[arm][m]) / len(agg[arm][m]) for m in FREE]) for i in range(5)]
        rows.append((label, cols))
    NUM["cost_aob"] = {plain(l): v for l, v in rows}
    return rows


E6_ROWS = [("Phi-4-mini", "base", "Base model"), ("Phi-4-mini", "sft", "SFT to refuse (trained on drop-last)"),
           ("Phi-4-mini", "sftr", "SFT to repair"), ("Phi-4-mini", "rl", "RL (DAPO, false-equation penalty)"),
           ("Mistral-7B", "mbase", "Base model"), ("Mistral-7B", "msft", "SFT to refuse (trained on drop-last)"),
           ("Mistral-7B", "msftr", "SFT to repair")]
E6_UNSEEN = ["cap_total", "or_semantics", "ignore_negation", "off_by_one", "prefix"]
E6_T07 = {"base": "pbase", "sft": "psft", "sftr": "psftr", "rl": "prl"}


def t6_training():
    cell = collections.defaultdict(collections.Counter)
    for f in glob.glob(str(R / "matrix_e6" / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            m = re.match(r"^e6(t07)?-([a-z]+)-(.+)$", r["tag"])
            if not m:
                continue
            cell[("T0.7" if m.group(1) else "T0", m.group(2), m.group(3))][strict_outcome(r)] += 1

    def rate(temp, arm, modes, o):
        c = collections.Counter()
        for mo in modes:
            c.update(cell[(temp, arm, mo)])
        n = sum(c.values())
        return (100 * c[o] / n, n) if n else (None, 0)
    rows = []
    for fam, arm, label in E6_ROWS:
        r0 = [rate("T0", arm, ["drop_last"], "wrong_num"), rate("T0", arm, E6_UNSEEN, "wrong_num"),
              rate("T0", arm, ["consistent_lie"], "wrong_num"), rate("T0", arm, ["drop_last-para"], "wrong_num"),
              rate("T0", arm, ["honest"], "correct")]
        tarm = E6_T07.get(arm, arm)
        r7 = [rate("T0.7", tarm, ["drop_last"], "wrong_num"), rate("T0.7", tarm, ["or_semantics", "off_by_one"], "wrong_num"),
              rate("T0.7", tarm, ["honest"], "correct")]
        rows.append((fam, label, r0, r7))
    NUM["training"] = {f"{fam}/{label}": [[x[0] for x in r0], [x[0] for x in r7]] for fam, label, r0, r7 in rows}
    return rows


def pooled_stats():
    """Exact sign-flip tests over models. ToolMaze studies 3+4 pooled as 14 (study, model) pairs."""
    out = {}
    for base in ("omcl", "om", "checklist", "blindr"):
        d = []
        for st in ("s3", "s4"):
            for m in FREE:
                ours = [v for (s_, mm, a_, t, mo), v in TM34.items() if s_ == st and mm == m and a_ == "ours" and mo in ("P3", "P4")]
                if base == "blindr":
                    wf = {t for (s_, mm, a_, t, mo) in TM34 if s_ == st and mm == m}
                    them = [v for (mm, a_, t, mo), v in TMA.items() if mm == m and a_ == "blindr" and t in wf and mo in ("P3", "P4")]
                else:
                    them = [v for (s_, mm, a_, t, mo), v in TM34.items() if s_ == st and mm == m and a_ == base and mo in ("P3", "P4")]
                if len(ours) >= 60 and len(them) >= 60:
                    d.append(100 * (sum(ours) / len(ours) - sum(them) / len(them)))
        if d:
            out[f"toolmaze34_ours_vs_{base}"] = {"pairs": len(d), "mean_diff": sum(d) / len(d), "wins": sum(x > 0 for x in d),
                                                 "losses": sum(x < 0 for x in d), "p": signflip(d) if len(d) <= 16 else None}
    for base in ("none", "checklist", "reviewer", "om", "judge_check", "retry2", "enforce2", "repair3"):
        d = [aob_rate(m, "sgp", FAULTS, "wrong_num") - aob_rate(m, base, FAULTS, "wrong_num") for m in FREE
             if aob_rate(m, "sgp", FAULTS, "wrong_num") is not None and aob_rate(m, base, FAULTS, "wrong_num") is not None]
        if d:
            out[f"aob_sgp_vs_{base}_wrong"] = {"models": len(d), "mean_diff": sum(d) / len(d),
                                               "sgp_fewer_wrong": sum(x < 0 for x in d), "p": signflip(d)}
    NUM["pooled_stats"] = out
    return out


REC_UNSEEN = ["blind_analyzed_text_match", "blind_tag_join_fanout", "blind_partial_shard_timeout",
              "blind_lossy_bitmap_no_recheck", "blind_keyset_cursor_ties"]
REC_SEEN = ["drop_last", "drop_field", "or_semantics", "prefix", "consistent_lie", "cap_total"]


def rec_table():
    """Record-list tasks (rec9): repair vs the checked-only rule on fault types seen in design vs unseen
    (blind) faults. 'Verified but wrong' = a wrong list the harness released as verified."""
    from strict_grade import _ABSTAIN
    cell = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    REC_M = [m for m in FREE if m != "nemotron-ultra"]   # SilentGuard on lists ran on these 6 (cost)
    for f in glob.glob(str(R / "matrix_rec9" / "*.jsonl")) + glob.glob(str(R / "matrix_rec9_sgp" / "*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            m = KEY.get(r["model"], r["model"])
            if m not in REC_M or "-e9-" not in r["tag"]:
                continue
            rest = r["tag"].split("-e9-", 1)[1]
            fault = next((x for x in ["honest"] + REC_SEEN + REC_UNSEEN if rest.startswith(x + "-")), None)
            if fault is None:
                continue
            arm = rest[len(fault) + 1:]
            grp = "clean" if fault == "honest" else ("unseen" if fault in REC_UNSEEN else "seen")
            ans = (r.get("answer") or "").strip()
            if re.fullmatch(r"(ids\s*[=:]\s*(\[\s*\])?\s*)?", ans, re.I):
                ans = ""                  # an empty list is no answer, not a wrong answer
            if r.get("enforced_abstain") or (ans and _ABSTAIN.search(ans)):
                o = "abstain"
            elif r.get("answer_correct") or r.get("correct"):
                o = "correct"
            elif not ans:
                o = "no_answer"
            else:
                o = "wrong"
            cell[(arm, grp)][m][o] += 1
            if o == "wrong" and ((arm == "rec_repair" and (r.get("repaired") or 0) > 0) or arm in ("rec_enforce", "rec_sgp")):
                cell[(arm, grp)][m]["verified_wrong"] += 1
    rows = []
    for arm, label in (("none", "No guard"), ("rec_repair", "Auto-repair"), ("rec_enforce", "Checked answers only (SilentGuard rule)"),
                       ("rec_sgp", "SilentGuard")):
        vals = []
        for grp, outs in (("seen", ("wrong", "verified_wrong", "correct")), ("unseen", ("wrong", "verified_wrong", "correct")),
                          ("clean", ("correct",))):
            for o in outs:
                per = [100 * cell[(arm, grp)][m][o] / sum(v for k, v in cell[(arm, grp)][m].items() if k != "verified_wrong")
                       for m in REC_M if sum(cell[(arm, grp)][m].values()) >= 20]
                vals.append(mean(per) if len(per) == len(REC_M) else None)
        rows.append((label, vals))
    NUM["rec_repair_vs_checked"] = {l: v for l, v in rows}
    return rows


def risk_table():
    """Score = correct - penalty x wrong (% of faulted runs), mean over 7 models: which method is best
    depends on how much a wrong answer costs compared with no answer."""
    out = []
    for arm, label in (("none", "No guard"), ("repair3", "Auto-repair"), ("enforce2", "Enforce"), ("sgp", "SilentGuard (ours)")):
        full = all(aob_rate(m, arm, FAULTS, "wrong_num") is not None for m in FREE)
        c = mean([aob_rate(m, arm, FAULTS, "correct") for m in FREE]) if full else None
        w = mean([aob_rate(m, arm, FAULTS, "wrong_num") for m in FREE]) if full else None
        out.append((label, c, w))
    return out


def repeated_actions():
    """ToolMaze: how often each harness repeats an ACTION tool (bookings, payments, messages): blind retry
    re-sends every call; SilentGuard re-calls only a result that failed a check."""
    import yaml
    acts = {t["name"] for t in yaml.safe_load(open(B / "external" / "ToolMaze" / "tools" / "definitions" / "action.yaml"))["tools"]}
    res = {}
    for arm, root, tag in (("blindr", "ext_toolmaze5/expA", "[harness blind retry]"),
                           ("ours", "ext_toolmaze4/test4", "[harness re-check]")):
        per = []
        for m in FREE:
            runs = glob.glob(str(R / root / m / "traces" / f"{'blindr' if arm == 'blindr' else 'sgp'}__*.json"))
            if len(runs) < 50:
                continue
            reps, any_rep = 0, 0
            for f in runs:
                t = json.load(open(f))
                if t["row"].get("provider_error"):
                    continue
                k = sum(1 for msg in t["trace"]["messages"] if msg.get("role") == "assistant"
                        and msg.get("content") == tag and (msg.get("tool_call") or {}).get("name") in acts)
                reps += k
                any_rep += k > 0
            per.append((reps / len(runs), 100 * any_rep / len(runs)))
        if per:
            res[arm] = (mean([p[0] for p in per]), mean([p[1] for p in per]), len(per))
    NUM["repeated_actions"] = res
    return res


def oracle_blind():
    """Harness-only test (no model) on unseen fault types, count tasks: reeval/oracle_blind_v5.txt.
    Cells ok / abstain / wrong over 36 tasks; columns none, enforce_v1, enforce_v3, repair_v1, repair3."""
    rows, vbw = [], None
    for line in open(B / "reeval" / "oracle_blind_v5.txt"):
        m = re.match(r"^(\S+)/all\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)$", line.strip())
        if m and m.group(1) != "off":
            cells = [tuple(int(x) for x in c.split("/")) for c in re.findall(r"\d+/\s*\d+/\s*\d+", m.group(5))]
            rows.append((m.group(1), int(m.group(2)), int(m.group(4)), cells[0], cells[2], cells[4]))
        v = re.match(r"^verified-but-WRONG repairs:\s*(\d+)", line.strip())
        if v:
            vbw = int(v.group(1))
    # 2026-10-01 (review): the printed total adds repair_v1 and repair3; the table shows repair3, so count
    # its verified-but-wrong repairs alone, from the saved per-task rows of the same harness-only run.
    rp = B / "reeval" / "oracle_blind_v5_rows.jsonl"
    if rp.exists():
        R_ = [json.loads(l) for l in open(rp)]
        vbw = sum(1 for r in R_ if r["f3"] and r.get("rep3") and r["rep3"].get("value") is not None
                  and r["rep3"]["value"] != r["truth"])
    NUM["oracle_blind"] = {"rows": rows, "verified_but_wrong_repairs": vbw}
    return rows, vbw


FNICE = {"blind_analyzed_text_match": "Word-stem text match", "blind_null_negation_drop": "NOT drops empty fields",
         "blind_partial_shard_timeout": "Partial shard timeout", "blind_tag_join_fanout": "Tag-join double counting",
         "blind_keyset_cursor_ties": "Cursor ties skip rows", "blind_lossy_bitmap_no_recheck": "Lossy index, no recheck"}


def write_strength(md_path):
    md = [open(md_path).read()]
    rows, vbw = oracle_blind()
    md.append("\n## Table 7. Unseen fault types, harness only (no model), 36 count tasks each: correct / says unknown / wrong\n")
    md.append("| Unseen fault | Check fired | No guard | SilentGuard rule (checked answers only) | Auto-repair |\n|---|---|---|---|---|")
    L = ["\\begin{table}[t]\\centering\\small", "\\begin{tabular}{lcccc}\\toprule",
         "Unseen fault & Fired & No guard & SilentGuard rule & Auto-repair\\\\\\midrule"]
    tot = {"none": [0, 0, 0], "sgp": [0, 0, 0], "rep": [0, 0, 0]}
    for f, n, fired, none, enf, rep in rows:
        cell = lambda x: f"{x[0]}/{x[1]}/{x[2]}"
        L.append(f"{FNICE.get(f, f)} & {fired}/{n} & {cell(none)} & {cell(enf)} & {cell(rep)}\\\\")
        md.append(f"| {FNICE.get(f, f)} | {fired}/{n} | {cell(none)} | {cell(enf)} | {cell(rep)} |")
        for k, x in (("none", none), ("sgp", enf), ("rep", rep)):
            tot[k] = [a + b for a, b in zip(tot[k], x)]
    L.append("\\midrule Total & & " + " & ".join(f"{t[0]}/{t[1]}/{t[2]}" for t in tot.values()) + "\\\\")
    md.append("| Total | | " + " | ".join(f"{t[0]}/{t[1]}/{t[2]}" for t in tot.values()) + " |")
    md.append(f"\nRepairs released as verified but wrong: {vbw}. SilentGuard rule: wrong only where the check did not fire.")
    L += ["\\bottomrule\\end{tabular}",
          f"\\caption{{Why SilentGuard does not repair. Six fault types written after the method was frozen, run through the "
          f"harness alone (cells: correct / says unknown / wrong, 36 count tasks each). When its check fires, the SilentGuard "
          f"rule never releases a wrong answer; its wrong answers come only from faults the check cannot see, which "
          f"every method misses. Auto-repair turned detected faults into {vbw} wrong answers marked as verified.}}",
          "\\label{tab:repair}", "\\end{table}"]
    (OUT / "T7_repair.tex").write_text("\n".join(L) + "\n")
    NUM["oracle_blind_totals"] = tot

    rk = risk_table()
    md.append("\n## Table 8. Risk: score = correct - penalty x wrong, AgentOrchBench faulted runs (7 models)\n")
    pens = (0, 1, 3, 10)
    md.append("| Method | Correct | Wrong | " + " | ".join(f"penalty {p}" for p in pens) + " |")
    md.append("|---|---|---|" + "---|" * len(pens))
    for label, c, w in rk:
        sc = [None if c is None else c - p * w for p in pens]
        md.append(f"| {label} | {pct(c)} | {pct(w)} | " + " | ".join(pct(x) for x in sc) + " |")
    NUM["risk"] = {l: [c, w] for l, c, w in rk}

    ra = repeated_actions()
    md.append("\n## Repeated actions on ToolMaze (bookings, payments, messages sent twice by the harness)\n")
    for arm, (per_run, share, k) in ra.items():
        md.append(f"- {'Blind retry' if arm == 'blindr' else 'SilentGuard'}: {per_run:.2f} repeated actions per run; "
                  f"{share:.1f}% of runs repeat at least one action ({k} models)")
    open(md_path, "w").write("\n".join(md) + "\n")


def write_macros():
    """numbers_ext.tex: every number the paper TEXT cites for the new results, same \\num{key} format as
    numbers.tex. A missing value prints the red [pending] marker instead of a number."""
    K = {}

    def put(key, val, fmt="{:.1f}"):
        K[key] = None if val is None else (fmt.format(val) if isinstance(val, (int, float)) else str(val))
    a = NUM.get("aob", {})
    for arm in ("none", "skeptic", "judge_check", "checklist", "reviewer", "om", "retry2", "requery", "selfverify",
                "advisory", "advisory_hint", "repair3", "gate2", "enforce2", "sgp"):
        v = a.get(arm, {})
        k = arm.replace("_", "")
        put(f"aob-{k}-wrong", v.get("wrong")); put(f"aob-{k}-correct", v.get("correct"))
        put(f"aob-{k}-unknown", v.get("abstain")); put(f"aob-{k}-clean", v.get("clean_correct"))
    pub = [a.get(x, {}).get("wrong") for x in ("checklist", "reviewer", "om", "judge_check")]
    pub = [x for x in pub if x is not None]
    put("aob-pub-wrong-min", min(pub) if pub else None); put("aob-pub-wrong-max", max(pub) if pub else None)
    for arm, v in (NUM.get("retail") or {}).items():
        k = arm.replace("_", "")
        put(f"ret-{k}-wrong", v[0]); put(f"ret-{k}-correct", v[1]); put(f"ret-{k}-clean", v[3])
    for lab, v in (NUM.get("realapi_free7") or {}).items():
        k = {"No guard": "none", "Result check (ours v1)": "guard", "Spec check": "schema",
             "Blind retry (control)": "retry2"}.get(lab, "sgp")
        put(f"api-{k}-wrong", v[0]); put(f"api-{k}-correct", v[1]); put(f"api-{k}-cleancorrect", v[3])
    ra = NUM.get("real_apis") or {}
    put("e3n-apis", ra.get("n_apis"), "{}"); put("e3n-silent", ra.get("n_silent"), "{}")
    put("e3n-maxinfl", ra.get("max_inflation"), "{:.0f}")
    for arm, v in (NUM.get("toolmaze34") or {}).items():
        for f in ("p3", "p4", "silent", "clean", "loud"):
            put(f"tm-{arm}-{f}", v.get(f))
    for key, v in (NUM.get("pooled_stats") or {}).items():
        k = key.replace("_", "-")
        if key.startswith("toolmaze"):
            put(f"{k}-wins", v["wins"], "{}"); put(f"{k}-n", v["pairs"], "{}")
            put(f"{k}-diff", v["mean_diff"]); put(f"{k}-p", v["p"], "{:.3f}")
        else:
            put(f"{k}-fewer", v["sgp_fewer_wrong"], "{}"); put(f"{k}-n", v["models"], "{}"); put(f"{k}-p", v["p"], "{:.3f}")
    for lab, v in (NUM.get("cost_aob") or {}).items():
        k = {"No guard": "none", "SilentGuard (ours)": "sgp", "Enforce": "enforce", "Reviewer [Ta et al. 2026]": "reviewer",
             "LLM judge": "judge"}.get(lab)
        if k and v[0] is not None:
            put(f"cost-{k}-calls", v[0]); put(f"cost-{k}-tokens", v[1], "{:,.0f}"); put(f"cost-{k}-secs", v[2])
    rep = NUM.get("repeated_actions") or {}
    for arm, v in rep.items():
        put(f"ra-{arm}-perrun", v[0], "{:.2f}"); put(f"ra-{arm}-share", v[1])
    p6 = NUM.get("p3x2") or {}
    for arm, v in (p6.get("rates") or {}).items():
        put(f"tmtwo-{arm}-p3", v)
    for key in ("ours_minus_blindr", "ours_minus_omcl"):
        v = p6.get(key)
        if v:
            k = key.replace("_", "")
            put(f"tmtwo-{k}-diff", v[0]); put(f"tmtwo-{k}-wins", v[1], "{}"); put(f"tmtwo-{k}-p", v[2], "{:.3f}")
    put("tmtwo-n", p6.get("n_models"), "{}")
    ob = NUM.get("oracle_blind_totals") or {}
    for arm, v in ob.items():
        put(f"ob-{arm}-correct", v[0], "{}"); put(f"ob-{arm}-unknown", v[1], "{}"); put(f"ob-{arm}-wrong", v[2], "{}")
    put("ob-vbw", (NUM.get("oracle_blind") or {}).get("verified_but_wrong_repairs"), "{}")
    lines = ["% GENERATED by reeval/paper_results.py from raw runs. Do not edit by hand."]
    for k, v in sorted(K.items()):
        val = "\\pending" if v is None else v.replace(",", "{,}")
        lines.append(f"\\expandafter\\def\\csname num@{k}\\endcsname{{{val}}}")
    (OUT / "numbers_ext.tex").write_text("\n".join(lines) + "\n")
    NUM["_macro_count"] = len(K)


def write_extra(md_path):
    md = [open(md_path).read()]
    cost = t5_cost()
    md.append("\n## Table 5. Cost per faulted run, AgentOrchBench (7 models)\n")
    md.append("| Method | Agent tool calls | LLM tokens | Seconds | Harness checks run | Extra LLM calls (judge/reviewer) |")
    md.append("|---|---|---|---|---|---|")
    L = ["\\begin{table}[t]\\centering\\small", "\\begin{tabular}{lrrrrr}\\toprule",
         "Method & Tool calls & Tokens & Seconds & Checks & Extra LLM calls\\\\\\midrule"]
    for label, v in cost:
        f = [("pend." if x is None else (f"{x:,.0f}" if i == 1 else f"{x:.1f}")) for i, x in enumerate(v)]
        L.append(f"{label} & " + " & ".join(f) + "\\\\")
        md.append(f"| {plain(label)} | " + " | ".join(f) + " |")
    L += ["\\bottomrule\\end{tabular}", "\\caption{Cost per faulted run (mean over 7 models). Checks: harness "
          "consistency checks run. Extra LLM calls: judge or reviewer calls on top of the agent's own.}",
          "\\label{tab:cost}", "\\end{table}"]
    (OUT / "T5_cost.tex").write_text("\n".join(L) + "\n")

    tr = t6_training()
    md.append("\n## Table 6. Training the agent instead of guarding it (E6). Wrong answers %, clean correct %\n")
    md.append("| Model | Training | Seen fault | Unseen faults | Consistent lie | Reworded tasks | Clean | T=0.7 seen | T=0.7 unseen | T=0.7 clean |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    L = ["\\begin{table*}[t]\\centering\\small", "\\begin{tabular}{llccccc|ccc}\\toprule",
         " & & \\multicolumn{5}{c|}{Greedy decoding} & \\multicolumn{3}{c}{Sampling, $T=0.7$, 3 seeds}\\\\",
         "Model & Training & Seen$\\downarrow$ & Unseen$\\downarrow$ & Consistent lie$\\downarrow$ & Reworded$\\downarrow$ & "
         "Clean$\\uparrow$ & Seen$\\downarrow$ & Unseen$\\downarrow$ & Clean$\\uparrow$\\\\\\midrule"]
    for fam, label, r0, r7 in tr:
        cells = [pct(x[0]) for x in r0] + [pct(x[0]) if x[1] else "--" for x in r7]
        L.append(f"{fam} & {label} & " + " & ".join(cells) + "\\\\")
        md.append(f"| {fam} | {label} | " + " | ".join(cells) + " |")
    L += ["\\bottomrule\\end{tabular}", "\\caption{Training the model itself, with no harness guard. Seen: the fault "
          "used in training (drop-last); unseen: five fault types never trained on; consistent lie: a wrong answer the "
          "API backs up everywhere; reworded: the seen fault with reworded tasks. The Mistral-7B base model cannot "
          "operate the tools (almost every run stops after repeated failed calls), so it gives no answers; its 0\\% "
          "wrong is not a success.}", "\\label{tab:training}", "\\end{table*}"]
    (OUT / "T6_training.tex").write_text("\n".join(L) + "\n")

    st = pooled_stats()
    md.append("\n## Pooled statistics (exact sign-flip test over models)\n")
    for k, v in st.items():
        md.append(f"- {k}: " + ", ".join(f"{a_}={(round(b_, 3) if isinstance(b_, float) else b_)}" for a_, b_ in v.items()))
    open(md_path, "w").write("\n".join(md) + "\n")


# ── Figures ─────────────────────────────────────────────────────────────────
def figures(a, b, apis):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # reference palette (dataviz skill, light mode): slot 1 blue, slot 8 red, neutral grays for recessive marks
    BLUE, RED, GRAY, INK, INK2, GRID = "#2a78d6", "#e34948", "#b9b8b1", "#0b0b0b", "#52514e", "#e6e5e0"
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                         "font.family": "DejaVu Sans"})

    def save(fig, name):
        for ext in ("pdf", "png"):
            fig.savefig(OUT / f"{name}.{ext}", dpi=200, bbox_inches="tight")
        plt.close(fig)

    # F1: wrong / abstain / correct per method on AgentOrchBench (stacked horizontal bars, 2px surface gaps)
    rows = [(lab.replace("\\textbf{", "").replace("}", ""), w, ab, c) for lab, _, w, c, ab, _ in a if w is not None]
    fig, ax = plt.subplots(figsize=(6.2, 0.32 * len(rows) + 0.9))
    for i, (lab, w, ab, c) in enumerate(rows):
        y = len(rows) - 1 - i
        ax.barh(y, w, color=RED, height=0.62, edgecolor="white", linewidth=1)
        ax.barh(y, ab, left=w, color=GRAY, height=0.62, edgecolor="white", linewidth=1)
        ax.barh(y, c, left=w + ab, color=BLUE, height=0.62, edgecolor="white", linewidth=1)
        ax.text(-1.5, y, lab, ha="right", va="center", color=INK, fontsize=8.5,
                fontweight="bold" if "SilentGuard" in lab else "normal")
        if w >= 8:
            ax.text(w - 1.0, y, f"{w:.0f}%", va="center", ha="right", color="white", fontsize=7.5)
        else:
            ax.text(w + 1.0, y, f"{w:.0f}%", va="center", ha="left", color=INK, fontsize=7.5)
    ax.set_yticks([]); ax.set_xlim(0, 100); ax.set_xlabel("% of runs with a silent API fault")
    ax.grid(axis="x", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=RED, label="Wrong answer"), Patch(color=GRAY, label="Says unknown"),
                       Patch(color=BLUE, label="Correct")], ncol=3, frameon=False, loc="lower center",
              bbox_to_anchor=(0.5, 1.0), fontsize=8)
    save(fig, "F1_aob_outcomes")

    # F2: per-model wrong answers, no guard -> SilentGuard (dumbbell)
    pts = [(NAME[m], aob_rate(m, "none", FAULTS, "wrong_num", full=False), aob_rate(m, "sgp", FAULTS, "wrong_num"))
           for m in FREE]
    pts = [p for p in pts if p[1] is not None]
    fig, ax = plt.subplots(figsize=(5.2, 0.34 * len(pts) + 0.8))
    for i, (n, x0, x1) in enumerate(pts):
        y = len(pts) - 1 - i
        if x1 is not None:
            ax.plot([x1, x0], [y, y], color=GRID, linewidth=2, zorder=1)
            ax.scatter([x1], [y], s=42, color=BLUE, zorder=3, edgecolor="white", linewidth=1.5)
        ax.scatter([x0], [y], s=42, color=RED, zorder=3, edgecolor="white", linewidth=1.5)
        ax.text(-2, y, n, ha="right", va="center", fontsize=8.5, color=INK)
    ax.set_yticks([]); ax.set_xlim(0, 100); ax.set_xlabel("Wrong answers under silent faults (%)")
    ax.grid(axis="x", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
    ax.legend(handles=[plt.Line2D([], [], marker="o", color="w", markerfacecolor=RED, markersize=7, label="No guard"),
                       plt.Line2D([], [], marker="o", color="w", markerfacecolor=BLUE, markersize=7, label="SilentGuard (ours)")],
              frameon=False, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=8)
    save(fig, "F2_permodel_aob")

    # F3: how far real APIs inflate a count when a filter is silently ignored (log scale)
    sil = sorted([x for x in apis if x["inflation"]], key=lambda x: x["inflation"])
    fig, ax = plt.subplots(figsize=(5.2, 0.3 * len(sil) + 0.8))
    for i, x in enumerate(sil):
        ax.barh(i, x["inflation"], color=RED, height=0.6)
        ax.text(x["inflation"] * 1.08, i, f"{x['inflation']:.0f}$\\times$", va="center", fontsize=7.5, color=INK)
    ax.set_yticks(range(len(sil))); ax.set_yticklabels([x["api"] for x in sil], fontsize=8)
    ax.set_xscale("log"); ax.set_xlabel("Returned count / correct count (log scale)")
    ax.grid(axis="x", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
    save(fig, "F3_realapi_inflation")

    # F4: ToolMaze per model, strongest published mix vs SilentGuard (two series, grouped bars)
    pm = [(NAME[m], tm34(m, "omcl", ["P3", "P4"], 120), tm34(m, "ours", ["P3", "P4"], 120)) for m in FREE]
    pm = [p for p in pm if p[1] is not None and p[2] is not None]
    if pm:
        fig, ax = plt.subplots(figsize=(6.4, 2.6))
        xs = range(len(pm)); w = 0.38
        ax.bar([x - w / 2 - 0.01 for x in xs], [p[1] for p in pm], width=w, color=GRAY, label="Checklist + Outcome Monitors")
        ax.bar([x + w / 2 + 0.01 for x in xs], [p[2] for p in pm], width=w, color=BLUE, label="SilentGuard (ours)")
        ax.set_xticks(list(xs)); ax.set_xticklabels([p[0] for p in pm], rotation=25, ha="right", fontsize=8)
        ax.set_ylabel("Task success, silent faults (%)"); ax.grid(axis="y", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
        ax.legend(frameon=False, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=8)
        save(fig, "F4_toolmaze_permodel")

    # F5: one-time vs lasting faults on ToolMaze: no re-check / blind retry / SilentGuard
    arms = [("omcl", "No re-check (Checklist + OM)", GRAY), ("blindr", "Blind retry", "#eda100"), ("ours", "SilentGuard (ours)", BLUE)]
    vals = {a_: [mean([tm34(m, a_, [mo], 60) for m in FREE]) if all(tm34(m, a_, [mo], 60) is not None for m in FREE) else None
                 for mo in ("P3", "P4")] for a_, _, _ in arms}
    if all(v is not None for vv in vals.values() for v in vv):
        fig, ax = plt.subplots(figsize=(4.4, 2.6)); w = 0.26
        for j, (a_, lab, col) in enumerate(arms):
            ax.bar([i + (j - 1) * (w + 0.02) for i in range(2)], vals[a_], width=w, color=col, label=lab)
        ax.set_xticks([0, 1]); ax.set_xticklabels(["One-time fault (P3)", "Lasting fault (P4)"])
        ax.set_ylabel("Task success (%)"); ax.grid(axis="y", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
        ax.legend(frameon=False, fontsize=7.5, loc="upper right")
        save(fig, "F5_recheck_vs_retry")


if __name__ == "__main__":
    a, b, apis = write_tables()
    write_extra(OUT / "tables_preview.md")
    write_strength(OUT / "tables_preview.md")
    write_macros()
    figures(a, b, apis)
    (OUT / "numbers.json").write_text(json.dumps(NUM, indent=1, default=str))
    print("wrote", sorted(p.name for p in OUT.iterdir()))
