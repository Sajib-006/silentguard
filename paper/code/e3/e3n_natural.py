#!/usr/bin/env python3
"""E3n (pilot P2-03): agents on 9 real public APIs with NO parameter documentation ("from memory").

The frozen study code (e3c_real_agents.py, e3c_more.py) is imported unchanged. This file only adds one
documentation variant, "N": the agent is told the API name, the endpoint URL (with the fixed format
parameters the code always sends), where the count is in the reply, and the task. No filter parameter
names, no tags, no query syntax. Tasks, truth, arms (none, schema, sgp, ...) and the row format are the
frozen ones; rows get "docs": "N" and a "rep" field.

Frozen code has no seed option, so each run is repeated --repeats times. The frozen run_one() always
passes seed=0 to the model. Here the seed is set to the repeat number (rep 0 = the frozen seed 0), through
a wrapper around providers.chat in THIS file. Nothing in the frozen files is edited.

No API key is needed to import (providers only reads a .env file if it exists) and none is needed for --check.

    python e3n_natural.py --check
    python e3n_natural.py --only gpt-oss-120b --arms none,schema --repeats 2 --threads 3 --out runs/e3n
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

# ---- find the frozen code (override with AOB_E3_DIR) ----
HERE = Path(__file__).resolve().parent
_cands = [os.environ.get("AOB_E3_DIR", ""),
          str(HERE.parents[1] / "silentguard_public/paper/code/e3"),
          "/mnt/disk/sajibacharjeedip/AgentOrchBench/code/e3"]
E3 = next((Path(c) for c in _cands if c and (Path(c) / "e3c_real_agents.py").exists()), None)
if E3 is None:
    sys.exit("cannot find the frozen e3 folder; set AOB_E3_DIR")
sys.path.insert(0, str(E3.parent / "eval_v7"))   # providers, roster, strict_grade
sys.path.insert(0, str(E3))
import e3c_more                                   # noqa: E402,F401  (registers 5 more APIs, patches fetch)
import e3c_real_agents as E                       # noqa: E402
import providers, roster                          # noqa: E402

FROZEN = ["e3c_real_agents.py", "e3c_more.py"]
ALL_APIS = ["ncbi", "uniprot", "europepmc", "gbif", "zenodo", "chembl", "arxiv", "datacite", "inat"]

NOTE = (" The search and filter parameters of this API are not documented here: use what you know about it.")
DOC_N = {
 "ncbi": "NCBI E-utilities esearch for the gene database. Endpoint: GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=gene&retmode=json&retmax=0 (then add your own search parameters). The JSON key esearchresult.count is the number of matching genes." + NOTE,
 "uniprot": "UniProtKB search. Endpoint: GET https://rest.uniprot.org/uniprotkb/search?format=json&size=1 (then add your own search parameters). The response header x-total-results is the number of matching entries." + NOTE,
 "europepmc": "Europe PMC search. Endpoint: GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?format=json&pageSize=1 (then add your own search parameters). The JSON key hitCount is the number of matching publications." + NOTE,
 "gbif": "GBIF occurrence search. Endpoint: GET https://api.gbif.org/v1/occurrence/search?limit=0 (then add your own search parameters). The JSON key count is the number of matching occurrence records." + NOTE,
 "zenodo": "Zenodo records search. Endpoint: GET https://zenodo.org/api/records?size=1 (then add your own search parameters). The JSON key hits.total is the number of matching records." + NOTE,
 "chembl": "ChEMBL molecule search. Endpoint: GET https://www.ebi.ac.uk/chembl/api/data/molecule.json?limit=1 (then add your own search parameters). The JSON key page_meta.total_count is the number of matching molecules." + NOTE,
 "arxiv": "arXiv API query. Endpoint: GET http://export.arxiv.org/api/query?max_results=1 (then add your own search parameters). The Atom feed field opensearch:totalResults is the number of matching papers." + NOTE,
 "datacite": "DataCite DOI search. Endpoint: GET https://api.datacite.org/dois?page[size]=1 (then add your own search parameters). The JSON key meta.total is the number of matching DOIs." + NOTE,
 "inat": "iNaturalist observation search. Endpoint: GET https://api.inaturalist.org/v1/observations?per_page=0 (then add your own search parameters). The JSON key total_results is the number of matching observations." + NOTE,
}
for _k, _d in DOC_N.items():
    E.API[_k]["doc_N"] = _d

# ---- seed per repeat (frozen run_one passes seed=0) ----
_tl = threading.local()
_orig_chat = providers.chat


def _chat(prov, model, msgs, **kw):
    kw["seed"] = getattr(_tl, "rep", 0)
    return _orig_chat(prov, model, msgs, **kw)


providers.chat = _chat


def lint(api):
    """doc_N must not contain any filter parameter name (allowed minus display minus names in the base URL)."""
    spec = E.API[api]
    base_names = {kv.split("=")[0] for kv in spec["base"].split("&")}
    secret = spec["allowed"] - spec["display"] - base_names
    return sorted(n for n in secret if re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", spec["doc_N"]))


def check(apis):
    print("frozen file hashes (sha256, first 16):")
    for f in FROZEN:
        print(f"  {f} {hashlib.sha256((E3 / f).read_bytes()).hexdigest()[:16]}")
    bad = 0
    for api in apis:
        spec = E.API[api]
        spec["gap"] = max(spec["gap"], 1.05)          # at most one GET per second per API
        print(f"\n== {api}\ndoc_N: {spec['doc_N']}")
        leaks = lint(api)
        if leaks:
            print("  LINT FAIL, doc_N names filter parameters:", leaks); bad += 1
        ok_api = not leaks
        for a, b in spec["tasks"]:
            t = E.truth(api, a, b)
            ok = isinstance(t, int) and t > 0
            ok_api &= ok
            print(f"  task {a}|{b:<18} truth={t}  {'OK' if ok else 'FAIL'}", flush=True)
        print(f"  RESULT {api}: {'PASS' if ok_api else 'FAIL'}")
        bad += (not ok_api) and not leaks
    print("\nCHECK", "PASSED for all APIs" if not bad else f"FAILED ({bad} problems)")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out"); ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--only", default=None, help="model key(s), comma separated")
    ap.add_argument("--apis", default=",".join(ALL_APIS))
    ap.add_argument("--arms", default="none,schema")
    ap.add_argument("--repeats", type=int, default=2, help="runs per task; rep r uses seed r")
    ap.add_argument("--limit-tasks", type=int, default=None)
    ap.add_argument("--check", action="store_true", help="no model call: print doc_N and test the truth queries")
    a = ap.parse_args()
    apis = a.apis.split(",")
    if a.check:
        sys.exit(check(apis))
    if not a.out:
        ap.error("--out is required")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    ents = [e for e in roster.roster(False, False) if e[1] != "anthropic"]
    if a.only:
        ents = [e for e in ents if e[0] in a.only.split(",")]
    jobs = [(e, api, t, "N", arm, rep) for e in ents for api in apis for t in E.API[api]["tasks"][:a.limit_tasks]
            for arm in a.arms.split(",") for rep in range(a.repeats)]
    done = set()
    for f in out.glob("e3n-*.jsonl"):
        for line in open(f):
            r = json.loads(line); done.add((r["key"], r["api"], r["task"], r["docs"], r["arm"], r.get("rep", 0)))
    jobs = [j for j in jobs if (j[0][0], j[1], f"{j[2][0]}|{j[2][1]}", j[3], j[4], j[5]) not in done]
    print(f"{len(jobs)} runs to do ({len(done)} already done)", flush=True)
    lock = threading.Lock(); q = list(jobs)

    def worker():
        while True:
            with lock:
                if not q:
                    return
                e, api, t, docs, arm, rep = q.pop(0)
            _tl.rep = rep
            row = E.run_one(e, api, t, docs, arm)
            row["rep"] = rep
            with lock:
                with open(out / f"e3n-{row['key']}.jsonl", "a") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(f"{time.strftime('%H:%M')} {row['key']:<16} {row['api']:<10} N {row['arm']:<6} rep{rep} {row['outcome']:<9} ans={row['answer'][:24]!r} truth={row['truth_before']}", flush=True)
    ths = [threading.Thread(target=worker) for _ in range(a.threads)]
    [t.start() for t in ths]; [t.join() for t in ths]
    print("E3N_DONE", flush=True)


if __name__ == "__main__":
    main()
