"""
CatalogAPI: a data asset catalog service.
Part of AgentOrchBench.
"""

import json
import os
import json as _json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request
from flask_cors import CORS

import os as _os
_EG_DEGRADED = _os.environ.get("EG_MODE", "full") == "degraded"

# ── v2: Pagination and rate-limiting mode ─────────────────────────────────────
# RATE_LIMIT_MODE=on  → enforce max 5 results per page with next_cursor pagination
# TENANT_MODE=on      → enforce per-tenant API key isolation (read-only key supported)
_RATE_LIMIT_MODE = _os.environ.get("RATE_LIMIT_MODE", "off") == "on"
_TENANT_MODE = _os.environ.get("TENANT_MODE", "off") == "on"

# ── SFR injection mode (B3 redesign; see reeval/B3_injection_redesign.md) ────
# _AOB_B3 marker: structural injection. The adversary is an API BUG, not an oracle —
# it never inspects filter VALUES and never asks "is this a complement query?", so it
# corrupts primary and complement queries by the same rule and cannot be gamed by the
# task author. Every /search reply carries an X-AOB-Injection provenance header.
#
#   off         → correct behaviour
#   standard    → ignore every condition, return the whole catalog (obvious wrong count)
#   drop_last   → evaluate C1..C(k-1), silently drop Ck. Only fires when k >= 2, so a
#                 single-predicate query is always served honestly.
#   drop_last_p → drop_last with probability p per request (seeded; flaky-API studies)
#   cac         → complement-aware corruption: remembers the last primary count and
#                 answers the next query with (total - N_corrupt) so N + M = total holds
#                 while the primary count is wrong. Defeats the naive complement check;
#                 used only for D7-R red-team runs.
#   adversarial → LEGACY value-keyword injection, kept for reproducing the pre-2026-09
#                 numbers in the appendix. Requires SFR_LEGACY_MODES=1.
_SFR_INJECT_MODE = _os.environ.get("SFR_INJECT_MODE", "off")
_SFR_INJECT_SEED = int(_os.environ.get("SFR_INJECT_SEED", "0"))
_SFR_INJECT_P = float(_os.environ.get("SFR_INJECT_P", "0.5"))
_SFR_LEGACY_MODES = _os.environ.get("SFR_LEGACY_MODES", "0") == "1"
_SFR_MODES = ("off", "standard", "drop_last", "drop_last_p", "cac",
              "cap_total", "or_semantics", "prefix",
              "ignore_negation", "off_by_one", "consistent_lie", "drop_field",
              # E10: six faults written by an author who never saw the detector or repair method
              "blind_analyzed_text_match", "blind_null_negation_drop", "blind_partial_shard_timeout",
              "blind_tag_join_fanout", "blind_keyset_cursor_ties", "blind_lossy_bitmap_no_recheck")
import blind_faults as _blind          # unmodified; hash in reeval/BLIND_FAULTS.sha256
_DROP_FIELDS = set(_os.environ.get("DROP_FIELDS", "QC_Status").split(","))
_SFR_CAP = int(_os.environ.get("CATALOG_CAP", "5"))   # cap_total: reported total never exceeds this

# Response projection. "full" returns the matching records (the benchmark default); "count_only"
# returns just the count, as a real aggregation endpoint does. Silent failure is only silent in
# the latter: with records present the agent can verify the filter by inspection.
_RESPONSE_PROJECTION = _os.environ.get("RESPONSE_PROJECTION", "full")
_SFR_CAC_STATE = {"last_corrupt": None, "total": None}
_SFR_REQ_N = {"n": 0}

# ── Infrastructure counterfactual: strict parameter validation (E7 baseline) ──
# STRICT_PARAM_MODE=on → return HTTP 400 for unknown/wrong parameter names
#   (the "just fix the API" infrastructure fix; eliminates SFR at source)
# STRICT_PARAM_MODE=off → permissive behavior (default — allows SFR to occur)
# Use this to compare: D7=3 agent-side detection vs. server-side param validation
# Set: SFR_INJECT_MODE=standard STRICT_PARAM_MODE=on → SFR eliminated by HTTP 400
_STRICT_PARAM_MODE = _os.environ.get("STRICT_PARAM_MODE", "off") == "on"

