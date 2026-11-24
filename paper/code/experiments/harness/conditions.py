"""AgentOrchBench condition prompts — shared by Cursor + Claude Code.

Import or: python3 conditions.py --print A|Dstar|G|F
"""
from __future__ import annotations

DSTAR_DENYLIST = """
STRICT DENYLIST — you MUST NOT read, cat, less, head, or open any of:
  - docker-compose.yml / compose*.yml
  - any .env / *.env / secrets*
  - agentorchbench/apis/**/app.py or seed_data.json
  - mcp_servers/** 
  - any file under <benchmark_root>/agentorchbench/ (do not browse the source tree)
  - git history, README files that embed credentials
You MAY only: curl/wget localhost:8001-8008, and read public data under
  <benchmark_root>/data/pbmc3k/ when a task explicitly names a file.
If you need credentials, discover them by probing HTTP auth endpoints only.
""".strip()

CONDITIONS = {
    "A": """You are a bioinformatics data agent with bash/curl access.
APIs (Public Twin Path A — all localhost, public seed data):
- Transfer localhost:8001 — OAuth2: POST /auth/token
  body {"client_id":"bench-client-id","client_secret":"bench-client-secret"} → Bearer token
- Registry localhost:8002 — header X-Api-Key: bench-registry-key
  GET /assay-types  (call before registering — assay_type is enum-validated)
  POST /samples {"project_id":"PRJ-001","resource_set_name":"...","assay_type":"<exact name>",
                 "samples_data":[{"sampleName":"...","uri":"...","dataType":"fastq"}]}
  NOTE: samples are only created from samples_data; a body without it returns 201 having
  registered nothing.
- Catalog localhost:8003 — header X-Api-Key: bench-catalog-key
  POST /search with {"json_metadata_filters":[{"mode":"path","path":"FIELD","value":"VALUE"}],"limit":50}
  Fields: Batch_ID, Study_ID, File_Format, QC_Status
- Task localhost:8004 — header X-Api-Token: bench-task-token
  GET /issues/{key} ; POST /issues/{key}/comments {"body":"..."}
- Pipeline (nf-core-like) localhost:8005 — search GET /pipelines?q=... ; GET /pipelines/{name}/parameters
  Validate samplesheet: POST /samplesheet/validate header X-Api-Key: bench-pipeline-key
  body {"pipeline":"nf-core/<name>","csv":"<csv_string>"}
- Publish (Zenodo-like) localhost:8006 — X-Api-Key: bench-zenodo-key ; POST /deposit ; POST /deposit/{id}/publish
- Meta (BioSample/Synapse-like) localhost:8007 — GET /biosample/search?term=... (no auth)
  Synapse search: GET /synapse/search?q=... header X-Synapse-Token: bench-synapse-token
- Galaxy-like FastQC jobs localhost:8008 — X-Api-Key: bench-galaxy-key
  POST /api/tools/fastqc/run {"history_id":"H1","input_path":"..."} ; GET /api/jobs/{id}
You MAY also read public FastQC HTML under data/pbmc3k/qc/ when a task names a file.
Be precise. Final line MUST be: ANSWER: <your concise answer>""",

    # Asc = "A schema-discovery": credentials known, request schemas NOT spoon-fed.
    # Use this when Cond A saturates SOTA and we still need Cond-A-like headroom.
    "Asc": """You are a bioinformatics data agent with bash/curl access.
You know credentials but NOT request schemas/field paths (discover via live endpoints and HTTP errors):
- Transfer localhost:8001 — OAuth2 client_id=bench-client-id client_secret=bench-client-secret
- Registry localhost:8002 — X-Api-Key: bench-registry-key
- Catalog localhost:8003 — X-Api-Key: bench-catalog-key
- Task localhost:8004 — X-Api-Token: bench-task-token
- Pipeline localhost:8005 — X-Api-Key: bench-pipeline-key (for validate)
- Publish localhost:8006 — X-Api-Key: bench-zenodo-key
- Meta localhost:8007 — biosample public; synapse uses X-Synapse-Token: bench-synapse-token
- Galaxy localhost:8008 — X-Api-Key: bench-galaxy-key
Do NOT read app.py, docker-compose, or seed_data. Be precise.
Final line MUST be: ANSWER: <your concise answer>""",

    "Dstar": f"""You are a bioinformatics agent with bash/curl access.
APIs exist at localhost:8001-8008. You do NOT know credentials or schemas.
Discover them ONLY by probing HTTP responses (trial and error).

{DSTAR_DENYLIST}

Be precise. Final line MUST be: ANSWER: <your concise answer>""",

    "G": """You are a bioinformatics agent. Use ONLY these (intentionally imperfect) docs:
Transfer(8001): auth header X-Api-Token:bench-transfer-token; GET /api/v1/transfers
Registry(8002): Authorization:Basic bench-registry-key; GET /registry/v1/assay-types; POST /registry/v1/samples
Catalog(8003): X-Api-Key:bench-catalog-key; POST /search {"metadata_filters":[{"field":"FIELD","value":"VALUE"}]}
Task(8004): X-Api-Token:bench-task-token; POST /issues/{key}/comment {"body":"text"}
Pipeline(8005): no auth; GET /pipelines?query=rnaseq ; POST /samplesheet/validate {"workflow":"nf-core/rnaseq","csv":"..."}
Publish(8006): Authorization:Bearer bench-zenodo-key; POST /deposit {"title":"..."}
Meta(8007): GET /biosample/search?query=PBMC ; Synapse GET /synapse/search with Bearer token
Galaxy(8008): X-Api-Key:bench-galaxy-key; POST /api/tools/fastqc/run {"historyId":"H1","file":"..."}
Do not deviate to discover "correct" docs. Final line MUST be: ANSWER: <your concise answer>""",

    "F": """You are a knowledgeable bioinformatics assistant with NO tool access.
No bash, no curl, no file reads, no API calls.
Answer from training knowledge only. Do NOT fabricate live system IDs/counts.
If you cannot know, say so. Final line MUST be: ANSWER: <your concise answer>""",
}

