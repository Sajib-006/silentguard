"""E9 record-level checks: for answers that are LISTS of records, not counts.

check_records(filters, response_json, ...) -> verdict dict:
  * every returned record satisfies every condition (read from its own metadata);
  * no record appears twice;
  * when the page holds everything, the number of records equals total_count;
  * the every-position count identity (d7_harness_v3) on the counts.
repair_records(...) keeps only the returned records that satisfy the conditions (deduplicated) and
accepts that set only if the same identity holds on FILTERED record lists, i.e. for each condition Ci:
|filter(F)| + |filter(F without Ci, and not Ci)| == |filter(F without Ci)|.
Record evidence can remove records that should not be there; it cannot reveal a record the API never
returned (predicted limit, see the manifesto).
"""
from __future__ import annotations
import copy, json
import requests
import d7_harness_v3 as v3


def _ok(meta, f):
    hit = meta.get(f.get("path")) == f.get("value")
    return (not hit) if f.get("negate") else hit


def matches(item, filters):
    meta = item.get("json_metadata") or {}
    return all(_ok(meta, f) for f in filters if f.get("mode", "path") == "path")


def _list(url, headers, filters, timeout):
    try:
        r = requests.post(url, headers=headers, timeout=timeout,
                          data=json.dumps({"json_metadata_filters": filters, "limit": 1000}))
        if r.status_code != 200:
            return None
        j = r.json()
        return j.get("results"), j.get("total_count")
    except (requests.RequestException, ValueError):
        return None


def check_records(url, headers, filters, j, timeout=15.0):
    res = j.get("results") or []; total = j.get("total_count")
    ids = [it.get("item_id") for it in res]
    bad = [it.get("item_id") for it in res if not matches(it, filters)]
    dup = len(ids) != len(set(ids))
    page = (j.get("query_used") or {}).get("limit")
    incomplete = total is not None and len(res) < total and (page is None or len(res) < page)
    over = total is not None and len(res) > total
    cnt = v3.verify_all(url, headers, filters, total, timeout=timeout)
    problems = []
    if bad: problems.append("some returned records do not satisfy the conditions")
    if dup: problems.append("some records are returned more than once")
    if incomplete or over: problems.append("the number of records does not match total_count")
    if cnt["ran"] and cnt["consistent"] is False: problems.append("the counts are not consistent across conditions")
    return {"ran": True, "consistent": not problems, "problems": problems, "n_bad": len(bad), "dup": dup,
            "ids": sorted(set(ids)), "count_check": cnt.get("consistent"),
            "text": ("[HARNESS CHECK] passed: every record matches the request and the counts are consistent."
                     if not problems else "[HARNESS CHECK] FAILED: " + "; ".join(problems) +
                     ". This result is not reliable.")}


def repair_records(url, headers, filters, j, timeout=15.0):
    keep = {}
    for it in j.get("results") or []:
        if matches(it, filters):
            keep[it["item_id"]] = it
    S = set(keep)
    for i, c in enumerate(filters):
        rest = [f for k, f in enumerate(filters) if k != i]
        neg = copy.deepcopy(c); neg["negate"] = not neg.get("negate", False)
        a = _list(url, headers, rest + [neg], timeout); b = _list(url, headers, rest, timeout)
        if a is None or b is None or a[0] is None or b[0] is None:
            return {"value": None, "route": None, "tried": ["records: query failed"]}
        A = {it["item_id"] for it in a[0] if matches(it, rest + [neg])}
        Bs = {it["item_id"] for it in b[0] if matches(it, rest)}
        if len(S) + len(A) != len(Bs) or not (S | A) <= Bs:
            return {"value": None, "route": None, "tried": [f"records: filtered partition fails at condition {i}"]}
    return {"value": sorted(S), "route": "filter", "tried": ["records: filtered partition holds"]}
