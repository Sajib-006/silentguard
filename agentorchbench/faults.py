"""Silent fault types. Each one returns HTTP 200 and a normal-looking result."""

FAULTS = {
    "off": "No fault (clean API).",
    "drop_last": "Drops the last filter, as an API does with a misnamed filter it does not know.",
    "cap_total": "Reports at most 5 matches, as when the page size is returned as the total.",
    "or_semantics": "Joins the conditions with OR instead of AND, as a faulty query builder does.",
    "drop_field": "Ignores any filter on one field, as with a field that is not indexed.",
    "ignore_negation": "Drops NOT from a condition, as an API without NOT support does.",
    "off_by_one": "Adds one to the count, as a range bug does.",
    "prefix": "Matches by prefix instead of equality (keeps the count identity true).",
    "consistent_lie": "Lies the same way on every query (keeps the count identity true).",
}

PAPER_FAULTS = ["drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]
"""The six fault types of the main AgentOrchBench results."""

CHECK_FIRES = {"drop_last": True, "cap_total": True, "or_semantics": True, "drop_field": True,
               "ignore_negation": "false alarm", "off_by_one": True, "prefix": False,
               "consistent_lie": False}
"""Whether the exact count identity fires on each fault ("false alarm": it fires even when the
count may be right)."""