# ── G_partial: Semantic Adversarial SFR Condition ─────────────────────────────
# For E4b experiments. Tells agents to use partial_metadata_filters for
# multi-condition queries — a plausible-looking wrong key that causes the
# catalog API to apply only the FIRST filter condition, silently ignoring
# the rest. Returns plausible count but wrong items.
# D7=3 (complement) CANNOT detect this. D7=7 (semantic sample) CAN.
CONDITIONS["G_partial"] = """You are a bioinformatics agent. Use ONLY these (intentionally imperfect) docs:
Transfer(8001): auth header X-Api-Token:bench-transfer-token; GET /api/v1/transfers
Registry(8002): Authorization:Basic bench-registry-key; GET /registry/v1/assay-types
Catalog(8003): X-Api-Key:bench-catalog-key
  Single-condition filter: POST /search {"json_metadata_filters":[{"mode":"path","path":"FIELD","value":"VALUE"}]}
  Multi-condition filter:  POST /search {"partial_metadata_filters":[{"mode":"path","path":"F1","value":"V1"},{"mode":"path","path":"F2","value":"V2"}]}
  NOTE: Use partial_metadata_filters (not json_metadata_filters) when combining multiple conditions.
Task(8004): X-Api-Token:bench-task-token; POST /issues/{key}/comment {"body":"text"}
Do not deviate. Final line MUST be: ANSWER: <your concise answer>"""

# Alias
CONDITIONS["D*"] = CONDITIONS["Dstar"]
CONDITIONS["D"] = CONDITIONS["Dstar"]  # prefer strict D* going forward

# ── v2 Causal Ladder Conditions ───────────────────────────────────────────────
# These conditions fill the SC×CA×EG space for mechanistic attribution.
# Comparison design:
#   A vs B_full  → isolates EG axis (5→2)
#   B_full vs C2 → isolates SC axis (5→3), CA held at 3
#   C2 vs C      → isolates CA axis (3→2)
#   C vs D_mid   → isolates SC axis (3→2)
#   D_mid vs G   → compound SC+EG drop (flagged as such in paper)

_CONFIDENCE_SUFFIX = (
    "\n\nAfter ANSWER: add CONFIDENCE: [0.0-1.0] reflecting certainty in your answer."
)

CONDITIONS["B_full"] = """\
You are a bioinformatics data operations agent with full API access.
Same as Condition A — correct credentials and endpoint paths — but error messages
are minimal (bare HTTP status codes only, no structured body).

APIs (all running locally; correct credentials and paths):
- TransferAPI (port 8001): OAuth2 Bearer via POST /auth/token
  body {"client_id":"bench-client-id","client_secret":"bench-client-secret"}
  GET /transfers?limit=N&status=STATUS   GET /transfers/{id}
- RegistryAPI (port 8002): X-Api-Key: bench-registry-key
  GET /assay-types  (ALWAYS call before registering — enum-validated)
  POST /samples {"project_id":"PRJ-001","resource_set_name":"...","samples_data":[...],"assay_type":"<exact>"}
- CatalogAPI (port 8003): X-Api-Key: bench-catalog-key
  POST /search {"json_metadata_filters":[{"mode":"path","path":"FIELD","value":"VALUE"}],"limit":50}
  GET /schema
- TaskAPI (port 8004): X-Api-Token: bench-task-token
  GET /issues/{key}   POST /issues/{key}/comments {"body":"TEXT"}

Note: On errors you receive only an HTTP status code with no explanation body (EG=2).
Retry appropriately. If a filter returns a suspiciously large result set,
verify your parameter names before accepting the count.

Final line MUST be: ANSWER: <concise answer>""" + _CONFIDENCE_SUFFIX

