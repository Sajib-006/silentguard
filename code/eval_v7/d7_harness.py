"""Harness-side SilentGuard: the complement check as a mechanism, not a suggestion.

The paper describes D7=3 as "an automatic harness-side check that fires on every filter call,
requires no prior failure history, and detects the violation on first encounter". The shipped
implementation is a paragraph appended to the system prompt, and measured compliance is 0-60%
(CRITICAL_EVAL F17): gpt-4o-mini never issued a negated complement query under any D7 level, so
it could not have detected anything, and its zero false-alarm rate reflects a check that never ran.

This module implements what the paper actually describes. After the agent's filter query returns,
the HARNESS issues the verification queries itself and appends a verdict to the observation. The
agent cannot skip it, mis-negate it, or forget it, so detection rate becomes a property of the
mechanism and only *remediation* remains a property of the model — which is precisely the split
the detection-remediation gap is about.

Invariant (v2, order-independent — see F15):
    k = 1 : N(C)         + N(not C)                  == total
    k >= 2: N(C1..Ck)    + N(C1..C(k-1) and not Ck)  == N(C1..C(k-1))
"""
from __future__ import annotations

import copy
import json
import os as _os

import requests

VERDICT_OK = ("[HARNESS CHECK] Complement verification PASSED: {n} + {m} = {t}. "
              "The filter partitions correctly; the count is consistent.")
VERDICT_FAIL = ("[HARNESS CHECK] COMPLEMENT VERIFICATION FAILED: {n} + {m} = {s}, expected {t}. "
                "The API did not apply your full filter. The count {n} is NOT trustworthy. "
                "Do not report it as correct.")
VERDICT_SKIP = "[HARNESS CHECK] not applicable to this query."

# Paraphrases: same information, same numbers, different words. Used only at evaluation time to
# test whether a model followed the idea or memorised the sentence.
VERDICT_OK_P = ("[AUDIT] Partition audit passed: {n} plus {m} equals {t}. The filter split the "
                "records correctly, so this total is self-consistent.")
VERDICT_FAIL_P = ("[AUDIT] PARTITION AUDIT DID NOT PASS: {n} plus {m} came to {s}, but {t} was "
                  "required. Part of the requested filter was not honoured by the service. "
                  "Treat the figure {n} as unsound and do not present it as the answer.")
if _os.environ.get("AOB_VERDICT_STYLE") == "paraphrase":
    VERDICT_OK, VERDICT_FAIL = VERDICT_OK_P, VERDICT_FAIL_P


def _post(url: str, headers: dict, filters: list, timeout: float) -> int | None:
    try:
        r = requests.post(url, headers=headers,
                          data=json.dumps({"json_metadata_filters": filters, "limit": 50}),
                          timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json().get("total_count")
    except (requests.RequestException, ValueError):
        return None


def verify_filter_call(url: str, headers: dict, filters: list, reported_n: int,
                       total: int | None, timeout: float = 15.0) -> dict:
    """Run the invariant for one filter query. Returns a verdict dict; never raises."""
    out = {"ran": False, "consistent": None, "n": reported_n, "m": None, "t": None,
           "extra_calls": 0, "text": VERDICT_SKIP}
    if not filters:
        return out

    last = filters[-1]
    if last.get("mode", "path") != "path":
        return out                      # only defined for equality predicates

    prefix = filters[:-1]
    neg = copy.deepcopy(last)
    neg["negate"] = not neg.get("negate", False)

    if prefix:
        t = _post(url, headers, prefix, timeout)
        m = _post(url, headers, prefix + [neg], timeout)
        out["extra_calls"] = 2
    else:
        t = total
        m = _post(url, headers, [neg], timeout)
        out["extra_calls"] = 1
    if t is None or m is None:
        return out                      # verification itself failed; stay silent

    out.update(ran=True, m=m, t=t, consistent=(reported_n + m == t))
    out["text"] = (VERDICT_OK if out["consistent"] else VERDICT_FAIL).format(
        n=reported_n, m=m, t=t, s=reported_n + m)
    return out
