"""Load AgentOrchBench tasks."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .paths import TASKS

SUITES = {
    "count": ("adv_d7_v5.jsonl", "36 count questions with 2 to 3 conditions (main suite of the paper)"),
    "lists": ("rec_v1.jsonl", "30 questions whose answer is a list of record IDs"),
    "retail": ("adv_retail_v1.jsonl", "30 count questions on a second domain (retail orders)"),
    "workflows": ("hd100_v7.jsonl", "100 multi-step workflows across the eight services"),
}


@dataclass
class Task:
    id: str
    suite: str
    domain: str
    prompt: str
    answer: Any                          # int for count suites, sorted list of IDs for lists
    conditions: list[dict] = field(default_factory=list)
    catalog: Optional[str] = None        # which seed data the catalog service must load
    raw: dict = field(default_factory=dict, repr=False)


def _answer(suite: str, gt: dict) -> Any:
    if suite == "lists":
        return sorted(gt.get("ids") or gt.get("must_include") or [])
    m = re.search(r"count=(\d+)", " ".join(gt.get("must_include") or []))
    return int(m.group(1)) if m else gt.get("must_include")


def load_tasks(suite: str = "count") -> list[Task]:
    """Return the tasks of one suite (see ``SUITES``)."""
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r}; choose from {sorted(SUITES)}")
    rows = [json.loads(l) for l in open(TASKS / SUITES[suite][0]) if l.strip()]
    return [Task(id=r["task_id"], suite=suite, domain=r.get("domain", "bio"), prompt=r["prompt"],
                 answer=_answer(suite, r.get("ground_truth") or {}), conditions=r.get("conds") or [],
                 catalog=r.get("catalog"), raw=r) for r in rows]
