#!/usr/bin/env python3
"""Real-API agent study with a wrong VALUE FORMAT in the documentation (2026-10-05; imports the frozen e3c and
e3c_more unchanged). iNaturalist silently ignores a d2 date written as DD/MM/YYYY when the day is above 12, and
returns the unfiltered count. The parameter name (d2) is valid, so the spec check passes; only the harness's
ignored-parameter probe (value swap) can see the fault. Docs A give the right format, docs G the wrong one.

    python e3i_badformat.py --check
    python e3i_badformat.py --threads 2 --out ../../runs/matrix_e3i_badformat/hf --apis inat_d2 --arms none,schema,sgp --docs G --only <models>
"""
import sys, threading, urllib.parse
import e3c_more  # noqa: F401  (registers the nine APIs and the count-first fetch)
import e3c_real_agents as E

inat = E.API["inat"]
E.API["inat_d2"] = dict(
    host=inat["host"], url=inat["url"], base=inat["base"], count=inat["count"], gap=inat["gap"],
    allowed=inat["allowed"], display=inat["display"],
    doc_A="iNaturalist observation search. GET https://api.inaturalist.org/v1/observations?per_page=0&taxon_name=<name>. The JSON field total_results is the number of matching observations. Restrict to observations made on or before a date with the d2 parameter, written as YYYY-MM-DD, e.g. &taxon_name=Puma concolor&d2=2019-12-31 (URL-encode spaces).",
    doc_G="iNaturalist observation search. GET https://api.inaturalist.org/v1/observations?per_page=0&taxon_name=<name>. The JSON field total_results is the number of matching observations. Restrict to observations made on or before a date with the d2 parameter, written as DD/MM/YYYY, e.g. &taxon_name=Puma concolor&d2=31/12/2019 (URL-encode spaces).",
    tasks=inat["tasks"],
    q="How many iNaturalist observations of '{a}' were made on or before 31 December {b}?",
    correct=lambda a, b: {"taxon_name": a, "d2": f"{b}-12-31"})
E._locks["inat_d2"] = threading.Lock()
E._last["inat_d2"] = 0.0

if __name__ == "__main__":
    if "--check" in sys.argv:
        spec = E.API["inat_d2"]; base = dict(urllib.parse.parse_qsl(spec["base"])); bad = 0
        for a, b in spec["tasks"]:
            t = E.truth("inat_d2", a, b)
            unf = E.fetch("inat_d2", E.with_params(spec["url"], {**base, "taxon_name": a}))[1]
            wrong = E.fetch("inat_d2", E.with_params(spec["url"], {**base, "taxon_name": a, "d2": f"31/12/{b}"}))[1]
            flagged = E.probe_ignored("inat_d2", spec, spec["url"], list({**base, "taxon_name": a, "d2": f"31/12/{b}"}.items()), wrong)
            clean = E.probe_ignored("inat_d2", spec, spec["url"], list({**base, "taxon_name": a, "d2": f"{b}-12-31"}.items()), t)
            ok = t not in (None, 0) and wrong == unf and t != unf and flagged == ["d2"] and clean == []
            bad += not ok
            print(f"{a:<16} {b}  truth={t}  wrong-format={wrong}  unfiltered={unf}  probe on wrong={flagged}  probe on right={clean}  {'OK' if ok else 'CHECK'}", flush=True)
        print("tasks needing a look:", bad)
    else:
        E.main()
