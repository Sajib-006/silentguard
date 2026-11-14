#!/usr/bin/env python3
"""Paper figures, drawn at exactly their printed size (one column = 7.7 cm = 3.03 in) from table_data.json and
saved with no cropping, so nothing is scaled when included and all text is 7 pt or larger.   python paper_figures.py <table_data.json> <figures_dir>"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

D = json.load(open(sys.argv[1]))
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
FREE, NAME = D["models"]["free"], D["models"]["name"]
COL = 3.03
BLUE, RED, GRAY, AMBER, INK, INK2, GRID = "#2a78d6", "#e34948", "#b9b8b1", "#e8a317", "#0b0b0b", "#52514e", "#e6e5e0"
plt.rcParams.update({"font.size": 8, "legend.fontsize": 7.5, "hatch.linewidth": 0.5, "figure.constrained_layout.use": True, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "font.family": "serif", "font.serif": ["Times New Roman", "Times", "STIXGeneral", "DejaVu Serif"],
                     "mathtext.fontset": "stix", "pdf.fonttype": 42})


def mean_of(block, arm, key, models=FREE):
    v = [((D[block].get(arm) or {}).get(m) or {}).get(key) for m in models]
    return None if any(x is None for x in v) else st.mean(v)


def save(fig, name):
    fig.savefig(OUT / f"{name}.pdf")
    fig.savefig(OUT / f"{name}.png", dpi=220)
    plt.close(fig)


# F1: answers under silent faults on AgentOrchBench, one stacked bar per method
F1 = [("Baselines", None), ("none", "No guard"), ("skeptic", "Caution prompt"), ("selfverify", "Self-verification"),
      ("requery", "Re-query"), ("retry2", "Blind retry"), ("judge_check", "LLM judge"),
      ("Published methods", None), ("checklist", "Checklist"), ("reviewer", "Reviewer"), ("om", "Outcome Monitors"),
      ("Harness actions", None), ("advisory_nonum", "Warning"), ("advisory_hint", "Warning + tip"),
      ("repair3", "Auto-repair"), ("enforce2", "Enforce"),
      ("Ours", None), ("sgp", "SilentGuard")]
rows = []
for a, lab in F1:
    if lab is None:
        rows.append((a, None, None, None))
    else:
        rows.append((lab, mean_of("aob", a, "wrong"), mean_of("aob", a, "unknown"), mean_of("aob", a, "correct")))
fig, ax = plt.subplots(figsize=(COL, 0.165 * len(rows) + 0.75))
for i, (lab, w, u, c) in enumerate(rows):
    y = len(rows) - 1 - i
    if w is None:
        continue
    ax.barh(y, w, color=RED, height=0.7, edgecolor="white", linewidth=0.6)
    ax.barh(y, u, left=w, color=GRAY, height=0.7, edgecolor="white", linewidth=0.6)
    ax.barh(y, c, left=w + u, color=BLUE, height=0.7, edgecolor="white", linewidth=0.6, hatch="////")
    ax.text(w - 1.5 if w >= 14 else w + 1.5, y, f"{w:.0f}", va="center", ha="right" if w >= 14 else "left",
            color="white" if w >= 14 else INK, fontsize=7)
ax.set_yticks([len(rows) - 1 - i for i in range(len(rows))])
ax.set_yticklabels([r[0] for r in rows])
for t, r in zip(ax.get_yticklabels(), rows):
    if r[1] is None:
        t.set_fontstyle("italic")
        t.set_color(INK2)
    elif "SilentGuard" in r[0]:
        t.set_fontweight("bold")
ax.tick_params(axis="y", length=0)
ax.set_xlim(0, 100)
ax.set_ylim(-0.6, len(rows) - 0.4)
ax.set_xlabel("% of runs with a silent API fault")
ax.grid(axis="x", color=GRID, linewidth=0.5)
ax.set_axisbelow(True)
ax.legend(handles=[Patch(color=RED, label="Wrong"), Patch(color=GRAY, label="Unknown"),
                    Patch(facecolor=BLUE, edgecolor="white", hatch="////", label="Correct")],
           ncol=3, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), handlelength=1.4, columnspacing=1.2)
save(fig, "F1_aob_outcomes")

# F3: real APIs, how far the count is inflated when a filter is ignored
sil = sorted([a for a in D["realapi_probe"]["apis"] if a.get("inflation")], key=lambda a: a["inflation"])
fig, ax = plt.subplots(figsize=(COL, 0.17 * len(sil) + 0.55))
for i, a in enumerate(sil):
    ax.barh(i, a["inflation"], color=RED, height=0.65)
    ax.text(a["inflation"] * 1.1, i, f"{a['inflation']:.0f}$\\times$", va="center", fontsize=7, color=INK)
SHORTAPI = {"NCBI E-utilities (gene)": "NCBI gene", "GitHub repository search": "GitHub search",
            "GBIF occurrences": "GBIF", "ChEMBL molecules": "ChEMBL"}
ax.set_yticks(range(len(sil)))
ax.set_yticklabels([SHORTAPI.get(a["api"], a["api"]) for a in sil])
ax.tick_params(axis="y", length=0)
ax.set_xscale("log")
ax.set_xlim(1, max(a["inflation"] for a in sil) * 4)
ax.set_xticks([1, 10, 100, 1000])
ax.set_xticklabels(["1", "10", "100", "1000"])
ax.set_xlabel("Returned count / correct count (log scale)")
ax.grid(axis="x", color=GRID, linewidth=0.5)
ax.set_axisbelow(True)
save(fig, "F3_realapi_inflation")

# F2: per model, no guard vs SilentGuard (appendix)
pts = [(NAME[m], D["aob"]["none"][m]["wrong"], D["aob"]["sgp"][m]["wrong"]) for m in FREE]
fig, ax = plt.subplots(figsize=(COL, 0.2 * len(pts) + 0.6))
for i, (n, x0, x1) in enumerate(pts):
    y = len(pts) - 1 - i
    ax.plot([x1, x0], [y, y], color=GRID, linewidth=1.6, zorder=1)
    ax.scatter([x0], [y], s=24, facecolor="white", edgecolor=RED, linewidth=1.2, zorder=3)
    ax.scatter([x1], [y], s=24, color=BLUE, zorder=3)
ax.set_yticks(range(len(pts)))
ax.set_yticklabels([p[0] for p in pts][::-1])
ax.tick_params(axis="y", length=0)
ax.set_xlim(-3, 100)
ax.set_xlabel("Wrong answers under silent faults (%)")
ax.grid(axis="x", color=GRID, linewidth=0.5)
ax.set_axisbelow(True)
ax.legend(handles=[plt.Line2D([], [], marker="o", ls="", markerfacecolor="white", markeredgecolor=RED, label="No guard"),
                    plt.Line2D([], [], marker="o", ls="", color=BLUE, label="SilentGuard")],
           frameon=False, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0))
save(fig, "F2_permodel_aob")

# F4: ToolMaze per model, Checklist + OM vs SilentGuard (appendix), dot plot like F2
pm = [(NAME[m], D["toolmaze"]["omcl"][m]["silent"], D["toolmaze"]["ours"][m]["silent"]) for m in FREE]
fig, ax = plt.subplots(figsize=(COL, 0.2 * len(pm) + 0.6))
for i, (n, x0, x1) in enumerate(pm):
    y = len(pm) - 1 - i
    ax.plot([x0, x1], [y, y], color=GRID, linewidth=1.6, zorder=1)
    ax.scatter([x0], [y], s=24, facecolor="white", edgecolor=INK2, linewidth=1.2, zorder=3)
    ax.scatter([x1], [y], s=24, color=BLUE, zorder=3)
ax.set_yticks(range(len(pm)))
ax.set_yticklabels([p[0] for p in pm][::-1])
ax.tick_params(axis="y", length=0)
ax.set_xlim(15, 50)
ax.set_xlabel("Task success under silent faults (%)")
ax.grid(axis="x", color=GRID, linewidth=0.5)
ax.set_axisbelow(True)
ax.legend(handles=[plt.Line2D([], [], marker="o", ls="", markerfacecolor="white", markeredgecolor=INK2,
                                label="Checklist + Outcome Monitors"),
                    plt.Line2D([], [], marker="o", ls="", color=BLUE, label="SilentGuard")],
           frameon=False, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0), columnspacing=1.0, handletextpad=0.3)
save(fig, "F4_toolmaze_permodel")

# F5: ToolMaze by fault type: no re-check / blind retry / SilentGuard (appendix)
arms = [("omcl", "No re-check", GRAY), ("blindr", "Blind retry", AMBER), ("ours", "SilentGuard", BLUE)]
groups = [("1-call fault", lambda a: mean_of("toolmaze", a, "p3")),
          ("2-call fault", lambda a: st.mean([D["toolmaze_twocall"][m][a] for m in FREE])
           if all(D["toolmaze_twocall"][m].get(a) is not None for m in FREE) else None),
          ("Lasting fault", lambda a: mean_of("toolmaze", a, "p4"))]
fig, ax = plt.subplots(figsize=(COL, 1.9))
w = 0.26
for j, (a, lab, col) in enumerate(arms):
    vals = [g[1](a) for g in groups]
    xs_ = [i + (j - 1) * (w + 0.02) for i in range(len(groups))]
    ax.bar(xs_, [v or 0 for v in vals], width=w, color=col, label=lab, edgecolor="white", linewidth=0.5,
           hatch="////" if a == "blindr" else None)
    for x_, v_ in zip(xs_, vals):
        if v_ is not None:
            ax.text(x_, v_ + 1, f"{v_:.0f}", ha="center", va="bottom", fontsize=7, color=INK2)
ax.set_xticks(range(len(groups)))
ax.set_xticklabels([g[0] for g in groups])
ax.set_ylabel("Task success (%)")
ax.grid(axis="y", color=GRID, linewidth=0.5)
ax.set_axisbelow(True)
ax.set_ylim(0, 72)
ax.legend(frameon=False, ncol=3, loc="lower center", bbox_to_anchor=(0.5, 1.0), columnspacing=1.0, handlelength=1.4)
save(fig, "F5_recheck_vs_retry")
print("figures done")

# F6: risk vs coverage under a mix of clean and faulted calls (main text). Each method is a line over fault rates
# 10%, 25% and 50% (dots; the larger dot is 25%). Advice-only methods share one gray style.
RC = OUT.parent.parent / "paper_results_new" / "risk_coverage.json"
if not RC.exists():
    RC = Path(sys.argv[1]).parent / "risk_coverage.json"
if RC.exists():
    R = json.load(open(RC))
    PS = ("0.1", "0.25", "0.5")
    styles = {"none": ("No guard", RED, "o", "-"),
              "advisory_nonum_abstain": ("Warning + may abstain", "#c2577a", "P", "-"),
              "enforce2": ("Enforce", "#7a5cc4", "^", "-"), "judge_block": ("Judge + blocking", "#3aa17e", "v", "-"),
              "sgp": ("SilentGuard", BLUE, "D", "-"), "repair3": ("Auto-repair", AMBER, "s", "--")}
    advice = ["skeptic", "selfverify", "retry2", "judge_check", "checklist", "reviewer", "om", "om_block",
              "judge_block_plain", "judge_advise_plain", "advisory_nonum"]
    fig, ax = plt.subplots(figsize=(COL, 2.45))
    handles = []
    for a in advice:
        if a not in R:
            continue
        xs = [R[a]["curve"][p]["coverage"] for p in PS]
        ys = [R[a]["curve"][p]["risk"] for p in PS]
        ax.plot(xs, ys, color="#d4d3cc", linewidth=0.6, zorder=1)
        ax.scatter(xs[1:2], ys[1:2], s=9, color="#c4c3bc", zorder=1)
        if a == "selfverify":
            ax.text(xs[1] - 1.2, ys[1] + 1.0, "Self-verification", fontsize=7, color="#8a8983", ha="right", va="bottom")
    for a, (lab, col, mk, ls) in styles.items():
        if a not in R:
            continue
        xs = [R[a]["curve"][p]["coverage"] for p in PS]
        ys = [R[a]["curve"][p]["risk"] for p in PS]
        hollow = a == "repair3"
        top = a == "sgp"
        ax.plot(xs, ys, color=col, linewidth=1.3, linestyle=ls, zorder=7 if top else (2 if hollow else 3))
        ax.scatter(xs, ys, s=[7, 28, 7], marker=mk, zorder=8 if top else (2 if hollow else 4), linewidth=1.0,
                   facecolor="white" if hollow else col, edgecolor=col)
        handles.append(plt.Line2D([], [], color=col, linestyle=ls, linewidth=1.3, marker=mk, markersize=4.5,
                                  markerfacecolor="white" if hollow else col, markeredgecolor=col, label=lab))
    handles.append(plt.Line2D([], [], color="#c4c3bc", linewidth=0.8, marker="o", markersize=3,
                              label="No evidence, or advice only"))
    ax.set_xlabel("Coverage: runs with an answer (%)")
    ax.set_ylabel("Risk: wrong among answers (%)")
    ax.set_ylim(0, 47)
    ax.set_xlim(40, 95)
    ax.grid(color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    ax.legend(handles=handles, frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.2), handlelength=2.0,
               labelspacing=0.3, columnspacing=1.0)
    save(fig, "F6_risk_coverage")
