"""
blind_faults.py -- six silent fault modes for the CatalogAPI POST /search endpoint.

Each fault models a real bug class seen in production search / count back ends.
Every fault:
  * returns a list of result records (the server still answers HTTP 200 with a
    plausible total_count = len(list), no error and no warning);
  * is deterministic: it depends only on the query and the stored rows
    (hashing uses zlib.crc32, never Python's salted hash());
  * is structural: it never looks at "is this a complement query?" and never
    adjusts a count to hide itself.

Entry point:
    apply(fault, rows, filters, tags_filter, search_text, return_meta, results, select) -> list

    rows        -- all stored rows (sqlite3.Row or dict with item_id, s3_uri, tags,
                   json_metadata, created_at, ...), in storage (rowid) order
    filters     -- the json_metadata_filters list from the request
    results     -- the correct answer (select(rows, filters, ...))
    select      -- catalog_app._select

See FAULTS.md for the real-world analogue of each fault.
"""

import json
import re
import zlib

FAULTS = [
    "analyzed_text_match",     # search engine: match on analyzed text field instead of term on keyword
    "null_negation_drop",      # SQL three-valued logic: field <> 'x' drops rows where field IS NULL
    "partial_shard_timeout",   # search cluster: one shard times out, 200 OK with partial hits
    "tag_join_fanout",         # ORM: COUNT(*) over a one-to-many JOIN without DISTINCT
    "keyset_cursor_ties",      # pagination: keyset cursor on a non-unique sort column with '>'
    "lossy_bitmap_no_recheck", # planner: BitmapAnd on lossy (page-level) bitmaps, recheck skipped
]

# ── tunables (fixed, so every fault is deterministic) ─────────────────────────
N_SHARDS = 5          # partial_shard_timeout: number of index shards
HOT_SHARD = 3         # partial_shard_timeout: the shard on the overloaded node
COST_TIMEOUT = 3      # partial_shard_timeout: query cost at which the hot shard times out
PAGE_SIZE = 10        # keyset_cursor_ties: internal page size of the backing index
SORT_FIELD = "Batch_ID"   # keyset_cursor_ties: default (non-unique) sort column
HEAP_PAGE_ROWS = 8    # lossy_bitmap_no_recheck: rows per heap page
LOSSY_MIN = 3         # lossy_bitmap_no_recheck: hits per page at which the page bit goes lossy


# ── helpers ──────────────────────────────────────────────────────────────────
def _meta(row):
    m = row["json_metadata"]
    return json.loads(m) if isinstance(m, str) else (m or {})


def _tags(row):
    t = row["tags"]
    return json.loads(t) if isinstance(t, str) else (t or [])


def _is_path(f):
    return isinstance(f, dict) and f.get("mode", "path") == "path"


def _split(filters):
    filters = filters or []
    return [f for f in filters if _is_path(f)], [f for f in filters if not _is_path(f)]


def _path_ok(meta, f):
    """Correct semantics of one path filter (same as catalog_app._match_filters)."""
    ok = meta.get(f.get("path", "")) == f.get("value")
    return (not ok) if f.get("negate", False) else ok


def _rebuild(rows, filters, tags_filter, search_text, return_meta, select, row_pred):
    """Re-run the search with a faulty path-filter predicate.

    row_pred(index, row, meta, path_filters) decides the path filters; every
    other part of the query (non-path modes, tags, search_text, item building)
    is delegated to the real select() so only the faulty piece differs.
    """
    path_f, other_f = _split(filters)
    out = []
    for i, row in enumerate(rows):
        if row_pred(i, row, _meta(row), path_f):
            out.extend(select([row], other_f, tags_filter, search_text, return_meta))
    return out


def _stable_hash(s):
    return zlib.crc32(str(s).encode("utf-8")) & 0xFFFFFFFF


# ── fault 1: analyzed_text_match ─────────────────────────────────────────────
_TOKEN_RE = re.compile(r"[0-9a-z_]+")


def _tokens(v):
    # like a standard analyzer: lowercase, split on '-', space, '/', '.', etc.
    # (underscore joins words, so "batch_1" stays one token)
    return set(_TOKEN_RE.findall(str(v).lower()))


def _analyzed_ok(meta, f):
    mv = meta.get(f.get("path", ""))
    qv = f.get("value")
    if isinstance(mv, str) and isinstance(qv, str):
        ok = bool(_tokens(mv) & _tokens(qv))       # default match operator = OR
    else:
        ok = mv == qv                               # numbers/bools mapped as exact types
    return (not ok) if f.get("negate", False) else ok


def _f_analyzed(rows, filters, tags_filter, search_text, return_meta, results, select):
    return _rebuild(rows, filters, tags_filter, search_text, return_meta, select,
                    lambda i, r, m, pf: all(_analyzed_ok(m, f) for f in pf))


# ── fault 2: null_negation_drop ──────────────────────────────────────────────
def _null_ok(meta, f):
    if f.get("negate", False) and meta.get(f.get("path", "")) is None:
        return False            # NULL <> 'x' is UNKNOWN, and WHERE treats UNKNOWN as false
    return _path_ok(meta, f)


