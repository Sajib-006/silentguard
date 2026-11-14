#!/usr/bin/env python3
"""E3b — does a model-free check catch the REAL silent failures, on every API?

The partition identity needs a NOT operator, which 8 of the 14 APIs lack, and it cannot negate a URL
parameter the API ignores. A simpler exact check works everywhere: a filter that is really applied
cannot return the SAME count for two values that exclude each other. So for each filter:
    fire  iff  count(filter=v1) == count(filter=v2) == count(no filter)
Run on every wrong parameter from E3 (silent or not) and on each API's documented filter, where it
must NOT fire. Read-only GETs, about one per second, no credentials, no personal data.

    python e3b_value_swap.py --out ../../reeval/e3b_value_swap.json
"""
from __future__ import annotations
import argparse, json, re, time
import requests

UA = {"User-Agent": "aob-research-silent-failure-study/1.0 (read-only measurement)"}


def j(path):
    def f(r):
        d = r.json()
        for k in path:
            d = d[k]
        return int(d)
    return f


def header(name):
    return lambda r: int(r.headers[name])


def arxiv(r):
    return int(re.search(r"<opensearch:totalResults[^>]*>(\d+)<", r.text).group(1))


# name, url, base params, count fn, wrong params [(name, v1, v2)], documented filter (param, v1, v2)
APIS = [
 ("NCBI E-utilities (gene)", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
  {"db": "gene", "term": "BRCA1", "retmode": "json", "retmax": 0, "tool": "aob_research"}, j(["esearchresult", "count"]),
  [("organism", "human", "mouse"), ("species", "Homo sapiens", "Mus musculus"), ("taxid", "9606", "10090")],
  ("term", "BRCA1 AND human[orgn]", "BRCA1 AND mouse[orgn]")),
 ("UniProtKB", "https://rest.uniprot.org/uniprotkb/search", {"query": "BRCA1", "size": 1, "format": "json"},
  header("x-total-results"),
  [("organism_id", "9606", "10090"), ("organism", "human", "mouse"), ("taxonomy", "9606", "10090")],
  ("query", "BRCA1 AND organism_id:9606", "BRCA1 AND organism_id:10090")),
 ("ClinicalTrials.gov v2", "https://clinicaltrials.gov/api/v2/studies", {"query.cond": "diabetes", "countTotal": "true", "pageSize": 1},
  j(["totalCount"]),
  [("status", "RECRUITING", "COMPLETED"), ("overallStatus", "RECRUITING", "COMPLETED"), ("recruitment_status", "RECRUITING", "COMPLETED")],
  ("filter.overallStatus", "RECRUITING", "COMPLETED")),
 ("Europe PMC", "https://www.ebi.ac.uk/europepmc/webservices/rest/search", {"query": "malaria", "format": "json", "pageSize": 1},
  j(["hitCount"]), [("year", "2020", "2019"), ("pub_year", "2020", "2019"), ("date", "2020", "2019")],
  ("query", "malaria AND PUB_YEAR:2020", "malaria AND PUB_YEAR:2019")),
 ("OpenAlex", "https://api.openalex.org/works", {"search": "malaria", "per_page": 1}, j(["meta", "count"]),
  [("year", "2020", "2019"), ("publication_year", "2020", "2019"), ("from_publication_date", "2020-01-01", "2019-01-01")],
  ("filter", "publication_year:2020", "publication_year:2019")),
 ("Crossref", "https://api.crossref.org/works", {"query": "malaria", "rows": 0}, j(["message", "total-results"]),
  [("year", "2020", "2019"), ("from-pub-date", "2020-01-01", "2019-01-01"), ("published", "2020", "2019")],
  ("filter", "from-pub-date:2020-01-01,until-pub-date:2020-12-31", "from-pub-date:2019-01-01,until-pub-date:2019-12-31")),
 ("openFDA drug events", "https://api.fda.gov/drug/event.json", {"search": "patient.drug.medicinalproduct:aspirin", "limit": 1},
  j(["meta", "results", "total"]), [("serious", "1", "2"), ("seriousness", "1", "2"), ("filter", "serious:1", "serious:2")],
  ("search", "patient.drug.medicinalproduct:aspirin AND serious:1", "patient.drug.medicinalproduct:aspirin AND serious:2")),
 ("DataCite", "https://api.datacite.org/dois", {"query": "climate", "page[size]": 1}, j(["meta", "total"]),
  [("type", "dataset", "software"), ("resourceType", "dataset", "software"), ("resource_type", "dataset", "software")],
  ("resource-type-id", "dataset", "software")),
 ("Zenodo", "https://zenodo.org/api/records", {"q": "climate", "size": 1}, j(["hits", "total"]),
  [("resource_type", "dataset", "software"), ("record_type", "dataset", "software"), ("kind", "dataset", "software")],
  ("q", "climate AND resource_type.type:dataset", "climate AND resource_type.type:software")),
 ("GitHub repository search", "https://api.github.com/search/repositories", {"q": "bioinformatics", "per_page": 1},
  j(["total_count"]), [("language", "python", "rust"), ("lang", "python", "rust")],
  ("q", "bioinformatics language:python", "bioinformatics language:rust")),
 ("ChEMBL molecules", "https://www.ebi.ac.uk/chembl/api/data/molecule.json", {"limit": 1}, j(["page_meta", "total_count"]),
  [("phase", "4", "3"), ("clinical_phase", "4", "3"), ("maxphase", "4", "3")], ("max_phase", "4", "3")),
 ("arXiv", "http://export.arxiv.org/api/query", {"search_query": "all:electron", "max_results": 1}, arxiv,
  [("cat", "hep-th", "cs.CL"), ("category", "hep-th", "cs.CL")],
  ("search_query", "all:electron AND cat:hep-th", "all:electron AND cat:cs.CL")),
 ("GBIF occurrences", "https://api.gbif.org/v1/occurrence/search", {"scientificName": "Puma concolor", "limit": 0},
  j(["count"]), [("countryCode", "US", "CA"), ("nation", "US", "CA"), ("country_code", "US", "CA")], ("country", "US", "CA")),
 ("iNaturalist", "https://api.inaturalist.org/v1/observations", {"taxon_name": "Puma concolor", "per_page": 0},
  j(["total_results"]), [("country", "US", "CA"), ("place", "US", "CA"), ("place_name", "United States", "Canada")],
  ("place_id", "1", "6712")),
]