# Known valid top-level POST /search body keys
_VALID_SEARCH_KEYS = frozenset({
    "json_metadata_filters", "limit", "offset", "tags", "search_text",
    "return_metadata", "cursor", "partial_metadata_filters",  # partial: adversarial injection key
})
_PAGE_SIZE = int(_os.environ.get("CATALOG_PAGE_SIZE", "5"))  # used when RATE_LIMIT_MODE=on
_READONLY_KEY = _os.environ.get("CATALOG_READONLY_KEY", "bench-catalog-key-readonly")

# Simple in-memory rate limiter (per-IP token bucket, resets on /reset)
import collections, threading
_rate_buckets: dict = collections.defaultdict(lambda: {"tokens": 10, "last": time.time()})
_rate_lock = threading.Lock()

def _check_rate_limit(ip: str, cost: int = 1) -> bool:
    """Returns True if request is allowed, False if rate-limited. Refill: 3 req/s."""
    if not _RATE_LIMIT_MODE:
        return True
    with _rate_lock:
        bucket = _rate_buckets[ip]
        now = time.time()
        refill = (now - bucket["last"]) * 3.0  # 3 tokens/second
        bucket["tokens"] = min(10.0, bucket["tokens"] + refill)
        bucket["last"] = now
        if bucket["tokens"] >= cost:
            bucket["tokens"] -= cost
            return True
        return False

app = Flask(__name__)
CORS(app)

DB_PATH = os.environ.get("DB_PATH", "/tmp/catalog_api.db")
SEED_PATH = Path(__file__).parent / "seed_data.json"
SEED_PATHS = {"small": SEED_PATH, "large": Path(__file__).parent / "seed_data_large.json",
              "large_train": Path(__file__).parent / "seed_data_large_train.json"}
_CATALOG = _os.environ.get("CATALOG_SEED", "small")
API_KEY = os.environ.get("API_KEY", "bench-catalog-key")
CREDENTIAL_MODE = os.environ.get("CREDENTIAL_MODE", "encapsulated")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))

ACTIVE_TOKENS: dict = {}

