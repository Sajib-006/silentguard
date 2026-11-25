"""Sanity gate for stage ctl5: the new arm shows the check's numbers under a fault and no failure on a clean call.
Run from code/eval_v7 on the server, on an idle worker (default 1). Sets the fault mode and puts it back to off."""
import inspect
import sys

import requests

import executor

worker = int(sys.argv[1]) if len(sys.argv) > 1 else 1
base = f"http://127.0.0.1:{8000 + 10 * worker + 3}"
cmd = ("curl -s -X POST http://localhost:8003/search -H \"X-Api-Key: bench-catalog-key\" "
       "-H \"Content-Type: application/json\" -d '{\"json_metadata_filters\":[{\"mode\":\"path\",\"path\":\"QC_Status\","
       "\"value\":\"qc-pass\"},{\"mode\":\"path\",\"path\":\"Sample_Type\",\"value\":\"tumor\"}],\"limit\":50}'")
sig = inspect.signature(executor.execute)
print("execute params:", list(sig.parameters))
kw = {}
for k in sig.parameters:
    if k == "port_base":
        kw[k] = 8000 + 10 * worker
    if k == "d7_auto":
        kw[k] = True
    if k == "d7_remediation":
        kw[k] = "advisory_num"


def mode(m):
    r = requests.post(base + "/set_sfr_mode", json={"mode": m, "catalog": "large", "projection": "count_only"}, timeout=10)
    return r.json().get("sfr_inject_mode")


try:
    print("mode:", mode("drop_last"))
    rec = executor.execute(cmd, "A", **kw)
    body = rec.get("full_body") or ""
    print("FAULT status", rec.get("status"), "| tail:", body[-430:])
    assert "Check numbers:" in body, "numbers missing under fault"
finally:
    print("mode:", mode("off"))
rec = executor.execute(cmd, "A", **kw)
body = rec.get("full_body") or ""
print("CLEAN tail:", body[-170:])
assert "FAILED" not in body and "Check numbers:" not in body, "failure text on a clean call"
print("SANITY GATE PASSED")
