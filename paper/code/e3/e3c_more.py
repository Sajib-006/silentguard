#!/usr/bin/env python3
"""E3c, five more real APIs (added 2026-10-01 after review): Zenodo, ChEMBL, arXiv, DataCite, iNaturalist.
The locked study code (e3c_real_agents.py) is imported unchanged; this file only registers the new APIs
and then runs its main(). Each wrong-docs parameter below was measured as silently ignored in E3.

    python e3c_more.py --check                       # verify every task: truth, wrong-docs query, unfiltered
    python e3c_more.py --threads 3 --out ../../runs/matrix_e3c_more --apis zenodo,chembl,arxiv,datacite,inat \
        --arms none,guard,schema,sgp,retry2 --docs A,G
"""
from __future__ import annotations

import re
import sys
import threading
import urllib.parse

import e3c_real_agents as E


def _arxiv_count(r):
    return int(re.search(r"<opensearch:totalResults[^>]*>(\d+)<", r.text).group(1))


MORE = {
 "zenodo": dict(host="zenodo.org", url="https://zenodo.org/api/records", base="size=1",
   count=E._j(["hits", "total"]), gap=1.2,
   allowed={"q", "size", "page", "sort", "type", "subtype", "communities", "status", "all_versions", "bounds"},
   display={"size", "page", "sort", "status", "all_versions"},
   doc_A="Zenodo records search. GET https://zenodo.org/api/records?size=1&q=<query>. The JSON field hits.total is the number of matching records. Restrict to a record type with the type parameter, e.g. &q=climate&type=dataset (types: dataset, software, publication, image, video).",
   doc_G="Zenodo records search. GET https://zenodo.org/api/records?size=1&q=<query>. The JSON field hits.total is the number of matching records. Restrict to a record type with the record_type parameter, e.g. &q=climate&record_type=dataset (types: dataset, software, publication, image, video).",
   tasks=[("climate", "dataset"), ("covid", "software"), ("ocean", "dataset"), ("genome", "software"), ("ecology", "image")],
   q="How many Zenodo records match '{a}' and have record type {b}?", correct=lambda a, b: {"q": a, "type": b}),
 "chembl": dict(host="www.ebi.ac.uk", url="https://www.ebi.ac.uk/chembl/api/data/molecule.json", base="limit=1",
   count=E._j(["page_meta", "total_count"]), gap=0.6,
   allowed={"limit", "offset", "format", "only", "order_by", "max_phase", "molecule_type", "therapeutic_flag", "oral",
            "parenteral", "topical", "natural_product", "first_approval", "black_box_warning", "prodrug", "chirality",
            "inorganic_flag", "polymer_flag", "withdrawn_flag", "availability_type", "molecule_chembl_id", "pref_name"},
   display={"limit", "offset", "format", "only", "order_by"},
   doc_A="ChEMBL molecule search. GET https://www.ebi.ac.uk/chembl/api/data/molecule.json?limit=1&molecule_type=<type>. The JSON field page_meta.total_count is the number of matching molecules. Restrict to a maximum clinical phase with the max_phase parameter, e.g. &molecule_type=Antibody&max_phase=4 (URL-encode spaces).",
   doc_G="ChEMBL molecule search. GET https://www.ebi.ac.uk/chembl/api/data/molecule.json?limit=1&molecule_type=<type>. The JSON field page_meta.total_count is the number of matching molecules. Restrict to a maximum clinical phase with the phase parameter, e.g. &molecule_type=Antibody&phase=4 (URL-encode spaces).",
   tasks=[("Small molecule", "4"), ("Antibody", "4"), ("Protein", "4"), ("Small molecule", "3"), ("Antibody", "2")],
   q="How many ChEMBL molecules of type '{a}' have a maximum clinical phase of {b}?",
   correct=lambda a, b: {"molecule_type": a, "max_phase": b}),
 "arxiv": dict(host="export.arxiv.org", url="http://export.arxiv.org/api/query", base="max_results=1",
   count=_arxiv_count, gap=3.2,
   allowed={"search_query", "id_list", "start", "max_results", "sortBy", "sortOrder"},
   display={"start", "max_results", "sortBy", "sortOrder", "id_list"},
   doc_A="arXiv API query. GET http://export.arxiv.org/api/query?max_results=1&search_query=<query>. The Atom feed field opensearch:totalResults is the number of matching papers. Restrict to a subject category INSIDE search_query with cat:, e.g. search_query=all:electron AND cat:hep-th (URL-encode spaces).",
   doc_G="arXiv API query. GET http://export.arxiv.org/api/query?max_results=1&search_query=<query>. The Atom feed field opensearch:totalResults is the number of matching papers. Restrict to a subject category with the cat parameter, e.g. &search_query=all:electron&cat=hep-th.",
   tasks=[("electron", "hep-th"), ("graphene", "cond-mat.mes-hall"), ("transformer", "cs.CL"), ("protein", "q-bio.BM"),
          ("neutrino", "hep-ph")],
   q="How many arXiv papers match all:{a} in the category {b}?", correct=lambda a, b: {"search_query": f"all:{a} AND cat:{b}"}),
 "datacite": dict(host="api.datacite.org", url="https://api.datacite.org/dois", base="page[size]=1",
   count=E._j(["meta", "total"]), gap=0.6,
   allowed={"query", "resource-type-id", "page[size]", "page[number]", "page[cursor]", "sort", "client-id",
            "provider-id", "registered", "published", "affiliation-id", "fields[dois]"},
   display={"page[size]", "page[number]", "page[cursor]", "sort", "fields[dois]"},
   doc_A="DataCite DOI search. GET https://api.datacite.org/dois?page[size]=1&query=<query>. The JSON field meta.total is the number of matching DOIs. Restrict to a resource type with the resource-type-id parameter, e.g. &query=climate&resource-type-id=dataset (types: dataset, text, software, image).",
   doc_G="DataCite DOI search. GET https://api.datacite.org/dois?page[size]=1&query=<query>. The JSON field meta.total is the number of matching DOIs. Restrict to a resource type with the resourceType parameter, e.g. &query=climate&resourceType=dataset (types: dataset, text, software, image).",
   tasks=[("climate", "dataset"), ("malaria", "text"), ("ocean", "dataset"), ("genome", "software"), ("glacier", "image")],
   q="How many DataCite DOIs match '{a}' and have resource type {b}?", correct=lambda a, b: {"query": a, "resource-type-id": b}),
 "inat": dict(host="api.inaturalist.org", url="https://api.inaturalist.org/v1/observations", base="per_page=0",
   count=E._j(["total_results"]), gap=1.2,
   allowed={"taxon_name", "taxon_id", "place_id", "per_page", "page", "d1", "d2", "year", "month", "quality_grade",
            "order", "order_by", "user_id", "project_id", "iconic_taxa"},
   display={"per_page", "page", "order", "order_by"},
   doc_A="iNaturalist observation search. GET https://api.inaturalist.org/v1/observations?per_page=0&taxon_name=<name>. The JSON field total_results is the number of matching observations. Restrict to an observation year with the year parameter, e.g. &taxon_name=Puma concolor&year=2020 (URL-encode spaces).",
   doc_G="iNaturalist observation search. GET https://api.inaturalist.org/v1/observations?per_page=0&taxon_name=<name>. The JSON field total_results is the number of matching observations. Restrict to an observation year with the observed_year parameter, e.g. &taxon_name=Puma concolor&observed_year=2020 (URL-encode spaces).",
   tasks=[("Puma concolor", "2020"), ("Ursus arctos", "2019"), ("Canis lupus", "2021"), ("Lynx rufus", "2018"),
          ("Vulpes vulpes", "2022")],
   q="How many iNaturalist observations of '{a}' were made in {b}?", correct=lambda a, b: {"taxon_name": a, "year": b}),
}
E.API.update(MORE)
for k in MORE:
    E._locks[k] = threading.Lock()
    E._last[k] = 0.0

