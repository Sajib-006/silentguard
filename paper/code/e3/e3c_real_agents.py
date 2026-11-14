#!/usr/bin/env python3
"""E3c — agents end to end on REAL public APIs, with correct or subtly wrong documentation.

Each task asks for a count that needs two conditions (e.g. a gene name AND an organism). The correct
way to apply the second condition is API-specific (inside the search term, or a documented parameter).
Docs A describe it correctly. Docs G describe it as a separate URL parameter that the real API
silently ignores (measured in E3). Ground truth is the correct query's count, taken right before and
right after each run (both accepted, since live counts drift).

Arms:
  none    raw responses.
  guard   our harness: for every filter parameter in the agent's request (display-only parameters from
          the API's docs are skipped), the harness re-issues the request WITHOUT it and with a nonsense
          value; if all three counts are equal, the parameter had no effect. The agent is told which
          parameter was ignored (no numbers), and a final count is accepted only if it came from a
          request whose parameters all had an effect; two rejections, then the harness abstains.
  schema  the upstream fix, emulated in the harness: requests with a parameter not in the API's
          documented list are rejected with an error naming the allowed parameters.
  sgp     the locked final method SG-P (reeval/METHOD_FINAL.md): checklist prompt + the exact
          request-vs-spec check (as schema) + the exact ignored-parameter probe (as guard); a failed
          probe is re-checked once, and if it fails again the count is withheld (not shown); final
          answers must come from a checked request, two rejections, then the harness abstains.
  retry2  blind retry control: every request sent twice, only the second response shown.
Read-only GETs, per-host rate limits well under each API's published guidance, no credentials.

    python e3c_real_agents.py --threads 4 --out ../../runs/matrix_e3c
"""
from __future__ import annotations
import argparse, json, re, sys, threading, time, urllib.parse
from pathlib import Path
import requests

A = Path("/mnt/disk/sajibacharjeedip/AgentOrchBench")
sys.path.insert(0, str(A / "code/eval_v7"))
import providers, roster                                    # noqa: E402
from strict_grade import strict_outcome                     # noqa: E402

UA = {"User-Agent": "aob-research-silent-failure-study/1.0 (read-only measurement)"}
NONSENSE = "zzq_aob_probe"


def _j(path):
    def f(r):
        d = r.json()
        for k in path:
            d = d[k]
        return int(d)
    return f


def _h(name):
    return lambda r: int(r.headers[name])


