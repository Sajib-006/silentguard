#!/usr/bin/env python3
"""E3d: real silent-fault kinds beyond the misnamed filter (read-only GETs, about one per second, no
credentials). Two probes per API where they apply:

 A. Page-size cap. Ask for more records per page than the API allows. Classes:
      rejected     4xx: the API says no
      capped       200, fewer records than asked although the total is larger, and no warning field:
                   a list (or a count read off the list) is silently cut, the real form of "cap total"
      honored      200 and as many records as asked (or the total is smaller than the request)
 B. Bad value for a real filter (typo in an enum value or a malformed date/number). Classes:
      rejected     4xx
      ignored      200, count equals the unfiltered baseline, no warning: the filter was silently dropped
      warned       200, count equals the baseline, but the response carries a warning
      applied      200, a different count (usually 0): the value was used as given, which is correct
    plus the documented filter (with a valid value) as a reference.

    python e3d_fault_kinds.py --out ../../reeval/e3d_fault_kinds.json
"""
from __future__ import annotations

import argparse
import json
import re
import time

import requests

UA = {"User-Agent": "aob-research-silent-failure-study/1.0 (read-only measurement)"}
PAUSE = float(__import__("os").environ.get("E3D_PAUSE", "1.2"))


def path(*keys):
    def f(d):
        for k in keys:
            d = d[k]
        return d
    return f


def arxiv_total(r):
    return int(re.search(r"<opensearch:totalResults[^>]*>(\d+)<", r.text).group(1))


def arxiv_len(r):
    return len(re.findall(r"<entry>", r.text))


