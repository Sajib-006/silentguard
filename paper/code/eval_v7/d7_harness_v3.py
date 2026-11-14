"""Detector v3: the partition identity at EVERY position, not only the last.

v1 (d7_harness.py, frozen) tests N(F) + N(F_{<k} & not C_k) = N(F_{<k}), so an API that ignores a
filter anywhere but the last position passes (k=2, C1 ignored: N(C2) + N(not C2) = total = N(C1)).
v3 repeats the test with each condition in turn as the one negated:
    for each i:  N(F) + N(F without C_i, and not C_i) = N(F without C_i)
Cost 2k queries instead of 2. The verdict text names the condition that failed but shows no numbers:
numbers printed by a warning get reported by models (E4).
"""
from __future__ import annotations
import copy, json
import requests

OK = "[HARNESS CHECK] passed: the result is consistent with every condition in the request."
FAIL = ("[HARNESS CHECK] FAILED: the result is not consistent with the request. The condition on {field} "
        "did not partition the records correctly, so this count is not reliable.")
SKIP = "[HARNESS CHECK] not applicable to this query."


def _post(url, headers, filters, timeout):
    try:
        r = requests.post(url, headers=headers, timeout=timeout,
                          data=json.dumps({"json_metadata_filters": filters, "limit": 50}))
        return r.json().get("total_count") if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def verify_all(url, headers, filters, reported_n, timeout=15.0):
    out = {"ran": False, "consistent": None, "n": reported_n, "failed_at": [], "extra_calls": 0, "text": SKIP}
    if not filters or reported_n is None or any(f.get("mode", "path") != "path" for f in filters):
        return out
    calls = 0; bad = []; detail = []
    for i, c in enumerate(filters):
        rest = [f for j, f in enumerate(filters) if j != i]
        neg = copy.deepcopy(c); neg["negate"] = not neg.get("negate", False)
        m = _post(url, headers, rest + [neg], timeout); t = _post(url, headers, rest, timeout); calls += 2
        if m is None or t is None:
            return {**out, "extra_calls": calls}
        detail.append({"field": c.get("path"), "value": c.get("value"), "negated_count": m, "without_count": t})
        if reported_n + m != t:
            bad.append(i)
    out.update(ran=True, consistent=not bad, failed_at=bad, extra_calls=calls, detail=detail,
               text=OK if not bad else FAIL.format(field=filters[bad[0]].get("path", "?")))
    return out


def condition_ignored(url, headers, cond, enums, timeout=15.0):
    """Value-swap screen for one condition on its own: if it returns the same count as a mutually
    exclusive value AND as no filter at all, the API is not applying it. None = cannot tell."""
    vals = [v for v in enums.get(cond.get("path"), []) if v != cond.get("value")]
    if not vals or cond.get("negate"):
        return None
    alt = dict(cond); alt["value"] = vals[0]
    a = _post(url, headers, [cond], timeout); b = _post(url, headers, [alt], timeout); t = _post(url, headers, [], timeout)
    if None in (a, b, t):
        return None
    return a == b == t