API = {
 "ncbi": dict(host="eutils.ncbi.nlm.nih.gov", url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
   base="db=gene&retmode=json&retmax=0", count=_j(["esearchresult", "count"]), gap=0.9,
   allowed={"db", "term", "retmode", "retmax", "retstart", "sort", "field", "tool", "email"},
   display={"db", "retmode", "retmax", "retstart", "sort", "tool", "email"},
   doc_A="NCBI E-utilities esearch (gene database). GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=gene&retmode=json&retmax=0&term=<query>. The JSON field esearchresult.count is the number of matching genes. Restrict to an organism INSIDE the term with the [orgn] tag, e.g. term=BRCA1 AND human[orgn] (URL-encode spaces).",
   doc_G="NCBI E-utilities esearch (gene database). GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=gene&retmode=json&retmax=0&term=<query>. The JSON field esearchresult.count is the number of matching genes. Restrict to an organism with the organism parameter, e.g. &term=BRCA1&organism=human.",
   tasks=[("BRCA1", "human"), ("TP53", "mouse"), ("CFTR", "human"), ("MYC", "mouse"), ("insulin", "zebrafish")],
   q="How many genes in the NCBI gene database match '{a}' in {b}?", correct=lambda a, b: {"term": f"{a} AND {b}[orgn]"}),
 "uniprot": dict(host="rest.uniprot.org", url="https://rest.uniprot.org/uniprotkb/search", base="format=json&size=1",
   count=_h("x-total-results"), gap=0.5,
   allowed={"query", "format", "size", "fields", "sort", "cursor", "includeIsoform"},
   display={"format", "size", "fields", "sort", "cursor", "includeIsoform"},
   doc_A="UniProtKB search. GET https://rest.uniprot.org/uniprotkb/search?format=json&size=1&query=<query>. The response header x-total-results is the number of matching entries. Restrict to an organism INSIDE the query with organism_id, e.g. query=kinase AND organism_id:9606 (human=9606, mouse=10090).",
   doc_G="UniProtKB search. GET https://rest.uniprot.org/uniprotkb/search?format=json&size=1&query=<query>. The response header x-total-results is the number of matching entries. Restrict to an organism with the organism_id parameter, e.g. &query=kinase&organism_id=9606 (human=9606, mouse=10090).",
   tasks=[("kinase", "9606"), ("kinase", "10090"), ("insulin", "9606"), ("BRCA1", "10090"), ("collagen", "9606")],
   q="How many UniProtKB entries match '{a}' for organism taxonomy id {b}?", correct=lambda a, b: {"query": f"{a} AND organism_id:{b}"}),
 "europepmc": dict(host="www.ebi.ac.uk", url="https://www.ebi.ac.uk/europepmc/webservices/rest/search",
   base="format=json&pageSize=1", count=_j(["hitCount"]), gap=0.6,
   allowed={"query", "format", "pageSize", "resultType", "cursorMark", "sort", "synonym"},
   display={"format", "pageSize", "resultType", "cursorMark", "sort", "synonym"},
   doc_A="Europe PMC search. GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?format=json&pageSize=1&query=<query>. The JSON field hitCount is the number of matching publications. Restrict to a publication year INSIDE the query with PUB_YEAR, e.g. query=malaria AND PUB_YEAR:2020.",
   doc_G="Europe PMC search. GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?format=json&pageSize=1&query=<query>. The JSON field hitCount is the number of matching publications. Restrict to a publication year with the year parameter, e.g. &query=malaria&year=2020.",
   tasks=[("malaria", "2020"), ("CRISPR", "2018"), ("tuberculosis", "2015"), ("zika", "2016"), ("dengue", "2019")],
   q="How many Europe PMC publications match '{a}' and were published in {b}?", correct=lambda a, b: {"query": f"{a} AND PUB_YEAR:{b}"}),
 "gbif": dict(host="api.gbif.org", url="https://api.gbif.org/v1/occurrence/search", base="limit=0",
   count=_j(["count"]), gap=0.5,
   allowed={"scientificName", "country", "year", "limit", "offset", "taxonKey", "basisOfRecord", "datasetKey"},
   display={"limit", "offset"},
   doc_A="GBIF occurrence search. GET https://api.gbif.org/v1/occurrence/search?limit=0&scientificName=<name>. The JSON field count is the number of matching occurrence records. Restrict to a country with the country parameter (ISO code), e.g. &country=US.",
   doc_G="GBIF occurrence search. GET https://api.gbif.org/v1/occurrence/search?limit=0&scientificName=<name>. The JSON field count is the number of matching occurrence records. Restrict to a country with the countryCode parameter (ISO code), e.g. &countryCode=US.",
   tasks=[("Puma concolor", "US"), ("Ursus arctos", "CA"), ("Canis lupus", "US"), ("Lynx lynx", "SE"), ("Vulpes vulpes", "GB")],
   q="How many GBIF occurrence records are there for '{a}' in country {b}?", correct=lambda a, b: {"country": b, "scientificName": a}),
}
PROTOCOL = ("You answer ONE question using an HTTP API. To call it, output exactly one line: RUN: GET <full URL>. "
            "You will then see the response. When you are done, output: ANSWER: count=<n>. If you cannot get a "
            "reliable number, output ANSWER: count cannot be determined. Use at most 10 calls.")

_locks = {k: threading.Lock() for k in API}
_last = {k: 0.0 for k in API}


def fetch(api, url):
    """Rate-limited GET for one host. Returns (status, count or None, text excerpt)."""
    spec = API[api]
    r = None
    for backoff in (0, 2, 5, 12):           # a rate limit or a transient 5xx is ours, not the model's
        with _locks[api]:
            wait = spec["gap"] + backoff - (time.time() - _last[api])
            if wait > 0:
                time.sleep(wait)
            try:
                r = requests.get(url, headers=UA, timeout=40)
            except requests.RequestException as e:
                _last[api] = time.time(); r = None; continue
            _last[api] = time.time()
        if r.status_code not in (429, 500, 502, 503, 504):
            break
    if r is None:
        return None, None, "request failed"
    c = None
    if r.status_code == 200:
        try:
            c = spec["count"](r)
        except Exception:
            pass
    body = r.text[:1500]
    if "x-total-results" in r.headers:
        body = f"(header x-total-results: {r.headers['x-total-results']})\n" + body
    return r.status_code, c, body