def _f_null(rows, filters, tags_filter, search_text, return_meta, results, select):
    if not any(f.get("negate", False) for f in _split(filters)[0]):
        return list(results)
    return _rebuild(rows, filters, tags_filter, search_text, return_meta, select,
                    lambda i, r, m, pf: all(_null_ok(m, f) for f in pf))


# ── fault 3: partial_shard_timeout ───────────────────────────────────────────
def _query_cost(filters, tags_filter, search_text):
    cost = 0
    for f in filters or []:
        mode = f.get("mode", "path") if isinstance(f, dict) else "path"
        if mode == "path":
            cost += 2 if f.get("negate", False) else 1   # must_not needs a complement scan
        elif mode in ("substring", "fts"):
            cost += 2                                    # wildcard / full-text scan
        else:
            cost += 1
    if tags_filter:
        cost += 1
    if search_text:
        cost += 2
    return cost


def _f_shard(rows, filters, tags_filter, search_text, return_meta, results, select):
    if _query_cost(filters, tags_filter, search_text) < COST_TIMEOUT:
        return list(results)
    return [it for it in results
            if _stable_hash(it["item_id"]) % N_SHARDS != HOT_SHARD]


# ── fault 4: tag_join_fanout ─────────────────────────────────────────────────
def _f_fanout(rows, filters, tags_filter, search_text, return_meta, results, select):
    # Only the compound-query path (>= 2 metadata conditions) goes through the SQL
    # builder that LEFT JOINs asset_tags; the single-filter fast path does not.
    if len(_split(filters)[0]) < 2:
        return list(results)
    tag_n = {r["item_id"]: len(_tags(r)) for r in rows}
    out = []
    for it in results:
        n = tag_n.get(it["item_id"], len(it.get("tags") or []))
        out.extend([it] * max(1, n))       # LEFT JOIN: 0 tags still gives 1 row
    return out


# ── fault 5: keyset_cursor_ties ──────────────────────────────────────────────
def _f_keyset(rows, filters, tags_filter, search_text, return_meta, results, select):
    key_of = {r["item_id"]: str(_meta(r).get(SORT_FIELD, "")) for r in rows}
    # ORDER BY Batch_ID; ties come back in storage order (stable sort)
    ordered = sorted(results, key=lambda it: key_of.get(it["item_id"], ""))
    out, i = [], 0
    while i < len(ordered):
        page = ordered[i:i + PAGE_SIZE]
        out.extend(page)
        if len(page) < PAGE_SIZE:
            break
        last = key_of.get(page[-1]["item_id"], "")
        # next request: WHERE Batch_ID > :last ORDER BY Batch_ID LIMIT PAGE_SIZE
        j = i + PAGE_SIZE
        while j < len(ordered) and key_of.get(ordered[j]["item_id"], "") <= last:
            j += 1                          # rows tied with the cursor are never fetched
        i = j
    return out


# ── fault 6: lossy_bitmap_no_recheck ─────────────────────────────────────────
def _f_lossy(rows, filters, tags_filter, search_text, return_meta, results, select):
    path_f, _ = _split(filters)
    pos = [f for f in path_f if not f.get("negate", False)]
    if len(pos) < 2:
        return list(results)                # single index scan: exact, no BitmapAnd
    metas = [_meta(r) for r in rows]
    # For every positive condition after the first, the bitmap keeps exact row bits,
    # except on dense pages (>= LOSSY_MIN hits), which are stored as one lossy
    # "whole page matches" bit to save memory. The recheck that should re-test those
    # rows is skipped, so every row on a lossy page passes that condition.
    lossy_pages = []
    for f in pos[1:]:
        hits = {}
        for i, m in enumerate(metas):
            if _path_ok(m, f):
                hits[i // HEAP_PAGE_ROWS] = hits.get(i // HEAP_PAGE_ROWS, 0) + 1
        lossy_pages.append({p for p, n in hits.items() if n >= LOSSY_MIN})
    first = pos[0]
    neg = [f for f in path_f if f.get("negate", False)]

    def pred(i, r, m, pf):
        if not _path_ok(m, first):          # leading index condition: exact
            return False
        if not all(_path_ok(m, f) for f in neg):   # heap filter: exact
            return False
        page = i // HEAP_PAGE_ROWS
        return all(_path_ok(m, f) or page in lp for f, lp in zip(pos[1:], lossy_pages))

    return _rebuild(rows, filters, tags_filter, search_text, return_meta, select, pred)


_IMPL = {
    "analyzed_text_match": _f_analyzed,
    "null_negation_drop": _f_null,
    "partial_shard_timeout": _f_shard,
    "tag_join_fanout": _f_fanout,
    "keyset_cursor_ties": _f_keyset,
    "lossy_bitmap_no_recheck": _f_lossy,
}
assert list(_IMPL) == FAULTS


def apply(fault, rows, filters, tags_filter, search_text, return_meta, results, select):
    """Return the (possibly wrong) result list that the faulty server would send.

    Unknown fault names or "off" return the correct results unchanged.
    """
    fn = _IMPL.get(fault)
    if fn is None:
        return list(results)
    rows = list(rows)
    return fn(rows, filters or [], tags_filter or [], search_text, return_meta, results, select)