SCHEMA = {
    "fields": [
        {"name": "Batch_ID", "type": "str", "required": True, "description": "Delivery batch identifier (e.g. batch_1)"},
        {"name": "Study_ID", "type": "str", "required": True, "description": "Short study code"},
        {"name": "File_Format", "type": "str", "required": False, "allowed": ["fastq", "cram", "bam", "vcf-somatic", "vcf-germline", "gvcf", "html", "zip", "parquet", "h5ad"], "description": "Data format"},
        {"name": "QC_Status", "type": "str", "required": False, "allowed": ["qc-pass", "qc-warn", "qc-fail", "qc-pending"], "description": "QC verdict (mutable)"},
        {"name": "QC_Tool", "type": "str", "required": False, "allowed": ["fastqc", "multiqc", "mosdepth", "custom"], "description": "Tool that produced QC output"},
        {"name": "Pipeline_Stage", "type": "str", "required": True, "allowed": ["raw-delivery", "qc-input", "qc-output", "qc-aggregate", "analysis-ready"], "description": "Lifecycle stage"},
        {"name": "Sample_Type", "type": "str", "required": False, "allowed": ["tumor", "normal", "unknown"], "description": "Sample type"},
        {"name": "Assay_Type", "type": "str", "required": False, "description": "Sequencing assay"},
        {"name": "Source", "type": "str", "required": True, "description": "Process that registered this asset"},
        {"name": "Organization", "type": "str", "required": False, "description": "Organizational unit"},
        {"name": "Transfer_ID", "type": "str", "required": False, "description": "Source transfer ID"},
    ],
    "facets": ["Batch", "QC", "File", "Pipeline", "Biology"]
}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS assets (
            item_id TEXT PRIMARY KEY,
            item_type TEXT DEFAULT 's3_file',
            s3_uri TEXT NOT NULL,
            tags TEXT DEFAULT '[]',
            json_metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            version INTEGER DEFAULT 1,
            created_by TEXT DEFAULT 'bench-seed'
        );
    """)
    if db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0:
        seed = json.loads(SEED_PATHS[_CATALOG].read_text())
        for a in seed.get("assets", []):
            db.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?)",
                (a["item_id"], a.get("item_type","s3_file"), a["s3_uri"],
                 json.dumps(a.get("tags",[])), json.dumps(a.get("json_metadata",{})),
                 a.get("created_at", datetime.now(timezone.utc).isoformat()),
                 a.get("updated_at", datetime.now(timezone.utc).isoformat()),
                 a.get("version",1), a.get("created_by","bench-seed"))
            )
        db.commit()
    db.close()


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if CREDENTIAL_MODE == "missing":
            return ("" if _EG_DEGRADED else jsonify({"error": "Unauthorized"})), 401
        api_key = request.headers.get("X-Api-Key")
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else None
        if api_key == API_KEY:
            return f(*args, **kwargs)
        if token and token in ACTIVE_TOKENS and ACTIVE_TOKENS[token]["expires"] > time.time():
            return f(*args, **kwargs)
        return ("" if _EG_DEGRADED else jsonify({"error": "Unauthorized"})), 401
    return decorated


@app.route("/auth/token", methods=["POST"])
def get_token():
    data = request.get_json() or {}
    if data.get("api_key") != API_KEY and data.get("client_secret") != API_KEY:
        return jsonify({"error": "invalid_credentials"}), 401
    token = str(uuid.uuid4())
    ACTIVE_TOKENS[token] = {"expires": time.time() + TOKEN_TTL}
    return jsonify({"token": token, "expires_in": TOKEN_TTL})


# ── B3: single source of truth for filter evaluation ─────────────────────────
def _match_filters(row, meta, tags, filters, prefix=False):
    """True if this row satisfies every filter. Same semantics as the inline loop."""
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
        elif mode == "fts":
            val = str(f.get("value", "")).lower()
            ok = any(val in str(v).lower() for v in meta.values()) or val in row["s3_uri"].lower()
        else:
            ok = True
        if negate:
            ok = not ok
        if not ok:
            return False
    return True


def _select(rows, filters, tags_filter, search_text, return_meta, prefix=False):
    """Apply filters/tags/text to rows and build the response items."""
    out = []
    for row in rows:
        meta = _json.loads(row["json_metadata"])
        tags = _json.loads(row["tags"])
        if not _match_filters(row, meta, tags, filters, prefix):
            continue
        if tags_filter and not all(t in tags for t in tags_filter):
            continue
        if search_text:
            txt = search_text.lower()
            if txt not in row["s3_uri"].lower() and not any(txt in str(v).lower() for v in meta.values()):
                continue
        item = {"item_id": row["item_id"], "item_type": row["item_type"],
                "s3_uri": row["s3_uri"], "tags": tags, "created_at": row["created_at"],
                "updated_at": row["updated_at"], "version": row["version"],
                "created_by": row["created_by"]}
        if return_meta:
            item["json_metadata"] = meta
        out.append(item)
    return out


def _inject(rows, filters, tags_filter, search_text, return_meta, results):
    """Apply the configured injection. Returns (results, provenance dict)."""
    import hashlib as _hl
    prov = {"mode": _SFR_INJECT_MODE, "applied": False, "dropped": 0,
            "k": len(filters), "seed": _SFR_INJECT_SEED}
    mode = _SFR_INJECT_MODE
    if mode == "off" or not filters:
        return results, prov
    if mode == "standard":
        prov["applied"] = True
        prov["dropped"] = len(filters)
        return _select(rows, [], tags_filter, search_text, return_meta), prov
    if mode in ("drop_last", "drop_last_p"):
        if len(filters) < 2:
            prov["note"] = "k<2: nothing to drop, served honestly"
            return results, prov
        if mode == "drop_last_p":
            _SFR_REQ_N["n"] += 1
            h = _hl.sha256(f"{_SFR_INJECT_SEED}:{_SFR_REQ_N['n']}".encode()).digest()[0] / 255.0
            prov["roll"] = round(h, 3)
            if h >= _SFR_INJECT_P:
                prov["note"] = "probabilistic: not this request"
                return results, prov
        prov["applied"] = True
        prov["dropped"] = 1
        return _select(rows, filters[:-1], tags_filter, search_text, return_meta), prov
    if mode.startswith("blind_"):
        out = _blind.apply(mode[len("blind_"):], rows, filters, tags_filter, search_text, return_meta,
                           results, _select)
        prov["applied"] = [it["item_id"] for it in out] != [it["item_id"] for it in results]
        prov["exact_n"] = len(results)
        return out, prov
    if mode == "drop_field":
        kept = [f for f in filters if f.get("path") not in _DROP_FIELDS]
        if len(kept) == len(filters):
            prov["note"] = "no filter on an ignored field: served honestly"
            return results, prov
        prov.update(applied=True, dropped=len(filters) - len(kept), fields=sorted(_DROP_FIELDS))
        return _select(rows, kept, tags_filter, search_text, return_meta), prov
    if mode == "ignore_negation":
        if not any(f.get("negate") for f in filters):
            prov["note"] = "no negated filter: served honestly"
            return results, prov
        stripped = [{k: v for k, v in f.items() if k != "negate"} for f in filters]
        prov["applied"] = True
        return _select(rows, stripped, tags_filter, search_text, return_meta), prov
    if mode == "off_by_one":
        pool = _select(rows, [], tags_filter, search_text, return_meta)
        have = {it["item_id"] for it in results}
        extra = [it for it in pool if it["item_id"] not in have][:1]
        if not extra:
            prov["note"] = "every row already matches: cannot add one"
            return results, prov
        prov["applied"] = True
        return results + extra, prov
    if mode == "consistent_lie":
        def true_n(F):
            return len(_select(rows, F, tags_filter, search_text, return_meta))
        def pos(f):
            return {k: v for k, v in f.items() if k != "negate"}
        def served(F):
            if len(F) < 2 or any(f.get("mode", "path") != "path" for f in F):
                return true_n(F)
            if F[-1].get("negate"):
                return max(0, served(F[:-1]) - served(F[:-1] + [pos(F[-1])]))
            n, npre = true_n(F), true_n(F[:-1])
            delta = min(max(1, n // 4), npre - n)
            return n + max(0, delta)
        want = served(filters)
        prov["applied"] = want != len(results)
        prov["exact_n"] = len(results)
        pool = _select(rows, [], tags_filter, search_text, return_meta)
        have = {it["item_id"] for it in results}
        filler = [it for it in pool if it["item_id"] not in have]
        out = (results + filler)[:want] if want >= len(results) else results[:want]
        return out, prov
    if mode == "cap_total":
        # pagination bug: the total is the page, and nothing says there is more
        if len(results) <= _SFR_CAP:
            prov["note"] = f"n<={_SFR_CAP}: under the cap, served honestly"
            return results, prov
        prov["applied"] = True
        prov["capped_from"] = len(results)
        return results[:_SFR_CAP], prov
    if mode == "or_semantics":
        # query-builder bug: the conjunction is evaluated as a disjunction
        if len(filters) < 2:
            prov["note"] = "k<2: AND and OR coincide, served honestly"
            return results, prov
        union = {}
        for f in filters:
            for item in _select(rows, [f], tags_filter, search_text, return_meta):
                union[item["item_id"]] = item
        prov["applied"] = True
        prov["exact_n"] = len(results)
        return list(union.values()), prov
    if mode == "prefix":
        # matching bug: equality evaluated as prefix match, on every query alike
        out = _select(rows, filters, tags_filter, search_text, return_meta, prefix=True)
        prov["applied"] = len(out) != len(results)
        prov["exact_n"] = len(results)
        if not prov["applied"]:
            prov["note"] = "no prefix collision for these values"
        return out, prov
    if mode == "cac":
        # complement-aware corruption: corrupt the first query by +1, then answer the
        # next query so the two counts sum to the catalog total.
        total_all = len(_select(rows, [], tags_filter, search_text, return_meta))
        st = _SFR_CAC_STATE
        if st["last_corrupt"] is None:
            corrupt_n = min(len(results) + 1, total_all)
            st["last_corrupt"] = corrupt_n
            st["total"] = total_all
            prov["applied"] = True
            prov["note"] = "primary corrupted (+1)"
            pool = _select(rows, [], tags_filter, search_text, return_meta)
            return pool[:corrupt_n], prov
        want = max(0, st["total"] - st["last_corrupt"])
        st["last_corrupt"] = None
        prov["applied"] = True
        prov["note"] = f"complement forced to {want} so N+M=total"
        pool = _select(rows, [], tags_filter, search_text, return_meta)
        return pool[:want], prov
    if mode == "adversarial":
        if not _SFR_LEGACY_MODES:
            prov["note"] = "legacy mode disabled (set SFR_LEGACY_MODES=1)"
            return results, prov
        PRIMARY_VALUES = {"qc-pending", "fastq", "batch_1"}
        COMPLEMENT_VALUES = {"qc-pass", "qc-fail", "h5ad", "html", "parquet"}
        fv = {f.get("value", "") for f in filters}
        if (fv & COMPLEMENT_VALUES) and not (fv & PRIMARY_VALUES):
            prov["note"] = "legacy: treated as complement, served honestly"
            return results, prov
        prov["applied"] = True
        prov["dropped"] = max(0, len(filters) - 1)
        prov["note"] = "legacy value-keyword injection"
        return _select(rows, filters[:1], tags_filter, search_text, return_meta), prov
    return results, prov


@app.route("/search", methods=["POST"])
@require_auth
def search():
    body = request.get_json() or {}

    # ── STRICT PARAM MODE (infrastructure counterfactual for E7 baseline) ────
    # When STRICT_PARAM_MODE=on, unknown/wrong parameter names return HTTP 400.
    # This is the "just fix the API" infrastructure fix that eliminates SFR at source.
    # Compare against D7=3 agent-side detection to establish the complementary defense argument.
    if _STRICT_PARAM_MODE:
        unknown_keys = [k for k in body if k not in _VALID_SEARCH_KEYS]
        if unknown_keys:
            return jsonify({
                "error": f"Unknown parameter(s): {unknown_keys}. "
                         f"Valid keys: {sorted(_VALID_SEARCH_KEYS)}. "
                         f"Use json_metadata_filters for field-level filtering.",
                "status_code": 400,
                "strict_param_mode": True,
            }), 400
        # Also reject the wrong filter key name in strict mode
        if "metadata_filters" in body:
            return jsonify({
                "error": "Unknown parameter 'metadata_filters'. Use 'json_metadata_filters'.",
                "status_code": 400,
                "strict_param_mode": True,
            }), 400

    limit = min(int(body.get("limit", 50)), 1000)
    filters = body.get("json_metadata_filters", [])
    tags_filter = body.get("tags", [])
    search_text = body.get("search_text")
    return_meta = body.get("return_json_metadata", True)

    # ── PARTIAL FILTER INJECTION (for adversarial SFR benchmark tasks) ──────
    # partial_metadata_filters applies ONLY the first condition of a multi-condition
    # filter, silently ignoring the rest. This creates plausible-count, wrong-items SFR:
    # the returned count is close to correct but items don't satisfy all conditions.
    # D7=3 (complement count) CANNOT detect this — D7=7 (semantic sampling) CAN.
    partial_filters = body.get("partial_metadata_filters", [])
    if partial_filters:
        # Apply only the first filter condition — ignore all others silently
        filters = partial_filters[:1]  # silent truncation is the SFR mechanism

    # Detect wrong top-level format: agent passed {"Study_ID": "X"} instead of
    # {"json_metadata_filters": [{"mode":"path","path":"Study_ID","value":"X"}]}
    KNOWN_PARAMS = {"json_metadata_filters","tags","search_text","return_json_metadata",
                    "limit","offset","s3_uri","s3_key","s3_bucket","item_type"}
    KNOWN_FIELDS = {"Batch_ID","Study_ID","File_Format","QC_Status","QC_Tool",
                    "Pipeline_Stage","Sample_Type","Assay_Type","Source","Organization",
                    "Transfer_ID","Patient_ID","Pipeline_Name","Weka_Path","Data_Residence"}
    wrong_top_level = [k for k in body if k not in KNOWN_PARAMS and k in KNOWN_FIELDS]
    if wrong_top_level:
        return jsonify({
            "error": f"Wrong filter format. Use json_metadata_filters not top-level keys. "
                     f"Detected wrong keys: {wrong_top_level}. "
                     f"Correct format: {{\"json_metadata_filters\": [{{\"mode\": \"path\", \"path\": \"{wrong_top_level[0]}\", \"value\": \"...\"}}]}}",
            "status_code": 400, "results": [], "total_count": 0
        }), 400

    # Validate filter format — return 400 for filters with wrong structure
    for f in filters:
        if not isinstance(f, dict):
            return jsonify({"error": f"Filter must be a dict, got {type(f).__name__}",
                           "status_code": 400, "results": [], "total_count": 0}), 400
        if "field" in f and "path" not in f:
            return jsonify({
                "error": f"Wrong filter format: use 'path' not 'field'. "
                         f"Got: {f}. Correct: {{\"mode\":\"path\",\"path\":\"{f.get('field','FIELD')}\",\"value\":\"...\"}}",
                "status_code": 400, "results": [], "total_count": 0
            }), 400
        if "metadata_filters" in body:
            return jsonify({
                "error": "Wrong param name: use 'json_metadata_filters' not 'metadata_filters'",
                "status_code": 400, "results": [], "total_count": 0
            }), 400

    db = get_db()
    rows = db.execute("SELECT * FROM assets").fetchall()

    results = []
    for row in rows:
        meta = json.loads(row["json_metadata"])
        tags = json.loads(row["tags"])
        match = True

        # Apply metadata filters (all must have correct {mode, path, value} format)
        for f in filters:
            mode = f.get("mode", "path")
            negate = f.get("negate", False)
            if mode == "path":
                field_match = meta.get(f.get("path","")) == f.get("value")
                if negate:
                    field_match = not field_match
                if not field_match:
                    match = False; break
            elif mode == "partition":
                # Randomized partition filter for D7=6 (D7-R): hash item_id by partition_key
                # Splits items into even/odd halves pseudo-randomly by partition_key
                partition_key = f.get("partition_key", "")
                side = f.get("side", "even")
                h = hash(row["item_id"] + partition_key) % 2
                partition_match = (h == 0) if side == "even" else (h == 1)
                if not partition_match:
                    match = False; break
            elif mode == "substring":
                val = f["value"].lower()
                field_match = any(val in str(v).lower() for v in meta.values())
                if negate:
                    field_match = not field_match
                if not field_match:
                    match = False; break
            elif mode == "fts":
                val = f["value"].lower()
                field_match = any(val in str(v).lower() for v in meta.values()) or val in row["s3_uri"].lower()
                if negate:
                    field_match = not field_match
                if not field_match:
                    match = False; break

        # Apply tag filter
        if match and tags_filter:
            if not all(t in tags for t in tags_filter):
                match = False

        # Apply text search
        if match and search_text:
            txt = search_text.lower()
            if txt not in row["s3_uri"].lower() and not any(txt in str(v).lower() for v in meta.values()):
                match = False

        if match:
            item = {
                "item_id": row["item_id"],
                "item_type": row["item_type"],
                "s3_uri": row["s3_uri"],
                "tags": tags,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "version": row["version"],
                "created_by": row["created_by"]
            }
            if return_meta:
                item["json_metadata"] = meta
            results.append(item)

    total = len(results)

    # ── B3 structural injection ───────────────────────────────────────────────
    _all_rows = db.execute("SELECT * FROM assets").fetchall()
    results, _prov = _inject(_all_rows, filters, tags_filter, search_text, return_meta, results)
    total = len(results)

    # v2: Rate limit check (RATE_LIMIT_MODE=on)
    client_ip = request.remote_addr or "unknown"
    if not _check_rate_limit(client_ip):
        if _EG_DEGRADED:
            return jsonify({"error": "429"}), 429
        return jsonify({
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Retry after 1 second.",
            "retry_after": 1,
            "status_code": 429,
        }), 429

    # v2: Pagination (RATE_LIMIT_MODE=on enforces page_size)
    cursor = int(body.get("cursor", 0))
    page_size = _PAGE_SIZE if _RATE_LIMIT_MODE else limit
    page_results = results[cursor:cursor + page_size]
    next_cursor = cursor + page_size if (cursor + page_size) < total else None

    resp = {
        "status_code": 200,
        "results": page_results,
        "total_count": total,
        "returned_count": len(page_results),
        "query_used": {"limit": page_size, "cursor": cursor, "json_metadata_filters": filters},
    }
    if _RESPONSE_PROJECTION == "count_only":
        # aggregation-endpoint behaviour: the count, and nothing to audit it against
        resp = {"status_code": 200, "total_count": total,
                "query_used": {"json_metadata_filters": filters}}
    if _RATE_LIMIT_MODE and next_cursor is not None:
        resp["next_cursor"] = next_cursor
        resp["has_more"] = True
    _r = jsonify(resp)
    # provenance: never shown to the agent (executor forwards the body only), but recorded
    # per call so an experiment can PROVE the adversary was active on the primary query.
    _r.headers["X-AOB-Injection"] = _json.dumps(_prov)
    return _r


@app.route("/schema", methods=["GET"])
@require_auth
def get_schema():
    lines = ["Catalog Asset Schema\n" + "="*50]
    for f in SCHEMA["fields"]:
        req = "yes" if f.get("required") else "no"
        allowed = f.get("allowed", "")
        allowed_str = f", allowed: {allowed}" if allowed else ""
        lines.append(f"  {f['name']:<20} {f['type']:<6} req={req}{allowed_str} — {f['description']}")
    return jsonify({
        "schema": "\n".join(lines),
        "field_count": len(SCHEMA["fields"]),
        "facets": SCHEMA["facets"],
        "fields": SCHEMA["fields"]
    })


@app.route("/register", methods=["POST"])
@require_auth
def register():
    body = request.get_json() or {}
    assets = body if isinstance(body, list) else [body]
    registered = []
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    for a in assets:
        s3_uri = a.get("s3_uri")
        if not s3_uri:
            continue
        item_id = a.get("item_id", str(uuid.uuid4()))
        meta = a.get("json_metadata", {})
        tags = a.get("tags", [])
        db.execute(
            "INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?,?,?,?)",
            (item_id, a.get("item_type","s3_file"), s3_uri,
             json.dumps(tags), json.dumps(meta), now, now, 1, "agent")
        )
        registered.append(item_id)
    db.commit()
    return jsonify({"registered": len(registered), "item_ids": registered}), 201


@app.route("/preview", methods=["POST"])
@require_auth
def preview():
    body = request.get_json() or {}
    assets = body if isinstance(body, list) else [body]
    preview_items = []
    for a in assets:
        meta = a.get("json_metadata", {})
        issues = []
        for f in SCHEMA["fields"]:
            if f.get("required") and f["name"] not in meta:
                issues.append(f"Missing required field: {f['name']}")
            if f.get("allowed") and meta.get(f["name"]) and meta[f["name"]] not in f["allowed"]:
                issues.append(f"Invalid value for {f['name']}: '{meta[f['name']]}'. Allowed: {f['allowed']}")
        preview_items.append({
            "s3_uri": a.get("s3_uri",""),
            "would_register": len(issues) == 0,
            "validation_issues": issues,
            "metadata_preview": meta
        })
    return jsonify({
        "preview": preview_items,
        "asset_count": len(preview_items),
        "valid_count": sum(1 for p in preview_items if p["would_register"])
    })


@app.route("/assets/<item_id>", methods=["GET"])
@require_auth
def get_asset(item_id):
    """GET single asset by item_id — used by D7=7 semantic sampling harness."""
    db = get_db()
    row = db.execute("SELECT * FROM assets WHERE item_id = ?", (item_id,)).fetchone()
    if not row:
        return jsonify({"error": f"Asset {item_id} not found"}), 404
    return jsonify({
        "item_id": row["item_id"],
        "s3_uri": row["s3_uri"],
        "item_type": row["item_type"],
        "json_metadata": json.loads(row["json_metadata"]),
        "tags": json.loads(row["tags"]),
    })


@app.route("/assets/<item_id>", methods=["PATCH"])
@require_auth
def update_asset(item_id):
    db = get_db()
    row = db.execute("SELECT * FROM assets WHERE item_id = ?", (item_id,)).fetchone()
    if not row:
        return jsonify({"error": f"Asset {item_id} not found"}), 404
    data = request.get_json() or {}
    meta = json.loads(row["json_metadata"])
    new_meta = data.get("json_metadata", {})
    meta.update(new_meta)
    now = datetime.now(timezone.utc).isoformat()
    new_version = row["version"] + 1
    db.execute(
        "UPDATE assets SET json_metadata=?, updated_at=?, version=? WHERE item_id=?",
        (json.dumps(meta), now, new_version, item_id)
    )
    if "tags" in data:
        existing_tags = json.loads(row["tags"])
        new_tags = list(set(existing_tags + data["tags"]))
        db.execute("UPDATE assets SET tags=? WHERE item_id=?", (json.dumps(new_tags), item_id))
    db.commit()
    return jsonify({"success": True, "item_id": item_id, "version": new_version})




@app.route("/reset", methods=["POST"])
def reset():
    import os
    db_path = os.environ.get("DB_PATH", "/tmp/catalog_api.db")
    try:
        g.pop("db", None)
    except Exception:
        pass
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    return jsonify({"status": "reset", "message": "Database reset to seed state"})


@app.route("/set_sfr_mode", methods=["POST"])
def set_sfr_mode():
    """Runtime toggle for SFR injection mode (for experiments without API restart).
    POST /set_sfr_mode {"mode": "off"|"standard"|"adversarial"}
    """
    global _SFR_INJECT_MODE, _SFR_INJECT_SEED, _SFR_INJECT_P, _CATALOG
    body = request.get_json() or {}
    if "catalog" in body:
        if body["catalog"] not in SEED_PATHS:
            return jsonify({"error": "catalog must be one of " + "|".join(SEED_PATHS)}), 400
        if body["catalog"] != _CATALOG:
            _CATALOG = body["catalog"]
            db_path = _os.environ.get("DB_PATH", "/tmp/catalog_api.db")
            g.pop("db", None)
            if _os.path.exists(db_path):
                _os.remove(db_path)
            init_db()
    mode = body.get("mode", "off")
    allowed = set(_SFR_MODES) | ({"adversarial"} if _SFR_LEGACY_MODES else set())
    if mode not in allowed:
        return jsonify({"error": f"Invalid mode: {mode}. Use {'|'.join(sorted(allowed))}"}), 400
    _SFR_INJECT_MODE = mode
    if "seed" in body:
        _SFR_INJECT_SEED = int(body["seed"])
    if "p" in body:
        _SFR_INJECT_P = float(body["p"])
    _SFR_CAC_STATE["last_corrupt"] = None
    _SFR_CAC_STATE["total"] = None
    _SFR_REQ_N["n"] = 0
    global _RESPONSE_PROJECTION
    if "projection" in body:
        if body["projection"] not in ("full", "count_only"):
            return jsonify({"error": "projection must be full|count_only"}), 400
        _RESPONSE_PROJECTION = body["projection"]
    return jsonify({"status": "ok", "sfr_inject_mode": _SFR_INJECT_MODE,
                    "seed": _SFR_INJECT_SEED, "p": _SFR_INJECT_P,
                    "projection": _RESPONSE_PROJECTION, "catalog": _CATALOG,
                    "n_assets": get_db().execute("SELECT COUNT(*) FROM assets").fetchone()[0]})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "catalog-api"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8003")), debug=False)
