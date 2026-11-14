# Silent fault modes for `POST /search` (`blind_faults.py`)

Each fault returns HTTP 200 and a normal-looking `total_count` (the length of the
returned list). None of them raises an error or adds a warning. All are
deterministic: the same query on the same data always gives the same answer.
Shard hashing uses `zlib.crc32`, not Python's salted `hash()`. No fault reads
filter values to decide "is this a complement query?", and none adjusts a count
to hide itself. None of them is one of the known modes (drop last filter, drop a
field, cap the total, AND read as OR, prefix match, ignore negation, off-by-one,
adversary).

Use: `blind_faults.apply(fault, rows, filters, tags_filter, search_text, return_meta, results, select)`.
Fault names are in `blind_faults.FAULTS`. Self-test: `python3 selftest_blind_faults.py`.

| # | Fault | Count moves | Fires on |
|---|-------|-------------|----------|
| 1 | `analyzed_text_match` | mostly up (down for negations) | conditions whose value has a `-`, space or capitals, when sibling values share a word |
| 2 | `null_negation_drop` | down | negated conditions on optional fields that some rows lack |
| 3 | `partial_shard_timeout` | down (about 1/5) | "costly" queries: cost >= 3 (plain condition = 1, negated = 2, tags = 1, text = 2) |
| 4 | `tag_join_fanout` | up | any query with >= 2 metadata conditions that matches rows with 2+ tags |
| 5 | `keyset_cursor_ties` | down | result sets larger than one internal page (10) that span a Batch_ID boundary |
| 6 | `lossy_bitmap_no_recheck` | up | >= 2 plain (non-negated) conditions where a later condition is dense on some 8-row page |

---

### 1. `analyzed_text_match`: match on an analyzed text field instead of an exact keyword term

**What it does.** Each `path` condition on a string field is run the way a search
engine runs a `match` query on an analyzed `text` field. Both the stored value and
the filter value are lowercased and split into words at `-`, spaces, `/`, `.` and so
on (`_` does not split, so `batch_1` stays one word). A row matches if it shares
**any** word with the filter value, because the default match operator is OR. So
`QC_Status = qc-pass` matches `qc-warn`, `qc-fail` and `qc-pending` (shared word
`qc`). `Pipeline_Stage = qc-input` also matches `qc-output` and `qc-aggregate`.
`File_Format = vcf-somatic` matches `vcf-germline`. A negated condition then
throws out all those siblings. Numbers and booleans keep exact matching.
**Real bug.** This is the classic Elasticsearch / OpenSearch mapping bug. Dynamic
mapping stores strings as `text` with a `.keyword` sub-field. Code that sends
`match` (or `term`) to `status` instead of `term` to `status.keyword` gets
word-level, case-folded matches. There are many Stack Overflow and issue-tracker
reports like "term query on hyphenated value returns wrong docs" and "match on
status returns every document". Solr `text_general` fields and Postgres
`to_tsvector` searches used for exact filters have the same problem.
**Queries hit.** Any query with a condition on a hyphenated or multi-word value
where other values of that field share a word (QC_Status, Pipeline_Stage `qc-*`,
File_Format `vcf-*`). On QC_Status it acts close to "field ignored", since every
value starts with `qc`. On other fields it only merges sibling values. Positive
conditions go up. Negated ones fall sharply. Queries on Study_ID, Batch_ID,
`fastq`, `bam` and `tumor` are not changed.

### 2. `null_negation_drop`: SQL three-valued logic on `!=`

**What it does.** A negated condition `field != V` should keep rows where the field
is missing. With this fault those rows are dropped, as if the filter were compiled
to `json_extract(meta, '$.field') <> 'V'`. For a missing key that expression is
`NULL <> 'V'`, which is UNKNOWN, and `WHERE` treats UNKNOWN as false. Positive
conditions are not changed.
**Real bug.** One of the most common SQL bugs. `WHERE status <> 'failed'` (or
`NOT IN (...)`) silently leaves out NULL rows, and a JSON path to a missing key
gives NULL. Many ORMs (Django `exclude()` on nullable joins, SQLAlchemy `!=`,
ActiveRecord `where.not`) and many "exclude" features in BI tools have shipped or
documented this. The fix is `IS DISTINCT FROM` or an `OR col IS NULL` clause.
**Queries hit.** Only queries with at least one negated condition on an optional
field (QC_Status, Sample_Type, File_Format, QC_Tool, Assay_Type, Organization,
Transfer_ID) where some matched rows lack that field. Required fields and
positive-only queries are not changed. This breaks the complement check
`N(A and B) + N(A and not B) = N(A)`, but only for sparse fields.

### 3. `partial_shard_timeout`: one shard times out and the hits are partial, status 200

**What it does.** Records are spread over 5 shards by `crc32(item_id) % 5`. Shard 3
sits on an overloaded node. When a query is "costly" (cost = 1 per plain condition,
2 per negated condition, since `must_not` scans the complement, 2 per
substring/fts condition, +1 for a tag filter, +2 for free text), and the cost is 3
or more, shard 3 times out. Its hits are silently missing, so about 20% of the true
matches are lost. Cheap queries are served in full.
**Real bug.** By default Elasticsearch / OpenSearch return **HTTP 200** with
`timed_out: true` or `_shards.failed > 0` and whatever hits the other shards gave
(`allow_partial_search_results=true`). Solr's `shards.tolerant=true`
(`partialResults=true`) and Cassandra / Scylla reads at consistency ONE behave the
same way. Most client wrappers never look at the `_shards` block, so the partial
count reaches users as if it were exact. Real postmortems describe "counts dropped
by a fifth during a GC storm on one data node".
**Queries hit.** All 3-condition queries, 2-condition queries with a negation, and
any query with a tag or text filter. Two plain conditions (cost 2) and
single-condition queries are exact, so simple spot checks pass.

