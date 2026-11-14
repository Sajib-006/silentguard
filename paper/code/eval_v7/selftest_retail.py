#!/usr/bin/env python3
"""Retail self-test (ROLE=orders) — same checks as the bio catalog: the new modes do what they claim, the catalog switch
works, provenance is honest, and the partition invariant fires on cap_total/or_semantics but
NOT on prefix. Exit 1 = do not run."""
import argparse, json, sys, requests
HDR = {"X-Api-Key": "bench-orders-key", "Content-Type": "application/json"}
ap = argparse.ArgumentParser(); ap.add_argument("--worker", type=int, default=0); a = ap.parse_args()
B = f"http://127.0.0.1:{8000 + a.worker * 10 + 1}"
fails = []
def chk(name, cond, info=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{info}]" if info else ""))
    if not cond: fails.append(name)
def mode(m, **kw):
    r = requests.post(f"{B}/set_sfr_mode", json={"mode": m, **kw}, timeout=20); r.raise_for_status()
    requests.post(f"{B}/reset", timeout=20).raise_for_status(); return r.json()
def q(filters, limit=1000):
    r = requests.post(f"{B}/search", headers=HDR, data=json.dumps({"json_metadata_filters": filters, "limit": limit}), timeout=20)
    return r.json()["total_count"], json.loads(r.headers.get("X-AOB-Injection", "{}")), r.json()
def F(p, v, neg=False):
    f = {"mode": "path", "path": p, "value": v}
    if neg: f["negate"] = True
    return f

j = mode("off", catalog="small", projection="full")
chk("small seed = 20", j["n_assets"] == 20, j)
j = mode("off", catalog="large", projection="full")
chk("large seed = 400", j["n_assets"] == 400, j)
T = q([])[0]; chk("large total 400", T == 400, T)

P = [F("Warehouse_ID", "WH-2"), F("Status", "shipped")]
NEG = [F("Warehouse_ID", "WH-2"), F("Status", "shipped", True)]
PRE = [F("Warehouse_ID", "WH-2")]
n0, m0, t0 = q(P)[0], q(NEG)[0], q(PRE)[0]
chk("honest partition holds", n0 + m0 == t0, (n0, m0, t0))
chk("honest n >= 6 for the test predicate", n0 >= 6, n0)

n, pv, body = q(P); m = q(NEG)[0]; t = q(PRE)[0]
mode("cap_total")
n, pv, body = q(P); m = q(NEG)[0]; t = q(PRE)[0]
chk("cap_total: n capped at 5", n == 5 and pv["applied"] and pv["capped_from"] == n0, (n, pv))
chk("cap_total: no has_more/next_cursor", "has_more" not in body and "next_cursor" not in body)
chk("cap_total: partition fires", n + m != t, (n, m, t))
nn, pv2, _ = q([F("Status", "cancelled"), F("Carrier", "dhl"), F("Customer_Tier", "gold"), F("Warehouse_ID", "WH-3")])
chk("cap_total: under-cap query served honestly, provenance says so", (not pv2["applied"]) and nn <= 5, (nn, pv2))

mode("or_semantics")
n, pv, _ = q(P); m = q(NEG)[0]; t = q(PRE)[0]
nb = q([F("Status", "shipped")])[0]
chk("or_semantics: n = |A u B|", n == t + nb - n0 and pv["applied"], (n, t, nb, n0, pv))
chk("or_semantics: partition fires", n + m != t, (n, m, t))
n1, pv1, _ = q(PRE)
chk("or_semantics: k=1 honest", n1 == t0 and not pv1["applied"], (n1, pv1))

mode("prefix")
n, pv, _ = q(P); m = q(NEG)[0]; t = q(PRE)[0]
chk("prefix: WH-2 has no collision -> honest", n == n0 and not pv["applied"], (n, pv))
P1 = [F("Warehouse_ID", "WH-1"), F("Status", "shipped")]
N1 = [F("Warehouse_ID", "WH-1"), F("Status", "shipped", True)]
R1 = [F("Warehouse_ID", "WH-1")]
mode("off"); e_n, e_t = q(P1)[0], q(R1)[0]
mode("prefix"); n, pv, _ = q(P1); m = q(N1)[0]; t = q(R1)[0]
chk("prefix: WH-1 inflated (matches WH-10..12)", n > e_n and t > e_t and pv["applied"] and pv["exact_n"] == e_n, (n, e_n, t, e_t, pv))
chk("prefix: partition STILL holds (blind spot)", n + m == t, (n, m, t))

mode("drop_last")
n, pv, _ = q(P); m = q(NEG)[0]; t = q(PRE)[0]
chk("drop_last on large catalog: n = N(prefix)", n == t0 and pv["applied"], (n, t0, pv))
chk("drop_last: partition fires", n + m != t, (n, m, t))

mode("off", projection="count_only")
_, _, body = q(P)
chk("count_only projection keeps working", "results" not in body and body["total_count"] == n0, body)
j = mode("off", catalog="small", projection="full")
chk("back to small seed", j["n_assets"] == 20, j)
print(f"\n{len(fails)} failure(s)"); sys.exit(1 if fails else 0)
