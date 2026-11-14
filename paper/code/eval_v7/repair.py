"""Repair-then-verify: recover a count through a faulty API, and accept only a verified repair.

Called by the harness when the partition check has FAILED on a filter query. It tries a small
library of generic repairs. Each repair comes with its own check, chosen so that the faults we know
about cannot make it pass with a wrong value. The first repair whose check passes is returned; if
none passes, the harness abstains. No fault labels are used anywhere.

  pad    add a filter that matches everything as the LAST filter, so a fault that drops the last
         filter drops the dummy. Precondition: the dummy alone must return the full catalog.
         Check: the partition identity holds on the padded queries.
  ie     if a conjunction comes back LARGER than every one of its parts, the API is not doing AND.
         Treat single-filter queries (where AND and OR agree) as honest and rebuild the AND count by
         inclusion-exclusion over the unions the API returns. Check: an independent complement
         query must match what an OR-API would return, and the result must lie in [0, min part].
  split  recount by splitting on enumerated fields the query does not use, two different ways
         (one field alone, and a two-field grid). A consistent API gives the same total both ways;
         a constant per-query error (e.g. +1) cannot, because the two splits have different numbers
         of pieces. Needs the value lists from /schema.

The module never raises; every outcome is reported with the queries it cost.
"""
from __future__ import annotations

import copy
import itertools
import json

import requests

TAUT_VALUE = "__aob_matches_nothing__"


class _Q:
    def __init__(self, base, headers, timeout):
        self.base, self.headers, self.timeout = base.rstrip("/"), headers, timeout
        self.cache, self.n = {}, 0

    def __call__(self, filters):
        key = json.dumps(filters, sort_keys=True)
        if key in self.cache:
            return self.cache[key]
        self.n += 1
        try:
            r = requests.post(self.base + "/search", headers=self.headers, timeout=self.timeout,
                              data=json.dumps({"json_metadata_filters": filters, "limit": 50}))
            v = r.json().get("total_count") if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            v = None
        self.cache[key] = v
        return v


def _neg(f):
    g = copy.deepcopy(f); g["negate"] = not g.get("negate", False); return g


def _eq(path, value):
    return {"mode": "path", "path": path, "value": value}


def _enums(base, headers, timeout):
    """Field -> allowed values, from /schema. Understands both schema shapes we serve."""
    try:
        r = requests.get(base.rstrip("/") + "/schema", headers=headers, timeout=timeout)
        j = r.json() if r.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        return {}
    out = {}
    for f in j.get("fields") or []:
        if isinstance(f, dict) and f.get("allowed"):
            out[f["name"]] = list(f["allowed"])
    for k, v in (j.get("enums") or {}).items():
        out[k] = list(v)
    return out


def _pad(q, F):
    taut = {"mode": "path", "path": F[0]["path"], "value": TAUT_VALUE, "negate": True}
    total = q([])
    if total is None or q([taut]) != total:
        return None, "pad: match-everything filter not honoured"
    P = lambda X: q(X + [taut])
    n, m, t = P(F), P(F[:-1] + [_neg(F[-1])]), P(F[:-1])
    if None in (n, m, t):
        return None, "pad: query failed"
    if n + m != t:
        return None, "pad: padded identity fails"
    return n, "pad"


def _ie(q, F):
    if len(F) < 2 or any(f.get("negate") for f in F):
        return None, "ie: needs >= 2 positive filters"
    singles = [q([f]) for f in F]
    whole = q(F)
    if None in singles or whole is None:
        return None, "ie: query failed"
    if not whole > max(singles):
        return None, "ie: not an OR pattern"
    c = 0
    for r in range(1, len(F) + 1):
        for T in itertools.combinations(F, r):
            v = q(list(T))
            if v is None:
                return None, "ie: query failed"
            c += (-1) ** (r + 1) * v
    if not 0 <= c <= min(singles):
        return None, "ie: result out of bounds"
    total, rest = q([]), q(F[:-1])
    got = q(F[:-1] + [_neg(F[-1])])
    if None in (total, rest, got) or got != total - whole + rest:
        return None, "ie: OR hypothesis fails its complement check"
    return c, "ie"


def _split(q, F, enums):
    used = {f.get("path") for f in F}
    rem = sorted(((p, v) for p, v in enums.items() if p not in used and len(v) >= 2),
                 key=lambda x: -len(x[1]))
    whole = q(F)

    def one(p, vals):
        parts = [q(F + [_eq(p, v)]) for v in vals]
        return None if any(x is None for x in parts) else sum(parts)

    def grid(p1, v1, p2, v2):
        parts = [q(F + [_eq(p1, a), _eq(p2, b)]) for a in v1 for b in v2]
        return None if any(x is None for x in parts) else sum(parts)

    if len(rem) >= 3:
        A = one(*rem[0]); B = grid(rem[1][0], rem[1][1], rem[2][0], rem[2][1])
        sizes = (len(rem[0][1]), len(rem[1][1]) * len(rem[2][1]))
    elif len(rem) == 2 and len(rem[0][1]) != len(rem[1][1]):
        A = one(*rem[0]); B = one(*rem[1])
        sizes = (len(rem[0][1]), len(rem[1][1]))
    else:
        return None, "split: not enough enumerated fields"
    if sizes[0] == sizes[1]:
        return None, "split: the two splits have the same number of pieces"
    if A is None or B is None:
        return None, "split: query failed"
    if A != B:
        return None, "split: the two recounts disagree"
    if whole is not None and A < whole:
        return None, "split: recount below the reported total"
    return A, "split"


def repair_count(base, headers, filters, timeout=15.0):
    """Try each repair in turn. Returns {value, route, tried, queries}; value None = abstain."""
    out = {"value": None, "route": None, "tried": [], "queries": 0}
    if not filters or any(f.get("mode", "path") != "path" for f in filters):
        out["tried"].append("not applicable")
        return out
    q = _Q(base, headers, timeout)
    enums = _enums(base, headers, timeout)
    for name, fn in (("pad", lambda: _pad(q, filters)),
                     ("ie", lambda: _ie(q, filters)),
                     ("split", lambda: _split(q, filters, enums))):
        try:
            v, why = fn()
        except Exception as e:                     # a repair must never crash the harness
            v, why = None, f"{name}: error {e.__class__.__name__}"
        out["tried"].append(why)
        if v is not None:
            out["value"], out["route"] = v, name
            break
    out["queries"] = q.n
    return out
