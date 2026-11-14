"""Port of Outcome Monitors (Panthi & Abdelfattah 2026, arXiv 2608.19303) contract mining.

Algorithm 2 (LearnContracts) mined from nominal P0 traces stored in the ToolMaze task files,
five-fold cross-fit by workflow. Algorithm 1 (CheckAndReceipt) is `Registry.check` plus the
receipt encoders in tm_arms.py. Shared, byte for byte, by the `om` arm and our `sg` arm.

Classes (names follow their fired-class list): explicit_error, unexpected_field,
missing_learned_field, learned_type_mismatch, learned_echo_mismatch, nonpositive_value,
unseen_category, affine_relation_broken, date_order_broken.
"""
from __future__ import annotations

import glob
import json
import random
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

# Our lexicon (theirs "appears in the supplement", which is not public). Matched on the
# last token of a snake_case field name.
LEXICON = {"status", "state", "type", "category", "class", "level", "condition", "mode",
           "tier", "priority", "channel", "currency", "availability", "kind", "method",
           "role", "grade", "severity", "unit", "direction", "phase", "outcome", "trend",
           "signal", "sentiment", "verdict", "stage"}
DATE_PAIRS = [("start_date", "end_date"), ("check_in_date", "check_out_date"),
              ("created_at", "updated_at")]
MIN_SHAPE = 2          # samples needed before presence / type / unexpected-field fire
MINE_TOL, RUN_TOL = 1e-8, 1e-5


