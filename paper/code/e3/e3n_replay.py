#!/usr/bin/env python3
"""E3n replay (no model call). Reads the rows written by e3n_natural.py. For every run of one arm (default
"none") it takes the FINAL query the agent sent (the last call with status 200 and a count) and tests it on the
live API, about one GET per second per API (read-only, results cached by URL):

  (a) spec check     the query uses a parameter name outside the API's `allowed` set.
  (b) ignored probe  the frozen probe_ignored() of e3c_real_agents.py: a non-display parameter is ignored when the
                     count is the same with it, without it, and with a nonsense value.
  (c) identity       only for APIs with a query-language string (ncbi term, uniprot query, europepmc query,
                     arxiv search_query; zenodo q and datacite query are tested too and reported as extra).
                     Split the top-level conjunction on AND. For each conjunct B (at most 4), with A = the others:
                     N(A AND B) + N(A NOT B) == N(A). Fires when the identity fails (relative gap > --tol).
                     "not testable" when: no query string (gbif, chembl, inat), the query has top-level OR / NOT /
                     ANDNOT, it has only one conjunct, it cannot be split (unbalanced brackets), or a request failed.

Wrong runs: outcome starting with "wrong". Correct runs: outcome "correct" (false alarm measure). Other outcomes
(abstain, no_answer) are counted but not tested. Needs no API key.

    python e3n_replay.py --rows runs/e3n --out replay_summary.json
    python e3n_replay.py --selftest        # parsing tests, no network
    python e3n_replay.py --try ncbi "BRCA1 AND human[orgn]"   # one live identity test
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
_cands = [os.environ.get("AOB_E3_DIR", ""),
          str(HERE.parents[1] / "silentguard_public/paper/code/e3"),
          "/mnt/disk/sajibacharjeedip/AgentOrchBench/code/e3"]
E3 = next((Path(c) for c in _cands if c and (Path(c) / "e3c_real_agents.py").exists()), None)
if E3 is None:
    sys.exit("cannot find the frozen e3 folder; set AOB_E3_DIR")
sys.path.insert(0, str(E3.parent / "eval_v7"))
sys.path.insert(0, str(E3))
import e3c_more                                   # noqa: E402,F401
import e3c_real_agents as E                       # noqa: E402

QP = {"ncbi": "term", "uniprot": "query", "europepmc": "query", "arxiv": "search_query",
      "zenodo": "q", "datacite": "query"}
EXTRA = {"zenodo", "datacite"}
NOT_FORM = {"arxiv": "({a}) ANDNOT ({b})", "zenodo": "({a}) AND NOT ({b})", "datacite": "({a}) AND NOT ({b})"}
DEFAULT_NOT = "({a}) NOT ({b})"
MAX_CONJ = 4


class NotTestable(Exception):
    pass


def split_top(q):
    """Split q on top-level ' AND ' (upper case). Return a list of conjuncts. Raise NotTestable when the query
    cannot be parsed or has other top-level operators."""
    q = q.strip()
    if not q:
        raise NotTestable("no query string")
    toks, cur, depth_p, depth_b, inq = [], [], 0, 0, False
    for ch in q:                                   # tokenise on top-level whitespace
        if ch == '"':
            inq = not inq
        elif not inq:
            if ch == "(": depth_p += 1
            elif ch == ")": depth_p -= 1
            elif ch == "[": depth_b += 1
            elif ch == "]": depth_b -= 1
            if depth_p < 0 or depth_b < 0:
                raise NotTestable("unbalanced brackets")
            if ch.isspace() and depth_p == 0 and depth_b == 0:
                if cur: toks.append("".join(cur)); cur = []
                continue
        cur.append(ch)
    if inq or depth_p or depth_b:
        raise NotTestable("unbalanced brackets or quotes")
    if cur: toks.append("".join(cur))
    if any(t in ("OR", "NOT", "ANDNOT") for t in toks):
        raise NotTestable("top-level OR / NOT / ANDNOT")
    parts, piece = [], []
    for t in toks:
        if t == "AND":
            if not piece: raise NotTestable("dangling AND")
            parts.append(" ".join(piece)); piece = []
        else:
            piece.append(t)
    if not piece: raise NotTestable("dangling AND")
    parts.append(" ".join(piece))
    if len(parts) < 2:
        raise NotTestable("only one conjunct (no top-level AND)")
    return parts


def wrap(s):
    return s if (s.startswith("(") and s.endswith(")") and s.count("(") == 1) or " " not in s else f"({s})"


_cache = {}


def count_url(api, url):
    if url not in _cache:
        _cache[url] = E.fetch(api, url)[1]
    return _cache[url]


def identity_test(api, url, tol=0.0):
    """Return dict(testable, fires, detail). Never raises."""
    u = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    key = QP[api]
    qs = [v for k, v in params if k == key]
    if not qs:
        return {"testable": False, "reason": f"no {key} parameter"}
    try:
        conj = split_top(qs[0])
    except NotTestable as e:
        return {"testable": False, "reason": str(e)}
    others = [(k, v) for k, v in params if k != key]
    base = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, "", ""))
    cnt = lambda qq: count_url(api, E.with_params(base, dict(others + [(key, qq)])))  # noqa: E731
    details, fires = [], False
    for i, B in enumerate(conj[:MAX_CONJ]):
        rest = conj[:i] + conj[i + 1:]
        A = " AND ".join(wrap(x) for x in rest) if len(rest) > 1 else rest[0]
        nA, nAB = cnt(A), cnt(f"{wrap(A) if len(rest) > 1 else A} AND {wrap(B)}")
        nAnB = cnt(NOT_FORM.get(api, DEFAULT_NOT).format(a=A, b=B))
        d = {"A": A, "B": B, "N_A": nA, "N_AB": nAB, "N_A_not_B": nAnB}
        if None in (nA, nAB, nAnB):
            return {"testable": False, "reason": "a request failed", "detail": details + [d]}
        gap = abs(nAB + nAnB - nA)
        d["gap"] = gap; d["fires"] = gap > tol * max(nA, 1)
        fires |= d["fires"]; details.append(d)
    return {"testable": True, "fires": fires, "detail": details}


def spec_test(api, params):
    allowed = E.API[api]["allowed"]
    return sorted({k for k, _ in params if k not in allowed})


def probe_test(api, url, params, c):
    return E.probe_ignored(api, E.API[api], url, params, c)


def final_call(row):
    ok = [x for x in row.get("calls", []) if x.get("status") == 200 and x.get("count") is not None]
    return ok[-1] if ok else None


def test_final(api, call, tol, cache):
    k = (api, call["url"])
    if k in cache:
        return cache[k]
    url, c = call["url"], call["count"]
    params = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    res = {"api": api, "url": url, "count": c}
    res["a_unknown_params"] = spec_test(api, params)
    res["a"] = bool(res["a_unknown_params"])
    res["b_ignored"] = probe_test(api, url, params, c)
    res["b"] = bool(res["b_ignored"])
    if api in QP:
        res["c_result"] = identity_test(api, url, tol)
    else:
        res["c_result"] = {"testable": False, "reason": "no query-language string (filters are separate parameters)"}
    res["c_testable"] = res["c_result"]["testable"]
    res["c"] = bool(res["c_result"].get("fires"))
    res["any"] = res["a"] or res["b"] or res["c"]
    cache[k] = res
    return res


def blank():
    return {"wrong_runs": 0, "wrong_no_final_query": 0, "wrong_tested": 0, "wrong_caught_a": 0, "wrong_caught_b": 0,
            "wrong_caught_c": 0, "wrong_caught_any": 0, "wrong_c_not_testable": 0,
            "correct_runs": 0, "correct_no_final_query": 0, "correct_tested": 0, "correct_flagged_a": 0,
            "correct_flagged_b": 0, "correct_flagged_c": 0, "correct_flagged_any": 0, "correct_c_not_testable": 0,
            "other_runs_not_tested": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", help="folder with e3n-*.jsonl")
    ap.add_argument("--out", default="e3n_replay_summary.json")
    ap.add_argument("--arm", default="none")
    ap.add_argument("--tol", type=float, default=0.001, help="identity fires when gap > tol * N(A) (0.1 percent covers live drift; 0 = exact)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--try", nargs=2, metavar=("API", "QUERY"), help="live identity test of one query string")
    a = ap.parse_args()
    for k in E.API:
        E.API[k]["gap"] = max(E.API[k]["gap"], 1.05)      # at most one GET per second per API
    if a.selftest:
        return selftest()
    if a.__dict__["try"]:
        api, q = a.__dict__["try"]
        spec = E.API[api]
        url = E.with_params(spec["url"], {**dict(urllib.parse.parse_qsl(spec["base"])), QP[api]: q})
        print(json.dumps(identity_test(api, url, a.tol), indent=1)); return
    if not a.rows:
        ap.error("--rows is required")
    rows = []
    for f in sorted(glob.glob(os.path.join(a.rows, "e3n-*.jsonl"))):
        rows += [json.loads(l) for l in open(f)]
    rows = [r for r in rows if r.get("arm") == a.arm and r.get("docs") == "N"]
    stats, per_run, cache = {}, [], {}
    for r in rows:
        api = r["api"]
        for s in (stats.setdefault(api, blank()), stats.setdefault("POOLED", blank())):
            pass
        o = r.get("outcome", "")
        kind = "wrong" if o.startswith("wrong") else "correct" if o == "correct" else None
        ents = [stats[api], stats["POOLED"]]
        if kind is None:
            for s in ents: s["other_runs_not_tested"] += 1
            continue
        for s in ents: s[f"{kind}_runs"] += 1
        fc = final_call(r)
        if fc is None:
            for s in ents: s[f"{kind}_no_final_query"] += 1
            per_run.append({"key": r["key"], "api": api, "task": r["task"], "rep": r.get("rep", 0), "kind": kind, "final": None})
            continue
        t = test_final(api, fc, a.tol, cache)
        for s in ents:
            s[f"{kind}_tested"] += 1
            if kind == "wrong":
                s["wrong_caught_a"] += t["a"]; s["wrong_caught_b"] += t["b"]; s["wrong_caught_c"] += t["c"]
                s["wrong_caught_any"] += t["any"]; s["wrong_c_not_testable"] += (not t["c_testable"])
            else:
                s["correct_flagged_a"] += t["a"]; s["correct_flagged_b"] += t["b"]; s["correct_flagged_c"] += t["c"]
                s["correct_flagged_any"] += t["any"]; s["correct_c_not_testable"] += (not t["c_testable"])
        per_run.append({"key": r["key"], "api": api, "task": r["task"], "rep": r.get("rep", 0), "kind": kind,
                        "answer": r.get("answer"), "truth": r.get("truth_before"), "final": t["url"], "a": t["a"],
                        "b": t["b"], "c": t["c"], "c_testable": t["c_testable"]})
    out = {"arm": a.arm, "tol": a.tol, "summary": stats, "unique_final_queries": list(cache.values()), "runs": per_run,
           "note": "query-language APIs: ncbi, uniprot, europepmc, arxiv (main); zenodo, datacite (extra). gbif, chembl, inat have no query string, so (c) is not testable there."}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(stats["POOLED"] if "POOLED" in stats else {}, indent=1)); print("written", a.out)


def selftest():
    cases = {"BRCA1 AND human[orgn]": ["BRCA1", "human[orgn]"],
             "kinase AND organism_id:9606": ["kinase", "organism_id:9606"],
             'malaria AND (PUB_YEAR:2020 OR PUB_YEAR:2021)': ["malaria", "(PUB_YEAR:2020 OR PUB_YEAR:2021)"],
             'all:"electron beam" AND cat:hep-th': ['all:"electron beam"', "cat:hep-th"],
             "a AND b AND c": ["a", "b", "c"]}
    for q, want in cases.items():
        assert split_top(q) == want, (q, split_top(q))
    for q in ["BRCA1", "a OR b AND c", "a AND NOT b", "a ANDNOT b", "(a AND b", "", "a AND"]:
        try:
            split_top(q); raise SystemExit(f"should fail: {q!r}")
        except NotTestable:
            pass
    print("selftest ok")


if __name__ == "__main__":
    main()
