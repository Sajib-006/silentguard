"""repair3 = detector v3 + a screen for conditions the API ignores + the frozen repair routes.

The frozen `split` route recounts through the same API, so a fault that is consistent with itself
(e.g. a field ignored in every query) makes both recounts agree on the same wrong number. Before any
repair, each condition is tested on its own with a value swap; if one has no effect, no route can put
it back, and the harness abstains.
"""
from __future__ import annotations
import repair                        # frozen (hash-checked)
import d7_harness_v3 as v3


def repair3_count(base, headers, filters, timeout=15.0):
    enums = repair._enums(base, headers, timeout)
    for c in filters:
        if v3.condition_ignored(base.rstrip("/") + "/search", headers, c, enums, timeout):
            return {"value": None, "route": None, "queries": 3 * len(filters),
                    "tried": [f"screen: the condition on {c.get('path')} has no effect on its own; no route can restore it"]}
    return repair.repair_count(base, headers, filters, timeout)