def kind(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "other"


def flatten(y, prefix="", depth=0):
    """Leaf scalars and arrays, dotted paths, dicts flattened two levels deep."""
    out = {}
    if not isinstance(y, dict):
        return {"$": y}
    for k, v in y.items():
        p = f"{prefix}{k}"
        if isinstance(v, dict) and depth < 2:
            out.update(flatten(v, p + ".", depth + 1))
        else:
            out[p] = v
    return out


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def parse_date(v):
    if not isinstance(v, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except (ValueError, TypeError):
            continue
    return None


def lex_match(field):
    return field.split(".")[-1].split("_")[-1].lower() in LEXICON


# ── nominal samples ──────────────────────────────────────────────────────────
def nominal_samples(data_dir):
    """{workflow_id: [(tool, args, output), ...]} from P0 task files (success, unperturbed)."""
    out = {}
    for f in sorted(glob.glob(f"{data_dir}/perturbed_tasks/c*/*_P0.json")):
        d = json.load(open(f))
        steps = []
        if "valid_paths" in d:
            for p in d["valid_paths"]:
                steps += p.get("execution_trace", [])
        else:
            steps = d.get("execution_trace", [])
        seen, rows = set(), []
        for s in steps:
            if s.get("status") != "success" or s.get("is_perturbed"):
                continue
            key = (s["tool_name"], json.dumps(s.get("arguments", {}), sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            rows.append((s["tool_name"], s.get("arguments", {}), s.get("output", {})))
        out[d["task_id"]] = rows
    return out


# ── Algorithm 2 ──────────────────────────────────────────────────────────────
def learn(samples):
    """samples: list of (tool, args, output). Returns {tool: spec}."""
    by_tool = defaultdict(list)
    for t, a, y in samples:
        if isinstance(y, dict):
            by_tool[t].append((a, flatten(y)))
    reg = {}
    for t, rows in by_tool.items():
        n = len(rows)
        keysets = [set(y) for _, y in rows]
        f_req = set.intersection(*keysets)
        f_all = set.union(*keysets)
        types = defaultdict(set)
        for _, y in rows:
            for f, v in y.items():
                types[f].add(kind(v))
        spec = {"n": n, "f_req": sorted(f_req), "f_all": sorted(f_all),
                "types": {f: sorted(k) for f, k in types.items()},
                "echo": [], "pos": [], "domain": {}, "affine": [], "order": []}
        # echo
        cand = defaultdict(lambda: [0, True])
        for a, y in rows:
            for f in set(a) & set(y):
                cand[f][0] += 1
                cand[f][1] &= (a[f] == y[f])
        spec["echo"] = sorted(f for f, (c, ok) in cand.items() if c >= 2 and ok)
        for f in f_req:
            vals = [y[f] for _, y in rows]
            if all(is_num(v) for v in vals) and len(vals) >= 3 and all(v > 0 for v in vals):
                spec["pos"].append(f)
            if lex_match(f) and all(isinstance(v, str) for v in vals) and len(vals) >= 4:
                norm = {v.strip().lower() for v in vals}
                if len(norm) <= 8:
                    spec["domain"][f] = sorted(norm)
        spec["pos"].sort()
        nums = [f for f in sorted(f_req) if all(is_num(y[f]) for _, y in rows)]
        for f in nums:
            xs = [y[f] for _, y in rows]
            if len(xs) < 4 or len(set(xs)) < 3:
                continue
            for g in nums:
                if g == f:
                    continue
                ys = [y[g] for _, y in rows]
                # fit through two points with distinct x, verify on all
                i, j = 0, next(k for k in range(n) if xs[k] != xs[0])
                al = (ys[j] - ys[i]) / (xs[j] - xs[i])
                be = ys[i] - al * xs[i]
                if all(abs(al * x + be - yv) <= MINE_TOL * max(1.0, abs(yv)) for x, yv in zip(xs, ys)):
                    spec["affine"].append([f, g, al, be])
        for f, g in DATE_PAIRS:
            pairs = [(parse_date(y.get(f)), parse_date(y.get(g))) for _, y in rows]
            pairs = [(p, q) for p, q in pairs if p and q]
            if len(pairs) >= 2 and all(p <= q for p, q in pairs):
                spec["order"].append([f, g])
        reg[t] = spec
    return reg


class Registry:
    def __init__(self, reg):
        self.reg = reg

    def check(self, tool, args, result):
        """Algorithm 1 detector half: ordered violation records (code, path, detail)."""
        spec = self.reg.get(tool)
        V = []
        if not isinstance(result, dict):
            return V
        y = flatten(result)
        if "error" in result and (spec is None or "error" not in spec["f_all"]):
            V.append(("explicit_error", "error", "error-bearing field(s): error"))
        if spec is None:
            return V
        if spec["n"] >= MIN_SHAPE:
            for f in y:
                if f not in spec["f_all"] and f != "error":
                    V.append(("unexpected_field", f, f"field absent from nominal traces: {f}"))
            for f in spec["f_req"]:
                if f not in y:
                    V.append(("missing_learned_field", f, f"missing nominal field {f}"))
                elif kind(y[f]) not in spec["types"].get(f, []) and not (
                        kind(y[f]) == "integer" and "number" in spec["types"].get(f, [])):
                    V.append(("learned_type_mismatch", f, f"field {f} has unexpected type"))
        for f in spec["echo"]:
            if f in args and f in y and args[f] != y[f]:
                V.append(("learned_echo_mismatch", f, f"field {f} differs from call argument"))
        for f in spec["pos"]:
            if f in y and is_num(y[f]) and y[f] <= 0:
                V.append(("nonpositive_value", f, f"nominally positive field {f} is not positive"))
        for f, dom in spec["domain"].items():
            if f in y and isinstance(y[f], str) and y[f].strip().lower() not in dom:
                V.append(("unseen_category", f, f"field {f} has unseen categorical value"))
        for f, g, al, be in spec["affine"]:
            if f in y and g in y and is_num(y[f]) and is_num(y[g]):
                if abs(al * y[f] + be - y[g]) > RUN_TOL * max(1.0, abs(y[g])):
                    V.append(("affine_relation_broken", f"{f},{g}",
                              f"fields {f} and {g} violate a nominal relation"))
        for f, g in spec["order"]:
            p, q = parse_date(y.get(f)), parse_date(y.get(g))
            if p and q and p > q:
                V.append(("date_order_broken", f"{f},{g}", f"{f} is later than {g}"))
        return V


def folds(workflow_ids, k=5, seed=20260925):
    ids = sorted(workflow_ids)
    random.Random(seed).shuffle(ids)
    return {w: i % k for i, w in enumerate(ids)}


def build_crossfit(data_dir, k=5):
    samples = nominal_samples(data_dir)
    fold_of = folds(samples.keys(), k)
    regs = []
    for i in range(k):
        train = [s for w, rows in samples.items() if fold_of[w] != i for s in rows]
        regs.append(learn(train))
    return fold_of, regs


# ── recovery relations Subs(tau), from public registry files only ────────────
def build_subs(tools_dir):
    subs = defaultdict(set)
    for name in ("source.yaml", "processor.yaml", "action.yaml"):
        for t in yaml.safe_load(open(Path(tools_dir) / "definitions" / name))["tools"]:
            for s in t.get("substitutes", []) or []:
                subs[t["name"]].add(s)
    alts = yaml.safe_load(open(Path(tools_dir) / "definitions" / "alternatives.yaml"))["alternatives"]
    for grp in alts.values():
        paths = grp.get("paths", [])
        entries = [p["tools"][0]["tool_name"] for p in paths if p.get("tools")]
        for p in paths:
            for step in p.get("tools", []):
                for e in entries:
                    if e != p["tools"][0]["tool_name"]:
                        subs[step["tool_name"]].add(e)
    return {k: sorted(v) for k, v in subs.items()}


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    fold_of, regs = build_crossfit(f"{root}/data")
    n_inv = sum(len(s["echo"]) + len(s["pos"]) + len(s["domain"]) + len(s["affine"]) + len(s["order"])
                + len(s["f_req"]) for r in regs for s in r.values())
    print("workflows", len(fold_of), "tools per fold", [len(r) for r in regs], "invariants(all folds)", n_inv)
