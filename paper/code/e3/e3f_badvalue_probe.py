#!/usr/bin/env python3
"""Step A (no model): when a real API silently ignores a typo in a filter VALUE, does one of our checks fire?
Read-only GETs, rate-limited. Europe PMC and GitHub: the value sits inside a query string, so only the count identity
N(A and B) + N(A and not B) = N(A) can see it. iNaturalist: the value is a normal parameter (d1), so the harness's
ignored-parameter probe applies.      python e3f_badvalue_probe.py > ../../reeval/e3f_badvalue_probe.json
"""
import json, time, urllib.parse
import requests
import e3c_more  # noqa: F401
import e3c_real_agents as E

rows = []


def typo(year):
    y = str(year)
    return y.replace("0", "o", 1) if "0" in y else y[:-1] + "x"


# Europe PMC: query-string value
spec = E.API["europepmc"]; base = dict(urllib.parse.parse_qsl(spec["base"]))
def epmc(q):
    return E.fetch("europepmc", E.with_params(spec["url"], {**base, "query": q}))[1]
for a, b in spec["tasks"]:
    bad = typo(b)
    nA, good, nbad, nnot_bad, nnot_good = epmc(a), epmc(f"{a} AND PUB_YEAR:{b}"), epmc(f"{a} AND PUB_YEAR:{bad}"), epmc(f"{a} NOT PUB_YEAR:{bad}"), epmc(f"{a} NOT PUB_YEAR:{b}")
    rows.append({"api": "europepmc", "task": f"{a}|{b}", "bad_value": bad, "N_A": nA, "N_good": good, "N_bad": nbad, "N_A_not_bad": nnot_bad,
                 "silently_ignored": nbad == nA, "wrong_count": nbad != good,
                 "identity_fires_on_bad": None if None in (nA, nbad, nnot_bad) else (nbad + nnot_bad != nA),
                 "identity_fires_on_good": None if None in (nA, good, nnot_good) else (good + nnot_good != nA)})

# iNaturalist: parameter value (d1 = observed on or after)
spec = E.API["inat"]; base = dict(urllib.parse.parse_qsl(spec["base"]))
def inat(params):
    return E.fetch("inat", E.with_params(spec["url"], {**base, **params}))[1]
for a, b in spec["tasks"]:
    good_v, bad_v = f"{b}-01-01", f"{b}-13-45"
    nA, good, nbad = inat({"taxon_name": a}), inat({"taxon_name": a, "d1": good_v}), inat({"taxon_name": a, "d1": bad_v})
    ig_bad = E.probe_ignored("inat", spec, spec["url"], list({**base, "taxon_name": a, "d1": bad_v}.items()), nbad)
    ig_good = E.probe_ignored("inat", spec, spec["url"], list({**base, "taxon_name": a, "d1": good_v}.items()), good)
    rows.append({"api": "inat", "task": f"{a}|{b}", "bad_value": bad_v, "N_A": nA, "N_good": good, "N_bad": nbad,
                 "silently_ignored": nbad == nA, "wrong_count": nbad != good,
                 "probe_flags_on_bad": ig_bad, "probe_flags_on_good": ig_good})

# GitHub repository search: query-string value (unauthenticated: 10 requests a minute)
def gh(q):
    for wait in (7, 30, 65):
        time.sleep(wait)
        r = requests.get("https://api.github.com/search/repositories", params={"q": q, "per_page": 1},
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "silent-api-probe"}, timeout=40)
        if r.status_code == 200:
            return r.json().get("total_count")
        if r.status_code not in (403, 429):
            return None
    return None
for a in ["bioinformatics", "genomics", "proteomics", "phylogenetics", "metagenomics"]:
    nA, good, nbad, nnot_bad, nnot_good = gh(a), gh(f"{a} language:python"), gh(f"{a} language:pythn"), gh(f"{a} -language:pythn"), gh(f"{a} -language:python")
    rows.append({"api": "github", "task": f"{a}|python", "bad_value": "pythn", "N_A": nA, "N_good": good, "N_bad": nbad, "N_A_not_bad": nnot_bad,
                 "silently_ignored": nbad == nA, "wrong_count": nbad != good,
                 "identity_fires_on_bad": None if None in (nA, nbad, nnot_bad) else (nbad + nnot_bad != nA),
                 "identity_fires_on_good": None if None in (nA, good, nnot_good) else (good + nnot_good != nA),
                 "N_A_not_good": nnot_good})
print(json.dumps(rows, indent=1))
