#!/usr/bin/env python3
"""Free replay (no model): would the count identity have fired on the FINAL query of each SilentGuard real-API run?
For every distinct final query of the sgp arm (wrong docs) on the query-language APIs, split the query into its
terms, take the last term as B and the rest as A, and test N(A and B) + N(A not B) = N(A) with two extra queries.
Run on wrong finals (does it catch them?) and on correct finals (does it raise false alarms?). Read-only GETs.
    python e3g_identity_replay.py > ../../reeval/e3g_identity_replay.json
"""
import collections, glob, json, re, urllib.parse
import e3c_more  # noqa: F401
import e3c_real_agents as E

QP = {"ncbi": "term", "uniprot": "query", "europepmc": "query", "arxiv": "search_query", "zenodo": "q", "datacite": "query"}
NOT = {"arxiv": "{a} ANDNOT {b}"}
TOK = re.compile(r'(?:[^\s\[\]"()]+|\[[^\]]*\]|"[^"]*"|\([^)]*\))+')


def terms(q):
    q = q.strip()
    parts = re.split(r"\s+AND\s+", q, flags=re.I) if re.search(r"\s+AND\s+", q, re.I) else TOK.findall(q)
    return [p for p in parts if p and p.upper() not in ("AND",)]


finals = collections.defaultdict(lambda: collections.Counter())
for f in glob.glob("../../runs/matrix_e3c_sgp/*.jsonl") + glob.glob("../../runs/matrix_e3c_more/*/*.jsonl"):
    for line in open(f):
        r = json.loads(line)
        if r.get("arm") != "sgp" or r.get("docs") != "G" or r.get("error") or r["api"] not in QP or r["key"].startswith("gpt-"):
            continue
        ok = [x for x in r["calls"] if x.get("status") == 200 and x.get("count") is not None]
        if not ok or r["outcome"] not in ("wrong_num", "correct"):
            continue
        finals[(r["api"], ok[-1]["url"], ok[-1]["count"])][r["outcome"]] += 1
rows = []
for (api, url, c0), outs in sorted(finals.items()):
    u = urllib.parse.urlsplit(url); params = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    q = params.get(QP[api], "")
    ts = terms(q)
    row = {"api": api, "query": q, "runs_wrong": outs["wrong_num"], "runs_correct": outs["correct"], "n_terms": len(ts)}
    if len(ts) >= 2:
        a, b = " AND ".join(ts[:-1]), ts[-1]
        base = urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, "", ""))
        cnt = lambda qq: E.fetch(api, E.with_params(base, {**params, QP[api]: qq}))[1]
        nF, nA, nAnB = cnt(q), cnt(a), cnt(NOT.get(api, "({a}) NOT ({b})").format(a=a, b=b))
        row.update({"A": a, "B": b, "N_F": nF, "N_A": nA, "N_A_not_B": nAnB,
                    "fires": None if None in (nF, nA, nAnB) else (nF + nAnB != nA)})
    rows.append(row)
print(json.dumps(rows, indent=1))