# name, url, base params, count(json or response), list(json or response), page probe, bad-value probe
APIS = [
 dict(name="NCBI E-utilities (gene)", url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
      base={"db": "gene", "term": "kinase", "retmode": "json", "tool": "aob_research"},
      count=lambda d: int(d["esearchresult"]["count"]), lst=lambda d: d["esearchresult"]["idlist"],
      page={"retmax": 100000},
      good={"term": "kinase AND human[orgn]"}, bad={"term": "kinase AND humn[orgn]"}, pagesmall={"retmax": 0}),
 dict(name="UniProtKB", url="https://rest.uniprot.org/uniprotkb/search",
      base={"query": "kinase", "format": "json"},
      count_hdr="x-total-results", lst=lambda d: d["results"],
      page={"size": 1000},
      good={"query": "kinase AND organism_id:9606"}, bad={"query": "kinase AND organism_id:96o6"}, pagesmall={"size": 1}),
 dict(name="ClinicalTrials.gov v2", url="https://clinicaltrials.gov/api/v2/studies",
      base={"query.cond": "diabetes", "countTotal": "true"},
      count=lambda d: int(d["totalCount"]), lst=lambda d: d["studies"],
      page={"pageSize": 5000},
      good={"filter.overallStatus": "RECRUITING"}, bad={"filter.overallStatus": "RECRUITNG"}, pagesmall={"pageSize": 1}),
 dict(name="Europe PMC", url="https://www.ebi.ac.uk/europepmc/webservices/rest/search",
      base={"query": "malaria", "format": "json"},
      count=lambda d: int(d["hitCount"]), lst=lambda d: d["resultList"]["result"],
      page={"pageSize": 5000},
      good={"query": "malaria AND PUB_YEAR:2020"}, bad={"query": "malaria AND PUB_YEAR:2o20"}, pagesmall={"pageSize": 1}),
 dict(name="OpenAlex", url="https://api.openalex.org/works",
      base={"search": "malaria"},
      count=lambda d: int(d["meta"]["count"]), lst=lambda d: d["results"],
      page={"per_page": 1000},
      good={"filter": "publication_year:2020"}, bad={"filter": "publication_year:2o20"}, pagesmall={"per_page": 1}),
 dict(name="Crossref", url="https://api.crossref.org/works",
      base={"query": "malaria"},
      count=lambda d: int(d["message"]["total-results"]), lst=lambda d: d["message"]["items"],
      page={"rows": 5000},
      good={"filter": "from-pub-date:2020-01-01,until-pub-date:2020-12-31"},
      bad={"filter": "from-pub-date:2020-13-01,until-pub-date:2020-12-31"}, pagesmall={"rows": 0}),
 dict(name="openFDA drug events", url="https://api.fda.gov/drug/event.json",
      base={"search": "patient.drug.medicinalproduct:aspirin"},
      count=lambda d: int(d["meta"]["results"]["total"]), lst=lambda d: d["results"],
      page={"limit": 5000},
      good={"search": "patient.drug.medicinalproduct:aspirin AND serious:1"},
      bad={"search": "patient.drug.medicinalproduct:aspirin AND serious:one"}, pagesmall={"limit": 1}),
 dict(name="DataCite", url="https://api.datacite.org/dois",
      base={"query": "climate"},
      count=lambda d: int(d["meta"]["total"]), lst=lambda d: d["data"],
      page={"page[size]": 5000},
      good={"resource-type-id": "dataset"}, bad={"resource-type-id": "datset"}, pagesmall={"page[size]": 1}),
 dict(name="Zenodo", url="https://zenodo.org/api/records",
      base={"q": "climate"},
      count=lambda d: int(d["hits"]["total"]), lst=lambda d: d["hits"]["hits"],
      page={"size": 5000},
      good={"type": "dataset"}, bad={"type": "datset"}, pagesmall={"size": 1}),
 dict(name="GitHub repository search", url="https://api.github.com/search/repositories",
      base={"q": "bioinformatics"},
      count=lambda d: int(d["total_count"]), lst=lambda d: d["items"],
      page={"per_page": 500},
      good={"q": "bioinformatics language:python"}, bad={"q": "bioinformatics language:pythn"}, pagesmall={"per_page": 1}),
 dict(name="ChEMBL molecules", url="https://www.ebi.ac.uk/chembl/api/data/molecule.json",
      base={},
      count=lambda d: int(d["page_meta"]["total_count"]), lst=lambda d: d["molecules"],
      page={"limit": 5000, "only": "molecule_chembl_id"},
      good={"max_phase": "4"}, bad={"max_phase": "four"}, pagesmall={"limit": 1}),
 dict(name="arXiv", url="http://export.arxiv.org/api/query",
      base={"search_query": "all:electron"},
      count_resp=arxiv_total, lst_resp=arxiv_len,
      page={"max_results": 5000},
      good={"search_query": "all:electron AND cat:hep-th"}, bad={"search_query": "all:electron AND cat:hep-tth"},
      pagesmall={"max_results": 1}),
 dict(name="GBIF occurrences", url="https://api.gbif.org/v1/occurrence/search",
      base={"scientificName": "Puma concolor"},
      count=lambda d: int(d["count"]), lst=lambda d: d["results"],
      page={"limit": 1000},
      good={"country": "US"}, bad={"country": "USA"}, pagesmall={"limit": 0}),
 dict(name="iNaturalist", url="https://api.inaturalist.org/v1/observations",
      base={"taxon_name": "Puma concolor"},
      count=lambda d: int(d["total_results"]), lst=lambda d: d["results"],
      page={"per_page": 1000},
      good={"d1": "2020-01-01"}, bad={"d1": "2020-13-45"}, pagesmall={"per_page": 0}),
 dict(name="Semantic Scholar", url="https://api.semanticscholar.org/graph/v1/paper/search",
      base={"query": "malaria"},
      count=lambda d: int(d["total"]), lst=lambda d: d.get("data", []),
      page={"limit": 1000},
      good={"year": "2020"}, bad={"year": "2o20"}, pagesmall={"limit": 1}),
]

WARN_WORDS = ("warning", "unknown", "unrecognized", "invalid", "not a valid", "ignored", "unsupported",
              "phrasesignored", "phrases ignored", "notfound")


def envelope(d):
    """Keys and scalar values outside the record lists, plus NCBI's warninglist (a dict of lists)."""
    if isinstance(d, dict):
        parts = []
        for k, v in d.items():
            if k.lower() in ("warninglist", "errorlist", "warnings", "messages"):
                parts.append(k + " " + json.dumps(v))
            elif not isinstance(v, list):
                parts.append(str(k) + " " + envelope(v))
        return " ".join(parts)
    return "" if isinstance(d, list) else str(d)


