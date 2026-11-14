#!/usr/bin/env python3
"""Does the count identity hold on live public APIs? (review round 2; read-only GETs, about one per second)
For every agent-study task on the five APIs whose query language has AND and NOT, the harness's identity
    N(A and B) + N(A and not B) = N(A)
is tested on the correct query. A failure on a correct query is a false alarm of the exact check on live data.
Counts are taken back to back; small drift between queries is reported separately from exact agreement.

    python e3e_identity_live.py > ../../reeval/e3e_identity_live.json
"""
import json

import e3c_more  # noqa: F401
import e3c_real_agents as E

Q = {  # api: (param, A, A and B, A and not B)
    "ncbi": ("term", "{a}", "{a} AND {b}[orgn]", "{a} NOT {b}[orgn]"),
    "uniprot": ("query", "{a}", "{a} AND organism_id:{b}", "{a} NOT organism_id:{b}"),
    "europepmc": ("query", "{a}", "{a} AND PUB_YEAR:{b}", "{a} NOT PUB_YEAR:{b}"),
    "arxiv": ("search_query", "all:{a}", "all:{a} AND cat:{b}", "all:{a} ANDNOT cat:{b}"),
    "zenodo": ("q", "{a}", "{a} AND resource_type.type:{b}", "{a} AND NOT resource_type.type:{b}"),
}
rows = []
for api, (key, qa, qab, qanb) in Q.items():
    spec = E.API[api]
    base = dict(E.urllib.parse.parse_qsl(spec["base"]))
    for a, b in spec["tasks"]:
        def count(q):
            return E.fetch(api, E.with_params(spec["url"], {**base, key: q.format(a=a, b=b)}))[1]
        nA, nAB, nAnB = count(qa), count(qab), count(qanb)
        ok = None if None in (nA, nAB, nAnB) else (nAB + nAnB == nA)
        rel = None if None in (nA, nAB, nAnB) or not nA else abs(nAB + nAnB - nA) / nA
        rows.append({"api": api, "task": f"{a}|{b}", "N_A": nA, "N_AB": nAB, "N_AnotB": nAnB, "holds": ok, "rel_gap": rel})
n = [r for r in rows if r["holds"] is not None]
print(json.dumps({"rows": rows, "n": len(n), "exact": sum(r["holds"] for r in n),
                  "within_0.1pct": sum(r["rel_gap"] <= 0.001 for r in n)}, indent=1))
