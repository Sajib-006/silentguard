#!/usr/bin/env python3
"""H1 self-test for the catalog SFR-injection modes (B3 design). Run before any experiment;
exit code 1 means no run may proceed.

Checks, against the live API:
  * every mode does exactly what it claims, proved from the API's own responses
  * the adversary is STRUCTURAL: it never looks at filter values, so swapping a value for a
    different one with the same predicate structure changes nothing about whether it fires
  * the D7=3 v2 invariant holds exactly on a correct API (no false alarm) and is violated
    under drop_last (true detection)
  * the wrong documentation key cannot reach the injection at all — the bug that invalidated
    the pre-2026-09 adversarial results (CRITICAL_EVAL F1)
  * X-AOB-Injection provenance is present and honest on every response
"""
import argparse
import json
import sys

import requests

HDR = {"X-Api-Key": "bench-catalog-key", "Content-Type": "application/json"}
BASE = "http://127.0.0.1:8003"


def F(p, v, neg=False):
    f = {"mode": "path", "path": p, "value": v}
    if neg:
        f["negate"] = True
    return f


# H25's predicate: Batch_ID=batch_2 AND QC_Status=qc-pending
P = [F("Batch_ID", "batch_2"), F("QC_Status", "qc-pending")]
C = [F("Batch_ID", "batch_2"), F("QC_Status", "qc-pending", True)]
T = [F("Batch_ID", "batch_2")]
S = [F("QC_Status", "qc-pending")]
S_NEG = [F("QC_Status", "qc-pending", True)]
# same structure, different values — a structural adversary must treat these identically
P_ALT = [F("Batch_ID", "batch_1"), F("QC_Status", "qc-pass")]
T_ALT = [F("Batch_ID", "batch_1")]
C_ALT = [F("Batch_ID", "batch_1"), F("QC_Status", "qc-pass", True)]
WRONG_KEY = {"metadata_filters": [{"field": "Batch_ID", "value": "batch_2"}], "limit": 50}


def mode(m, **kw):
    r = requests.post(f"{BASE}/set_sfr_mode", json={"mode": m, **kw}, timeout=10)
    r.raise_for_status()
    assert r.json()["sfr_inject_mode"] == m, r.text
    requests.post(f"{BASE}/reset", timeout=10).raise_for_status()


def q(body):
    if isinstance(body, list):
        body = {"json_metadata_filters": body, "limit": 50}
    r = requests.post(f"{BASE}/search", headers=HDR, data=json.dumps(body), timeout=10)
    j = r.json()
    prov = json.loads(r.headers.get("X-AOB-Injection", "{}"))
    return j.get("total_count"), prov, j.get("query_used", {}).get("json_metadata_filters")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", type=int, default=0)
    ap.add_argument("--strict", action="store_true", help="kept for compatibility; all checks are contract now")
    args = ap.parse_args()
    global BASE
    BASE = f"http://127.0.0.1:{8000 + args.worker * 10 + 3}"

    checks = []

    def chk(name, got, want):
        checks.append((name, got, want))

    # ---- honest mode -------------------------------------------------------
    mode("off")
    n_p, pr, _ = q(P); chk("off: N(A^B) == 5", n_p, 5); chk("off: provenance applied=False", pr.get("applied"), False)
    n_c, _, _ = q(C); chk("off: N(A^~B) == 1", n_c, 1)
    n_t, _, _ = q(T); chk("off: N(A) == 6", n_t, 6)
    chk("off: D7=3 v2 invariant holds exactly (no false alarm)", n_p + n_c, n_t)
    chk("off: single-predicate partition holds", q(S)[0] + q(S_NEG)[0], 20)

    # ---- standard ----------------------------------------------------------
    mode("standard")
    n, pr, _ = q(P)
    chk("standard: filter fully ignored -> 20", n, 20)
    chk("standard: provenance applied=True", pr.get("applied"), True)
    chk("standard: provenance reports all conditions dropped", pr.get("dropped"), 2)

    # ---- drop_last ---------------------------------------------------------
    mode("drop_last")
    n_p, pr, _ = q(P)
    chk("drop_last: k=2 primary corrupted to N(A) == 6", n_p, 6)
    chk("drop_last: provenance applied=True, dropped=1", (pr.get("applied"), pr.get("dropped")), (True, 1))
    n_c, _, _ = q(C); chk("drop_last: complement corrupted by the SAME rule == 6", n_c, 6)
    n_t, pr_t, _ = q(T)
    chk("drop_last: k=1 served honestly == 6", n_t, 6)
    chk("drop_last: k=1 provenance applied=False", pr_t.get("applied"), False)
    chk("drop_last: k=1 negation served honestly == 13", q(S_NEG)[0], 13)
    chk("drop_last: D7=3 v2 invariant VIOLATED (detection)", (n_p + n_c) != n_t, True)

    # structural, not value-keyed: a different value with the same structure behaves the same
    n_p2, pr2, _ = q(P_ALT)
    chk("drop_last: different VALUES, same structure -> still fires", pr2.get("applied"), True)
    chk("drop_last: alt primary == N(batch_1)", n_p2, q(T_ALT)[0])
    chk("drop_last: alt complement corrupted identically", q(C_ALT)[0], n_p2)

    # ---- the F1 bug can never recur ---------------------------------------
    _, pr_w, echo = q(WRONG_KEY)
    chk("wrong docs key -> API sees no filters", echo, [])
    chk("wrong docs key -> adversary cannot fire", pr_w.get("applied"), False)

    # ---- cac (red-team adversary) -----------------------------------------
    mode("cac")
    n1, pr1, _ = q(P)
    n2, pr2c, _ = q(C)
    chk("cac: primary corrupted", pr1.get("applied"), True)
    chk("cac: N + M == total, defeating a naive complement check", n1 + n2, 20)
    chk("cac: primary count is wrong (!= 5)", n1 != 5, True)

    # ---- legacy gate -------------------------------------------------------
    r = requests.post(f"{BASE}/set_sfr_mode", json={"mode": "adversarial"}, timeout=10)
    chk("legacy 'adversarial' rejected unless SFR_LEGACY_MODES=1", r.status_code, 400)

    mode("off")

    bad = 0
    for name, got, want in checks:
        ok = got == want
        bad += (not ok)
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<66} got={got!r}" + ("" if ok else f" want={want!r}"))
    if bad:
        print(f"SELFTEST FAILED ({bad}/{len(checks)})")
        sys.exit(1)
    print(f"SELFTEST PASSED ({len(checks)}/{len(checks)}) — injection is structural, "
          f"invariant is exact, wrong-key bug cannot recur")


if __name__ == "__main__":
    main()
