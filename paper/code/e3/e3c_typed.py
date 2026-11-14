#!/usr/bin/env python3
"""E3c with typed tool calls (2026-10-02, review item #3): the agent calls a typed tool instead of writing a URL.
The tool's parameter schema (the API's documented parameters, all strings, no extra keys allowed) is shown up
front, and the harness rejects any parameter outside it, as strict function calling would. The prose documentation
(right or subtly wrong) is unchanged, so the wrong-docs condition now pits wrong prose against a correct schema.

Arms:
  typed      typed tool, strict schema, no other check.
  typed_sgp  typed tool plus SG-P: result checklist, the ignored-parameter probe with one re-check, withholding,
             and the checked-answer gate (same logic as arm sgp in e3c_real_agents.py).

    python e3c_typed.py --threads 3 --out ../../runs/matrix_e3c_typed/<grp> --apis <apis> --arms typed,typed_sgp --docs A,G
"""
from __future__ import annotations

import json
import re
import urllib.parse

import e3c_more  # noqa: F401  (registers the five added APIs and the count-first view)
import e3c_real_agents as E
import providers
import roster
from strict_grade import strict_outcome

TYPED_PROTOCOL = ("You answer ONE question using one API tool. To call the tool, output exactly one line: "
                  "CALL: <JSON object with the tool's parameters>. Parameters must match the tool schema below; "
                  "unknown parameters are rejected. You will then see the response. When you are done, output: "
                  "ANSWER: count=<n>. If you cannot get a reliable number, output ANSWER: count cannot be "
                  "determined. Use at most 10 calls.")


def tool_schema(spec):
    props = {p: {"type": "string"} for p in sorted(spec["allowed"])}
    return {"name": "search", "parameters": {"type": "object", "properties": props, "additionalProperties": False}}


def run_one_typed(model_entry, api, task, docs, arm):
    key, prov, model, _o, _s = model_entry
    spec = E.API[api]
    a, b = task
    t0 = E.truth(api, a, b)
    sysmsg = (TYPED_PROTOCOL + "\n\nTOOL SCHEMA:\n" + json.dumps(tool_schema(spec)) +
              "\n\nAPI DOCUMENTATION:\n" + spec["doc_" + docs])
    if arm == "typed_sgp":
        sysmsg += "\n\n" + E.SGP_CHECKLIST
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": spec["q"].format(a=a, b=b)}]
    calls, verified, tainted, rejections, answer, err, abstained = [], set(), set(), 0, None, None, False
    S = roster.SETTINGS
    base = dict(urllib.parse.parse_qsl(spec["base"]))
    for turn in range(12):
        try:
            resp = providers.chat(prov, model, msgs, temperature=S["temperature"], max_tokens=S["max_tokens"], seed=0)
        except Exception as e:  # noqa: BLE001
            err = f"{e.__class__.__name__}: {str(e)[:150]}"
            break
        text = (resp.get("text") or "").strip()
        msgs.append({"role": "assistant", "content": text or "(empty)"})
        m_call = re.search(r"CALL:\s*(\{.*\})", text, re.S)
        m_ans = re.search(r"^ANSWER:\s*(.+)$", text, re.M | re.I)
        if m_ans and not (m_call and m_call.start() < m_ans.start()):
            cand = m_ans.group(1).strip()
            if arm == "typed_sgp":
                nums = set(re.findall(r"\b\d+\b", cand))
                if nums and not (nums & (verified - tainted)):
                    if rejections < 2:
                        rejections += 1
                        msgs.append({"role": "user", "content": "HARNESS: answer rejected. That count did not come "
                                     "from a request whose filters all had an effect. Query in a way that applies "
                                     "every condition, or answer that the count cannot be determined."})
                        continue
                    cand = "count cannot be determined (harness: no verified count)"
                    abstained = True
            answer = cand
            break
        if not m_call or len(calls) >= 10:
            msgs.append({"role": "user", "content": "Output CALL: <JSON parameters> or ANSWER: count=<n>"})
            continue
        try:
            args = json.loads(m_call.group(1))
            assert isinstance(args, dict)
        except (ValueError, AssertionError):
            msgs.append({"role": "user", "content": "Error: the CALL arguments are not a valid JSON object."})
            continue
        if isinstance(args.get("parameters"), dict) and set(args) <= {"name", "parameters"}:
            args = args["parameters"]       # the {name, parameters} form native function calling uses
        args = {str(k): ("" if v is None else str(v)) for k, v in args.items()}
        rec = {"params": sorted(args)}
        unknown = [k for k in args if k not in spec["allowed"]]
        if unknown:   # strict schema: what strict function calling enforces
            rec.update(status=400, count=None, schema_rejected=unknown)
            calls.append(rec)
            msgs.append({"role": "user", "content": f"Error: unknown parameter(s) {unknown}; the tool accepts only "
                                                    f"{sorted(spec['allowed'])}."})
            continue
        params = {**base, **args}
        url = E.with_params(spec["url"], params)
        st, c, body = E.fetch(api, url)
        rec.update(status=st, count=c)
        view = f"HTTP {st}\n{body}"
        if arm == "typed_sgp" and st == 200 and c is not None:
            plist = list(params.items())
            ignored = E.probe_ignored(api, spec, url, plist, c)
            if ignored:
                st2, c2, body2 = E.fetch(api, url)
                ignored2 = E.probe_ignored(api, spec, url, plist, c2) if (st2 == 200 and c2 is not None) else ignored
                rec["sgp_recheck"] = {"count": c2, "ignored": ignored2}
                if not ignored2:
                    ignored, c, body = [], c2, body2
                    view = f"HTTP {st2}\n{body2}\n\n[HARNESS CHECK] The first result failed a check; the harness re-ran the request and this result passed."
                else:
                    ignored = ignored2
                    tainted.add(str(c))
                    tainted.add(str(c2))
                    rec["withheld"] = True
                    view = (f"HTTP {st}\n[HARNESS] Result withheld: the parameter(s) {ignored} had no effect on the "
                            "result (checked twice). This count does not apply that filter, so it is not shown. Query "
                            "in a way that applies every condition, or answer that the count cannot be determined.")
            rec["ignored"] = ignored
            if not ignored and str(c) not in tainted:
                verified.add(str(c))
        calls.append(rec)
        msgs.append({"role": "user", "content": view})
    t1 = E.truth(api, a, b)
    row = {"model": model, "key": key, "provider": prov, "api": api, "task": f"{a}|{b}", "docs": docs, "arm": arm,
           "truth_before": t0, "truth_after": t1, "answer": answer or "", "error": err, "calls": calls,
           "rejections": rejections, "harness_abstained": abstained, "tainted": sorted(tainted),
           "final_text": msgs[-1]["content"][:600] if msgs else ""}
    o = "no_answer"
    for t in (t0, t1):
        o = strict_outcome({"answer": answer or "", "ground_truth_used": {"must_include": [f"count={t}"]}, "state_ok": None})
        if o == "correct":
            break
    row["outcome"] = o
    return row


_orig_run_one = E.run_one


def run_one(model_entry, api, task, docs, arm):
    if arm in ("typed", "typed_sgp"):
        return run_one_typed(model_entry, api, task, docs, arm)
    return _orig_run_one(model_entry, api, task, docs, arm)


E.run_one = run_one

if __name__ == "__main__":
    E.main()