# Zenodo, ChEMBL and DataCite put the total after the record list, and the study shows the agent only the
# first 1,500 characters of a body, so the count would be cut off. For these APIs the JSON is re-serialised
# with the object that holds the count first; nothing is removed or changed. Old APIs are untouched.
COUNT_FIRST = {"zenodo": lambda d: {"hits": {"total": d["hits"]["total"], **{k: v for k, v in d["hits"].items() if k != "total"}},
                                    **{k: v for k, v in d.items() if k != "hits"}},
               "chembl": lambda d: {"page_meta": d["page_meta"], **{k: v for k, v in d.items() if k != "page_meta"}},
               "datacite": lambda d: {"meta": d["meta"], **{k: v for k, v in d.items() if k != "meta"}}}
_orig_fetch = E.fetch


def fetch(api, url):
    if api not in COUNT_FIRST:
        return _orig_fetch(api, url)
    import json as _json
    import time as _time
    import requests as _rq
    spec = E.API[api]
    r = None
    for backoff in (0, 2, 5, 12):
        with E._locks[api]:
            wait = spec["gap"] + backoff - (_time.time() - E._last[api])
            if wait > 0:
                _time.sleep(wait)
            try:
                r = _rq.get(url, headers=E.UA, timeout=40)
            except _rq.RequestException:
                E._last[api] = _time.time()
                r = None
                continue
            E._last[api] = _time.time()
        if r.status_code not in (429, 500, 502, 503, 504):
            break
    if r is None:
        return None, None, "request failed"
    c, body = None, r.text[:1500]
    if r.status_code == 200:
        try:
            c = spec["count"](r)
            body = _json.dumps(COUNT_FIRST[api](r.json()))[:1500]
        except Exception:  # noqa: BLE001
            pass
    return r.status_code, c, body


E.fetch = fetch


def check():
    """For each task: the truth (documented query), the wrong-docs query and the query without the
    second condition. The task is valid if truth > 0 and the wrong-docs count equals the unfiltered one."""
    bad = 0
    for api, spec in MORE.items():
        for a, b in spec["tasks"]:
            base = dict(urllib.parse.parse_qsl(spec["base"]))
            t = E.truth(api, a, b)
            first = {k: v for k, v in spec["correct"](a, b).items()}
            # unfiltered = documented query minus the second condition
            if api == "arxiv":
                unf = {"search_query": f"all:{a}"}
                wrong = {"search_query": f"all:{a}", "cat": b}
            else:
                key2 = [k for k in first if first[k] == b][0]
                unf = {k: v for k, v in first.items() if k != key2}
                wname = re.search(r"&(\w+)=", spec["doc_G"].split("e.g.")[1].split("&", 2)[-1] and
                                  "&" + spec["doc_G"].split("e.g.")[1].split("&")[-1]).group(1)
                wrong = {**unf, wname: b}
            c_unf = E.fetch(api, E.with_params(spec["url"], {**base, **unf}))[1]
            c_wrong = E.fetch(api, E.with_params(spec["url"], {**base, **wrong}))[1]
            ok = t not in (None, 0) and c_wrong == c_unf and t != c_unf
            bad += not ok
            print(f"{api:<9} {a[:18]:<18} {b:<18} truth={t}  wrong-docs={c_wrong}  unfiltered={c_unf}  {'OK' if ok else 'CHECK'}",
                  flush=True)
    print("tasks needing a look:", bad)


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        E.main()