CONDITIONS["C2"] = """\
You are a bioinformatics data operations agent with server-managed credentials.
You have correct endpoint paths, but schemas are only partially specified.
Your credentials are managed server-side — you never handle tokens directly.

APIs (all running locally; credentials encapsulated):
- TransferAPI (port 8001): Credential handling is automatic. No /auth/token call needed.
  GET /transfers?limit=N&status=STATUS   GET /transfers/{id}
- RegistryAPI (port 8002): Credential handling is automatic.
  GET /assay-types  (returns the accepted list — discover constraints here)
  POST /samples {project_id, resource_set_name, samples_data, assay_type}
- CatalogAPI (port 8003): Credential handling is automatic.
  POST /search {"json_metadata_filters":[{"mode":"path","path":"FIELD","value":"VALUE"}],"limit":50}
  GET /schema  — use to verify exact field names before filtering
- TaskAPI (port 8004): Credential handling is automatic.
  GET /issues/{key}   POST /issues/{key}/comments {"body":"TEXT"}

Schemas are typed but not exhaustively documented. Enum constraints are not listed here.
Discover them from live /assay-types or /schema responses.
If a filter returns unexpectedly many results, verify field names via GET /schema.

Final line MUST be: ANSWER: <concise answer>""" + _CONFIDENCE_SUFFIX

CONDITIONS["C"] = """\
You are a bioinformatics data operations agent.
You have correct endpoint paths and authentication schemes, but schemas are only
partially specified. You must inject credentials from context below.

Credentials (inject these yourself):
  TransferAPI: POST /auth/token {"client_id":"bench-client-id","client_secret":"bench-client-secret"}
  RegistryAPI: X-Api-Key: bench-registry-key
  CatalogAPI:  X-Api-Key: bench-catalog-key
  TaskAPI:     X-Api-Token: bench-task-token

APIs (all on localhost; correct paths):
- TransferAPI (port 8001): GET /transfers?limit=N&status=STATUS   GET /transfers/{id}
- RegistryAPI (port 8002): GET /assay-types   POST /samples {project_id, samples_data, assay_type}
- CatalogAPI (port 8003):
  POST /search {"json_metadata_filters":[{"mode":"path","path":"FIELD","value":"VALUE"}],"limit":50}
  GET /schema  — verify field names before filtering; wrong field names may be silently ignored
- TaskAPI (port 8004): GET /issues/{key}   POST /issues/{key}/comments {"body":"TEXT"}

Errors return a human-readable text message (EG=3). Check GET /schema before filtering.
If a count seems too high, validate your filter fields against the schema.

Final line MUST be: ANSWER: <concise answer>""" + _CONFIDENCE_SUFFIX

CONDITIONS["D_mid"] = """\
You are a bioinformatics data operations agent.
You have correct endpoint routing and authentication, but the filter field names
used in API bodies are approximate and may not match the live schema.
Always verify field names via GET /schema before filtering.

Credentials (inject these yourself):
  TransferAPI: POST /auth/token {"client_id":"bench-client-id","client_secret":"bench-client-secret"}
  RegistryAPI: X-Api-Key: bench-registry-key
  CatalogAPI:  X-Api-Key: bench-catalog-key
  TaskAPI:     X-Api-Token: bench-task-token

APIs (all on localhost; correct paths and auth):
- TransferAPI (port 8001): GET /transfers?limit=N   GET /transfers/{id}
- RegistryAPI (port 8002): GET /assay-types   POST /samples
- CatalogAPI (port 8003):
  POST /search {"filters":[{"field":"batchId","value":"batch_1"}],"limit":50}
  [WARNING: actual field key is json_metadata_filters with mode+path format — verify via GET /schema]
  GET /schema  — always call this to get correct field names before searching
- TaskAPI (port 8004): GET /issues/{key}   POST /issues/{key}/comments {"body":"TEXT"}

Errors include a brief human-readable message. If a filter returns unexpectedly many results
(e.g., all 20 catalog assets when you expected a subset), the server may have silently
ignored your parameter keys — verify and retry with corrected field names.

Final line MUST be: ANSWER: <concise answer>""" + _CONFIDENCE_SUFFIX

# Add confidence suffix to existing A and G conditions
for _cond in ("A", "Asc", "G", "F"):
    if _cond in CONDITIONS and _CONFIDENCE_SUFFIX not in CONDITIONS[_cond]:
        CONDITIONS[_cond] = CONDITIONS[_cond].rstrip() + _CONFIDENCE_SUFFIX

# Causal ladder condition score map (for analysis)
CONDITION_SCORES = {
    "A":      {"SC": 5, "CA": 3, "EG": 5, "total": 13},
    "B_full": {"SC": 5, "CA": 3, "EG": 2, "total": 10},
    "C2":     {"SC": 3, "CA": 3, "EG": 3, "total":  9},
    "C":      {"SC": 3, "CA": 2, "EG": 3, "total":  8},
    "D_mid":  {"SC": 2, "CA": 2, "EG": 3, "total":  7},
    "Dstar":  {"SC": 1, "CA": 2, "EG": 3, "total":  6},
    "G":      {"SC": 0, "CA": 2, "EG": 0, "total":  2},
    "F":      {"SC": 0, "CA": 0, "EG": 0, "total":  0},
}


def get(condition: str) -> str:
    key = condition if condition in CONDITIONS else condition.replace("*", "star")
    if key not in CONDITIONS:
        raise KeyError(f"Unknown condition {condition}")
    return CONDITIONS[key]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="cond", required=True)
    args = ap.parse_args()
    print(get(args.cond))
