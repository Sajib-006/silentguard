#!/usr/bin/env python3
"""Strict outcome grader. Replaces the digit-scan rule used in the E1-E5 analyses.

The old rule took every integer anywhere in the ANSWER text. Two failure modes, both found in data:
  * a CONFIDENCE value on the same line is read as a count: "count=0 CONFIDENCE: 0.6" contains 0
    and 6, so a true count of 6 was scored correct;
  * an abstention with a confidence value is read as a wrong number: "count=null CONFIDENCE: 0.8"
    contains 0 and 8, so it was scored wrong_num instead of abstain.
  * an abstention that restates the task picks up digits from filter values ("Study_ID=ST-10 ...
    cannot be determined" contains 10).
An empty answer (the run aborted or hit the turn cap) is its own outcome, no_answer, separate from
a deliberate abstention (added 2026-09-24 after review: aborts had been counted as abstain).
Strict rule: cut everything from CONFIDENCE on; drop digit-bearing identifiers (ST-10, WH-1); if `count=` is present it alone decides (a digit
is the answer, anything else is an abstention); otherwise exactly one integer must be present,
and several different integers count as wrong (hedging across numbers is not an answer).
"""
from __future__ import annotations
import re

_CONF = re.compile(r"\bconfidence\b.*$", re.I | re.S)
# identifiers that carry digits but are never counts: ST-10, WH-1, P-3, ord-0012, batch_1
_IDS = re.compile(r"\b[A-Za-z]+[-_]\d+\b")
_ABSTAIN = re.compile(r"cannot be determined|can.t be determined|undetermin|unknown|unable to|"
                      r"not (?:be )?(?:reliabl|trustworth|determin|availabl)|unreliable|"
                      r"withheld|no verified count|not possible to", re.I)
_COUNT = re.compile(r"count\s*=\s*([^\s,;)]+)", re.I)


def strict_outcome(r: dict) -> str:
    gt = (r.get("ground_truth_used") or {}).get("must_include") or []
    m = re.search(r"count=(\d+)", " ".join(gt))
    truth = m.group(1) if m else None
    if not (r.get("answer") or "").strip():
        return "no_answer"                         # aborted / never answered: not a deliberate abstention
    ans = _CONF.sub("", r.get("answer") or "")
    c = _COUNT.search(ans)
    if c:
        v = c.group(1).strip().rstrip(".")
        if not v.isdigit():
            return "abstain"                       # count=null / unknown / undetermined
        num = v
    else:
        nums = set(re.findall(r"\b\d+\b", _IDS.sub(" ", ans)))
        if not nums:
            return "abstain"
        if _ABSTAIN.search(ans) and len(nums) > 1:
            return "abstain"                       # a refusal that restates numbers from the task
        if len(nums) > 1:
            return "wrong_num"                     # several candidate counts = no single answer
        num = nums.pop()
    if num == truth and r.get("state_ok") is not False:
        return "correct"
    return "wrong_num"
