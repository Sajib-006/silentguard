"""SilentGuard in 30 seconds. No model, no network, no API key.

A toy API silently ignores the filter on one field: it still answers with a plausible number.
The plain call returns a wrong count. SilentGuard tests the count identity, re-runs the request
once, and withholds the value, so an agent never sees a number that is proven wrong.

    python examples/01_quickstart.py
"""
import random

from silentguard import Condition, SilentGuard

random.seed(0)
RECORDS = [{"status": random.choice(["pass", "fail", "pending"]),
            "tissue": random.choice(["tumor", "normal"]),
            "assay": random.choice(["WGS", "WES", "RNA"])} for _ in range(500)]


def make_api(ignored_field=None):
    """count(conditions) for an API that silently ignores one field (None = a correct API)."""
    def count(conditions):
        kept = [c for c in conditions if c.field != ignored_field]
        return sum(all(c.matches(r) for c in kept) for r in RECORDS)
    return count


query = [Condition("status", "pass"), Condition("tissue", "tumor")]
truth = make_api()(query)

for name, api in [("correct API", make_api()), ("API that ignores 'tissue'", make_api("tissue"))]:
    guard = SilentGuard(api)
    result = guard.count(query)
    print(f"{name}")
    print(f"  plain call returns : {api(query)}   (true answer {truth})")
    print(f"  SilentGuard        : {result.status}, value released = {result.value}")
    if result.check.failed:
        print(f"  failed condition   : {', '.join(map(str, result.check.failed))}")
    print(f"  agent sees         : {result.observation()}")
    # The answer gate: a final answer is accepted only if the harness verified that value.
    decision = guard.check_answer(api(query))
    print(f"  final answer {api(query)!s:>4}  : {'accepted' if decision.accepted else 'rejected'}\n")
