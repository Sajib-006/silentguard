#!/usr/bin/env python3
"""E3 — do real public APIs silently ignore a wrong filter parameter? And does the partition check
hold on real data?

For each API: a baseline query (no filter), the documented way to apply a filter, and plausible
WRONG ways (a parameter name an agent reading slightly wrong docs could use). Each wrong way is
classified from the API's own response:
  rejected       4xx/5xx — the API says no. Loud, safe.
  warned         200, but the body mentions the parameter or carries a warning field.
  SILENT         200, count identical to the unfiltered baseline, no warning — the filter was
                 dropped and nothing says so. This is the fault class the paper is about.
  applied        200, count equals the documented filtered count (an alias that works).
  changed        200, count differs from both (partial or different semantics).
Then, for APIs whose query language has NOT, the partition identity on real data:
  N(A AND B) + N(A AND NOT B) == N(A).

Read-only GETs, about one request per second, no credentials, no personal data.
    python e3_real_apis.py --out ../../reeval/e3_real_apis.json
"""
from __future__ import annotations
import argparse, json, re, time
import requests

UA = {"User-Agent": "aob-research-silent-failure-study/1.0 (read-only measurement)"}
PAUSE = 1.2


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


APIS = [
 dict(name="NCBI E-utilities (gene)", url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
      base={"db": "gene", "term": "BRCA1", "retmode": "json", "retmax": 0, "tool": "aob_research"},
      right={"term": "BRCA1 AND human[orgn]"},
      wrong=[{"organism": "human"}, {"species": "Homo sapiens"}, {"taxid": "9606"}],
      count=j(["esearchresult", "count"]),
      part=("term", "BRCA1", "human[orgn]", "{a} AND {b}", "{a} NOT {b}")),
 dict(name="UniProtKB", url="https://rest.uniprot.org/uniprotkb/search",
      base={"query": "BRCA1", "size": 1, "format": "json"},
      right={"query": "BRCA1 AND organism_id:9606"},
      wrong=[{"organism_id": "9606"}, {"organism": "human"}, {"taxonomy": "9606"}],
      count=header("x-total-results"),
      part=("query", "BRCA1", "organism_id:9606", "{a} AND {b}", "{a} NOT {b}")),
 dict(name="ClinicalTrials.gov v2", url="https://clinicaltrials.gov/api/v2/studies",
      base={"query.cond": "diabetes", "countTotal": "true", "pageSize": 1},
      right={"filter.overallStatus": "RECRUITING"},
      wrong=[{"status": "RECRUITING"}, {"overallStatus": "RECRUITING"}, {"recruitment_status": "RECRUITING"}],
      count=j(["totalCount"])),
 dict(name="Europe PMC", url="https://www.ebi.ac.uk/europepmc/webservices/rest/search",
      base={"query": "malaria", "format": "json", "pageSize": 1},
      right={"query": "malaria AND PUB_YEAR:2020"},
      wrong=[{"year": "2020"}, {"pub_year": "2020"}, {"date": "2020"}],
      count=j(["hitCount"]),
      part=("query", "malaria", "PUB_YEAR:2020", "{a} AND {b}", "{a} NOT {b}")),
 dict(name="OpenAlex", url="https://api.openalex.org/works",
      base={"search": "malaria", "per_page": 1},
      right={"filter": "publication_year:2020"},
      wrong=[{"year": "2020"}, {"publication_year": "2020"}, {"from_publication_date": "2020-01-01"}],
      count=j(["meta", "count"])),
 dict(name="Crossref", url="https://api.crossref.org/works",
      base={"query": "malaria", "rows": 0},
      right={"filter": "from-pub-date:2020-01-01,until-pub-date:2020-12-31"},
      wrong=[{"year": "2020"}, {"from-pub-date": "2020-01-01"}, {"published": "2020"}],
      count=j(["message", "total-results"])),
 dict(name="openFDA drug events", url="https://api.fda.gov/drug/event.json",
      base={"search": "patient.drug.medicinalproduct:aspirin", "limit": 1},
      right={"search": "patient.drug.medicinalproduct:aspirin AND serious:1"},
      wrong=[{"serious": "1"}, {"seriousness": "1"}, {"filter": "serious:1"}],
      count=j(["meta", "results", "total"])),
 dict(name="DataCite", url="https://api.datacite.org/dois",
      base={"query": "climate", "page[size]": 1},
      right={"resource-type-id": "dataset"},
      wrong=[{"type": "dataset"}, {"resourceType": "dataset"}, {"resource_type": "dataset"}],
      count=j(["meta", "total"])),
 dict(name="Zenodo", url="https://zenodo.org/api/records",
      base={"q": "climate", "size": 1},
      right={"q": "climate AND resource_type.type:dataset"},
      wrong=[{"resource_type": "dataset"}, {"record_type": "dataset"}, {"kind": "dataset"}],
      count=j(["hits", "total"]),
      part=("q", "climate", "resource_type.type:dataset", "{a} AND {b}", "{a} AND NOT {b}")),
 dict(name="GitHub repository search", url="https://api.github.com/search/repositories",
      base={"q": "bioinformatics", "per_page": 1},
      right={"q": "bioinformatics language:python"},
      wrong=[{"language": "python"}, {"lang": "python"}],
      count=j(["total_count"]),
      part=("q", "bioinformatics", "language:python", "{a} {b}", "{a} -{b}")),
 dict(name="ChEMBL molecules", url="https://www.ebi.ac.uk/chembl/api/data/molecule.json",
      base={"limit": 1},
      right={"max_phase": "4"},
      wrong=[{"phase": "4"}, {"clinical_phase": "4"}, {"maxphase": "4"}],
      count=j(["page_meta", "total_count"])),
 dict(name="arXiv", url="http://export.arxiv.org/api/query",
      base={"search_query": "all:electron", "max_results": 1},
      right={"search_query": "all:electron AND cat:hep-th"},
      wrong=[{"cat": "hep-th"}, {"category": "hep-th"}],
      count=arxiv,
      part=("search_query", "all:electron", "cat:hep-th", "{a} AND {b}", "{a} ANDNOT {b}")),
 dict(name="GBIF occurrences", url="https://api.gbif.org/v1/occurrence/search",
      base={"scientificName": "Puma concolor", "limit": 0},
      right={"country": "US"},
      wrong=[{"countryCode": "US"}, {"nation": "US"}, {"country_code": "US"}],
      count=j(["count"])),
 dict(name="iNaturalist", url="https://api.inaturalist.org/v1/observations",
      base={"taxon_name": "Puma concolor", "per_page": 0},
      right={"place_id": "1"},
      wrong=[{"country": "US"}, {"place": "US"}, {"place_name": "United States"}],
      count=j(["total_results"])),
 dict(name="Semantic Scholar", url="https://api.semanticscholar.org/graph/v1/paper/search",
      base={"query": "malaria", "limit": 1},
      right={"year": "2020"},
      wrong=[{"publication_year": "2020"}, {"pub_year": "2020"}, {"published": "2020"}],
      count=j(["total"])),
]


