#!/usr/bin/env python3
"""Deterministic verifier for AgentOrchBench answers.

Usage:
  python3 verifier.py --task C01 --answer "7"
  python3 verifier.py --task Y03 --answer "FAILED:transfers=1,assets=0; ..."
Prints JSON: {"task_id","correct","missing","matched","mode"}
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

GT_PATH = Path(__file__).parent / "ground_truth.json"


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def verify(task_id: str, answer: str, gt: dict | None = None) -> dict:
    if gt is None:
        gt = json.loads(GT_PATH.read_text())
    spec = gt[task_id]
    ans = normalize(answer)
    ans_l = ans.lower()
    mode = spec.get("match", "must_include")
    missing: list[str] = []
    matched: list[str] = []

    if mode == "exact":
        expected = normalize(spec["exact"])
        ok = ans == expected
        if ok:
            matched.append(expected)
        else:
            missing.append(expected)
    elif mode == "regex":
        pat = spec["regex"]
        if re.search(pat, ans, flags=re.IGNORECASE | re.DOTALL):
            matched.append(pat)
        else:
            missing.append(pat)
    else:
        for needle in spec.get("must_include", []):
            ok = needle in ans or needle.lower() in ans_l
            if ok:
                matched.append(needle)
            else:
                missing.append(needle)
        # also_ok: if any also_ok token present, drop listed soft needles from missing
        also = spec.get("also_ok", [])
        soft = set(spec.get("soft_needles", []))
        if also and any(a.lower() in ans_l for a in also):
            missing = [m for m in missing if m not in soft]
        # legacy C30
        if task_id == "C30" and missing:
            if any(a.lower() in ans_l for a in also) and "400" in matched:
                missing = [m for m in missing if m != "400"]
        # Per-task also_ok: treat as alternate for ANY still-missing needle if also_ok hits
        # (narrow): if also_ok present and every missing needle has a note — keep simple:
        if also and missing and spec.get("also_ok_satisfies"):
            if any(a.lower() in ans_l for a in also):
                missing = []

    correct = len(missing) == 0
    return {
        "task_id": task_id,
        "correct": correct,
        "matched": matched,
        "missing": missing,
        "mode": mode,
        "answer": ans[:500],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--answer", required=True)
    args = ap.parse_args()
    print(json.dumps(verify(args.task, args.answer)))


if __name__ == "__main__":
    main()
