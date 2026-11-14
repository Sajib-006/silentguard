#!/usr/bin/env python3
"""Step 2 of the paper tables: read table_data.json (made by paper_tables_data.py) and write every
LaTeX table of the paper. Fast (no run files are read), so layout can be changed and recompiled at once.

    python paper_tables_render.py <table_data.json> <out_dir>

Conventions (same in every table): mean over models with standard error as a small +- value; the best
value in a column is bold and the second best underlined; arrows give the better direction; "--" means
the arm was not run on that benchmark; our method is the shaded row.
"""
from __future__ import annotations

import collections
import itertools
import json
import math
import sys
from pathlib import Path

D = json.load(open(sys.argv[1]))
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
FREE, OAI, NAME = D["models"]["free"], D["models"]["oai"], D["models"]["name"]
# Nemotron-3-Ultra on the control arms and the SilentGuard list arm (run on Genentech's internal platform, same code and grader):
# only wrong and clean shares exist for the control arms, so correct, unknown and by-fault cells stay on 6 models.
EXT = Path(sys.argv[1]).parent / "nemotron_external.json"
NEMO_EXT = EXT.exists()
D["rec6"] = D.get("rec")
if NEMO_EXT:
    X = json.load(open(EXT))
    for arm, v in X["aob"].items():
        D["aob"][arm]["nemotron-ultra"] = dict(v)
    D["rec"] = [(lab, [(6 * a + b) / 7 for a, b in zip(vals, X["rec"][lab])]) for lab, vals in D["rec6"]]
ALL13 = FREE + OAI


# ── helpers ─────────────────────────────────────────────────────────────────
def stats(vals):
    """mean and standard error over models; None unless every model has a value"""
    if not vals or any(v is None for v in vals):
        return None
    n = len(vals)
    m = sum(vals) / n
    se = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1)) / math.sqrt(n) if n > 1 else 0.0
    return m, se


def signflip(d):
    d = [x for x in d if x is not None]
    if not d:
        return None
    obs = abs(sum(d))
    return sum(1 for s in itertools.product([1, -1], repeat=len(d))
               if abs(sum(a * b for a, b in zip(s, d))) >= obs - 1e-12) / 2 ** len(d)


def rank_marks(values, lower_better):
    """index -> 'best' / 'second' for the non-None values of one column"""
    idx = [i for i, v in enumerate(values) if v is not None]
    order = sorted(idx, key=lambda i: values[i] if lower_better else -values[i])
    marks = {}
    if order:
        marks[order[0]] = "best"
        best = values[order[0]]
        for i in order[1:]:
            if abs(values[i] - best) < 0.05:
                marks[i] = "best"
        rest = [i for i in order if i not in marks]
        if rest and len(order) >= 3:
            for i in rest:
                if abs(values[i] - values[rest[0]]) < 0.05:
                    marks[i] = "second"
    return marks


def cell(st, mark=None, se=True, dec=1):
    if st is None:
        return "--"
    m, s = st
    txt = f"{m:.{dec}f}"
    if mark == "best":
        txt = f"\\textbf{{{txt}}}"
    elif mark == "second":
        txt = f"\\underline{{{txt}}}"
    return txt + (f"\\se{{{s:.1f}}}" if se else "")


def per_model(block, arm, key, models=FREE):
    return [(block.get(arm, {}).get(m) or {}).get(key) for m in models]


SIX = [m for m in FREE if m != "nemotron-ultra"]   # arms run without Nemotron-3-Ultra (cost)
SIX_ARMS = {"judge_block", "om_block", "judge_block_plain", "judge_advise_plain", "advisory_abstain",
            "advisory_nonum_abstain"}


def _no_semicolons(text):
    """Captions use plain sentences: a semicolon becomes a full stop (a comma inside brackets)."""
    out, depth, i = [], 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == ";" and text[i + 1:i + 2] == " " and text[i - 1:i] != "\\":
            if depth:
                out.append(",")
            else:
                out.append(".")
                j = i + 2
                if j < len(text) and text[j].islower():
                    out.append(" " + text[j].upper())
                    i = j + 1
                    continue
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def write(name, lines):
    lines = [_no_semicolons(l) if l.startswith("\\caption{") else l for l in lines]
    (OUT / name).write_text("\n".join(lines) + "\n")


OURS_ROW = "\\rowcolor{ours}"
SHORTLAB = {"Checklist [Sun et al. 2024]": "Checklist", "Reviewer [Ta et al. 2026]": "Reviewer",
            "Outcome Monitors [Panthi \\& Abdelfattah 2026]": "Outcome Monitors",
            "Outcome Monitors [Panthi & Abdelfattah 2026]": "Outcome Monitors",
            "Blind retry (control)": "Blind retry", "Re-query, both counts shown": "Re-query",
            "Warning only": "Warning (last condition)", "Warning + recovery tip": "Warning + tip",
            "Answer gate": "Answer gate (last cond.)",
            "LLM judge + blocking": "Judge + blocking", "Outcome Monitors + blocking": "OM + blocking",
            "LLM judge without check results + blocking": "Judge, no evidence, + blocking",
            "LLM judge without check results": "Judge, no evidence",
            "Warning (last-condition check) + permission to abstain": "Warning (last cond.) + may abstain",
            "Warning (full check)": "Warning", "Warning (full check) + permission to abstain": "Warning + may abstain",
            "SilentGuard (ours)": "\\textbf{SilentGuard}", "\\textbf{SilentGuard (ours)}": "\\textbf{SilentGuard}"}
HEAD = "\\rowcolor{head}"

# ── Table 1: AgentOrchBench, every method; Table 1b: the other benchmarks ─────
def ms(arm, key=None):
    """models behind a cell: all 7, except the control arms' correct, unknown and by-fault cells (6)"""
    if arm in SIX_ARMS and (D["aob"].get(arm, {}).get("nemotron-ultra") or {}).get(key) is None:
        return SIX
    return FREE


def six_mark(arm, key):
    return "$^{\\dagger}$" if ms(arm, key) is SIX else ""


AOB_ROWS = [
    ("Baselines and controls", [("No guard", "none"), ("Caution prompt", "skeptic"), ("Self-verification", "selfverify"),
                                ("Re-query", "requery"), ("Blind retry", "retry2"), ("LLM judge", "judge_check")]),
    ("Published methods", [("Checklist", "checklist"), ("Reviewer", "reviewer"), ("Outcome Monitors", "om")]),
    ("Evidence, permission, enforcement",
     [("Judge, no evidence", "judge_advise_plain"), ("\\quad + blocking", "judge_block_plain"),
      ("Judge + blocking", "judge_block"), ("OM + blocking", "om_block"),
      ("Warning + may abstain", "advisory_nonum_abstain")]),
    (None, [("\\textbf{SilentGuard (ours)}", "sgp")])]
AOB_COLS = [("wrong", True, "Wrong$\\downarrow$"), ("correct", False, "Correct$\\uparrow$"),
            ("unknown", None, "Unknown"), ("clean", False, "Clean$\\uparrow$")]
AOB_HEAD = ["Wrong$\\downarrow$", "Corr.$\\uparrow$", "Unk.", "Clean$\\uparrow$"]
OTHER_BLOCKS = [
    ("Retail domain", "retail",
     [("No guard", "none"), ("Auto-repair", "repair3"), ("Enforce", "enforce2"), ("\\textbf{SilentGuard (ours)}", "sgp")],
     [("wrong", True, "Wrong$\\downarrow$"), ("correct", False, "Correct$\\uparrow$"), ("clean", False, "Clean$\\uparrow$")]),
    ("Real APIs (9)", "realapi",
     [("No guard", "none"), ("Blind retry", "retry2"), ("Spec check alone", "schema"), ("Typed calls", "typed"),
      ("Typed calls + ours", "typed_sgp"), ("\\textbf{SilentGuard (ours)}", "sgp")],
     [("wrong", True, "Wrong$\\downarrow$"), ("correct", False, "Correct$\\uparrow$"), ("clean", False, "Right docs$\\uparrow$")]),
    ("ToolMaze", "toolmaze",
     [("Checklist", "checklist"), ("Outcome Monitors", "om"), ("Checklist + OM", "omcl"),
      ("Blind retry", "blindr"), ("\\textbf{SilentGuard (ours)}", "ours")],
     [("silent", False, "Silent$\\uparrow$"), ("loud", False, "Loud$\\uparrow$"), ("clean", False, "Clean$\\uparrow$")])]


def block_grid(blk, rows, cols):
    """rows present in the data only; grid of (mean, se) per column"""
    out = []
    for lab, arm in rows:
        g = [stats(per_model(D[blk], arm, k, ms(arm, k))) for k, _, _ in cols]
        if any(x is not None for x in g):
            out.append((lab, arm, g))
    return out


def gain(blk, rows, ours_arm, key, lower):
    """ours minus the best other row (7-model rows only), with an exact sign-flip test over models"""
    o = per_model(D[blk], ours_arm, key)
    best = None
    for lab, arm in rows:
        if arm == ours_arm or arm in SIX_ARMS:
            continue
        st = stats(per_model(D[blk], arm, key))
        if st is None:
            continue
        if best is None or (st[0] < best[1] if lower else st[0] > best[1]):
            best = (arm, st[0], lab)
    if best is None or stats(o) is None:
        return None
    other = per_model(D[blk], best[0], key)
    return {"vs": best[2], "diff": stats(o)[0] - best[1], "p": signflip([a - b for a, b in zip(o, other)])}


def main_table():
    rows = [(g, lab, arm) for g, rs in AOB_ROWS for lab, arm in rs]
    grid = [[stats(per_model(D["aob"], arm, k, ms(arm, k))) for k, _, _ in AOB_COLS] for _, _, arm in rows]
    marks = [rank_marks([grid[i][j][0] if grid[i][j] else None for i in range(len(rows))], AOB_COLS[j][1])
             if AOB_COLS[j][1] is not None else {} for j in range(len(AOB_COLS))]
    L = ["\\begin{table}[t]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{2.6pt}",
         "\\begin{tabular}{@{}lcccc@{}}", "\\toprule", HEAD,
         "\\textbf{Method} & " + " & ".join(AOB_HEAD) + "\\\\", "\\midrule"]
    group = "start"
    for i, (g, lab, arm) in enumerate(rows):
        if g != group:
            if group != "start":
                L.append("\\midrule")
            if g:
                L.append(f"\\multicolumn{{5}}{{@{{}}l}}{{\\textit{{{g}}}}}\\\\")
            group = g
        L.append((OURS_ROW if arm == "sgp" else "") + f"{lab} & " +
                 " & ".join(cell(grid[i][j], marks[j].get(i), se=(j in (0, 3))) + six_mark(arm, AOB_COLS[j][0])
                            for j in range(len(AOB_COLS))) + "\\\\")
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{\\textbf{SilentGuard gives the fewest wrong answers on AgentOrchBench.} Mean over 7 open "
          "models ($\\pm$ standard error), \\% of runs with a silent "
          "fault (Wrong, Corr., Unk.)\\ or with no fault (Clean). Unk.: the answer says the count cannot be determined. "
          "Bold is best and underlined is second. The LLM judge sees the check results and advises. Published: checklist \\citep{sun2024toolsfail}, reviewer "
          "\\citep{ta2026reinforced}, Outcome Monitors (OM) \\citep{panthi2026outcome}. Blocking: a flagged result is withheld and only accepted counts may "
          "be reported. No evidence: the judge does not see the check results.}",
          "\\label{tab:main}", "\\end{table}"]
    write("tab_main.tex", L)
    NUMS = {"aob-wrong": gain("aob", [(l, a) for _, l, a in rows], "sgp", "wrong", True),
            "aob-clean": gain("aob", [(l, a) for _, l, a in rows], "sgp", "clean", False)}
    for title, blk, brows, cols in OTHER_BLOCKS:
        ours = brows[-1][1]
        for k, lower, _ in cols:
            if lower is not None:
                NUMS[f"{blk}-{k}"] = gain(blk, brows, ours, k, lower)
    return {k: v for k, v in NUMS.items() if v}


