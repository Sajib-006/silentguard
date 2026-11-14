"""Exact invariants that any correct filtered API must satisfy.

The count identity. For a request with conditions C1..Ck and any position i,

    N(all conditions) + N(others, and not Ci) == N(others)

because "Ci" and "not Ci" split the records that match the other conditions into two parts.
An API that ignores, misreads or caps a condition breaks this identity for at least one i,
even though every single response looks normal (HTTP 200, a plausible number).

The record checks. When the answer is a list of records, every returned record must satisfy
every condition, no record may appear twice, and a complete page must hold exactly the number
of records the API reports.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class Condition:
    """One equality condition: ``record[field] == value`` (or ``!=`` when ``negate`` is True)."""

    field: str
    value: Any
    negate: bool = False

    def negated(self) -> "Condition":
        return replace(self, negate=not self.negate)

    def matches(self, record: Mapping[str, Any]) -> bool:
        hit = record.get(self.field) == self.value
        return (not hit) if self.negate else hit

    def __str__(self) -> str:
        return f"{self.field}{'!=' if self.negate else '='}{self.value}"


CountFn = Callable[[Sequence[Condition]], Optional[int]]
"""A function that asks the API how many records match a list of conditions.
It must return ``None`` when the request itself fails, so the check can stay silent."""


@dataclass
class CountCheck:
    """Result of the count identity on one reported count."""

    ran: bool
    consistent: Optional[bool]
    reported: Optional[int]
    failed: list[Condition] = field(default_factory=list)
    extra_calls: int = 0
    details: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.ran and self.consistent is True

    @property
    def broken(self) -> bool:
        return self.ran and self.consistent is False


def check_count(count: CountFn, conditions: Sequence[Condition], reported: Optional[int]) -> CountCheck:
    """Test ``reported`` against the count identity at every condition position.

    Costs two extra API calls per condition. If any extra call fails, the check does not run
    (``ran=False``) and says nothing about the result.
    """
    conds = list(conditions)
    if not conds or reported is None:
        return CountCheck(ran=False, consistent=None, reported=reported)
    calls, failed, details = 0, [], []
    for i, c in enumerate(conds):
        rest = conds[:i] + conds[i + 1:]
        negated = count(rest + [c.negated()])
        without = count(rest)
        calls += 2
        if negated is None or without is None:
            return CountCheck(ran=False, consistent=None, reported=reported, extra_calls=calls)
        details.append({"condition": str(c), "negated_count": negated, "without_count": without})
        if reported + negated != without:
            failed.append(c)
    return CountCheck(ran=True, consistent=not failed, reported=reported, failed=failed,
                      extra_calls=calls, details=details)


@dataclass
class RecordCheck:
    """Result of the record checks on one returned list."""

    consistent: bool
    problems: list[str]
    bad_records: list[Any]
    duplicates: bool


def check_records(records: Iterable[Mapping[str, Any]], conditions: Sequence[Condition],
                  reported_total: Optional[int] = None, complete: bool = True,
                  key: str = "id", fields: Callable[[Mapping[str, Any]], Mapping[str, Any]] = lambda r: r
                  ) -> RecordCheck:
    """Check a returned list of records against the request that produced it.

    ``fields`` maps a record to the dict the conditions are read from (for example
    ``lambda r: r["metadata"]``). ``complete`` says whether the page should hold every match.
    """
    records = list(records)
    ids = [r.get(key) for r in records]
    bad = [r.get(key) for r in records if not all(c.matches(fields(r)) for c in conditions)]
    dup = len(ids) != len(set(ids))
    problems = []
    if bad:
        problems.append("some returned records do not satisfy the conditions")
    if dup:
        problems.append("some records are returned more than once")
    if reported_total is not None and complete and len(records) != reported_total:
        problems.append("the number of records does not match the reported total")
    return RecordCheck(consistent=not problems, problems=problems, bad_records=bad, duplicates=dup)


def check_params(params: Iterable[str], allowed: Iterable[str]) -> list[str]:
    """Return the request parameters that the API does not document.

    Many APIs ignore unknown parameters and still answer HTTP 200, so a misspelled filter
    silently disappears. Checking the request against the published parameter list catches it.
    """
    allowed = set(allowed)
    return [p for p in params if p not in allowed]