def with_params(url, params):
    u = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(params, doseq=False), ""))


def truth(api, a, b):
    spec = API[api]
    base = dict(urllib.parse.parse_qsl(spec["base"]))
    base.update(spec["correct"](a, b))
    return fetch(api, with_params(spec["url"], base))[1]


SGP_CHECKLIST = ("RESULT CHECKLIST\nBefore you use any tool result, check it against this list:\n"
                 "1. Presence and type: every field you need is present and has the right type.\n"
                 "2. Agreement with your request: values that repeat your request (IDs, names, dates, codes) match what you asked for.\n"
                 "3. Plausibility: numbers and categories are sensible (for example, no negative prices or impossible values).\n"
                 "4. Consistency: the result does not contradict itself and is about what you asked.\n"
                 "If any check fails, treat the result as incorrect: retry the tool, use another tool that can do the same step, "
                 "or stop and report which step failed.")


def probe_ignored(api, spec, url, params, c):
    """The guard's ignored-parameter probe: a filter had no effect if the count is the same with it,
    without it, and with a nonsense value."""
    ignored = []
    for k, v in params:
        if k in spec["display"] or not v:
            continue
        rest = [(kk, vv) for kk, vv in params if kk != k]
        c_without = fetch(api, with_params(url, dict(rest)))[1]
        c_nonsense = fetch(api, with_params(url, dict(rest + [(k, NONSENSE)])))[1]
        if c_without is not None and c == c_without and (c_nonsense is None or c_nonsense == c):
            ignored.append(k)
    return ignored


