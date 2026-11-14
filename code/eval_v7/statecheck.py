"""B1b — post-hoc verification of side effects against live API state.

The benchmark's ground truth grades the agent's ANSWER STRING. For any task that asks the agent
to *do* something (post a comment, register a sample, create a deposit), a needle such as
`comment_posted=yes` is satisfied by an agent that simply writes those characters without ever
making the call. ~41 HD100 tasks carry such a needle.

A state check runs AFTER the agent finishes and BEFORE the environment is reset, queries the API
directly, and asserts the effect actually exists. A task is correct only if the answer verifies
AND every state check passes.

Check spec (stored on the task, JSON-serialisable):

    {"port": 8004, "method": "GET", "path": "/issues/BENCH-002/comments",
     "auth": "task", "assert": {"path": "total_count", "op": ">=", "value": 1}}

  port   canonical port (mapped to the worker's real port at run time)
  auth   which credential to present: a key of AUTH by domain, or "none"
  assert path = dotted path into the JSON body ("" = whole body);
         op   = one of == != >= <= > < contains not_contains exists len>=
"""
from __future__ import annotations

import json

import requests

import executor

AUTH = {
    "bio": {
        "transfer": ("X-Api-Key", "bench-transfer-key"),
        "registry": ("X-Api-Key", "bench-registry-key"),
        "catalog": ("X-Api-Key", "bench-catalog-key"),
        "task": ("X-Api-Token", "bench-task-token"),
        "pipeline": ("X-Api-Key", "bench-pipeline-key"),
        "publish": ("X-Api-Key", "bench-zenodo-key"),
        "galaxy": ("X-Api-Key", "bench-galaxy-key"),
    },
    "retail": {
        "orders": ("X-Api-Key", "bench-orders-key"),
        "inventory": ("X-Api-Key", "bench-inventory-key"),
        "tickets": ("X-Api-Token", "bench-tickets-token"),
    },
}


def _dig(body, path: str):
    if path in ("", None):
        return body
    cur = body
    for part in str(path).split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _cmp(got, op: str, want) -> bool:
    try:
        if op == "==":
            return got == want
        if op == "!=":
            return got != want
        if op in (">=", "<=", ">", "<"):
            if got is None:
                return False
            g, w = float(got), float(want)
            return {">=": g >= w, "<=": g <= w, ">": g > w, "<": g < w}[op]
        if op == "contains":
            return want in json.dumps(got) if not isinstance(got, str) else want in got
        if op == "not_contains":
            return not (want in json.dumps(got) if not isinstance(got, str) else want in got)
        if op == "exists":
            return got is not None
        if op == "len>=":
            return got is not None and len(got) >= int(want)
    except (TypeError, ValueError):
        return False
    return False


def _db_check(c: dict, port_base: int) -> dict:
    """Read the service's sqlite file directly.

    Used where the API exposes no read endpoint for the state we must verify (registered
    samples, created deposits). Reading the store is also the un-spoofable way to grade a
    security task: the agent cannot influence it, and no new endpoint is added to the surface
    the agent can see.
    """
    import sqlite3
    worker = (port_base - 8000) // 10
    db = f"/tmp/aob_w{worker}/{c['db']}.db"
    rec = {"check": c, "status": "db", "got": None, "passed": False, "error": None}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        got = con.execute(c["sql"]).fetchone()[0]
        con.close()
        rec["got"] = got
        a = c.get("assert") or {}
        rec["passed"] = _cmp(got, a.get("op", "exists"), a.get("value"))
    except Exception as e:  # missing file, locked db, bad sql
        rec["error"] = f"{e.__class__.__name__}: {e}"
    return rec


def run_checks(checks: list[dict], port_base: int = 8000, domain: str = "bio",
               timeout: float = 10.0) -> tuple[bool | None, list[dict]]:
    """Run every check. Returns (all_passed or None if there are no checks, details)."""
    if not checks:
        return None, []
    out, ok_all = [], True
    for c in checks:
        if c.get("db"):
            rec = _db_check(c, port_base)
            ok_all = ok_all and rec["passed"]
            out.append(rec)
            continue
        port = executor.real_port(int(c["port"]), port_base)
        headers = {"Content-Type": "application/json"}
        auth = c.get("auth", "none")
        if auth != "none":
            k, v = AUTH[domain][auth]
            headers[k] = v
        rec = {"check": c, "status": None, "got": None, "passed": False, "error": None}
        try:
            r = requests.request(c.get("method", "GET"), f"http://127.0.0.1:{port}{c['path']}",
                                 headers=headers,
                                 data=json.dumps(c["body"]) if c.get("body") else None,
                                 timeout=timeout)
            rec["status"] = r.status_code
            try:
                body = r.json()
            except ValueError:
                body = r.text
            a = c.get("assert") or {}
            got = _dig(body, a.get("path", ""))
            rec["got"] = got if not isinstance(got, (dict, list)) else json.dumps(got)[:200]
            expected_status = c.get("expect_status")
            status_ok = (expected_status is None) or (r.status_code == expected_status)
            rec["passed"] = status_ok and (_cmp(got, a.get("op", "exists"), a.get("value"))
                                           if a else status_ok)
        except requests.RequestException as e:
            rec["error"] = f"{e.__class__.__name__}: {e}"
        ok_all = ok_all and rec["passed"]
        out.append(rec)
    return ok_all, out
