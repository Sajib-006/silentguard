"""
RegistryAPI: a sample registry service.
Part of AgentOrchBench.
"""

import json
import os
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

# ── v2: RBAC / multi-tenant mode ─────────────────────────────────────────────
# TENANT_MODE=on → enforce per-tenant API key isolation
# Tenant 1 (default): bench-registry-key  → full read+write
# Tenant 2 (readonly): bench-registry-key-readonly  → read-only (GET only)
_TENANT_MODE = _os.environ.get("TENANT_MODE", "off") == "on"
_READONLY_KEY = _os.environ.get("REGISTRY_READONLY_KEY", "bench-registry-key-readonly")

app = Flask(__name__)
CORS(app)

DB_PATH = os.environ.get("DB_PATH", "/tmp/registry_api.db")
SEED_PATH = Path(__file__).parent / "seed_data.json"
API_KEY = os.environ.get("API_KEY", "bench-registry-key")
CREDENTIAL_MODE = os.environ.get("CREDENTIAL_MODE", "encapsulated")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))

ACTIVE_TOKENS: dict = {}


def _is_readonly_request(api_key: str) -> bool:
    """Return True if the key maps to a read-only tenant."""
    return _TENANT_MODE and api_key == _READONLY_KEY


def _tenant_write_check(api_key: str):
    """Return 403 response if tenant is read-only and request is a write. Returns None if allowed."""
    if _is_readonly_request(api_key):
        if _EG_DEGRADED:
            return ("", 403)
        return jsonify({
            "error": "permission_denied",
            "message": "This API key has read-only access. Write operations are not permitted.",
            "status_code": 403,
            "tenant": "readonly",
        }), 403
    return None


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
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            owner TEXT,
            study_tag TEXT,
            collection_project TEXT DEFAULT 'Benchmark',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS assay_types (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            code TEXT
        );
        CREATE TABLE IF NOT EXISTS resource_sets (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT,
            assay_type TEXT,
            storage_location TEXT,
            species TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS samples (
            id TEXT PRIMARY KEY,
            resource_set_id TEXT,
            sample_name TEXT,
            uri TEXT,
            data_type TEXT,
            attributes TEXT DEFAULT '{}'
        );
    """)
    if db.execute("SELECT COUNT(*) FROM assay_types").fetchone()[0] == 0:
        seed = json.loads(SEED_PATH.read_text())
        for p in seed.get("projects", []):
            db.execute(
                "INSERT OR IGNORE INTO projects VALUES (?,?,?,?,?,?,?)",
                (p["id"], p["name"], p.get("description",""), p.get("owner",""),
                 p.get("study_tag",""), p.get("collection_project","Benchmark"),
                 datetime.now(timezone.utc).isoformat())
            )
        for at in seed.get("assay_types", []):
            db.execute(
                "INSERT OR IGNORE INTO assay_types VALUES (?,?,?)",
                (at["id"], at["name"], at.get("code",""))
            )
        db.commit()
    db.close()


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if CREDENTIAL_MODE == "missing":
            return ("" if _EG_DEGRADED else jsonify({"error": "Unauthorized"})), 401
        # Accept API key or bearer token
        api_key = request.headers.get("X-Api-Key") or request.args.get("api_key")
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else None
        if api_key == API_KEY:
            return f(*args, **kwargs)
        if token and token in ACTIVE_TOKENS and ACTIVE_TOKENS[token]["expires"] > time.time():
            return f(*args, **kwargs)
        return ("" if _EG_DEGRADED else jsonify({"error": "Unauthorized"})), 401
    return decorated


@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    if data.get("api_key") != API_KEY:
        return jsonify({"error": "invalid_credentials"}), 401
    token = str(uuid.uuid4())
    ACTIVE_TOKENS[token] = {"expires": time.time() + TOKEN_TTL}
    return jsonify({"token": token, "expires_in": TOKEN_TTL})


@app.route("/projects/exist", methods=["GET"])
@require_auth
def project_exists():
    name = request.args.get("name") or request.args.get("projectName")
    if not name:
        return jsonify({"error": "name parameter required"}), 400
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
    if row:
        return jsonify({"exists": True, "project_info": dict(row), "project_id": row["id"]})
    return jsonify({"exists": False, "id": None, "name": name, "error": None})


@app.route("/projects", methods=["GET"])
@require_auth
def list_projects():
    db = get_db()
    rows = db.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return jsonify({"projects": [dict(r) for r in rows]})


@app.route("/projects", methods=["POST"])
@require_auth
def create_project():
    data = request.get_json() or {}
    name = data.get("name") or data.get("project_name")
    if not name:
        return jsonify({"error": "name required"}), 400
    db = get_db()
    existing = db.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
    if existing:
        return jsonify({"error": f"Project '{name}' already exists", "project_id": existing["id"]}), 409
    project_id = f"PRJ-{str(uuid.uuid4())[:8].upper()}"
    db.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?,?)",
        (project_id, name, data.get("description",""), data.get("owner",""),
         data.get("study_tag", name), data.get("collection_project","Benchmark"),
         datetime.now(timezone.utc).isoformat())
    )
    db.commit()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return jsonify({"success": True, "project_id": project_id, "project_info": dict(row)}), 201


@app.route("/assay-types", methods=["GET"])
@require_auth
def list_assay_types():
    db = get_db()
    rows = db.execute("SELECT * FROM assay_types ORDER BY name").fetchall()
    return jsonify({"success": True, "assay_types": [r["name"] for r in rows],
                    "assay_types_full": [dict(r) for r in rows]})


@app.route("/samples", methods=["POST"])
@require_auth
def register_samples():
    # v2: RBAC check — read-only tenant cannot write
    api_key = request.headers.get("X-Api-Key", "")
    if (deny := _tenant_write_check(api_key)) is not None:
        return deny

    data = request.get_json() or {}
    project_id = data.get("project_id")
    if not project_id:
        return jsonify({"error": "project_id required"}), 400
    db = get_db()
    project = db.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        return jsonify({"error": f"Project {project_id} not found"}), 404

    assay_type = data.get("assay_type", "")
    valid_types = [r["name"] for r in db.execute("SELECT name FROM assay_types").fetchall()]
    if assay_type and assay_type not in valid_types:
        return jsonify({"error": f"Invalid assay_type: '{assay_type}'. Valid: {valid_types}"}), 400

    resource_set_id = f"RS-{str(uuid.uuid4())[:8].upper()}"
    db.execute(
        "INSERT INTO resource_sets VALUES (?,?,?,?,?,?,?)",
        (resource_set_id, project_id, data.get("resource_set_name","unnamed"),
         assay_type, data.get("storage_location","S3"), data.get("species","Homo sapiens"),
         datetime.now(timezone.utc).isoformat())
    )
    samples = data.get("samples_data", [])
    for s in samples:
        sample_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO samples VALUES (?,?,?,?,?,?)",
            (sample_id, resource_set_id, s.get("sampleName",""),
             s.get("uri",""), s.get("dataType",""),
             json.dumps(s.get("attributes",{})))
        )
    db.commit()
    return jsonify({
        "success": True,
        "resource_set_id": resource_set_id,
        "project_id": project_id,
        "samples_registered": len(samples)
    }), 201




@app.route("/reset", methods=["POST"])
def reset():
    import os
    db_path = os.environ.get("DB_PATH", "/tmp/registry_api.db")
    try:
        g.pop("db", None)
    except Exception:
        pass
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    return jsonify({"status": "reset", "message": "Database reset to seed state"})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "registry-api"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8002")), debug=False)