def run_one(model_entry, api, task, docs, arm):
    key, prov, model, _o, _s = model_entry
    spec = API[api]; a, b = task
    t0 = truth(api, a, b)
    sysmsg = PROTOCOL + "\n\nAPI DOCUMENTATION:\n" + spec["doc_" + docs]
    if arm == "sgp":
        sysmsg += "\n\n" + SGP_CHECKLIST
    msgs = [{"role": "system", "content": sysmsg},
            {"role": "user", "content": spec["q"].format(a=a, b=b)}]
    calls, verified, tainted, rejections, answer, err, abstained = [], set(), set(), 0, None, None, False
    S = roster.SETTINGS
    for turn in range(12):
        try:
            resp = providers.chat(prov, model, msgs, temperature=S["temperature"], max_tokens=S["max_tokens"], seed=0)
        except Exception as e:
            err = f"{e.__class__.__name__}: {str(e)[:150]}"; break
        text = (resp.get("text") or "").strip()
        msgs.append({"role": "assistant", "content": text or "(empty)"})
        m_run = re.search(r"RUN:\s*GET\s+(\S+)", text)
        m_ans = re.search(r"^ANSWER:\s*(.+)$", text, re.M | re.I)
        if m_ans and not (m_run and m_run.start() < m_ans.start()):
            cand = m_ans.group(1).strip()
            if arm in ("guard", "sgp"):
                nums = set(re.findall(r"\b\d+\b", cand))
                if nums and not (nums & (verified - tainted)):
                    if rejections < 2:
                        rejections += 1
                        msgs.append({"role": "user", "content": "HARNESS: answer rejected. That count did not come "
                                     "from a request whose filters all had an effect. Query in a way that applies "
                                     "every condition, or answer that the count cannot be determined."})
                        continue
                    cand = "count cannot be determined (harness: no verified count)"; abstained = True
            answer = cand; break
        if not m_run:
            msgs.append({"role": "user", "content": "Output RUN: GET <url> or ANSWER: count=<n>"}); continue
        url = m_run.group(1).strip().strip("'\"<>")
        u = urllib.parse.urlsplit(url)
        if u.netloc != spec["host"] or len(calls) >= 10:
            msgs.append({"role": "user", "content": f"Error: only {spec['host']} is allowed (max 10 calls)."}); continue
        params = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        rec = {"url": url[:400], "params": [k for k, _ in params]}
        unknown = [k for k, _ in params if k not in spec["allowed"]]
        if arm in ("schema", "sgp") and unknown:
            rec.update(status=400, count=None, schema_rejected=unknown); calls.append(rec)
            msgs.append({"role": "user", "content": f"HTTP 400: unknown parameter(s) {unknown}. Allowed parameters: {sorted(spec['allowed'])}."})
            continue
        st, c, body = fetch(api, url)
        if arm == "retry2":
            # blind retry control: every request is sent twice and only the second response is shown
            rec["retry2_first"] = c
            st, c, body = fetch(api, url)
        rec.update(status=st, count=c)
        view = f"HTTP {st}\n{body}"
        if arm in ("guard", "sgp") and st == 200 and c is not None:
            ignored = probe_ignored(api, spec, url, params, c)
            if arm == "sgp" and ignored:
                # SG-P: one re-check with the same request; the probe is exact, so a second failure withholds
                st2, c2, body2 = fetch(api, url)
                ignored2 = probe_ignored(api, spec, url, params, c2) if (st2 == 200 and c2 is not None) else ignored
                rec["sgp_recheck"] = {"count": c2, "ignored": ignored2}
                if not ignored2:
                    ignored, c, body = [], c2, body2
                    view = f"HTTP {st2}\n{body2}\n\n[HARNESS CHECK] The first result failed a check; the harness re-ran the request and this result passed."
                else:
                    ignored = ignored2
                    tainted.add(str(c)); tainted.add(str(c2))
                    rec["withheld"] = True
                    view = (f"HTTP {st}\n[HARNESS] Result withheld: the parameter(s) {ignored} had no effect on the result "
                            "(checked twice). This count does not apply that filter, so it is not shown. Query in a way "
                            "that applies every condition, or answer that the count cannot be determined.")
            rec["ignored"] = ignored
            if ignored and arm == "guard":
                tainted.add(str(c))
                view += (f"\n\n[HARNESS CHECK] FAILED: the parameter(s) {ignored} had no effect on the result "
                         "(the count is the same with them, without them, and with a nonsense value). The API is "
                         "ignoring them, so this count does NOT apply that filter.")
            elif not ignored and str(c) not in tainted:
                verified.add(str(c))
        calls.append(rec)
        msgs.append({"role": "user", "content": view})
    t1 = truth(api, a, b)
    row = {"model": model, "key": key, "provider": prov, "api": api, "task": f"{a}|{b}", "docs": docs, "arm": arm,
           "truth_before": t0, "truth_after": t1, "answer": answer or "", "error": err, "calls": calls,
           "rejections": rejections, "harness_abstained": abstained, "tainted": sorted(tainted), "final_text": msgs[-1]["content"][:600] if msgs else ""}
    for t in (t0, t1):
        o = strict_outcome({"answer": answer or "", "ground_truth_used": {"must_include": [f"count={t}"]}, "state_ok": None})
        if o == "correct":
            break
    row["outcome"] = o
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--only", default=None); ap.add_argument("--apis", default="ncbi,uniprot,europepmc,gbif")
    ap.add_argument("--arms", default="none,guard,schema"); ap.add_argument("--docs", default="A,G")
    ap.add_argument("--limit-tasks", type=int, default=None)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    ents = [e for e in roster.roster(False, False) if e[1] != "anthropic"]
    if a.only:
        ents = [e for e in ents if e[0] in a.only.split(",")]
    jobs = []
    for e in ents:
        for api in a.apis.split(","):
            for t in API[api]["tasks"][:a.limit_tasks]:
                for docs in a.docs.split(","):
                    for arm in a.arms.split(","):
                        jobs.append((e, api, t, docs, arm))
    lock = threading.Lock(); files = {}
    done = set()
    for f in out.glob("e3c-*.jsonl"):
        for line in open(f):
            r = json.loads(line); done.add((r["key"], r["api"], r["task"], r["docs"], r["arm"]))
    jobs = [j for j in jobs if (j[0][0], j[1], f"{j[2][0]}|{j[2][1]}", j[3], j[4]) not in done]
    print(f"{len(jobs)} runs to do ({len(done)} already done)", flush=True)
    q = list(jobs)

    def worker():
        while True:
            with lock:
                if not q:
                    return
                j = q.pop(0)
            row = run_one(*j)
            with lock:
                fn = out / f"e3c-{row['key']}.jsonl"
                with open(fn, "a") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(f"{time.strftime('%H:%M')} {row['key']:<16} {row['api']:<10} {row['docs']} {row['arm']:<6} {row['outcome']:<9} ans={row['answer'][:24]!r} truth={row['truth_before']}", flush=True)
    ths = [threading.Thread(target=worker) for _ in range(a.threads)]
    [t.start() for t in ths]; [t.join() for t in ths]
    print("E3C_DONE", flush=True)


if __name__ == "__main__":
    main()