def get(url, params, count):
    for wait in (0, 5, 15, 40):
        time.sleep(1.2 + wait)
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
        except requests.RequestException:
            continue
        if r.status_code in (403, 429, 500, 502, 503, 504):
            continue
        if r.status_code != 200:
            return None, r.status_code
        try:
            return count(r), 200
        except Exception:
            return None, "parse"
    return None, "unreachable"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); a = ap.parse_args()
    out = []
    for name, url, base, count, wrong, doc in APIS:
        b, bs = get(url, base, count)
        row = {"api": name, "baseline": b, "probes": []}
        if b is None:
            row["unreachable"] = str(bs); out.append(row); print(f"{name:<26} UNREACHABLE ({bs})"); continue
        for kind, (param, v1, v2) in [("wrong", w) for w in wrong] + [("documented", doc)]:
            p1 = dict(base); p1[param] = v1
            p2 = dict(base); p2[param] = v2
            c1, s1 = get(url, p1, count); c2, s2 = get(url, p2, count)
            if c1 is None or c2 is None:
                verdict = f"no count (HTTP {s1}/{s2})"; fired = None
            else:
                fired = (c1 == c2 == b)
                verdict = "FIRES: filter had no effect" if fired else "quiet: filter changed the count"
            row["probes"].append({"kind": kind, "param": param, "v1": v1, "v2": v2, "c1": c1, "c2": c2,
                                  "fired": fired, "verdict": verdict})
        out.append(row)
        print(f"{name:<26} base={b:<9} " + " | ".join(
            f"{p['kind'][0]}:{p['param']}={'FIRE' if p['fired'] else ('-' if p['fired'] is False else 'n/a')}" for p in row["probes"]), flush=True)
    json.dump(out, open(a.out, "w"), indent=1)
    # join with E3 classes: detection on SILENT probes, false alarms on documented filters
    e3 = {r["api"]: r for r in json.load(open(a.out.replace("e3b_value_swap", "e3_real_apis")))}
    tp = fn = fa = ok_doc = other_fire = other_n = 0
    for r in out:
        if r.get("unreachable") or r["api"] not in e3:
            continue
        cls = {list(x["param"])[0]: x["class"] for x in e3[r["api"]]["wrong"]}
        for p in r["probes"]:
            if p["fired"] is None:
                continue
            if p["kind"] == "documented":
                fa += p["fired"]; ok_doc += not p["fired"]
            elif cls.get(p["param"]) == "SILENT":
                tp += p["fired"]; fn += not p["fired"]
            else:
                other_n += 1; other_fire += p["fired"]
    print(f"\nSILENT probes caught: {tp}/{tp + fn}   documented filters falsely flagged: {fa}/{fa + ok_doc}"
          f"   non-silent wrong params flagged: {other_fire}/{other_n}")


if __name__ == "__main__":
    main()