def get(api, extra, want_list=False):
    p = dict(api["base"])
    p.update(extra)
    r, last = None, None
    for wait in (0, 5, 15, 40):
        time.sleep(PAUSE + wait)
        try:
            r = requests.get(api["url"], params=p, headers=UA, timeout=60)
        except requests.RequestException as e:
            r, last = None, e.__class__.__name__
            continue
        if r.status_code not in (403, 429, 500, 502, 503, 504):
            break
    if r is None:
        return {"status": None, "err": last}
    out = {"status": r.status_code, "count": None, "n_list": None, "warn": False}
    if r.status_code != 200:
        out["body"] = r.text[:200]
        return out
    try:
        if "count_resp" in api:
            out["count"] = api["count_resp"](r)
            out["n_list"] = api["lst_resp"](r)
        else:
            d = r.json()
            out["count"] = int(r.headers[api["count_hdr"]]) if "count_hdr" in api else api["count"](d)
            out["n_list"] = len(api["lst"](d))
            env = envelope(d).lower()
            nonempty_warn = any(w in env for w in WARN_WORDS) and not re.search(r'"warninglist": \{\}', env)
            out["warn"] = bool(nonempty_warn)
    except Exception as e:  # noqa: BLE001
        out["err"] = "parse:" + e.__class__.__name__
    return out


def page_size(params):
    for k in ("retmax", "size", "pageSize", "per_page", "rows", "limit", "page[size]", "max_results"):
        if k in params:
            return int(params[k])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    rows = []
    for api in APIS:
        if a.only and a.only.lower() not in api["name"].lower():
            continue
        row = {"api": api["name"]}
        # B: bad value for a real filter (small pages)
        base = get(api, api["pagesmall"])
        good = get(api, {**api["pagesmall"], **api["good"]})
        bad = get(api, {**api["pagesmall"], **api["bad"]})
        if bad.get("status") in (None, 403, 429) or base.get("count") is None:
            cls_b = "unreachable"
        elif bad["status"] >= 400:
            cls_b = "rejected"
        elif bad.get("count") is None:
            cls_b = "unparsed"
        elif bad["count"] == base.get("count"):
            cls_b = "warned" if bad.get("warn") else "ignored"
        else:
            cls_b = "applied"
        row["bad_value"] = {"param": api["bad"], "class": cls_b, "baseline": base, "documented": good, "bad": bad}
        # A: page size above the limit
        asked = page_size(api["page"])
        pg = get(api, api["page"])
        if pg.get("status") in (None, 403, 429):
            cls_a = "unreachable"
        elif pg["status"] >= 400:
            cls_a = "rejected"
        elif pg.get("n_list") is None:
            cls_a = "unparsed"
        elif pg["n_list"] < asked and (pg.get("count") or 0) > pg["n_list"]:
            cls_a = "warned" if pg.get("warn") else "capped"
        else:
            cls_a = "honored"
        row["page_cap"] = {"asked": asked, "class": cls_a, "got": pg.get("n_list"), "total": pg.get("count"),
                           "status": pg.get("status"), "body": pg.get("body"), "err": pg.get("err")}
        rows.append(row)
        print(f"{api['name']:<26} bad value: {cls_b:<9} (base {base.get('count')}, documented {good.get('count')}, "
              f"bad {bad.get('count')} status {bad.get('status')})  |  page cap: {cls_a:<8} asked {asked} got "
              f"{pg.get('n_list')} of {pg.get('count')} status {pg.get('status')}", flush=True)
    json.dump(rows, open(a.out, "w"), indent=1)
    from collections import Counter
    print("\nbad value:", dict(Counter(r["bad_value"]["class"] for r in rows)))
    print("page cap:", dict(Counter(r["page_cap"]["class"] for r in rows)))
    print("E3D_DONE")


if __name__ == "__main__":
    main()
