"""Score an agent's final answer as correct, wrong, unknown (abstained) or no_answer.

Count answers use the paper's strict grader (``paper/code/eval_v7/strict_grade.py``):
``count=<n>`` decides when present; an abstention such as "cannot be determined" is unknown;
several different numbers are wrong.
"""
from __future__ import annotations

import importlib.util
import re

from .paths import PAPER_CODE
from .tasks import Task

_spec = importlib.util.spec_from_file_location("aob_strict_grade", PAPER_CODE / "eval_v7" / "strict_grade.py")
_strict = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_strict)

_MAP = {"correct": "correct", "wrong_num": "wrong", "abstain": "unknown", "no_answer": "no_answer"}


def score(task: Task, answer: str | int | None) -> str:
    """Return ``correct``, ``wrong``, ``unknown`` or ``no_answer``."""
    if answer is None or str(answer).strip() == "":
        return "no_answer"
    if task.suite == "lists":
        text = str(answer)
        if re.search(r"cannot be determined|unknown", text, re.I):
            return "unknown"
        ids = sorted(set(re.findall(r"[A-Za-z]+-\d+", text)))
        return "correct" if ids == task.answer else "wrong"
    text = f"count={answer}" if isinstance(answer, int) else str(answer)
    row = {"answer": text, "ground_truth_used": {"must_include": [f"count={task.answer}"]}}
    return _MAP[_strict.strict_outcome(row)]