### 4. `tag_join_fanout`: COUNT(*) over a one-to-many JOIN without DISTINCT

**What it does.** Compound queries (2 or more metadata conditions) go through a SQL
builder that `LEFT JOIN`s the `asset_tags` table so it can return tags, and it
counts `COUNT(*)` instead of `COUNT(DISTINCT item_id)`. Each matching asset is
returned once per tag (once if it has no tags), so the count grows. The
single-condition fast path does not join, so it stays exact.
**Real bug.** Join fan-out is one of the most reported ORM and reporting bugs.
Django `.filter()` across a multi-valued relation returns duplicates (hence
`.distinct()`). Rails `joins(:tags).count` counts join rows. SQLAlchemy `query.count()`
over joined eager loads is another case. Looker / BI "fan-out" warnings exist for
the same reason. It often ships when a new JOIN is added for one output column
only.
**Queries hit.** Any query with 2 or more `path` conditions whose matches include
rows with 2+ tags. Counts go up, often by 1.5-2x. Single-condition queries and
queries that match only untagged or single-tag assets are not changed. Duplicates
show up in the list only if the client checks item_ids.

### 5. `keyset_cursor_ties`: keyset pagination on a non-unique sort column with `>`

**What it does.** The API collects results from its backing index 10 at a time,
sorted by the default sort column `Batch_ID`. It pages with a keyset cursor:
`WHERE Batch_ID > :last_batch ORDER BY Batch_ID LIMIT 10`. Because `Batch_ID` is not
unique and there is no `item_id` tie-break, any rows that share the `Batch_ID` of the
last row on a page, but did not fit on that page, are never fetched. The total is
the sum of what was fetched, so it comes out low.
**Real bug.** A well-known pagination bug. Keyset / seek pagination needs a unique
sort key such as `(created_at, id) > (:ts, :id)`. With `created_at > :ts` alone, rows
with the same timestamp are skipped. This has been reported for Stripe-style
cursors built on timestamps, GitHub / Jira sync jobs losing items that were
bulk-created in the same second, Elasticsearch `search_after` without a
tie-breaker, and Kafka Connect JDBC `timestamp` mode (which documents that it
misses rows sharing a timestamp unless you use `timestamp+incrementing`).
**Queries hit.** Only queries with more than 10 true matches where a page boundary
falls inside a Batch_ID group. Small result sets (most 3-condition queries) and
results from a single batch that fit on one page are exact. A query pinned to one
Batch_ID with more than 10 hits is capped at 10.

### 6. `lossy_bitmap_no_recheck`: BitmapAnd on lossy pages with the recheck skipped

**What it does.** With 2 or more plain (non-negated) conditions, the planner ANDs
per-condition bitmaps. The first condition is checked exactly. For each later
condition, the bitmap keeps exact row bits, except on "dense" heap pages (8 rows
per page, 3 or more hits). Those pages are stored as one lossy "whole page
matches" bit to save memory. The recheck that should re-test the rows on lossy
pages is skipped, so a row on such a page passes that condition even if its own
value is different. Negated conditions are a heap filter and stay exact.
**Real bug.** PostgreSQL's Bitmap Heap Scan turns pages lossy when the bitmap
passes `work_mem` and then needs a `Recheck Cond`. BRIN and bloom indexes are
always lossy for the same reason. Wrong results in lossy / recheck paths have
come up in the PostgreSQL bug lists (tidbitmap / GIN / BRIN recheck fixes).
MySQL's `index_merge_intersection` gave wrong results for years, and the usual
advice was `optimizer_switch='index_merge_intersection=off'`. Hand-built bitmap or
Bloom-filter prefilters in search services that skip the final check fail the
same way.
**Queries hit.** Queries with 2 or more positive conditions where a later condition
is common on some storage pages (for example Study_ID or Source in data loaded by
batch). Rows on those pages that match the first condition but not the later one
are counted, so the count goes up by a data-dependent amount. Single-condition
queries and queries whose later conditions are rare on every page are exact. It
is not "drop a condition": the dropped check applies only on dense pages.

---

## Self-test result (`python3 selftest_blind_faults.py`, 80 fake rows, 8 two-condition + 6 three-condition queries)

| Fault | 2-cond changed | 3-cond changed | unchanged |
|-------|---------------:|---------------:|----------:|
| analyzed_text_match | 4 | 2 | 8 |
| null_negation_drop | 2 | 3 | 9 |
| partial_shard_timeout | 2 | 3 | 9 |
| tag_join_fanout | 6 | 3 | 5 |
| keyset_cursor_ties | 2 | 2 | 10 |
| lossy_bitmap_no_recheck | 3 | 3 | 8 |

All 6 faults: deterministic (two runs give the same ids), change the count on some
2- and 3-condition queries, and leave it the same on others. SELF-TEST PASSED.
