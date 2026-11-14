"""SilentGuard: the harness, not the model, decides what happens to a result that fails a check.

For each count the agent asks for, SilentGuard
  1. calls the API and tests the result with the exact count identity,
  2. re-runs the same request once if the identity breaks (some faults clear on a second call),
  3. withholds the value if it breaks again, so the model never sees a number proven wrong,
  4. only warns (and keeps the value) when a learned check fails, because learned checks
     can be wrong themselves.

At the end, the answer gate accepts a final count only if the harness verified that exact value.
Otherwise it asks the agent to re-query, and after ``max_rejections`` it answers that the count
cannot be determined. This mirrors the method in the paper (``paper/code/eval_v7/executor.py``,
arm ``sgp``, and ``paper/code/eval_v7/runner.py``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from .invariants import Condition, CountCheck, CountFn, check_count

WITHHELD_MESSAGE = ("The count was withheld: this result failed an exact consistency check twice "
                    "(the harness already re-ran the request). Do not guess the count. Re-query "
                    "differently, or answer that the count cannot be determined.")
REJECT_MESSAGE = ("Your answer is not a count that passed the harness check. Re-query to get a count "
                  "that passes the check, or answer that the count cannot be determined.")
UNDETERMINED = "cannot be determined"

LearnedCheck = Callable[[Sequence[Condition], Optional[int]], Optional[str]]
"""A learned check returns a warning text when it thinks a result is wrong, else None."""


@dataclass
class GuardedCount:
    """What the harness releases to the agent for one count request."""

    status: str                      # "verified", "verified_on_recheck", "withheld", "warned", "unchecked"
    value: Optional[int]             # None when withheld
    check: CountCheck
    recheck: Optional[CountCheck] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def withheld(self) -> bool:
        return self.status == "withheld"

    def observation(self) -> str:
        """The text the agent sees, as a JSON object."""
        out: dict[str, Any] = {"count": self.value, "verification": self.status}
        if self.withheld:
            out["warning"] = WITHHELD_MESSAGE
        elif self.warnings:
            out["warning"] = " ".join(self.warnings)
        return json.dumps(out)


@dataclass
class AnswerDecision:
    accepted: bool
    final: bool                      # True when the harness ends the task (accepted, or out of tries)
    answer: Any
    message: str = ""


class SilentGuard:
    """Wrap a count API with exact checks and harness-side decisions.

    Parameters
    ----------
    count:
        ``count(conditions) -> int | None`` that calls the real API.
    learned_checks:
        Optional checks learned from clean data. A failure only adds a warning.
    recheck:
        Re-run a failing request once before withholding (the paper's default).
    max_rejections:
        How many times the answer gate rejects an unverified final answer before it answers
        that the count cannot be determined.
    """

    def __init__(self, count: CountFn, learned_checks: Sequence[LearnedCheck] = (),
                 recheck: bool = True, max_rejections: int = 2):
        self._count = count
        self.learned_checks = list(learned_checks)
        self.recheck = recheck
        self.max_rejections = max_rejections
        self.verified: set[int] = set()
        self.rejections = 0
        self.extra_calls = 0

    def reset(self) -> None:
        """Start a new task."""
        self.verified.clear()
        self.rejections = 0

    def count(self, conditions: Sequence[Condition]) -> GuardedCount:
        """Ask the API for a count and release only what the checks allow."""
        conditions = list(conditions)
        value = self._count(conditions)
        first = check_count(self._count, conditions, value)
        self.extra_calls += first.extra_calls
        if first.passed:
            return self._accept("verified", value, first)
        if first.broken:
            if self.recheck:
                value2 = self._count(conditions)
                second = check_count(self._count, conditions, value2)
                self.extra_calls += 1 + second.extra_calls
                if second.passed:
                    return self._accept("verified_on_recheck", value2, first, second)
                return GuardedCount("withheld", None, first, second)
            return GuardedCount("withheld", None, first)
        # The exact check could not run (for example a request it does not cover).
        warnings = [w for w in (chk(conditions, value) for chk in self.learned_checks) if w]
        return GuardedCount("warned" if warnings else "unchecked", value, first, warnings=warnings)

    def _accept(self, status, value, first, second=None) -> GuardedCount:
        self.verified.add(value)
        return GuardedCount(status, value, first, second)

    def check_answer(self, answer: Any) -> AnswerDecision:
        """Answer gate: accept a final count only if the harness verified it."""
        if isinstance(answer, str) and UNDETERMINED in answer.lower():
            return AnswerDecision(True, True, UNDETERMINED)
        try:
            value = int(answer)
        except (TypeError, ValueError):
            value = None
        if value is not None and value in self.verified:
            return AnswerDecision(True, True, value)
        self.rejections += 1
        if self.rejections > self.max_rejections:
            return AnswerDecision(False, True, UNDETERMINED,
                                  "No verified count after the rejection budget; the count cannot be determined.")
        return AnswerDecision(False, False, None, REJECT_MESSAGE)

    # Tool helpers -------------------------------------------------------------------------

    def tool_schema(self, name: str = "count_records",
                    description: str = "Count the records that match all conditions.") -> dict:
        """An OpenAI-style function tool for ``count``."""
        return {"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": {"conditions": {
                "type": "array", "description": "Conditions that must all hold.",
                "items": {"type": "object", "properties": {
                    "field": {"type": "string"}, "value": {"type": ["string", "number", "boolean"]},
                    "negate": {"type": "boolean", "default": False}},
                    "required": ["field", "value"]}}},
                "required": ["conditions"]}}}

    def call_tool(self, arguments: dict | str) -> str:
        """Run ``count`` from tool-call arguments and return the observation text."""
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        conds = [Condition(c["field"], c["value"], bool(c.get("negate", False)))
                 for c in args.get("conditions", [])]
        return self.count(conds).observation()
