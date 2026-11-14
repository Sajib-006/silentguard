import json, urllib.parse
import e3c_more  # noqa: F401
import e3c_real_agents as E
out = []
def cnt(api, params):
    spec = E.API[api]; base = dict(urllib.parse.parse_qsl(spec["base"]))
    st, c, body = E.fetch(api, E.with_params(spec["url"], {**base, **params}))
    return st, c
A = {"taxon_name": "Puma concolor"}
nA = cnt("inat", A); good = cnt("inat", {**A, "d1": "2020-01-01"})
out.append(("inat base", nA, "good d1=2020-01-01", good))
for k, v in [("d1", "01/01/2020"), ("d1", "01-01-2020"), ("d1", "2020/01/01"), ("d1", "20200101"), ("d1", "1 Jan 2020"), ("d1", "2020-1-1"),
             ("d1", "Jan 2020"), ("d1", "2020"), ("month", "13"), ("quality_grade", "reserch"), ("quality_grade", "research"),
             ("year", "20-20"), ("year", "2020"), ("iconic_taxa", "Mammal"), ("iconic_taxa", "Mammalia"), ("d2", "31/12/2019"), ("d2", "2019-12-31")]:
    out.append(("inat", k, v, cnt("inat", {**A, k: v})))
G = {"scientificName": "Puma concolor"}
out.append(("gbif base", cnt("gbif", G), "good year=2020", cnt("gbif", {**G, "year": "2020"})))
for k, v in [("year", "2015-2020"), ("year", "2015,2020"), ("eventDate", "01/01/2020"), ("eventDate", "2020"), ("country", "United States"), ("basisOfRecord", "HUMAN_OBSERVATON"), ("month", "13")]:
    out.append(("gbif", k, v, cnt("gbif", {**G, k: v})))
for o in out: print(o)