def _meta_text(d):
    """Keys and scalar values of the response, skipping lists — i.e. the envelope, not the records.
    A data record can legitimately contain words like 'warning' (ChEMBL: black_box_warning)."""
    if isinstance(d, dict):
        return " ".join(str(k) + " " + _meta_text(v) for k, v in d.items() if not isinstance(v, list))
    return "" if isinstance(d, list) else str(d)


def get(api, extra):
    p = dict(api["base"]); p.update(extra)
    r = None
    for wait in (0, 5, 15, 40):                 # rate limits and transient 5xx are not answers
        time.sleep(PAUSE + wait)
        try:
            r = requests.get(api["url"], params=p, headers=UA, timeout=30)
        except requests.RequestException as e:
            r = None; last = e.__class__.__name__; continue
        if r.status_code not in (403, 429, 500, 502, 503, 504):
            break
    if r is None:
        return {"status": None, "count": None, "err": last}
    out = {"status": r.status_code, "count": None, "err": None}
    if r.status_code == 200:
        try:
            out["count"] = api["count"](r)
        except Exception as e:
            out["err"] = "parse:" + e.__class__.__name__
        try:
            env = _meta_text(r.json()).lower()
        except ValueError:
            env = ""
        out["warn"] = any(w in env for w in ("warning", "unknown", "unrecognized", "invalid",
                                             "not a valid", "ignored", "unsupported"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    results = []
    for api in APIS:
        if a.only and a.only.lower() not in api["name"].lower():
            continue
        base = get(api, {})
        right = get(api, api["right"])
        row = {"api": api["name"], "baseline": base, "documented": right, "wrong": []}
        if base.get("count") is None:
            row["unreachable"] = f"baseline status {base.get('status')} {base.get('err') or ''}".strip()
            results.append(row)
            print(f"{api['name']:<28} UNREACHABLE ({row['unreachable']}) — excluded", flush=True)
            continue
        for w in api["wrong"]:
            g = get(api, w)
            if g["status"] is None or g["status"] >= 400:
                cls = "rejected"
            elif g["count"] is None:
                cls = "unparsed"
            elif g.get("warn") and g["count"] == base.get("count"):
                cls = "warned"
            elif g["count"] == base.get("count"):
                cls = "SILENT"
            elif g["count"] == right.get("count"):
                cls = "applied"
            else:
                cls = "changed"
            row["wrong"].append({"param": w, **g, "class": cls})
        if api.get("part") and base.get("status") == 200:
            key, A, B, AND, NOT = api["part"]
            nA = get(api, {key: A}); nAB = get(api, {key: AND.format(a=A, b=B)})
            nAnB = get(api, {key: NOT.format(a=A, b=B)})
            vals = [x.get("count") for x in (nA, nAB, nAnB)]
            row["partition"] = {"N(A)": vals[0], "N(A&B)": vals[1], "N(A&~B)": vals[2],
                                "holds": None if None in vals else vals[1] + vals[2] == vals[0]}
        results.append(row)
        w = ", ".join(f"{list(x['param'])[0]}={x['class']}" for x in row["wrong"])
        p = row.get("partition")
        print(f"{api['name']:<28} base={base.get('count')} doc={right.get('count')} | {w}"
              + (f" | partition {p['N(A&B)']}+{p['N(A&~B)']} vs {p['N(A)']}: {p['holds']}" if p else ""), flush=True)
    json.dump(results, open(a.out, "w"), indent=1)

    reach = [r for r in results if not r.get("unreachable")]
    n_api = len(reach)
    print(f"\nreachable APIs: {n_api}/{len(results)}")
    silent_api = sum(any(x["class"] == "SILENT" for x in r["wrong"]) for r in reach)
    probes = [x for r in reach for x in r["wrong"]]
    c = {}
    for x in probes:
        c[x["class"]] = c.get(x["class"], 0) + 1
    print(f"\nAPIs with at least one SILENTLY ignored wrong filter: {silent_api}/{n_api}")
    print(f"wrong-parameter probes by class: {c}  (n={len(probes)})")
    parts = [r["partition"] for r in results if r.get("partition")]
    print(f"partition identity on real data: holds {sum(1 for p in parts if p['holds'])}/{len(parts)}")


if __name__ == "__main__":
    main()