def other_table():
    L = ["\\begin{table}[t]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{3pt}",
         "\\begin{tabular}{@{}lccc@{}}", "\\toprule"]
    for bi, (title, blk, brows, cols) in enumerate(OTHER_BLOCKS):
        G = block_grid(blk, brows, cols)
        marks = [rank_marks([g[2][j][0] if g[2][j] else None for g in G], cols[j][1]) for j in range(len(cols))]
        if bi:
            L.append("\\midrule")
        L.append(HEAD + f"\\textbf{{{title}}} & " + " & ".join(c[2] for c in cols) + "\\\\")
        L.append("\\midrule")
        for i, (lab, arm, g) in enumerate(G):
            L.append((OURS_ROW if "SilentGuard (ours)" in lab else "") + f"{lab} & " +
                     " & ".join(cell(g[j], marks[j].get(i)) for j in range(len(cols))) + "\\\\")
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{\\textbf{The other benchmarks}, each with the methods run on it (mean over 7 open models, $\\pm$ "
          "standard error, \\%). Retail: our count benchmark on a second schema. Real APIs: agents given slightly "
          "wrong documentation; right docs: \\% correct with the right documentation. Typed: tool calls with a strict "
          "parameter schema, as in function calling. ToolMaze \\citep{zhu2026whentoolsfail}: \\% task success under "
          "silent faults, loud (explicit) errors and no fault; OM: Outcome Monitors; blind retry was not run on loud "
          "errors. Bold: best; underlined: second.}",
          "\\label{tab:other}", "\\end{table}"]
    write("tab_other.tex", L)


# ── Table 2: per model ───────────────────────────────────────────────────────
def permodel_table():
    L = ["\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{3pt}",
         "\\resizebox{\\columnwidth}{!}{%", "\\begin{tabular}{@{}lcccccc@{}}", "\\toprule", HEAD,
         " & \\multicolumn{3}{c}{\\textbf{AgentOrchBench} (wrong $\\downarrow$)} & \\multicolumn{3}{c}{\\textbf{ToolMaze} (success $\\uparrow$)}\\\\",
         HEAD + "\\cmidrule(lr){2-4}\\cmidrule(l){5-7}",
         HEAD + "\\textbf{Model} & None & Ours & $\\Delta$ & C+OM & Ours & $\\Delta$\\\\", "\\midrule"]
    da, dt = [], []
    for m in FREE:
        a0 = (D["aob"]["none"].get(m) or {})
        a1 = (D["aob"]["sgp"].get(m) or {})
        t0 = (D["toolmaze"]["omcl"].get(m) or {}).get("silent") if m in FREE else None
        t1 = (D["toolmaze"]["ours"].get(m) or {}).get("silent") if m in FREE else None
        w0, w1 = a0.get("wrong"), a1.get("wrong")
        done = min(a0.get("done") or 0, a1.get("done") or 0)
        name = NAME[m] + ("" if done >= 0.97 else f" ({100 * done:.0f}\\%)")
        f = lambda x: "--" if x is None else f"{x:.1f}"
        dA = None if (w0 is None or w1 is None) else w1 - w0
        dT = None if (t0 is None or t1 is None) else t1 - t0
        if m in FREE and dA is not None:
            da.append(dA)
        if m in FREE and dT is not None:
            dt.append(dT)
        g = lambda x, lower: "--" if x is None else f"\\textcolor{{{'gain' if (x < 0) == lower else 'loss'}}}{{${x:+.1f}$}}"
        if m == OAI[0]:
            L.append("\\midrule")
            L.append("\\multicolumn{7}{@{}l}{\\textit{Partial runs (finished share in brackets)}}\\\\")
        B = lambda x, win: f"\\textbf{{{f(x)}}}" if win else f(x)
        aw = w0 is not None and w1 is not None and w1 <= w0
        tw = t0 is not None and t1 is not None and t1 >= t0
        L.append(f"{name} & {B(w0, not aw and w0 is not None)} & {B(w1, aw)} & {g(dA, True)} & "
                 f"{B(t0, not tw and t0 is not None)} & {B(t1, tw)} & {g(dT, False)}\\\\")
    L += ["\\midrule",
          f"\\textit{{Ours better}} & \\multicolumn{{3}}{{c}}{{{sum(x < 0 for x in da)}/{len(da)}, $p={signflip(da):.3f}$}} & "
          f"\\multicolumn{{3}}{{c}}{{{sum(x > 0 for x in dt)}/{len(dt)}, $p={signflip(dt):.3f}$}}\\\\",
          "\\bottomrule", "\\end{tabular}}",
          "\\caption{\\textbf{The gain holds for every model on AgentOrchBench and for 5 of 7 on ToolMaze.} "
          "AgentOrchBench: \\% wrong answers under silent faults, no guard against SilentGuard. ToolMaze: \\% task "
          "success under silent faults, the strongest published mix (Checklist + Outcome Monitors, C+OM) against "
          "SilentGuard. $\\Delta$: ours minus the other, computed before rounding (green: ours better; red: ours worse; bold: "
          "the better of the pair). $p$: exact sign-flip test over the 7 models.}",
          "\\label{tab:permodel}", "\\end{table}"]
    write("tab_permodel.tex", L)
    return {"aob": [sum(x < 0 for x in da), len(da), signflip(da)], "tm": [sum(x > 0 for x in dt), len(dt), signflip(dt)]}


# ── Table 3: real-API probes ─────────────────────────────────────────────────
APISHORT = {"NCBI E-utilities (gene)": "NCBI gene", "GitHub repository search": "GitHub search",
            "GBIF occurrences": "GBIF", "ChEMBL molecules": "ChEMBL", "ClinicalTrials.gov v2": "ClinicalTrials.gov",
            "openFDA drug events": "openFDA"}


def realapi_probe_table():
    apis, fire = D["realapi_probe"]["apis"], D["realapi_probe"]["fire"]
    order = {"silent": 0, "mixed": 1, "rejected": 2}
    rows = sorted(apis, key=lambda a: (order.get(a["behaviour"], 3), -(a["inflation"] or 0)))
    word = {"silent": "ignored", "mixed": "mixed", "rejected": "rejected"}
    L = ["\\begin{table}[h]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{4pt}",
         "\\begin{tabular}{@{}lrrrc@{}}", "\\toprule", HEAD,
         "\\textbf{API} & \\textbf{Returned} & \\textbf{Correct} & \\textbf{Over} & \\textbf{Caught}\\\\",
         "\\midrule"]
    group = None
    GT = {"silent": "Misnamed filter ignored (HTTP 200)", "mixed": "Mixed", "rejected": "Misnamed filter rejected (error)"}
    for a in rows:
        if a["behaviour"] != group:
            if group:
                L.append("\\midrule")
            group = a["behaviour"]
            L.append(f"\\multicolumn{{5}}{{@{{}}l}}{{\\textit{{{GT.get(group, group)}}}}}\\\\")
        fk = next((v for k, v in fire.items() if k.startswith(a["api"][:12])), None)
        chk = "--" if not fk or fk[1] == 0 else f"{fk[0]}/{fk[1]}"
        infl = f"{a['inflation']:.0f}$\\times$" if a["inflation"] else "--"
        doc = "--" if a["doc"] is None else f"{a['doc']:,}"
        L.append(f"{APISHORT.get(a['api'], a['api'])} & {a['base']:,} & {doc} & {infl} & {chk}\\\\".replace(",", "{,}"))
    nsil = sum(a["behaviour"] == "silent" for a in apis)
    L += ["\\bottomrule", "\\end{tabular}",
          f"\\caption{{Real-API probes. Of {len(apis)} public APIs, {nsil} answer a request with a plausibly misnamed "
          "filter with HTTP 200 and the unfiltered count (ignored); the others reject the unknown parameter with an "
          "error (rejected). Returned: the count with the misnamed filter, or the unfiltered count when the API "
          "rejected it; Correct: the count with the documented filter; Over: returned / correct. Caught: misnamed "
          "filters flagged by the value-swap check (swap the filter's value; an ignored filter returns the same "
          "count). --: not applicable, or not measured (rate limit).}",
          "\\label{tab:realapis}", "\\end{table}"]
    write("tab_realapis.tex", L)


# ── Table 4: harness response ablation (AgentOrchBench + retail) ─────────────
ABL = [("Warning (last condition)", "advisory"), ("Warning", "advisory_nonum"), ("Warning + tip", "advisory_hint"),
       ("Auto-repair", "repair3"), ("Answer gate (last condition)", "gate2"), ("Enforce", "enforce2"),
       ("\\textbf{SilentGuard}", "sgp")]


def ablation_table():
    cols = [("aob", "wrong", True), ("aob", "correct", False), ("aob", "unknown", None), ("aob", "clean", False),
            ("retail", "wrong", True), ("retail", "clean", False)]
    grid = [[stats(per_model(D[b], arm, k)) for b, k, _ in cols] for _, arm in ABL]
    marks = [rank_marks([g[j][0] if g[j] else None for g in grid], cols[j][2]) if cols[j][2] is not None else {}
             for j in range(len(cols))]
    L = ["\\begin{table*}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{7pt}",
         "\\begin{tabular}{@{}lcccccc@{}}", "\\toprule", HEAD,
         " & \\multicolumn{4}{c}{\\textbf{AgentOrchBench}} & \\multicolumn{2}{c}{\\textbf{Retail}}\\\\",
         HEAD + "\\cmidrule(lr){2-5}\\cmidrule(l){6-7}",
         HEAD + "\\textbf{Harness action} & Wrong$\\downarrow$ & Correct$\\uparrow$ & Unknown & Clean$\\uparrow$ & Wrong$\\downarrow$ & Clean$\\uparrow$\\\\",
         "\\midrule"]
    for i, (lab, arm) in enumerate(ABL):
        row = " & ".join(cell(grid[i][j], marks[j].get(i), se=False) for j in range(len(cols)))
        L.append((OURS_ROW if arm == "sgp" else "") + f"{lab} & {row}\\\\")
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{\\textbf{Ablation of the harness action after a failed check} (mean over 7 open models, \\%). "
          "Warnings keep wrong answers high; auto-repair gives the most correct answers but releases wrong ones "
          "marked as verified (Table~\\ref{tab:repair}); SilentGuard withholds only what an exact check proves wrong. "
          "Last condition: these two earlier arms check the last condition only; all other rows use the full check. "
          "Warning + tip: the warning also names the recovery tools; Enforce: only checked counts are accepted. "
          "Unknown: the answer says the value cannot be determined; --: not run on retail. Bold: best; underlined: second.}",
          "\\label{tab:ablation}", "\\end{table*}"]
    write("tab_ablation.tex", L)


# ── Table 5: blind retry vs re-check ─────────────────────────────────────────
def retry_table():
    rows = [("No re-check", "omcl", None), ("Blind retry", "blindr", "retry2"), ("\\textbf{SilentGuard}", "ours", "sgp")]
    ra = D.get("repeated_actions") or {}
    cols = []
    for lab, tm, api in rows:
        p3 = stats(per_model(D["toolmaze"], tm, "p3"))
        two = stats([D["toolmaze_twocall"][m].get(tm) for m in FREE])
        p4 = stats(per_model(D["toolmaze"], tm, "p4"))
        aw = stats(per_model(D["realapi"], api, "wrong")) if api else None
        rep = ra.get("blindr" if tm == "blindr" else "ours") if tm != "omcl" else None
        cols.append([p3, two, p4, aw, (rep[0], 0.0) if rep else None])
    lower = [False, False, False, True, True]
    marks = [rank_marks([c[j][0] if c[j] else None for c in cols], lower[j]) for j in range(5)]
    L = ["\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{2.6pt}",
         "\\resizebox{\\columnwidth}{!}{%", "\\begin{tabular}{@{}lccccc@{}}", "\\toprule", HEAD,
         " & \\multicolumn{3}{c}{\\textbf{ToolMaze} success$\\uparrow$} & \\textbf{Real APIs} & \\textbf{Repeated}\\\\",
         HEAD + "\\cmidrule(lr){2-4}",
         HEAD + "\\textbf{Harness} & 1-call fault & 2-call fault & Lasting & wrong$\\downarrow$ & actions$\\downarrow$\\\\",
         "\\midrule"]
    for i, (lab, tm, _) in enumerate(rows):
        cs = [cell(cols[i][j], marks[j].get(i), se=False, dec=(2 if j == 4 else 1)) for j in range(5)]
        L.append((OURS_ROW if tm == "ours" else "") + f"{lab} & " + " & ".join(cs) + "\\\\")
    L += ["\\bottomrule", "\\end{tabular}}",
          "\\caption{\\textbf{Blind retry helps only when the fault clears.} Blind retry (the no re-check harness plus "
          "every call sent twice, second result kept) clears a short fault that lasts one or two calls. On a fault "
          "that lasts (ToolMaze P4) it adds little over the same harness without retry, and on real APIs, which ignore "
          "a misnamed filter every time, it does not reduce wrong answers. It also repeats bookings, payments and "
          "messages. No re-check: the checklist with Outcome Monitors, without retry. Mean over 7 open models. "
          "Values are \\% except repeated actions (repeated action calls per ToolMaze run). Bold: best; underlined: second.}",
          "\\label{tab:retry}", "\\end{table}"]
    write("tab_retry.tex", L)


# ── Table 6: repair on unseen faults ─────────────────────────────────────────
SHORT = {"Word-stem text match": "Word-stem match", "NOT drops empty fields": "NOT drops empty",
         "Partial shard timeout": "Partial shard", "Tag-join double counting": "Tag-join double",
         "Cursor ties skip rows": "Cursor skips ties", "Lossy index, no recheck": "Lossy index"}


def repair_table():
    rows, vbw = D["oracle"]["rows"], D["oracle"]["vbw"]
    L = ["\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{3pt}",
         "\\resizebox{\\columnwidth}{!}{%", "\\begin{tabular}{@{}lcccc@{}}", "\\toprule", HEAD,
         "\\textbf{Unseen fault} & \\textbf{Fired} & \\textbf{No guard} & \\textbf{SilentGuard} & \\textbf{Auto-repair}\\\\",
         "\\midrule"]
    tot = {"n": [0, 0, 0], "s": [0, 0, 0], "r": [0, 0, 0]}
    c = lambda x: f"{x[2]}/{x[0]}/{x[1]}"
    for f, n, fired, none, enf, rep in rows:
        L.append(f"{SHORT.get(f, f)} & {fired}/{n} & {c(none)} & {c(enf)} & {c(rep)}\\\\")
        for k, x in (("n", none), ("s", enf), ("r", rep)):
            tot[k] = [a + b for a, b in zip(tot[k], x)]
    L += ["\\midrule", f"Total & & {c(tot['n'])} & {OURSCELL(c(tot['s']))} & {c(tot['r'])}\\\\", "\\bottomrule", "\\end{tabular}}",
          "\\caption{\\textbf{Auto-repair returns wrong answers marked as verified.} Six fault types written after the "
          "method was frozen, run through the harness alone (36 count tasks each; cells: wrong / correct / unknown). "
          "Fired: tasks where the count check fired. When its check fires, SilentGuard (bold) never releases a wrong "
          "answer.}",
          "\\label{tab:repair}", "\\end{table}"]
    write("tab_repair.tex", L)


def OURSCELL(x):
    return f"\\textbf{{{x}}}"


# ── Table 7: cost ────────────────────────────────────────────────────────────
COST_KEEP = ["No guard", "Caution prompt", "Self-verification", "Blind retry (control)", "LLM judge",
             "Checklist [Sun et al. 2024]", "Reviewer [Ta et al. 2026]", "Outcome Monitors [Panthi & Abdelfattah 2026]",
             "Enforce", "SilentGuard (ours)"]
COST_NAME = {"Blind retry (control)": "Blind retry", "Checklist [Sun et al. 2024]": "Checklist",
             "Reviewer [Ta et al. 2026]": "Reviewer", "Outcome Monitors [Panthi & Abdelfattah 2026]": "Outcome Monitors",
             "SilentGuard (ours)": "\\textbf{SilentGuard}"}


def cost_table(full=False):
    cost = dict((l, v) for l, v in D["cost"])
    keep = [l for l, _ in D["cost"]] if full else COST_KEEP
    L = ["\\begin{table}[t]" if not full else "\\begin{table}[h]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{2.5pt}",
         "\\begin{tabular}{@{}lrrrr@{}}", "\\toprule", HEAD,
         " & \\textbf{Tool} & & \\textbf{Time} & \\textbf{Extra LLM}\\\\",
         HEAD + "\\textbf{Method} & \\textbf{calls} & \\textbf{Tokens} & \\textbf{(s)} & \\textbf{calls}\\\\", "\\midrule"]
    for l in keep:
        v = cost.get(l)
        if not v or v[0] is None:
            continue
        name = SHORTLAB.get(l, COST_NAME.get(l, l)).replace("&", "\\&")
        L.append((OURS_ROW if "SilentGuard" in l else "") + f"{name} & {v[0]:.1f} & {v[1]:,.0f} & {v[2]:.1f} & {v[4]:.1f}\\\\".replace(",", "{,}"))
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{\\textbf{Cost per faulted run} on AgentOrchBench (mean over 7 open models). Columns give agent tool calls, LLM tokens, wall time and extra LLM "
          "calls (judge or reviewer) on top of the agent's own. SilentGuard's checks run in the harness and need no "
          "extra LLM call." + ("" if full else " All methods: Table~\\ref{tab:app-cost}.") + "}",
          "\\label{tab:app-cost}" if full else "\\label{tab:cost}", "\\end{table}"]
    write("tab_cost_full.tex" if full else "tab_cost.tex", L)


# ── Table 8: training ────────────────────────────────────────────────────────
def training_table(full=False):
    tr = D["training"]
    f = lambda x: "--" if x is None else f"{x:.1f}"
    if not full:
        L = ["\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{3pt}",
             "\\begin{tabular}{@{}llccc@{}}", "\\toprule", HEAD,
             "\\textbf{Model} & \\textbf{Training} & \\textbf{Seen}$\\downarrow$ & \\textbf{Unseen}$\\downarrow$ & \\textbf{Clean}$\\uparrow$\\\\",
             "\\midrule"]
        short = {"Base model": "None", "SFT to refuse (trained on drop-last)": "SFT, refuse", "SFT to repair": "SFT, repair",
                 "RL (DAPO, false-equation penalty)": "RL (DAPO)"}
        last = None
        for fam, lab, r0, _ in tr:
            if last and fam != last:
                L.append("\\midrule")
            L.append(f"{fam if fam != last else ''} & {short.get(lab, lab)} & {f(r0[0])} & {f(r0[1])} & {f(r0[4])}\\\\")
            last = fam
        L += ["\\bottomrule", "\\end{tabular}",
              "\\caption{\\textbf{Training the model does not replace the harness.} \\% wrong answers on the fault used "
              "in training (seen, drop-last) and on five fault types never trained on (unseen); clean: \\% correct with "
              "no fault. Training to refuse transfers to unseen faults; training to repair does not. Mistral-7B base cannot use the tools, "
              "so its 0\\% wrong is not a success. Full results: Table~\\ref{tab:app-training}.}",
              "\\label{tab:training}", "\\end{table}"]
        write("tab_training.tex", L)
        return
    L = ["\\begin{table*}[h]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{4pt}",
         "\\resizebox{\\textwidth}{!}{%", "\\begin{tabular}{@{}llccccc|ccc@{}}", "\\toprule", HEAD,
         " & & \\multicolumn{5}{c|}{Greedy decoding} & \\multicolumn{3}{c}{Sampling, $T=0.7$, 3 seeds}\\\\",
         HEAD + "\\textbf{Model} & \\textbf{Training} & Seen$\\downarrow$ & Unseen$\\downarrow$ & Consistent lie$\\downarrow$ & "
         "Reworded$\\downarrow$ & Clean$\\uparrow$ & Seen$\\downarrow$ & Unseen$\\downarrow$ & Clean$\\uparrow$\\\\", "\\midrule"]
    for fam, lab, r0, r7 in tr:
        L.append(f"{fam} & {lab} & " + " & ".join(f(x) for x in r0) + " & " + " & ".join(f(x) for x in r7) + "\\\\")
    L += ["\\bottomrule", "\\end{tabular}}",
          "\\caption{Training the model itself, full results (\\% wrong; clean: \\% correct). Seen: drop-last, the "
          "fault used in training. Unseen: cap-total, AND$\\to$OR, ignore-NOT, off-by-one and prefix. Consistent lie: "
          "a wrong answer the API backs up in every query. Reworded: the seen fault with reworded tasks. With sampling, "
          "unseen covers AND$\\to$OR and off-by-one only. SFT: "
          "supervised fine-tuning; RL: reinforcement learning with DAPO. Mistral-7B base cannot use the tools, so its "
          "0\\% wrong is not a success.}",
          "\\label{tab:app-training}", "\\end{table*}"]
    write("tab_training_full.tex", L)


# ── Appendix tables ──────────────────────────────────────────────────────────
FN = {"drop_last": "Drop last", "cap_total": "Cap total", "or_semantics": "AND$\\to$OR", "drop_field": "Ignore field",
      "ignore_negation": "Ignore NOT", "off_by_one": "Off by one"}


def app_aob_full():
    labels = D["aob_labels"]
    F2 = {"drop_last": ("Drop", "last"), "cap_total": ("Cap", "total"), "or_semantics": ("AND", "$\\to$OR"),
          "drop_field": ("Ignore", "field"), "ignore_negation": ("Ignore", "NOT"), "off_by_one": ("Off by", "one")}
    nf = len(D["faults"])
    L = ["\\begin{table*}[t]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{2.7pt}",
         "\\begin{tabular}{@{}l" + "c" * nf + "cccc@{}}", "\\toprule", HEAD,
         " & \\multicolumn{" + str(nf) + "}{c}{\\textbf{Wrong answers by fault type}$\\downarrow$} & \\multicolumn{4}{c}{\\textbf{All faults}}\\\\",
         HEAD + f"\\cmidrule(lr){{2-{1 + nf}}}\\cmidrule(l){{{2 + nf}-{5 + nf}}}",
         HEAD + " & " + " & ".join(F2[f][0] for f in D["faults"]) + " & & & & \\\\",
         HEAD + "\\textbf{Method} & " + " & ".join(F2[f][1] for f in D["faults"]) + " & Wrong$\\downarrow$ & Correct$\\uparrow$ & Unknown & Clean$\\uparrow$\\\\",
         "\\midrule"]
    for arm, lab in labels:
        byf = [stats([((D["aob"][arm].get(m) or {}).get("by_fault") or {}).get(f) for m in ms(arm, "by_fault")]) for f in D["faults"]]
        tot = [stats(per_model(D["aob"], arm, k, ms(arm, k))) for k in ("wrong", "correct", "unknown", "clean")]
        lab = SHORTLAB.get(lab, lab)
        L.append((OURS_ROW if arm == "sgp" else "") + f"{lab} & " + " & ".join(cell(x, se=False) for x in byf) + " & "
                 + " & ".join(cell(x) for x in tot) + "\\\\")
    six_rows = [SHORTLAB.get(lab, lab) for arm, lab in labels if ms(arm, "by_fault") is SIX]
    six_note = (" By-fault columns of " + ", ".join(six_rows) + ": 6 models (no breakdown by fault for Nemotron-3-Ultra).") if six_rows else ""
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{AgentOrchBench, all methods (mean over 7 open models, \\%; $\\pm$ standard error). Wrong, correct and "
          "unknown are shares of the runs with a silent fault; clean is \\% correct with no fault. Auto-repair has the "
          "most correct answers but its wrong answers are released as verified (Table~\\ref{tab:repair}). Last "
          "condition: the earlier check of the last condition only; all other harness rows use the full check. "
          "No evidence: the judge does not see the consistency queries; OM: Outcome Monitors." + six_note + "}",
          "\\label{tab:app-aob-full}", "\\end{table*}"]
    write("tab_app_aob_full.tex", L)


def app_ci():
    """95% confidence intervals (t over models) for Tables main and other, block by block"""
    t975 = {5: 2.571, 6: 2.447, 12: 2.179, 13: 2.160}

    def ci(blk, arm, k):
        st = stats(per_model(D[blk], arm, k, ms(arm, k)))
        if st is None:
            return "--"
        h = t975[len(ms(arm, k)) - 1] * st[1]
        return f"[{max(0, st[0] - h):.1f}, {min(100, st[0] + h):.1f}]" + six_mark(arm, k)
    L = ["\\begin{table*}[t]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{8pt}",
         "\\begin{tabular}{@{}lcccc@{}}", "\\toprule", HEAD,
         "\\textbf{AgentOrchBench} & " + " & ".join(c[2] for c in AOB_COLS) + "\\\\", "\\midrule"]
    for g, rs in AOB_ROWS:
        for lab, arm in rs:
            lab = {"\\quad + blocking": "Judge, no evidence, + blocking"}.get(lab, lab)
            L.append((OURS_ROW if arm == "sgp" else "") + f"{lab} & " + " & ".join(ci("aob", arm, k) for k, _, _ in AOB_COLS) + "\\\\")
    for title, blk, brows, cols in OTHER_BLOCKS:
        L += ["\\midrule", HEAD + f"\\textbf{{{title}}} & " + " & ".join(c[2] for c in cols) + " & \\\\", "\\midrule"]
        for lab, arm, g in block_grid(blk, brows, cols):
            L.append((OURS_ROW if "SilentGuard (ours)" in lab else "") + f"{lab} & " + " & ".join(ci(blk, arm, k) for k, _, _ in cols) + " & \\\\")
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{95\\% confidence intervals for Tables~\\ref{tab:main} and~\\ref{tab:other} ($t$ interval over models, "
          "clipped to [0, 100]).}", "\\label{tab:app-ci}", "\\end{table*}"]
    write("tab_app_ci.tex", L)


def app_permodel_all():
    arms = [("none", "None"), ("checklist", "Check."), ("reviewer", "Rev."), ("om", "OM"), ("judge_check", "Judge"),
            ("retry2", "Retry"), ("selfverify", "Self-v."), ("enforce2", "Enf."), ("sgp", "\\textbf{Ours}")]
    L = ["\\begin{table*}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{5pt}",
         "\\begin{tabular}{@{}l" + "c" * len(arms) + "@{}}", "\\toprule", HEAD,
         "\\textbf{Model} & " + " & ".join(a[1] for a in arms) + "\\\\", "\\midrule"]
    for m in FREE + ["gpt-4.1", "gpt-4o", "gpt-4o-mini"]:
        vals = [(D["aob"][a].get(m) or {}).get("wrong") for a, _ in arms]
        dn = [(D["aob"][a].get(m) or {}).get("done") or 0 for a, _ in arms]
        mk = rank_marks(vals, True) if m in FREE else {}
        cs = []
        for j, v in enumerate(vals):
            if v is None:
                cs.append("--")
                continue
            t = f"{v:.1f}"
            t = f"\\textbf{{{t}}}" if mk.get(j) == "best" else (f"\\underline{{{t}}}" if mk.get(j) == "second" else t)
            if dn[j] < 0.97:
                t += f"\\,{{\\scriptsize({100 * dn[j]:.0f}\\%)}}"
            cs.append(t)
        if m == "gpt-4.1":
            L.append("\\midrule")
        L.append(f"{NAME[m]} & " + " & ".join(cs) + "\\\\")
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{AgentOrchBench, wrong answers per model and method (\\%). None: no guard; Check.: checklist; Rev.: "
          "reviewer; OM: Outcome Monitors; Judge: LLM judge; Retry: blind retry; Self-v.: self-verification; Enf.: "
          "Enforce; Ours: SilentGuard. Bold: best in the row; underlined: second. The three older GPT models below the "
          "line finished only part of their runs (finished share in brackets) and are not ranked; the GPT-5.6 models "
          "are in Table~\\ref{tab:frontier}.}", "\\label{tab:app-permodel}", "\\end{table*}"]
    write("tab_app_permodel.tex", L)


def app_realapi_models(K):
    """Real APIs per model, open and frontier (the frontier models ran no guard and SilentGuard only)."""
    fr = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
    f = lambda x: "--" if x is None else f"{x:.1f}"
    get = lambda arm, m, k: (D["realapi"][arm].get(m) or {}).get(k)
    L = ["\\begin{table}[h]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{2.5pt}",
         "\\begin{tabular}{@{}lcccc@{}}", "\\toprule", HEAD,
         " & \\textbf{No guard} & \\multicolumn{2}{c}{\\textbf{SilentGuard}} & \\textbf{Right docs}\\\\",
         HEAD + "\\cmidrule(lr){3-4}",
         HEAD + "\\textbf{Model} & Wrong$\\downarrow$ & Wrong$\\downarrow$ & Corr.$\\uparrow$ & Corr.$\\uparrow$\\\\", "\\midrule"]

    def block(models, title):
        cols = [[get("none", m, "wrong") for m in models], [get("sgp", m, "wrong") for m in models],
                [get("sgp", m, "correct") for m in models], [get("none", m, "clean") for m in models]]
        for i, m in enumerate(models):
            L.append(f"{NAME[m].replace('GPT-5.6-', 'GPT-5.6 ')} & " + " & ".join(f(c[i]) for c in cols) + "\\\\")
        mean = [None if any(v is None for v in c) else sum(c) / len(c) for c in cols]
        L.append("\\rowcolor{ours}" + f"\\textit{{{title}}} & " + " & ".join(f(v) for v in mean) + "\\\\")
        return mean
    block(FREE, "Mean, open")
    L.append("\\midrule")
    mf = block(fr, "Mean, frontier")
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{Agents on nine real public APIs, per model (45 runs per model and cell, \\%). No guard and "
          "SilentGuard: agents given slightly wrong documentation. Right docs: \\% correct with no guard and the right "
          "documentation. The frontier models ran these two arms only.}",
          "\\label{tab:app-realapi-models}", "\\end{table}"]
    write("tab_app_realapi_models.tex", L)
    if all(v is not None for v in mf):
        K["frapi-none-wrong"], K["frapi-sgp-wrong"] = f"{mf[0]:.1f}", f"{mf[1]:.1f}"
        K["frapi-sgp-correct"], K["frapi-none-clean"] = f"{mf[2]:.1f}", f"{mf[3]:.1f}"
        K["frapi-none-correct"] = f"{sum(get('none', m, 'correct') for m in fr) / 3:.1f}"


def app_toolmaze_full():
    L = ["\\begin{table}[h]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{3pt}", "\\resizebox{\\columnwidth}{!}{%",
         "\\begin{tabular}{@{}lccccc@{}}", "\\toprule", HEAD,
         "\\textbf{Method} & P3$\\uparrow$ & P4$\\uparrow$ & P3+P4$\\uparrow$ & P0$\\uparrow$ & P1+P2$\\uparrow$\\\\", "\\midrule"]
    lab = dict(D["toolmaze_labels"])
    for arm, _ in D["toolmaze_labels"]:
        name = {"checklist": "Checklist", "om": "Outcome Monitors", "omcl": "Checklist + OM", "blindr": "Blind retry",
                "ours": "\\textbf{SilentGuard}"}[arm]
        cs = [cell(stats(per_model(D["toolmaze"], arm, k))) for k in ("p3", "p4", "silent", "clean", "loud")]
        L.append((OURS_ROW if arm == "ours" else "") + f"{name} & " + " & ".join(cs) + "\\\\")
    del lab
    L += ["\\bottomrule", "\\end{tabular}}",
          "\\caption{ToolMaze, all modes (\\% task success by its own judge, mean $\\pm$ SE over 7 models, 180 fresh "
          "workflows). P3: silent fault on the first call; P4: silent fault on every call; P0: no fault; P1, P2: "
          "explicit errors. Blind retry was not run on P1/P2.}", "\\label{tab:app-toolmaze}", "\\end{table}"]
    write("tab_app_toolmaze.tex", L)


def app_twocall():
    L = ["\\begin{table}[h]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{4pt}",
         "\\resizebox{\\columnwidth}{!}{%", "\\begin{tabular}{@{}lccc@{}}", "\\toprule", HEAD,
         "\\textbf{Model} & \\textbf{No re-check} & \\textbf{Blind retry} & \\textbf{SilentGuard}\\\\", "\\midrule"]
    f = lambda x: "--" if x is None else f"{x:.1f}"
    for m in FREE:
        v = D["toolmaze_twocall"][m]
        L.append(f"{NAME[m]} & {f(v.get('omcl'))} & {f(v.get('blindr'))} & {f(v.get('ours'))}\\\\")
    L += ["\\bottomrule", "\\end{tabular}}",
          "\\caption{ToolMaze short fault lasting two calls, per model (\\% task success, 180 workflows; runs "
          "whose model calls failed at the provider are left out). No re-check: the checklist with Outcome Monitors.}",
          "\\label{tab:app-twocall}", "\\end{table}"]
    write("tab_app_twocall.tex", L)


def app_realapi_agents():
    arms = [("none", "No guard"), ("retry2", "Blind retry"), ("guard", "Value-swap check alone"), ("schema", "Spec check alone"),
            ("sgp", "\\textbf{SilentGuard}")]
    L = ["\\begin{table}[h]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{3pt}", "\\resizebox{\\columnwidth}{!}{%",
         "\\begin{tabular}{@{}lcccc@{}}", "\\toprule", HEAD,
         "\\textbf{Method} & Wrong$\\downarrow$ & Correct$\\uparrow$ & Unknown & Right docs$\\uparrow$\\\\", "\\midrule"]
    for a, lab in arms:
        cs = [cell(stats(per_model(D["realapi"], a, k))) for k in ("wrong", "correct", "unknown", "clean")]
        L.append((OURS_ROW if a == "sgp" else "") + f"{lab} & " + " & ".join(cs) + "\\\\")
    L += ["\\bottomrule", "\\end{tabular}}",
          "\\caption{Agents on nine real public APIs, mean $\\pm$ standard error over 7 open models (45 runs per model, "
          "method and documentation). Wrong, correct, unknown: \\% of runs with slightly wrong documentation (the rest "
          "ran out of turns with no answer); right docs: \\% correct with the right documentation. Value-swap check alone: the "
          "harness withholds a count whose filter had no effect, with no spec check; it leaves the fewest wrong answers "
          "but few correct ones. The spec check does most of the work, because it rejects the misnamed filter and the "
          "agent rewrites the query.}", "\\label{tab:app-realapi}", "\\end{table}"]
    write("tab_app_realapi.tex", L)


def app_risk():
    pens = (0, 1, 3, 10)
    L = ["\\begin{table}[h]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{3.5pt}",
         "\\begin{tabular}{@{}lcc" + "c" * len(pens) + "@{}}", "\\toprule", HEAD,
         " & & & \\multicolumn{" + str(len(pens)) + "}{c}{\\textbf{Score, penalty} $\\lambda$}\\\\",
         HEAD + "\\textbf{Method} & Correct & Wrong & " + " & ".join(f"$\\lambda={p}$" for p in pens) + "\\\\", "\\midrule"]
    for lab, c, w in D["risk"]:
        if c is None:
            continue
        sc = [c - p * w for p in pens]
        L.append((OURS_ROW if "SilentGuard" in lab else "") + f"{lab} & {c:.1f} & {w:.1f} & " + " & ".join(f"{x:.1f}" for x in sc) + "\\\\")
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{Which method is best depends on what a wrong answer costs. Score = correct $-\\ \\lambda \\times$ "
          "wrong (\\% of faulted runs, AgentOrchBench, 7 models). With $\\lambda=0$ (a wrong answer costs no more than "
          "no answer) auto-repair wins; from $\\lambda=1$ on, SilentGuard wins.}", "\\label{tab:app-risk}", "\\end{table}"]
    write("tab_app_risk.tex", L)


def m_(blk, arm, key):
    st = stats(per_model(D[blk], arm, key))
    return None if st is None else st[0]


def prereg_table():
    """Pre-registered predictions of the confirmatory experiments (reeval/EXTERNAL_PLAN.md), each checked
    against the data here, so the outcome column cannot drift from the numbers."""
    pooled = D.get("pooled") or {}
    two = {a_: stats([D["toolmaze_twocall"][m].get(a_) for m in FREE]) for a_ in ("ours", "blindr", "omcl")}
    two = {k: (v[0] if v else None) for k, v in two.items()}
    rep = D.get("repeated_actions") or {}

    def fewer(base):
        v = pooled.get(f"aob_sgp_vs_{base}_wrong") or {}
        return v.get("sgp_fewer_wrong"), v.get("models")
    P = [
        ("A1", "SilentGuard $>$ blind retry on lasting faults (ToolMaze P4)",
         f"{m_('toolmaze', 'ours', 'p4'):.1f} vs {m_('toolmaze', 'blindr', 'p4'):.1f}",
         m_("toolmaze", "ours", "p4") > m_("toolmaze", "blindr", "p4")),
        ("A2", "SilentGuard $\\geq$ blind retry $-2$ on one-time faults (P3)",
         f"{m_('toolmaze', 'ours', 'p3'):.1f} vs {m_('toolmaze', 'blindr', 'p3'):.1f}",
         m_("toolmaze", "ours", "p3") >= m_("toolmaze", "blindr", "p3") - 2),
        ("A3", "Blind retry repeats action calls",
         f"{rep['blindr'][0]:.2f} vs {rep['ours'][0]:.2f} per run" if rep.get("blindr") and rep.get("ours") else "--",
         bool(rep.get("blindr") and rep.get("ours") and rep["blindr"][0] > rep["ours"][0])),
        ("B1", "Loud faults: SilentGuard within 3 points of Checklist + OM",
         f"{m_('toolmaze', 'ours', 'loud'):.1f} vs {m_('toolmaze', 'omcl', 'loud'):.1f}",
         abs(m_("toolmaze", "ours", "loud") - m_("toolmaze", "omcl", "loud")) <= 3),
        ("C1", "AgentOrchBench wrong within 5 points of Enforce",
         f"{m_('aob', 'sgp', 'wrong'):.1f} vs {m_('aob', 'enforce2', 'wrong'):.1f}",
         abs(m_("aob", "sgp", "wrong") - m_("aob", "enforce2", "wrong")) <= 5),
        ("C2", "AgentOrchBench correct $\\geq$ answer gate $-5$",
         f"{m_('aob', 'sgp', 'correct'):.1f} vs {m_('aob', 'gate2', 'correct'):.1f}",
         m_("aob", "sgp", "correct") >= m_("aob", "gate2", "correct") - 5),
        ("D1", "Checklist, Reviewer, Outcome Monitors all above 40\\% wrong",
         ", ".join(f"{m_('aob', a_, 'wrong'):.1f}" for a_ in ("checklist", "reviewer", "om")),
         all(m_("aob", a_, "wrong") > 40 for a_ in ("checklist", "reviewer", "om"))),
        ("D2", "SilentGuard fewer wrong than each published method on 7/7 models",
         ", ".join(f"{fewer(a_)[0]}/{fewer(a_)[1]}" for a_ in ("checklist", "reviewer", "om")),
         all(fewer(a_)[0] == fewer(a_)[1] == len(FREE) for a_ in ("checklist", "reviewer", "om"))),
        ("D3", "Outcome Monitors within 10 points of no guard",
         f"{m_('aob', 'om', 'wrong'):.1f} vs {m_('aob', 'none', 'wrong'):.1f}",
         abs(m_("aob", "om", "wrong") - m_("aob", "none", "wrong")) <= 10),
        ("E1", "Retail: wrong and clean within 5 points of Enforce",
         f"{m_('retail', 'sgp', 'wrong'):.1f}/{m_('retail', 'sgp', 'clean'):.1f} vs {m_('retail', 'enforce2', 'wrong'):.1f}/{m_('retail', 'enforce2', 'clean'):.1f}",
         m_("retail", "sgp", "wrong") <= m_("retail", "enforce2", "wrong") + 5
         and m_("retail", "sgp", "clean") >= m_("retail", "enforce2", "clean") - 5),
        ("F1", "Real APIs (first 4): wrong $\\leq$ best single check $+3$, and correct $\\geq$ spec check $-5$",
         f"{m_('realapi4', 'sgp', 'wrong'):.1f} vs {min(m_('realapi4', 'guard', 'wrong'), m_('realapi4', 'schema', 'wrong')):.1f}; "
         f"{m_('realapi4', 'sgp', 'correct'):.1f} vs {m_('realapi4', 'schema', 'correct'):.1f}",
         m_("realapi4", "sgp", "wrong") <= min(m_("realapi4", "guard", "wrong"), m_("realapi4", "schema", "wrong")) + 3
         and m_("realapi4", "sgp", "correct") >= m_("realapi4", "schema", "correct") - 5),
        ("R1", "AgentOrchBench: blind retry within 5 points of no guard",
         f"{m_('aob', 'retry2', 'wrong'):.1f} vs {m_('aob', 'none', 'wrong'):.1f}",
         abs(m_("aob", "retry2", "wrong") - m_("aob", "none", "wrong")) <= 5),
        ("R2", "AgentOrchBench: re-query within 10 points of no guard",
         f"{m_('aob', 'requery', 'wrong'):.1f} vs {m_('aob', 'none', 'wrong'):.1f}",
         abs(m_("aob", "requery", "wrong") - m_("aob", "none", "wrong")) <= 10),
        ("R3", "SilentGuard fewer wrong than blind retry on 7/7 models",
         ", ".join(f"{fewer(a_)[0]}/{fewer(a_)[1]}" for a_ in ("retry2",)),
         fewer("retry2")[0] == fewer("retry2")[1] == len(FREE)),
        ("BR1", "Two-call fault: blind retry within 5 points of no re-check",
         f"{two['blindr']:.1f} vs {two['omcl']:.1f}" if two["blindr"] is not None else "--",
         two["blindr"] is not None and abs(two["blindr"] - two["omcl"]) <= 5),
        ("BR2", "Two-call fault: SilentGuard $\\geq$ blind retry",
         f"{two['ours']:.1f} vs {two['blindr']:.1f}" if two["ours"] is not None else "--",
         two["ours"] is not None and two["ours"] >= two["blindr"]),
        ("BR3", "Real APIs (first 4): blind retry within 5 points of no guard",
         f"{m_('realapi4', 'retry2', 'wrong'):.1f} vs {m_('realapi4', 'none', 'wrong'):.1f}",
         abs(m_("realapi4", "retry2", "wrong") - m_("realapi4", "none", "wrong")) <= 5),
    ]
    rec = dict(D.get("rec6") or [])
    full, enf, nog, rep_ = rec.get("SilentGuard"), rec.get("Checked answers only (SilentGuard rule)"), rec.get("No guard"), rec.get("Auto-repair")
    if full and enf and nog and rep_ and all(x is not None for x in full):
        P += [("L1", "Lists: SilentGuard wrong within 3 points of checked lists only (seen, unseen)",
               f"{full[0]:.1f}/{full[3]:.1f} vs {enf[0]:.1f}/{enf[3]:.1f}",
               abs(full[0] - enf[0]) <= 3 and abs(full[3] - enf[3]) <= 3),
              ("L2", "Lists: SilentGuard fewer wrong than no guard and auto-repair (seen, unseen)",
               f"{full[0]:.1f}/{full[3]:.1f} vs {nog[0]:.1f}/{nog[3]:.1f}, {rep_[0]:.1f}/{rep_[3]:.1f}",
               full[0] < min(nog[0], rep_[0]) and full[3] < min(nog[3], rep_[3])),
              ("L3", "Lists: clean correct within 5 points of no guard",
               f"{full[6]:.1f} vs {nog[6]:.1f}", abs(full[6] - nog[6]) <= 5)]
    jb = [(D["aob"].get("judge_block", {}).get(m) or {}).get("wrong") for m in SIX]
    sg6 = [(D["aob"]["sgp"].get(m) or {}).get("wrong") for m in SIX]
    if all(x is not None for x in jb + sg6):
        ob = stats(per_model(D["aob"], "om_block", "wrong", SIX))
        om6 = stats(per_model(D["aob"], "om", "wrong", SIX))
        jbc = stats(per_model(D["aob"], "judge_block", "clean", SIX))
        nc6 = stats(per_model(D["aob"], "none", "clean", SIX))
        P += [("K1", "Judge + blocking has more wrong answers than SilentGuard on 6/6 models",
               f"{sum(a > b for a, b in zip(jb, sg6))}/6", all(a > b for a, b in zip(jb, sg6))),
              ("K2", "Outcome Monitors + blocking within 10 points of Outcome Monitors (wrong)",
               f"{ob[0]:.1f} vs {om6[0]:.1f}", abs(ob[0] - om6[0]) <= 10),
              ("K3", "Judge + blocking clean correct at least 10 points below no guard",
               f"{jbc[0]:.1f} vs {nc6[0]:.1f}", jbc[0] <= nc6[0] - 10)]
        jp = stats(per_model(D["aob"], "judge_block_plain", "wrong", SIX))
        jpc = stats(per_model(D["aob"], "judge_block_plain", "clean", SIX))
        jbw = stats(jb)
        if jp and jpc:
            P += [("P1", "Judge + blocking without check results: at least 20 points more wrong than with them",
                   f"{jp[0]:.1f} vs {jbw[0]:.1f}", jp[0] >= jbw[0] + 20),
                  ("P2", "Judge + blocking without check results: clean correct within 10 points of with them",
                   f"{jpc[0]:.1f} vs {jbc[0]:.1f}", abs(jpc[0] - jbc[0]) <= 10)]
    w6 = lambda arm: stats(per_model(D["aob"], arm, "wrong", SIX))
    if w6("judge_advise_plain") and w6("advisory_abstain"):
        cells = {a: w6(a)[0] for a in ("none", "judge_advise_plain", "judge_block_plain", "judge_check", "judge_block",
                                       "advisory", "advisory_abstain")}
        P += [("X1", "Judge without check results, advising: wrong within 10 points of no guard",
               f"{cells['judge_advise_plain']:.1f} vs {cells['none']:.1f}", abs(cells["judge_advise_plain"] - cells["none"]) <= 10),
              ("X2", "Judge in four settings: evidence and blocking at least 40 points below each other cell",
               f"{cells['judge_block']:.1f} vs {cells['judge_advise_plain']:.1f}, {cells['judge_block_plain']:.1f}, {cells['judge_check']:.1f}",
               all(cells[a] - cells["judge_block"] >= 40 for a in ("judge_advise_plain", "judge_block_plain", "judge_check"))),
              ("X3", "Warning (last-condition check) + permission: fewer wrong than the warning alone, but more than 20\\%",
               f"{cells['advisory_abstain']:.1f} vs {cells['advisory']:.1f}",
               cells["advisory_abstain"] < cells["advisory"] and cells["advisory_abstain"] > 20)]
    if w6("advisory_nonum_abstain"):
        yb = [((D["aob"]["advisory_nonum_abstain"].get(m) or {}).get("by_fault") or {}).get("drop_field") for m in SIX]
        ya = per_model(D["aob"], "advisory_nonum_abstain", "wrong", SIX)
        ys = per_model(D["aob"], "sgp", "wrong", SIX)
        ymean = sum(ya) / len(ya)
        P += [("Y1", "Full-check warning + permission: wrong on ignore field below 15\\%",
               f"{sum(yb) / len(yb):.1f}", sum(yb) / len(yb) < 15),
              ("Y2", "Full-check warning + permission: overall wrong between 3 and 8\\%", f"{ymean:.1f}", 3 <= ymean <= 8),
              ("Y3", "Full-check warning + permission: more wrong than SilentGuard on at least 4 of 6 models",
               f"{sum(a > b for a, b in zip(ya, ys))}/6", sum(a > b for a, b in zip(ya, ys)) >= 4)]
    ty, sc = m_("realapi", "typed", "wrong"), m_("realapi", "schema", "wrong")
    if ty is not None and sc is not None:
        by = D.get("realapi_by_api") or {}
        def api_w(api):
            c = by.get(f"{api}|typed") or {}
            n = sum(c.values())
            return 100 * c.get("wrong_num", 0) / n if n else None
        q = [api_w(x) for x in ("ncbi", "uniprot", "europepmc")]
        t1 = per_model(D["realapi"], "typed", "wrong")
        t2 = per_model(D["realapi"], "typed_sgp", "wrong")
        nle = sum(1 for x, y in zip(t2, t1) if x is not None and y is not None and x <= y)
        P += [("T1", "Typed tool calls: wrong within 5 points of the spec check (real APIs)", f"{ty:.1f} vs {sc:.1f}",
               abs(ty - sc) <= 5),
              ("T2", "Typed tool calls: more than 30\\% wrong on NCBI, UniProt and Europe PMC",
               ", ".join("--" if x is None else f"{x:.0f}" for x in q), all(x is not None and x > 30 for x in q)),
              ("T3", "Typed calls + SilentGuard: no more wrong than typed calls on at least 6 of 7 models", f"{nle}/7",
               nle >= 6)]
    pbf = Path(sys.argv[1]).parent / "e3i_badformat_summary.json"
    if pbf.exists():
        BF = json.load(open(pbf))
        if all(BF["n_models"].get(k, 0) >= 6 for k in ("none|G", "schema|G", "sgp|G", "none|A")):
            M = BF["mean"]; n_, s_, g_, a_ = M["none|G"], M["schema|G"], M["sgp|G"], M["none|A"]
            P += [("V1", "Wrong value format (iNaturalist): no guard wrong at least 80\\%", f"{n_['wrong']:.1f}", n_["wrong"] >= 80),
                  ("V2", "Wrong value format: spec check alone within 10 points of no guard (wrong)",
                   f"{s_['wrong']:.1f} vs {n_['wrong']:.1f}", abs(s_["wrong"] - n_["wrong"]) <= 10),
                  ("V3", "Wrong value format: SilentGuard wrong at most 20\\% and at least 50 points below the spec check",
                   f"{g_['wrong']:.1f} vs {s_['wrong']:.1f}", g_["wrong"] <= 20 and s_["wrong"] - g_["wrong"] >= 50),
                  ("V4", "Wrong value format: SilentGuard correct at least 50\\%", f"{g_['correct']:.1f}", g_["correct"] >= 50),
                  ("V5", "Wrong value format: no guard with the right docs correct at least 85\\%", f"{a_['correct']:.1f}",
                   a_["correct"] >= 85)]
    L = ["\\begin{table*}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{4pt}",
         "\\begin{tabular}{@{}lp{9.2cm}lc@{}}", "\\toprule", HEAD,
         "\\textbf{ID} & \\textbf{Prediction, written before the run} & \\textbf{Observed} & \\textbf{Outcome}\\\\", "\\midrule"]
    for pid, text, obs, ok in P:
        out = "held" if ok else "\\textbf{\\textcolor{loss}{failed}}"
        L.append(f"{pid} & {text} & {obs} & {out}\\\\")
    held = sum(1 for p in P if p[3])
    L += ["\\bottomrule", "\\end{tabular}",
          f"\\caption{{Predictions of the confirmatory experiments, written down before each run, and their outcomes "
          f"({held} of {len(P)} held). All were written, with the method frozen by file hashes, before the runs; failed "
          "predictions (bold red) are reported and discussed in the text (blind retry, \\S\\ref{sec:res-retry}). IDs are "
          "the labels used in the prediction log. Observed values are means over the 7 open models (\\%), or the number of "
          "models with fewer wrong answers. Rows L, K, P, X and Y use the 6 models the predictions were written for, so "
          "their no-guard values differ slightly from the 7-model tables. Within $x$ points: the absolute "
          "difference is at most $x$. The rows from L down are control runs added after the first results; each was written "
          "before its own run.}", "\\label{tab:app-prereg}", "\\end{table*}"]
    write("tab_app_prereg.tex", L)
    return held, len(P)


SEEN6 = ["drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]


def extra_tables(K):
    """Tables from the extra analyses (each optional): list answers (rec), check budget (harness only),
    gate cutoff sensitivity. Adds the numbers the text cites to K."""
    here = Path(sys.argv[1]).parent
    # list answers
    rec = D.get("rec")
    if isinstance(rec, list) and rec:
        f = lambda x: "--" if x is None else f"{x:.1f}"
        rows = {lab: v for lab, v in rec}
        order = [("No guard", "No guard"), ("Auto-repair", "Auto-repair"),
                 ("Checked answers only (SilentGuard rule)", "Checked lists only")]
        if rows.get("SilentGuard") and all(x is not None for x in rows["SilentGuard"]):
            order.append(("SilentGuard", "\\textbf{SilentGuard}"))
        vals = [rows.get(k) for k, _ in order]
        cols = [(0, True), (2, False), (3, True), (5, False), (6, False)]
        marks = [rank_marks([v[c] if v else None for v in vals], lower) for c, lower in cols]
        L = ["\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{2.3pt}",
             "\\begin{tabular}{@{}lccccc@{}}", "\\toprule", HEAD,
             " & \\multicolumn{2}{c}{\\textbf{Seen faults}} & \\multicolumn{2}{c}{\\textbf{Unseen faults}} & \\textbf{Clean}\\\\",
             HEAD + "\\cmidrule(lr){2-3}\\cmidrule(lr){4-5}\\cmidrule(l){6-6}",
             HEAD + "\\textbf{Method} & Wrong$\\downarrow$ & Corr.$\\uparrow$ & Wrong$\\downarrow$ & Corr.$\\uparrow$ & Corr.$\\uparrow$\\\\", "\\midrule"]
        for i, ((key, lab), v) in enumerate(zip(order, vals)):
            cs = []
            for j, (c, _) in enumerate(cols):
                t = f(v[c] if v else None)
                mk = marks[j].get(i)
                cs.append(f"\\textbf{{{t}}}" if mk == "best" else (f"\\underline{{{t}}}" if mk == "second" else t))
            L.append((OURS_ROW if "SilentGuard" in lab else "") + f"{lab} & " + " & ".join(cs) + "\\\\")
        L += ["\\bottomrule", "\\end{tabular}",
              "\\caption{\\textbf{The same rule gives the fewest wrong lists.} The 30 tasks ask for the IDs of all "
              "matching records. Six fault types were seen in design and five were written later (unseen). The record checks test that "
              "every returned record meets the filter, that no record repeats, and that the list matches the reported "
              "total and the count identity. Checked lists only: the answer rule alone; SilentGuard adds the re-check, "
              "withholding and the result checklist. Corr.: correct. Bold: best; underlined: second. Mean over 7 open "
              "models, \\% of runs.}",
              "\\label{tab:records}", "\\end{table}"]
        write("tab_records.tex", L)
        nr = rows.get("No guard")
        sg = rows.get("Checked answers only (SilentGuard rule)")
        rp = rows.get("Auto-repair")
        full = rows.get("SilentGuard")
        if full and all(x is not None for x in full):
            K.update({"rec-full-seen-wrong": f"{full[0]:.1f}", "rec-full-unseen-wrong": f"{full[3]:.1f}",
                      "rec-full-seen-correct": f"{full[2]:.1f}", "rec-full-unseen-correct": f"{full[5]:.1f}",
                      "rec-full-clean": f"{full[6]:.1f}", "rec-none-clean": f"{nr[6]:.1f}"})
        if nr and sg and rp:
            K.update({"rec-none-seen-wrong": f"{nr[0]:.1f}", "rec-none-unseen-wrong": f"{nr[3]:.1f}",
                      "rec-sg-seen-wrong": f"{sg[0]:.1f}", "rec-sg-unseen-wrong": f"{sg[3]:.1f}",
                      "rec-rep-seen-wrong": f"{rp[0]:.1f}", "rec-rep-unseen-wrong": f"{rp[3]:.1f}",
                      "rec-rep-unseen-correct": f"{rp[5]:.1f}", "rec-sg-unseen-correct": f"{sg[5]:.1f}"})
    # check budget, harness only
    p = here / "oracle_budget_summary.json"
    if p.exists():
        B = json.load(open(p))
        strats = [("fixed-last", "Last condition only"), ("random-1", "1 random condition"),
                  ("random-2", "2 random conditions"), ("sequential", "All, stop at first failure"),
                  ("all", "\\textbf{All conditions (SilentGuard)}")]
        L = ["\\begin{table*}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{7pt}",
             "\\begin{tabular}{@{}lccccc@{}}", "\\toprule", HEAD,
             " & \\multicolumn{2}{c}{\\textbf{Extra queries}} & \\multicolumn{2}{c}{\\textbf{Seen faults}} & \\textbf{Check-aware}\\\\",
             HEAD + "\\cmidrule(lr){2-3}\\cmidrule(lr){4-5}",
             HEAD + "\\textbf{Positions checked} & clean & faulted & caught$\\uparrow$ & wrong$\\downarrow$ & caught$\\uparrow$\\\\",
             "\\midrule"]
        nw = st_mean([B[f]["none"]["wrong"] for f in SEEN6])
        L.append(f"No check & 0.0 & 0.0 & 0.0 & {nw:.1f} & 0.0\\\\")
        for s, lab in strats:
            fc = st_mean([B[f][s]["extra_calls"] for f in SEEN6])
            det = st_mean([B[f][s]["detect"] for f in SEEN6])
            wr = st_mean([B[f][s]["wrong"] for f in SEEN6])
            L.append((OURS_ROW if s == "all" else "") + f"{lab} & {B['off'][s]['extra_calls']:.1f} & {fc:.1f} & {det:.1f} & {wr:.1f} & "
                     f"{B['consistent_lie'][s]['detect']:.1f}\\\\")
            key = {"fixed-last": "last", "random-1": "r1", "random-2": "r2", "sequential": "seq", "all": "all"}[s]
            K[f"bud-{key}-caught"] = f"{det:.1f}"
            K[f"bud-{key}-wrong"] = f"{wr:.1f}"
            K[f"bud-{key}-calls"] = f"{fc:.1f}"
            K[f"bud-{key}-lie"] = f"{B['consistent_lie'][s]['detect']:.1f}"
        K["bud-none-wrong"] = f"{nw:.1f}"
        K["bud-clean-fa"] = f"{max(B['off'][s]['detect'] for s, _ in strats):.1f}"
        K["bud-dropfield-r1"] = f"{B['drop_field']['random-1']['detect']:.1f}"
        K["bud-dropfield-all"] = f"{B['drop_field']['all']['detect']:.1f}"
        lie_max = max(B["consistent_lie"][s_]["detect"] for s_, _ in strats)
        L += ["\\bottomrule", "\\end{tabular}",
              "\\caption{How many conditions to check, measured on the harness alone (no model; 36 count tasks per fault "
              "type; detection averaged exactly over every choice of positions). Extra queries per filter call on clean "
              "and faulted runs; caught: share of faulted results the check flags; wrong: share released with a wrong "
              "count under the answer rule. No budget raised a false alarm on clean runs. Check-aware: the consistent "
              f"lie, an API built to keep the identity true; no budget catches more than {lie_max:.1f}\\% of it.}}",
              "\\label{tab:app-budget}", "\\end{table*}"]
        write("tab_app_budget.tex", L)
    # gate cutoff sensitivity
    p = here / "gate_sensitivity.json"
    if p.exists():
        G = json.load(open(p))
        L = ["\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{4pt}",
             "\\begin{tabular}{@{}cccc@{}}", "\\toprule", HEAD,
             "\\textbf{Cutoff} $\\tau$ & \\textbf{Min.\\ support} & \\textbf{Withheld (\\%)} & \\textbf{Changed}\\\\", "\\midrule"]
        for tau, v in G["by_tau"].items():
            t = float(tau)
            lab = f"{100 * t:g}\\%"
            L.append(f"{lab} & $\\geq${v['min_support_for_exact']} & {v['exact_share']:.1f} & " + f"{v['changed_vs_1pct']:,}".replace(",", "{,}") + "\\\\")
        L += ["\\bottomrule", "\\end{tabular}",
              f"\\caption{{Sensitivity to the cutoff on ToolMaze. A learned check is treated as exact when "
              f"$1/(n+2)\\le\\tau$, that is, when it held on at least the listed number of clean examples. Over all "
              + f"{G['decisions']:,}".replace(",", "{,}") + f" withhold-or-warn decisions logged in our ToolMaze runs, the largest support is {G['max_support']}, "
              f"so every cutoff below {100 / (G['max_support'] + 2):.2f}\\% (1/{G['max_support'] + 2}) gives the same decisions as 1\\% (Changed: decisions that differ from 1\\%). On AgentOrchBench and real APIs every "
              "check is exact and the cutoff has no effect.}", "\\label{tab:app-gate}", "\\end{table}"]
        write("tab_app_gate.tex", L)
        K["gate-maxsupport"] = str(G["max_support"])
        K["gate-decisions"] = f"{G['decisions']:,}".replace(",", "{,}")
        K["gate-safe-tau"] = f"{100 / (G['max_support'] + 2):.2f}"


def more_tables(K):
    here = Path(sys.argv[1]).parent
    NICE = {"ncbi": "NCBI gene", "uniprot": "UniProtKB", "europepmc": "Europe PMC", "gbif": "GBIF", "zenodo": "Zenodo",
            "chembl": "ChEMBL", "arxiv": "arXiv", "datacite": "DataCite", "inat": "iNaturalist"}
    by = D.get("realapi_by_api") or {}
    if by:
        L = ["\\begin{table}[h]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{3pt}",
             "\\begin{tabular}{@{}lccccc@{}}", "\\toprule", HEAD,
             " & \\multicolumn{3}{c}{\\textbf{Wrong}$\\downarrow$} & \\multicolumn{2}{c}{\\textbf{Correct}$\\uparrow$}\\\\",
             HEAD + "\\cmidrule(lr){2-4}\\cmidrule(l){5-6}",
             HEAD + "\\textbf{API} & None & Retry & Ours & None & Ours\\\\", "\\midrule"]
        for i, api in enumerate(["ncbi", "uniprot", "europepmc", "gbif", "zenodo", "chembl", "arxiv", "datacite", "inat"]):
            if i == 4:
                L.append("\\midrule")
            def r(arm, o):
                c = by.get(f"{api}|{arm}") or {}
                n = sum(c.values())
                return "--" if not n else f"{100 * c.get(o, 0) / n:.0f}"
            L.append(f"{NICE[api]} & {r('none', 'wrong_num')} & {r('retry2', 'wrong_num')} & \\textbf{{{r('sgp', 'wrong_num')}}} & "
                     f"{r('none', 'correct')} & \\textbf{{{r('sgp', 'correct')}}}\\\\")
        L += ["\\bottomrule", "\\end{tabular}",
              "\\caption{Real-API agent study per API, wrong documentation (\\% of runs, 7 models pooled, 35 runs per API "
              "and arm). The four APIs above the line were chosen before the method was frozen; the five below were added "
              "later, with the method unchanged. On NCBI, UniProt, Europe PMC and arXiv the correct filter goes inside a query-language "
              "string that the wrong documentation never mentions, so after the spec check rejects the misnamed parameter "
              "the agent must find that syntax itself; on the others the correct parameter is in the list of allowed "
              "parameters that the spec check returns, which is the most likely reason for the gap. None: no guard. Retry: "
              "blind retry. Ours: SilentGuard (bold).}", "\\label{tab:app-realapi-per}", "\\end{table}"]
        write("tab_app_realapi_per.tex", L)
    p = here / "e3d_fault_kinds_final.json"
    if p.exists():
        E = {r["api"]: r for r in json.load(open(p))}
        probe = {a["api"]: a["behaviour"] for a in D["realapi_probe"]["apis"]}
        W = {"silent": "\\textbf{\\textcolor{loss}{ignored}}", "rejected": "rejected", "mixed": "mixed",
             "ignored": "\\textbf{\\textcolor{loss}{ignored}}", "applied": "applied",
             "capped": "\\textbf{\\textcolor{loss}{capped}}", "honored": "honored", "unreachable": "--"}
        L = ["\\begin{table}[h]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{4pt}",
             "\\begin{tabular}{@{}lccc@{}}", "\\toprule", HEAD,
             " & \\textbf{Misnamed} & \\textbf{Bad filter} & \\textbf{Page over}\\\\",
             HEAD + "\\textbf{API} & \\textbf{filter} & \\textbf{value} & \\textbf{limit}\\\\", "\\midrule"]
        n_any = n_cap = n_bad = n_reach = 0
        for api, beh in probe.items():
            e = E.get(api, {})
            bv = (e.get("bad_value") or {}).get("class", "unreachable")
            pc = (e.get("page_cap") or {}).get("class", "unreachable")
            silent_any = beh == "silent" or bv == "ignored" or pc == "capped"
            n_any += silent_any
            if bv != "unreachable":
                n_reach += 1
                n_cap += pc == "capped"
                n_bad += bv == "ignored"
            L.append(f"{APISHORT.get(api, api)} & {W.get(beh, beh)} & {W.get(bv, bv)} & {W.get(pc, pc)}\\\\")
        L += ["\\bottomrule", "\\end{tabular}",
              "\\caption{Three kinds of silent fault in real public APIs (read-only probes). Misnamed filter: a plausibly "
              "misnamed filter parameter; rejected means the API returned an error. Bad filter value: a typo in the value of a valid filter (for example a bad year or "
              "date); ignored means the count equals the unfiltered one, applied means the value was used as given (usually "
              "zero results), which is correct. Page over limit: a request for more records per page than the API allows; "
              "capped means HTTP 200 with fewer records than asked and no error; honored means the API returned the "
              "larger page. Bold red: silent. --: rate-limited, not measured.}",
              "\\label{tab:app-faultkinds}", "\\end{table}"]
        write("tab_app_faultkinds.tex", L)
        K["fk-any"], K["fk-n"] = str(n_any), str(len(probe))
        K["fk-cap"], K["fk-bad"], K["fk-reach"] = str(n_cap), str(n_bad), str(n_reach)
        fire = D["realapi_probe"]["fire"]
        K["vs-caught"], K["vs-n"] = str(sum(v[0] for v in fire.values())), str(sum(v[1] for v in fire.values()))
    p = here / "e3f_badvalue_probe.json"
    if p.exists():
        BV = json.load(open(p))
        info = {"europepmc": ("Europe PMC", "query string", "count identity"), "github": ("GitHub search", "query string", "count identity"),
                "inat": ("iNaturalist", "parameter", "value swap")}
        L = ["\\begin{table}[h]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{2.3pt}",
             "\\begin{tabular}{@{}llcccc@{}}", "\\toprule", HEAD,
             "\\textbf{API} & \\textbf{Typo in} & \\textbf{Ignored} & \\textbf{Over} & \\textbf{Fired} & \\textbf{FA}\\\\", "\\midrule"]
        fired = lambda r: bool(r.get("identity_fires_on_bad") or r.get("probe_flags_on_bad"))
        fa = lambda r: bool(r.get("identity_fires_on_good") or r.get("probe_flags_on_good"))
        for api, (name, where, chk) in info.items():
            rs = [r for r in BV if r["api"] == api]
            ov = [r["N_bad"] / r["N_good"] for r in rs]
            fmt = lambda x: f"{x:.0f}" if x >= 10 else f"{x:.1f}"
            L.append(f"{name} & {where} & {sum(r['silently_ignored'] for r in rs)}/{len(rs)} & {fmt(min(ov))}--{fmt(max(ov))}$\\times$ & "
                     f"{sum(fired(r) for r in rs)}/{len(rs)} & {sum(fa(r) for r in rs)}/{len(rs)}\\\\")
        L += ["\\bottomrule", "\\end{tabular}",
              "\\caption{A typo in the \\emph{value} of a valid filter, on the three APIs that silently ignore it (five probes "
              "each, read-only, no model). The parameter name is valid, so the spec check passes. Ignored: the API returned "
              "the unfiltered count; Over: returned / correct. Fired: the count identity (Europe PMC, GitHub, where the "
              "value sits inside the query string) or the value-swap check (iNaturalist) flagged the result; FA: the same "
              "check fired on the correctly spelled query.}", "\\label{tab:app-badvalue}", "\\end{table}"]
        write("tab_app_badvalue.tex", L)
        K["bv-n"], K["bv-ignored"] = str(len(BV)), str(sum(r["silently_ignored"] for r in BV))
        K["bv-fired"], K["bv-fa"] = str(sum(fired(r) for r in BV)), str(sum(fa(r) for r in BV))
    p = here / "e3i_badformat_summary.json"
    if p.exists():
        BF = json.load(open(p))
        if all(BF["n_models"].get(k, 0) >= 6 for k in ("none|G", "schema|G", "sgp|G", "none|A")):
            M = BF["mean"]; nm = BF["n_models"]["sgp|G"]
            L = ["\\begin{table}[h]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{4pt}",
                 "\\begin{tabular}{@{}lccc@{}}", "\\toprule", HEAD,
                 "\\textbf{Method} & Wrong$\\downarrow$ & Correct$\\uparrow$ & Unknown\\\\", "\\midrule"]
            for key, lab in (("none|G", "No guard"), ("schema|G", "Spec check alone"), ("sgp|G", "\\textbf{SilentGuard}")):
                L.append((OURS_ROW if key == "sgp|G" else "") + f"{lab} & {M[key]['wrong']:.1f} & {M[key]['correct']:.1f} & {M[key]['unknown']:.1f}\\\\")
            sg = BF["per_model"]["sgp|G"]
            L += ["\\bottomrule", "\\end{tabular}",
                  "\\caption{A wrong value format in the documentation of a real API (iNaturalist date filter, 5 tasks, mean "
                  f"over {nm} open models, \\% of runs). The documentation gives the date as DD/MM/YYYY, which the API silently "
                  "ignores; the parameter name is valid, so the spec check passes. With the right documentation an "
                  f"unguarded agent is correct {M['none|A']['correct']:.1f}\\% of the time. Predictions for this run are V1 to V5 in "
                  "Table~\\ref{tab:app-prereg}.}", "\\label{tab:app-badformat}", "\\end{table}"]
            write("tab_app_badformat.tex", L)
            for key, tag in (("none|G", "none"), ("schema|G", "schema"), ("sgp|G", "sgp")):
                for k in ("wrong", "correct", "unknown"):
                    K[f"bf-{tag}-{k}"] = f"{M[key][k]:.1f}"
            K["bf-none-cleancorrect"] = f"{M['none|A']['correct']:.1f}"
            K["bf-sgp-wrong-max"] = f"{max(v['wrong'] for v in sg.values()):.0f}"
            K["bf-models"] = str(nm)
    p = here / "checkability_summary.json"
    if p.exists():
        C = json.load(open(p))
        nice = {"search_list": "Search or list", "get_by_id": "Get by ID", "aggregate": "Aggregate", "count": "Count",
                "paginate": "Pagination", "other_read": "Other read", "write": "Write"}
        L = ["\\begin{table}[h]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{4pt}",
             "\\begin{tabular}{@{}lcc@{}}", "\\toprule", HEAD,
             "\\textbf{Operation kind} & \\textbf{Operations} & \\textbf{Exact check}\\\\", "\\midrule"]
        for k, v in sorted(C["by_kind"].items(), key=lambda kv: -kv[1]["n"]):
            L.append(f"{nice.get(k, k)} & {v['n']} & {v['exact']}\\\\")
        L += ["\\midrule", f"Total & {C['n_ops']} & {C['exact']}\\\\", "\\bottomrule", "\\end{tabular}",
              f"\\caption{{Which checks real API operations allow, read from the documentation of {C['n_apis']} public "
              f"APIs. Exact check: the operation allows a record-membership check ({C['membership']} operations), the "
              f"count identity ({C['count_identity']}) or a value swap ({C['value_swap']}). All {C['spec']} have a "
              "published parameter list, so the spec check applies to every one. The APIs are those of "
              "Table~\\ref{tab:realapis} without GitHub and openFDA. Write operations were not counted as "
              "allowing the write-then-read check of Table~\\ref{tab:checks}. These are well-documented APIs; the "
              "share will be lower for undocumented or free-text tools.}", "\\label{tab:app-checkability}", "\\end{table}"]
        write("tab_app_checkability.tex", L)
        K["chk-n"], K["chk-exact"], K["chk-apis"] = str(C["n_ops"]), str(C["exact"]), str(C["n_apis"])
        K["chk-exact-pct"] = f"{100 * C['exact'] / C['n_ops']:.0f}"


def review_extras(K):
    here = Path(sys.argv[1]).parent
    for arm in ("typed", "typed_sgp"):
        st = stats(per_model(D["realapi"], arm, "wrong"))
        if st:
            K[f"api-{arm.replace('_', '')}-wrong"] = f"{st[0]:.1f}"
    for arm, key in (("judge_block", "judgeblock"), ("om_block", "omblock"), ("judge_block_plain", "judgeblockplain")):
        st = stats(per_model(D["aob"], arm, "wrong", ms(arm, "wrong")))
        if st:
            K[f"aob-{key}-wrong"] = f"{st[0]:.1f}"
            cl = stats(per_model(D["aob"], arm, "clean", ms(arm, "clean")))
            K[f"aob-{key}-clean"] = f"{cl[0]:.1f}" if cl else "--"
    p = here / "seeing_vs_acting.json"
    if p.exists():
        S = json.load(open(p))
        K["sva-judge-flag"] = f"{S['judge_check']['flag_rate_fault']:.1f}"
        K["sva-judge-wrong"] = f"{S['judge_check']['wrong_after_flag']:.1f}"
        K["sva-judge-clean"] = f"{S['judge_check']['flag_rate_clean']:.1f}"
        K["sva-adv-wrong"] = f"{S['advisory']['wrong_after_flag']:.1f}"
        K["sva-om-fault"] = f"{S['om']['flag_rate_fault']:.1f}"
        K["sva-om-clean"] = f"{S['om']['flag_rate_clean']:.1f}"
    p = here / "bootstrap_ci.json"
    if p.exists():
        Bt = json.load(open(p))
        t = Bt["tm_success_ours_vs_omcl"]
        K["boot-tm-lo"], K["boot-tm-hi"] = f"{t['lo']:.1f}", f"{t['hi']:.1f}"
        NICE = [("aob_wrong_sgp_vs_none", "AgentOrchBench", "wrong", "No guard"),
                ("aob_wrong_sgp_vs_selfverify", "AgentOrchBench", "wrong", "Self-verification"),
                ("aob_wrong_sgp_vs_judge_check", "AgentOrchBench", "wrong", "LLM judge"),
                ("aob_wrong_sgp_vs_om", "AgentOrchBench", "wrong", "Outcome Monitors"),
                ("aob_wrong_sgp_vs_retry2", "AgentOrchBench", "wrong", "Blind retry"),
                ("aob_wrong_sgp_vs_enforce2", "AgentOrchBench", "wrong", "Enforce"),
                ("aob_correct_sgp_vs_none", "AgentOrchBench", "correct", "No guard"),
                ("aob_correct_sgp_vs_repair3", "AgentOrchBench", "correct", "Auto-repair"),
                ("api_wrong_sgp_vs_none", "Real APIs (9)", "wrong", "No guard"),
                ("api_wrong_sgp_vs_retry2", "Real APIs (9)", "wrong", "Blind retry"),
                ("api_wrong_sgp_vs_schema", "Real APIs (9)", "wrong", "Spec check alone"),
                ("api_correct_sgp_vs_none", "Real APIs (9)", "correct", "No guard"),
                ("api_correct_sgp_vs_schema", "Real APIs (9)", "correct", "Spec check alone"),
                ("tm_success_ours_vs_omcl", "ToolMaze", "success", "Checklist + OM"),
                ("tm_success_ours_vs_om", "ToolMaze", "success", "Outcome Monitors"),
                ("tm_success_ours_vs_checklist", "ToolMaze", "success", "Checklist")]
        L = ["\\begin{table}[h]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{4pt}",
             "\\begin{tabular}{@{}llc@{}}", "\\toprule", HEAD,
             "\\textbf{Measure} & \\textbf{Against} & \\textbf{Difference [95\\% interval]}\\\\", "\\midrule"]
        last = None
        for key, bench, meas, other in NICE:
            v = Bt.get(key)
            if not v:
                continue
            if bench != last:
                if last:
                    L.append("\\midrule")
                L.append(f"\\multicolumn{{3}}{{@{{}}l}}{{\\textit{{{bench}}}}}\\\\")
            L.append(f"{meas} & {other} & ${v['diff']:+.1f}$ [${v['lo']:+.1f}$, ${v['hi']:+.1f}$]\\\\")
            last = bench
        L += ["\\bottomrule", "\\end{tabular}",
              "\\caption{Task-cluster bootstrap for the key differences (SilentGuard minus the other method, in "
              "percentage points, computed before rounding). We used 2{,}000 resamples of tasks, the same draw for "
              "every model and method, and the mean over 7 open models. An interval that excludes 0 means the difference holds across "
              "tasks, not only on average. OM: Outcome Monitors.}", "\\label{tab:app-boot}", "\\end{table}"]
        write("tab_app_boot.tex", L)


def risk_extras(K):
    """Where does withholding pay? score = correct - lambda * wrong; lambda* = the cost of a wrong answer (in units of a
    correct one) above which SilentGuard scores higher than the other method."""
    here = Path(sys.argv[1]).parent
    rows = []
    def add(setting, items):
        sg = items["SilentGuard"]
        for name, (c, w) in items.items():
            if name == "SilentGuard":
                continue
            lam = None if w <= sg[1] else max(0.0, (c - sg[0]) / (w - sg[1]))
            rows.append((setting, name, c, w, sg[0], sg[1], lam))
    a = lambda arm, k: m_("aob", arm, k)
    add("AgentOrchBench, seen faults (7 models)",
        {"No guard": (a("none", "correct"), a("none", "wrong")), "Auto-repair": (a("repair3", "correct"), a("repair3", "wrong")),
         "SilentGuard": (a("sgp", "correct"), a("sgp", "wrong"))})
    rec = dict(D.get("rec") or [])
    if rec.get("SilentGuard") and all(x is not None for x in rec["SilentGuard"]):
        for setting, ci, wi in (("Lists, seen faults (7 models)", 2, 0), ("Lists, unseen faults (7 models)", 5, 3)):
            add(setting, {"No guard": (rec["No guard"][ci], rec["No guard"][wi]),
                          "Auto-repair": (rec["Auto-repair"][ci], rec["Auto-repair"][wi]),
                          "SilentGuard": (rec["SilentGuard"][ci], rec["SilentGuard"][wi])})
    ob = D["oracle"]["rows"]
    tot = {"n": [0, 0, 0], "s": [0, 0, 0], "r": [0, 0, 0]}
    for f, n, fired, none, enf, rep in ob:
        for k, x in (("n", none), ("s", enf), ("r", rep)):
            tot[k] = [u + v for u, v in zip(tot[k], x)]
    N = sum(tot["n"])
    pc = lambda t: (100 * t[0] / N, 100 * t[2] / N)
    add("Unseen count faults, harness only", {"No guard": pc(tot["n"]), "Auto-repair": pc(tot["r"]), "SilentGuard": pc(tot["s"])})
    L = ["\\begin{table}[h]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{4pt}",
         "\\begin{tabular}{@{}lccc@{}}", "\\toprule", HEAD,
         "\\textbf{Against} & \\textbf{Other} & \\textbf{SilentGuard} & $\\lambda^{*}$\\\\", "\\midrule"]
    last, words = None, set()
    for setting, name, c, w, sc, sw, lam in rows:
        if setting != last:
            if last:
                L.append("\\midrule")
            L.append(f"\\multicolumn{{4}}{{@{{}}l}}{{\\textit{{{setting}}}}}\\\\")
        lt = "always" if lam == 0 else ("never" if lam is None else f"{lam:.1f}")
        words.add(lt if lt in ("always", "never") else "")
        L.append(f"{name} & {c:.1f} / {w:.1f} & {sc:.1f} / {sw:.1f} & {lt}\\\\")
        last = setting
        key = {"AgentOrchBench, seen faults (7 models)": "aob", "Lists, seen faults (7 models)": "listseen",
               "Lists, unseen faults (7 models)": "listunseen", "Unseen count faults, harness only": "unseen"}[setting]
        K[f"lam-{key}-{'repair' if name == 'Auto-repair' else 'none'}"] = lt
    note = ("; always: SilentGuard scores higher for any $\\lambda$" if "always" in words else "") + \
           ("; never: the other method scores higher for any $\\lambda$" if "never" in words else "")
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{Where withholding pays. Cells: \\% correct / \\% wrong of faulted runs. With score $=$ correct "
          "$-\\ \\lambda\\times$ wrong, $\\lambda^{*}$ is the cost of a wrong answer, relative to a correct one, above "
          "which SilentGuard scores higher than the other method (computed from unrounded values" + note + "). Against "
          "no guard $\\lambda^{*}$ is small everywhere. Against auto-repair on count tasks it is large on the fault "
          "types repair was built for and small on fault types no method was built for.}",
          "\\label{tab:app-risk}", "\\end{table}"]
    write("tab_app_risk.tex", L)
    p = here / "ignored_flags.json"
    if p.exists():
        F = json.load(open(p))
        K["ig-adv-mention"] = f"{F['advisory']['mention_pct']:.0f}"
        K["ig-adv-conf"] = f"{F['advisory']['conf_mean']:.2f}"
        K["ig-clean-conf"] = f"{F['advisory']['clean_conf_mean']:.2f}"
        K["ig-judge-mention"] = f"{F['judge_check']['mention_pct']:.0f}"
        K["ig-judge-high"] = f"{F['judge_check']['conf_high_pct']:.0f}"
    p = here / "risk_coverage.json"
    if p.exists():
        RC = json.load(open(p))
        for arm in ("none", "judge_check", "repair3", "enforce2", "sgp", "judge_block", "advisory_nonum_abstain"):
            if arm in RC:
                for pr_ in ("0.25", "0.5", "0.1"):
                    c = RC[arm]["curve"][pr_]
                    tag = {"0.25": "", "0.5": "50", "0.1": "10"}[pr_]
                    K[f"rc{tag}-{arm.replace('_', '')}-cov"] = f"{c['coverage']:.0f}"
                    K[f"rc{tag}-{arm.replace('_', '')}-risk"] = f"{c['risk']:.1f}"
    # honesty numbers next to the headline (review round 3)
    ob = D["oracle"]["rows"]
    tt = {"n": [0, 0, 0], "s": [0, 0, 0]}
    for f_, n_, fired_, none_, enf_, rep_ in ob:
        tt["n"] = [u + v for u, v in zip(tt["n"], none_)]
        tt["s"] = [u + v for u, v in zip(tt["s"], enf_)]
    N_ = sum(tt["n"])
    K["un-none-wrong"], K["un-sgp-wrong"] = f"{100 * tt['n'][2] / N_:.1f}", f"{100 * tt['s'][2] / N_:.1f}"
    K["un-none-correct"], K["un-sgp-correct"] = f"{100 * tt['n'][0] / N_:.1f}", f"{100 * tt['s'][0] / N_:.1f}"
    rec_ = dict(D.get("rec") or [])
    if rec_.get("No guard"):
        K["rec-none-unseen-correct"] = f"{rec_['No guard'][5]:.1f}"
        K["rec-none-seen-correct"] = f"{rec_['No guard'][2]:.1f}"
    by = D.get("realapi_by_api") or {}
    def pooled(apis, arm, o):
        c = collections.Counter()
        for a_ in apis:
            c.update(by.get(f"{a_}|{arm}") or {})
        n = sum(c.values())
        return 100 * c.get(o, 0) / n if n else None
    for tag, apis in (("first4", ["ncbi", "uniprot", "europepmc", "gbif"]), ("later5", ["zenodo", "chembl", "arxiv", "datacite", "inat"])):
        for arm in ("none", "sgp"):
            for o, ok in (("wrong_num", "wrong"), ("correct", "correct")):
                v = pooled(apis, arm, o)
                if v is not None:
                    K[f"api-{tag}-{arm}-{ok}"] = f"{v:.1f}"
    ev = [x for x in per_model(D["aob"], "enforce2", "wrong") if x is not None]
    if ev:
        K["enf-min"], K["enf-max"] = f"{min(ev):.1f}", f"{max(ev):.1f}"
    p = here / "enforce_gap.json"
    if p.exists():
        G = json.load(open(p))
        K["eg-enf-requery"], K["eg-enf-wrong"] = str(G["enforce2"]["requery_until_pass"]), str(G["enforce2"]["wrong"])
        K["eg-sgp-requery"], K["eg-sgp-wrong"] = str(G["sgp"]["requery_until_pass"]), str(G["sgp"]["wrong"])
    c = dict((l, v) for l, v in D["cost"])
    if c.get("LLM judge") and c.get("SilentGuard (ours)"):
        K["cost-judge-tokens"] = f"{c['LLM judge'][1]:,.0f}".replace(",", "{,}")
        K["cost-sgp-tokens"] = f"{c['SilentGuard (ours)'][1]:,.0f}".replace(",", "{,}")
        K["cost-none-tokens"] = f"{c['No guard'][1]:,.0f}".replace(",", "{,}")
    p = here / "e3e_identity_live.json"
    if p.exists():
        I = json.load(open(p))
        K["live-id-n"], K["live-id-exact"] = str(I["n"]), str(I["exact"])


def crossed_table(K):
    """Evidence, permission and enforcement on the same 6 models and 2 seeds (stage cross5 and earlier stages)."""
    def w(arm, key="wrong"):
        v = per_model(D["aob"], arm, key, ms(arm, key))
        return None if any(x is None for x in v) else v
    need = ["none", "judge_advise_plain", "judge_block_plain", "judge_check", "judge_block", "advisory_nonum",
            "advisory_nonum_abstain", "sgp", "advisory", "advisory_abstain"]
    if any(w(a) is None for a in need):
        return
    mean = lambda v: sum(v) / len(v)
    short = {"none": "none", "judge_advise_plain": "jap", "judge_block_plain": "jbp", "judge_check": "jc", "judge_block": "jb",
             "advisory_nonum": "adv", "advisory_nonum_abstain": "abst", "sgp": "sgp", "advisory": "adv1",
             "advisory_abstain": "abst1"}
    for a in need:
        K[f"x6-{short[a]}"] = f"{mean(w(a)):.1f}"
        K[f"x6-{short[a]}-min"] = f"{min(w(a)):.0f}"
        K[f"x6-{short[a]}-max"] = f"{max(w(a)):.0f}"
        K[f"x6-{short[a]}-clean"] = f"{mean(w(a, 'clean')):.1f}"
        K[f"x6-{short[a]}-cap"] = str(math.ceil(max(w(a))))
    rng = lambda v: f"({min(v):.1f}--{max(v):.1f})" if max(v) < 10 else f"({min(v):.0f}--{max(v):.0f})"
    row = lambda lab, a, ours=False: ((OURS_ROW if ours else "") + f"{lab} & " + (f"\\textbf{{{mean(w(a)):.1f}}}" if ours else f"{mean(w(a)):.1f}")
                                      + f" {rng(w(a))} & {mean(w(a, 'clean')):.1f}\\\\")
    L = ["\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{2.5pt}",
         "\\begin{tabular}{@{}lcc@{}}", "\\toprule", HEAD,
         "\\textbf{(a) One LLM judge} & \\textbf{Advises} & \\textbf{Blocks}\\\\", "\\midrule",
         f"Without the check's results & {mean(w('judge_advise_plain')):.1f} & {mean(w('judge_block_plain')):.1f}\\\\",
         f"With the check's results & {mean(w('judge_check')):.1f} & \\cellcolor{{ours}}\\textbf{{{mean(w('judge_block')):.1f}}}\\\\",
         "\\midrule", HEAD + "\\textbf{(b) Acting on the check} & \\textbf{Wrong$\\downarrow$} & \\textbf{Clean$\\uparrow$}\\\\",
         "\\midrule", row("No guard (no check)", "none"), row("Warning", "advisory_nonum"),
         row("Warning + may abstain", "advisory_nonum_abstain"), row("SilentGuard (withholds)", "sgp", True),
         "\\bottomrule", "\\end{tabular}",
         "\\caption{\\textbf{Evidence is necessary, and enforcement makes it reliable.} \\% wrong answers under silent "
         "faults on AgentOrchBench; the same 7 open models and 2 seeds in every cell. (a) One judge (gpt-oss-120b) in four settings. "
         "It does or does not see the results of the count check's extra queries, and its verdict is advice to the agent "
         "(advises) or is enforced by the harness (blocks). (b) Three ways to act on the full count check; may abstain: the prompt allows "
         "the answer that the count cannot be determined. In brackets: lowest and highest "
         "model. Clean: \\% correct with no fault. All methods: Table~\\ref{tab:main}.}",
         "\\label{tab:twobytwo}", "\\end{table}"]
    write("tab_twobytwo.tex", L)


def frontier_table(K):
    """Three frontier models on the six key arms (full runs on Genentech's internal platform, summary file frontier_runs.json),
    next to the mean of the 7 open models computed from our own run files."""
    here = Path(sys.argv[1]).parent
    p = here / "frontier_runs.json"
    if not p.exists():
        return
    Fr = json.load(open(p))
    arms_open = ["none", "selfverify", "advisory_nonum", "advisory_nonum_abstain", "enforce2", "sgp"]

    def open_mean(key):
        return [st_mean(per_model(D["aob"], a, key, ms(a, key))) for a in arms_open]
    rows = [("Open models", open_mean("wrong"), open_mean("clean"))]
    names = {"gpt-5.6-sol": "GPT-5.6 Sol", "gpt-5.6-terra": "GPT-5.6 Terra", "gpt-5.6-luna": "GPT-5.6 Luna"}
    for m, n in names.items():
        rows.append((n, Fr["wrong"][m], Fr["clean"][m]))

    def line(lab, vals, lower):
        best = min(vals) if lower else max(vals)
        cs = [("\\textbf{%.1f}" % v if abs(v - best) < 0.05 else "%.1f" % v) for v in vals]
        cs[-1] = "\\cellcolor{ours}" + cs[-1]
        return lab + " & " + " & ".join(cs) + "\\\\"
    L = ["\\begin{table}[t]", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{2.6pt}",
         "\\begin{tabular}{@{}lcccccc@{}}", "\\toprule", HEAD,
         "\\textbf{Model} & None & Self-v. & Warn & W+A & Enf. & \\textbf{Ours}\\\\", "\\midrule",
         "\\multicolumn{7}{@{}l}{\\textit{Wrong answers under silent faults (\\%, $\\downarrow$)}}\\\\"]
    L += [line(lab, w, True) for lab, w, _ in rows]
    L += ["\\midrule", "\\multicolumn{7}{@{}l}{\\textit{Correct on clean tasks (\\%, $\\uparrow$)}}\\\\"]
    L += [line(lab, c, False) for lab, _, c in rows]
    L += ["\\bottomrule", "\\end{tabular}",
          "\\caption{\\textbf{Frontier models fall for silent faults too. SilentGuard cuts wrong answers the most and "
          "keeps clean accuracy.} AgentOrchBench, six methods on three frontier models (2 seeds) and the mean of the 7 "
          "open models. Self-v.: self-verification, the model runs the count check itself. "
          "Warn: the harness warns. W+A: warning plus permission to abstain. Enf.: Enforce. None: no guard. Ours: "
          "SilentGuard. Bold: best in the row. Means are computed before rounding.}",
          "\\label{tab:frontier}", "\\end{table}"]
    write("tab_frontier.tex", L)
    idx = {a: i for i, a in enumerate(Fr["arms"])}
    fms = list(names)
    for a in Fr["arms"]:
        for key in ("wrong", "clean"):
            v = [Fr[key][m][idx[a]] for m in fms]
            K[f"fr-{a}-{key}"] = f"{sum(v) / len(v):.1f}"
            K[f"fr-{a}-{key}-min"] = f"{min(v):.1f}"
            K[f"fr-{a}-{key}-max"] = f"{max(v):.1f}"
    for m in fms:
        tag = m.split("-")[-1]
        for a in Fr["arms"]:
            K[f"fr-{tag}-{a}-wrong"] = f"{Fr['wrong'][m][idx[a]]:.1f}"
            K[f"fr-{tag}-{a}-clean"] = f"{Fr['clean'][m][idx[a]]:.1f}"
    ow, oc = open_mean("wrong"), open_mean("clean")
    K["open7-permission-wrong"], K["open7-enforce-wrong"] = f"{ow[3]:.1f}", f"{ow[4]:.1f}"
    K["open7-selfverify-clean"], K["open7-none-clean"] = f"{oc[1]:.1f}", f"{oc[0]:.1f}"


def st_mean(xs):
    return sum(xs) / len(xs)


def number_macros(nums, prereg):
    """numbers_tab.tex: every number the text cites that comes from these tables."""
    K = {}
    for k, v in nums["main"].items():
        kk = k.replace("_", "")
        K[f"gain-{kk}"] = f"{abs(v['diff']):.1f}"
        K[f"gainp-{kk}"] = f"{v['p']:.3f}" if v["p"] is not None else "--"
    a, t = nums["permodel"]["aob"], nums["permodel"]["tm"]
    K["pm-aob-wins"], K["pm-aob-n"], K["pm-aob-p"] = str(a[0]), str(a[1]), f"{a[2]:.3f}"
    K["pm-tm-wins"], K["pm-tm-n"], K["pm-tm-p"] = str(t[0]), str(t[1]), f"{t[2]:.3f}"
    ctl = D.get("controls") or {}
    for i, (q, lab, v) in ctl.items():
        if "learned" in (q or "") or lab == "Withhold":
            K["learned-warn" if "Warn" in lab else "learned-withhold"] = f"{v:.1f}"
    K["prereg-held"], K["prereg-n"] = str(prereg[0]), str(prereg[1])
    for arm in ("omcl", "blindr", "ours"):
        for k in ("p3", "p4", "clean"):
            v = m_("toolmaze", arm, k)
            if v is not None:
                K[f"tmx-{arm}-{k}"] = f"{v:.1f}"
    extra_tables(K)
    more_tables(K)
    review_extras(K)
    risk_extras(K)
    crossed_table(K)
    frontier_table(K)
    app_realapi_models(K)
    bp4, op4 = m_("toolmaze", "blindr", "p4"), m_("toolmaze", "omcl", "p4")
    if bp4 is not None and op4 is not None:
        K["tmx-retrygain-p4"] = f"{bp4 - op4:.1f}"
    r7 = dict(D.get("rec") or [])
    if r7.get("SilentGuard") and r7.get("Checked answers only (SilentGuard rule)"):
        a7, b7 = r7["SilentGuard"], r7["Checked answers only (SilentGuard rule)"]
        K["l1-gap7-seen"], K["l1-gap7-unseen"] = f"{abs(a7[0] - b7[0]):.1f}", f"{abs(a7[3] - b7[3]):.1f}"
    prp = Path(sys.argv[1]).parent / "e3g_identity_replay.json"
    if prp.exists():
        RP = json.load(open(prp))
        wr = lambda cond: sum(r["runs_wrong"] for r in RP if cond(r))
        K["rp-wrong"] = str(wr(lambda r: True))
        K["rp-fires"] = str(wr(lambda r: r.get("fires") is True))
        K["rp-holds"] = str(wr(lambda r: r.get("fires") is False))
        K["rp-one"] = str(wr(lambda r: r["n_terms"] < 2))
        K["rp-correct"] = str(sum(r["runs_correct"] for r in RP if r["n_terms"] >= 2))
        K["rp-correct-fires"] = str(sum(r["runs_correct"] for r in RP if r["n_terms"] >= 2 and r.get("fires")))
        K["rp-apis"] = str(len({r["api"] for r in RP}))
    lines = ["% GENERATED by paper_tables_render.py from table_data.json. Do not edit by hand."]
    lines += [f"\\expandafter\\def\\csname num@{k}\\endcsname{{{v}}}" for k, v in sorted(K.items())]
    (OUT / "numbers_tab.tex").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    nums = {"main": main_table(), "permodel": permodel_table()}
    other_table()
    realapi_probe_table()
    ablation_table()
    retry_table()
    repair_table()
    cost_table(full=True)
    training_table(full=True)
    app_aob_full()
    app_ci()
    app_permodel_all()
    app_toolmaze_full()
    app_twocall()
    app_realapi_agents()
    # app_risk() dropped: on seen faults auto-repair scores higher at every penalty; the unseen-fault table is the fair test
    number_macros(nums, prereg_table())
    (OUT / "table_nums.json").write_text(json.dumps(nums, indent=1))
    print("rendered", sorted(p.name for p in OUT.glob("tab_*.tex")))
