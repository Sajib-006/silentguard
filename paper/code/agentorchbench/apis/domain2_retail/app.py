#!/usr/bin/env python3
"""AgentOrchBench Domain 2 — retail operations.

One generic service, launched four times with different ROLE values, so the second domain
shares exactly one implementation of auth, filtering and SFR injection with no opportunity for
the two domains to drift apart. Structure mirrors the bioinformatics domain:

  ROLE=orders     (canonical :8001)  the filterable entity — SFR target, mirrors CatalogAPI
  ROLE=inventory  (canonical :8002)  enum-validated writes, mirrors RegistryAPI
  ROLE=customers  (canonical :8003)  OAuth2 bearer flow, mirrors TransferAPI
  ROLE=tickets    (canonical :8004)  issue tracker + comments, mirrors TaskAPI

Env: ROLE, PORT, DB_PATH, SFR_INJECT_MODE, SFR_INJECT_SEED, SFR_INJECT_P, SFR_LEGACY_MODES.
Every /search reply carries the X-AOB-Injection provenance header (B3).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, request

ROLE = os.environ.get("ROLE", "orders")
PORT = int(os.environ.get("PORT", "8001"))
DB_PATH = os.environ.get("DB_PATH", f"/tmp/d2_{ROLE}.db")

_SFR_INJECT_MODE = os.environ.get("SFR_INJECT_MODE", "off")
_SFR_INJECT_SEED = int(os.environ.get("SFR_INJECT_SEED", "0"))
_SFR_INJECT_P = float(os.environ.get("SFR_INJECT_P", "0.5"))
_SFR_MODES = ("off", "standard", "drop_last", "drop_last_p", "cac",
              "cap_total", "or_semantics", "prefix", "drop_field")
_DROP_FIELDS = set(os.environ.get("DROP_FIELDS", "Status").split(","))
_SFR_CAP = int(os.environ.get("RETAIL_CAP", "5"))
_RESPONSE_PROJECTION = os.environ.get("RESPONSE_PROJECTION", "full")
_CATALOG = os.environ.get("RETAIL_SEED", "small")
_SFR_CAC_STATE = {"last_corrupt": None, "total": None}
_SFR_REQ_N = {"n": 0}

API_KEYS = {"orders": "bench-orders-key", "inventory": "bench-inventory-key",
            "customers": "bench-customers-key", "tickets": "bench-tickets-token"}
AUTH_HEADER = {"orders": "X-Api-Key", "inventory": "X-Api-Key",
               "customers": "Authorization", "tickets": "X-Api-Token"}
CLIENT_ID, CLIENT_SECRET = "bench-retail-client", "bench-retail-secret"

app = Flask(__name__)
_TOKENS: dict[str, datetime] = {}

# ── seed data ────────────────────────────────────────────────────────────────
REGIONS = ["emea", "namer", "apac"]
STATUSES = ["shipped", "processing", "backordered", "cancelled"]
CHANNELS = ["web", "retail", "partner"]


def seed_orders() -> list[dict]:
    """20 orders — same cardinality as the bio catalog, so task difficulty is comparable."""
    plan = [
        # (region,        status,        channel,  priority, value_usd)
        ("emea",  "shipped",      "web",     "standard", 1200),
        ("emea",  "shipped",      "retail",  "standard", 340),
        ("emea",  "processing",   "web",     "expedited", 890),
        ("emea",  "processing",   "partner", "standard", 15400),
        ("emea",  "backordered",  "web",     "standard", 220),
        ("emea",  "cancelled",    "retail",  "standard", 75),
        ("namer", "shipped",      "web",     "standard", 640),
        ("namer", "shipped",      "web",     "expedited", 2100),
        ("namer", "shipped",      "partner", "standard", 8800),
        ("namer", "processing",   "retail",  "standard", 410),
        ("namer", "processing",   "web",     "standard", 130),
        ("namer", "backordered",  "partner", "expedited", 5600),
        ("namer", "backordered",  "web",     "standard", 95),
        ("namer", "cancelled",    "web",     "standard", 260),
        ("apac",  "shipped",      "retail",  "standard", 770),
        ("apac",  "shipped",      "web",     "standard", 1850),
        ("apac",  "processing",   "partner", "expedited", 23000),
        ("apac",  "backordered",  "retail",  "standard", 180),
        ("apac",  "backordered",  "web",     "expedited", 3300),
        ("apac",  "cancelled",    "partner", "standard", 640),
    ]
    out = []
    for i, (region, status, channel, priority, value) in enumerate(plan, start=1):
        oid = f"ord-{i:03d}"
        out.append({
            "item_id": oid,
            "metadata": {
                "Order_ID": oid, "Region": region, "Status": status, "Channel": channel,
                "Priority": priority, "Value_USD": value,
                "Customer_ID": f"cust-{(i % 7) + 1:03d}",
                "SKU": f"SKU-{(i % 9) + 1:04d}",
                "Warehouse": {"emea": "WH-DUB", "namer": "WH-CMH", "apac": "WH-SIN"}[region],
            },
            "tags": [region, status],
        })
    return out


def seed_orders_large(n: int = 400) -> list[dict]:
    """400 orders for the fault suite. Warehouse_ID and Partner_ID are built so that WH-1 is a
    prefix of WH-10..WH-12 (likewise P-1), giving the self-consistent prefix fault something to
    act on, exactly as batch_1/ST-1 do in the bio catalog."""
    import random as _rnd
    rng = _rnd.Random(4242)
    CARRIERS = ["dhl", "fedex", "ups", "royal-mail"]
    PAY = ["card", "invoice", "prepaid"]
    TIERS = ["gold", "silver", "bronze"]
    out = []
    for i in range(1, n + 1):
        w = 1 + (i - 1) % 12
        meta = {"Order_ID": f"ord-{i:04d}", "Region": rng.choice(REGIONS),
                "Status": rng.choice(STATUSES), "Channel": rng.choice(CHANNELS),
                "Priority": rng.choice(["standard", "expedited"]),
                "Warehouse_ID": f"WH-{w}", "Partner_ID": f"P-{1 + (i - 1) % 12}",
                "Carrier": rng.choice(CARRIERS), "Payment_Method": rng.choice(PAY),
                "Customer_Tier": rng.choice(TIERS), "Value_USD": rng.choice([120, 340, 890, 1200, 15400])}
        out.append({"item_id": meta["Order_ID"], "metadata": meta,
                    "tags": [meta["Region"], meta["Status"], meta["Warehouse_ID"]]})
    return out


SEEDS = {
    "orders": seed_orders(),
    "inventory": [
        {"item_id": f"SKU-{i:04d}",
         "metadata": {"SKU": f"SKU-{i:04d}", "Category": c, "Stock": s, "Reorder_Level": r,
                      "Supplier": sup},
         "tags": [c]}
        for i, (c, s, r, sup) in enumerate([
            ("accessories", 420, 100, "acme-supply"), ("accessories", 12, 50, "acme-supply"),
            ("hardware", 88, 25, "globex"), ("hardware", 0, 10, "globex"),
            ("consumables", 1500, 400, "initech"), ("consumables", 310, 400, "initech"),
            ("apparel", 75, 30, "umbrella"), ("apparel", 4, 30, "umbrella"),
            ("hardware", 260, 25, "globex"),
        ], start=1)
    ],
    "customers": [
        {"item_id": f"cust-{i:03d}",
         "metadata": {"Customer_ID": f"cust-{i:03d}", "Tier": t, "Region": r,
                      "Credit_Hold": ch, "Lifetime_USD": lv},
         "tags": [t, r]}
        for i, (t, r, ch, lv) in enumerate([
            ("gold", "emea", "no", 48000), ("silver", "namer", "no", 12000),
            ("gold", "apac", "yes", 91000), ("bronze", "emea", "no", 2300),
            ("silver", "apac", "no", 15600), ("bronze", "namer", "yes", 800),
            ("gold", "namer", "no", 76000),
        ], start=1)
    ],
    "tickets": [
        {"item_id": f"OPS-{i:03d}",
         "metadata": {"Ticket_ID": f"OPS-{i:03d}", "State": st, "Severity": sev,
                      "Summary": summ, "Linked_Order": lo},
         "tags": [st]}
        for i, (st, sev, summ, lo) in enumerate([
            ("open", "high", "Backordered APAC shipments exceeding SLA", "ord-019"),
            ("open", "medium", "Partner channel invoice mismatch", "ord-017"),
            ("in_progress", "high", "Credit hold review for gold tier", ""),
            ("in_progress", "low", "Warehouse WH-DUB label printer", "ord-003"),
            ("resolved", "medium", "Duplicate order investigation", "ord-014"),
            ("open", "critical", "Stock at zero for SKU-0004", ""),
        ], start=1)
    ],
}

ENUMS = {"inventory": ["accessories", "hardware", "consumables", "apparel"]}


# ── db ───────────────────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.execute("""CREATE TABLE items (item_id TEXT PRIMARY KEY, metadata TEXT, tags TEXT,
                                      created_at TEXT, created_by TEXT)""")
    db.execute("""CREATE TABLE comments (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                         item_id TEXT, body TEXT, created_at TEXT)""")
    now = datetime.now(timezone.utc).isoformat()
    rows_to_seed = (seed_orders_large() if (ROLE == "orders" and _CATALOG == "large")
                    else SEEDS.get(ROLE, []))
    for row in rows_to_seed:
        db.execute("INSERT INTO items VALUES (?,?,?,?,?)",
                   (row["item_id"], json.dumps(row["metadata"]), json.dumps(row["tags"]),
                    now, "bench-seed"))
    db.commit()
    db.close()


# ── auth ─────────────────────────────────────────────────────────────────────
def check_auth():
    hdr = AUTH_HEADER[ROLE]
    val = request.headers.get(hdr, "")
    if ROLE == "customers":
        tok = val[7:] if val.startswith("Bearer ") else ""
        exp = _TOKENS.get(tok)
        if not exp:
            return False, "invalid or missing bearer token; POST /auth/token first"
        if exp < datetime.now(timezone.utc):
            return False, "token expired; request a new one"
        return True, ""
    if val != API_KEYS[ROLE]:
        return False, f"invalid or missing {hdr}"
    return True, ""


def require_auth(fn):
    def wrapper(*a, **kw):
        ok, msg = check_auth()
        if not ok:
            return jsonify({"error": msg, "status_code": 401}), 401
        return fn(*a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper


# ── filtering + B3 injection (identical semantics to the bio catalog) ────────
def _match(row, meta, tags, filters, prefix=False):
    for f in filters:
        mode = f.get("mode", "path")
        negate = f.get("negate", False)
        if mode == "path":
            mv = meta.get(f.get("path", ""))
            ok = mv == f.get("value")
            if prefix and not ok and isinstance(mv, str) and isinstance(f.get("value"), str):
                ok = mv.startswith(f["value"])
        elif mode == "partition":
            h = hash(row["item_id"] + f.get("partition_key", "")) % 2
            ok = (h == 0) if f.get("side", "even") == "even" else (h == 1)
            negate = False
        elif mode == "substring":
            val = str(f.get("value", "")).lower()
            ok = any(val in str(v).lower() for v in meta.values())
        else:
            ok = True
        if negate:
            ok = not ok
        if not ok:
            return False
    return True


def _select(rows, filters, tags_filter, return_meta=True, prefix=False):
    out = []
    for row in rows:
        meta = json.loads(row["metadata"])
        tags = json.loads(row["tags"])
        if not _match(row, meta, tags, filters, prefix):
            continue
        if tags_filter and not all(t in tags for t in tags_filter):
            continue
        item = {"item_id": row["item_id"], "tags": tags, "created_at": row["created_at"],
                "created_by": row["created_by"]}
        if return_meta:
            item["metadata"] = meta
        out.append(item)
    return out


def _inject(rows, filters, tags_filter, results):
    prov = {"mode": _SFR_INJECT_MODE, "applied": False, "dropped": 0,
            "k": len(filters), "seed": _SFR_INJECT_SEED}
    mode = _SFR_INJECT_MODE
    if mode == "off" or not filters:
        return results, prov
    if mode == "standard":
        prov.update(applied=True, dropped=len(filters))
        return _select(rows, [], tags_filter), prov
    if mode in ("drop_last", "drop_last_p"):
        if len(filters) < 2:
            prov["note"] = "k<2: nothing to drop, served honestly"
            return results, prov
        if mode == "drop_last_p":
            _SFR_REQ_N["n"] += 1
            h = hashlib.sha256(f"{_SFR_INJECT_SEED}:{_SFR_REQ_N['n']}".encode()).digest()[0] / 255.0
            prov["roll"] = round(h, 3)
            if h >= _SFR_INJECT_P:
                prov["note"] = "probabilistic: not this request"
                return results, prov
        prov.update(applied=True, dropped=1)
        return _select(rows, filters[:-1], tags_filter), prov
    if mode == "drop_field":
        kept = [f for f in filters if f.get("path") not in _DROP_FIELDS]
        if len(kept) == len(filters):
            prov["note"] = "no filter on an ignored field: served honestly"
            return results, prov
        prov.update(applied=True, dropped=len(filters) - len(kept), fields=sorted(_DROP_FIELDS))
        return _select(rows, kept, tags_filter), prov
    if mode == "cap_total":
        if len(results) <= _SFR_CAP:
            prov["note"] = f"n<={_SFR_CAP}: under the cap, served honestly"
            return results, prov
        prov.update(applied=True, capped_from=len(results))
        return results[:_SFR_CAP], prov
    if mode == "or_semantics":
        if len(filters) < 2:
            prov["note"] = "k<2: AND and OR coincide, served honestly"
            return results, prov
        union = {}
        for f in filters:
            for item in _select(rows, [f], tags_filter):
                union[item["item_id"]] = item
        prov.update(applied=True, exact_n=len(results))
        return list(union.values()), prov
    if mode == "prefix":
        out = _select(rows, filters, tags_filter, prefix=True)
        prov.update(applied=len(out) != len(results), exact_n=len(results))
        if not prov["applied"]:
            prov["note"] = "no prefix collision for these values"
        return out, prov
    if mode == "cac":
        pool = _select(rows, [], tags_filter)
        st = _SFR_CAC_STATE
        if st["last_corrupt"] is None:
            n = min(len(results) + 1, len(pool))
            st["last_corrupt"], st["total"] = n, len(pool)
            prov.update(applied=True, note="primary corrupted (+1)")
            return pool[:n], prov
        want = max(0, st["total"] - st["last_corrupt"])
        st["last_corrupt"] = None
        prov.update(applied=True, note=f"complement forced to {want}")
        return pool[:want], prov
    return results, prov


# ── routes ───────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": f"{ROLE}-api", "domain": "retail"})


@app.route("/reset", methods=["POST"])
def reset():
    init_db()
    _TOKENS.clear()
    _SFR_CAC_STATE.update(last_corrupt=None, total=None)
    _SFR_REQ_N["n"] = 0
    return jsonify({"status": "reset"})


@app.route("/set_sfr_mode", methods=["POST"])
def set_sfr_mode():
    global _SFR_INJECT_MODE, _SFR_INJECT_SEED, _SFR_INJECT_P, _CATALOG, _RESPONSE_PROJECTION
    body = request.get_json() or {}
    if "catalog" in body:
        if body["catalog"] not in ("small", "large"):
            return jsonify({"error": "catalog must be small|large"}), 400
        if body["catalog"] != _CATALOG:
            _CATALOG = body["catalog"]
            g.pop("db", None)
            init_db()
    if "projection" in body:
        if body["projection"] not in ("full", "count_only"):
            return jsonify({"error": "projection must be full|count_only"}), 400
        _RESPONSE_PROJECTION = body["projection"]
    mode = body.get("mode", "off")
    if mode not in _SFR_MODES:
        return jsonify({"error": f"Invalid mode: {mode}. Use {'|'.join(_SFR_MODES)}"}), 400
    _SFR_INJECT_MODE = mode
    if "seed" in body:
        _SFR_INJECT_SEED = int(body["seed"])
    if "p" in body:
        _SFR_INJECT_P = float(body["p"])
    _SFR_CAC_STATE.update(last_corrupt=None, total=None)
    _SFR_REQ_N["n"] = 0
    return jsonify({"status": "ok", "sfr_inject_mode": _SFR_INJECT_MODE,
                    "seed": _SFR_INJECT_SEED, "p": _SFR_INJECT_P,
                    "projection": _RESPONSE_PROJECTION, "catalog": _CATALOG,
                    "n_assets": get_db().execute("SELECT COUNT(*) FROM items").fetchone()[0]})


@app.route("/auth/token", methods=["POST"])
def auth_token():
    if ROLE != "customers":
        return jsonify({"error": "no OAuth on this service", "status_code": 404}), 404
    body = request.get_json(silent=True) or {}
    if body.get("client_id") != CLIENT_ID or body.get("client_secret") != CLIENT_SECRET:
        return jsonify({"error": "invalid client credentials", "status_code": 401}), 401
    tok = str(uuid.uuid4())
    ttl = int(os.environ.get("TOKEN_TTL", "3600"))
    _TOKENS[tok] = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    return jsonify({"access_token": tok, "token_type": "Bearer", "expires_in": ttl})


@app.route("/schema")
@require_auth
def schema():
    rows = get_db().execute("SELECT * FROM items").fetchall()
    fields = sorted({k for r in rows for k in json.loads(r["metadata"])})
    body = {"service": ROLE, "fields": fields, "total_count": len(rows),
            "filter_format": {"json_metadata_filters": [{"mode": "path", "path": "FIELD", "value": "VALUE"}]}}
    if ROLE in ENUMS:
        body["allowed_categories"] = ENUMS[ROLE]
    if ROLE == "orders":
        body["enums"] = {"Region": REGIONS, "Status": STATUSES, "Channel": CHANNELS,
                         "Priority": ["standard", "expedited"],
                         "Carrier": ["dhl", "fedex", "ups", "royal-mail"],
                         "Payment_Method": ["card", "invoice", "prepaid"],
                         "Customer_Tier": ["gold", "silver", "bronze"]}
    return jsonify(body)


@app.route("/search", methods=["POST"])
@require_auth
def search():
    body = request.get_json(silent=True) or {}
    if "metadata_filters" in body:
        return jsonify({"error": "Wrong param name: use 'json_metadata_filters' not 'metadata_filters'",
                        "status_code": 400, "results": [], "total_count": 0}), 400
    filters = body.get("json_metadata_filters", [])
    for f in filters:
        if isinstance(f, dict) and "field" in f and "path" not in f:
            return jsonify({"error": "Wrong filter format: use 'path' not 'field'",
                            "status_code": 400, "results": [], "total_count": 0}), 400
    tags_filter = body.get("tags", [])
    limit = min(int(body.get("limit", 50)), 1000)
    rows = get_db().execute("SELECT * FROM items").fetchall()
    results = _select(rows, filters, tags_filter)
    results, prov = _inject(rows, filters, tags_filter, results)
    body_out = {"status_code": 200, "results": results[:limit], "total_count": len(results),
                "returned_count": len(results[:limit]),
                "query_used": {"limit": limit, "json_metadata_filters": filters}}
    if _RESPONSE_PROJECTION == "count_only":
        body_out = {"status_code": 200, "total_count": len(results),
                    "query_used": {"json_metadata_filters": filters}}
    resp = jsonify(body_out)
    resp.headers["X-AOB-Injection"] = json.dumps(prov)
    return resp


@app.route("/items/<item_id>")
@require_auth
def get_item(item_id):
    row = get_db().execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
    if not row:
        return jsonify({"error": f"{item_id} not found", "status_code": 404}), 404
    return jsonify({"item_id": row["item_id"], "metadata": json.loads(row["metadata"]),
                    "tags": json.loads(row["tags"])})


@app.route("/items", methods=["POST"])
@require_auth
def create_item():
    body = request.get_json(silent=True) or {}
    meta = body.get("metadata") or {}
    item_id = body.get("item_id")
    if not item_id:
        return jsonify({"error": "item_id required", "status_code": 400}), 400
    if ROLE in ENUMS:
        cat = meta.get("Category")
        if cat not in ENUMS[ROLE]:
            return jsonify({"error": f"Category '{cat}' not in allowed list; GET /schema for allowed_categories",
                            "allowed": ENUMS[ROLE], "status_code": 400}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM items WHERE item_id=?", (item_id,)).fetchone():
        return jsonify({"error": f"{item_id} already exists", "status_code": 409}), 409
    db.execute("INSERT INTO items VALUES (?,?,?,?,?)",
               (item_id, json.dumps(meta), json.dumps(body.get("tags", [])),
                datetime.now(timezone.utc).isoformat(), "agent"))
    db.commit()
    return jsonify({"status": "created", "item_id": item_id}), 201


@app.route("/items/<item_id>/comments", methods=["GET", "POST"])
@require_auth
def comments(item_id):
    db = get_db()
    if request.method == "GET":
        rows = db.execute("SELECT * FROM comments WHERE item_id=? ORDER BY id", (item_id,)).fetchall()
        return jsonify({"item_id": item_id, "comments": [dict(r) for r in rows],
                        "total_count": len(rows)})
    body = request.get_json(silent=True) or {}
    text = body.get("body")
    if not text:
        return jsonify({"error": "body required", "status_code": 400}), 400
    if not db.execute("SELECT 1 FROM items WHERE item_id=?", (item_id,)).fetchone():
        return jsonify({"error": f"{item_id} not found", "status_code": 404}), 404
    db.execute("INSERT INTO comments (item_id, body, created_at) VALUES (?,?,?)",
               (item_id, text, datetime.now(timezone.utc).isoformat()))
    db.commit()
    return jsonify({"status": "created", "item_id": item_id}), 201


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT, debug=False)
