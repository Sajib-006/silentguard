"""
TransferAPI: a data transfer management service.
Part of AgentOrchBench: open-source benchmark environment for SPINE-Bench.

Auth modes (set CREDENTIAL_MODE env var):
  encapsulated : MCP server holds credentials, agent never sees tokens
  injected     : credentials in config file agent can read
  exposed      : token passed directly in task prompt to agent
  missing      : all requests return 401 (tests credential failure mode)
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

app = Flask(__name__)
CORS(app)

DB_PATH = os.environ.get("DB_PATH", "/tmp/transfer_api.db")
SEED_PATH = Path(__file__).parent / "seed_data.json"
CLIENT_ID = os.environ.get("CLIENT_ID", "bench-client-id")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "bench-client-secret")
CREDENTIAL_MODE = os.environ.get("CREDENTIAL_MODE", "encapsulated")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))

ACTIVE_TOKENS: dict = {}


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
        CREATE TABLE IF NOT EXISTS agreements (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            provider TEXT,
            study_ids TEXT DEFAULT '[]',
            approval_status TEXT DEFAULT 'RECEIVING_DATA'
        );
        CREATE TABLE IF NOT EXISTS transfers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            agreement_id TEXT,
            status TEXT DEFAULT 'NOT_STARTED',
            qc_status TEXT DEFAULT 'NOT_STARTED',
            routing_status TEXT DEFAULT 'NOT_STARTED',
            total_files INTEGER DEFAULT 0,
            size_bytes INTEGER DEFAULT 0,
            sent_at TEXT,
            updated_at TEXT,
            is_test INTEGER DEFAULT 0,
            study_ids TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS transfer_files (
            id TEXT PRIMARY KEY,
            transfer_id TEXT,
            name TEXT,
            size_bytes INTEGER,
            s3_uri TEXT
        );
    """)
    # seed if empty
    if db.execute("SELECT COUNT(*) FROM transfers").fetchone()[0] == 0:
        seed = json.loads(SEED_PATH.read_text())
        for a in seed.get("agreements", []):
            db.execute(
                "INSERT INTO agreements VALUES (?,?,?,?,?)",
                (a["id"], a["name"], a.get("provider",""), json.dumps(a.get("study_ids",[])), a.get("approval_status","RECEIVING_DATA"))
            )
        for t in seed.get("transfers", []):
            db.execute(
                "INSERT INTO transfers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (t["id"], t["name"], t.get("agreement_id"), t.get("status","SUCCESS"),
                 t.get("qc_status","SUCCESS"), t.get("routing_status","SUCCESS"),
                 t.get("total_files",1), t.get("size_bytes",0),
                 t.get("sent_at", datetime.now(timezone.utc).isoformat()),
                 t.get("updated_at", datetime.now(timezone.utc).isoformat()),
                 1 if t.get("is_test") else 0,
                 json.dumps(t.get("study_ids",[])))
            )
            for f in t.get("files", []):
                db.execute(
                    "INSERT INTO transfer_files VALUES (?,?,?,?,?)",
                    (str(uuid.uuid4()), t["id"], f["name"], f.get("size_bytes",0), f.get("s3_uri",""))
                )
        db.commit()
    db.close()


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if CREDENTIAL_MODE == "missing":
            return jsonify({"error": "Unauthorized", "message": "No credentials configured"}), 401
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return ("" if _EG_DEGRADED else jsonify({"error": "Missing bearer token"})), 401
        token = auth[7:]
        if token not in ACTIVE_TOKENS or ACTIVE_TOKENS[token]["expires"] < time.time():
            return ("" if _EG_DEGRADED else jsonify({"error": "Invalid or expired token"})), 401
        return f(*args, **kwargs)
    return decorated


@app.route("/auth/token", methods=["POST"])
def get_token():
    data = request.get_json() or {}
    form = request.form
    client_id = data.get("client_id") or form.get("client_id")
    client_secret = data.get("client_secret") or form.get("client_secret")
    if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
        return jsonify({"error": "invalid_client"}), 401
    token = str(uuid.uuid4())
    ACTIVE_TOKENS[token] = {"expires": time.time() + TOKEN_TTL}
    return jsonify({
        "access_token": token,
        "token_type": "bearer",
        "expires_in": TOKEN_TTL,
        "scope": "transfers agreements"
    })


@app.route("/transfers", methods=["GET"])
@require_auth
def list_transfers():
    db = get_db()
    limit = min(int(request.args.get("limit", 10)), 100)
    page = int(request.args.get("page", 1))
    offset = (page - 1) * limit

    where, params = [], []
    status = request.args.get("status") or request.args.get("transfer_status")
    if status:
        statuses = status.split(",")
        placeholders = ",".join("?" * len(statuses))
        where.append(f"t.status IN ({placeholders})")
        params.extend(statuses)
    query = request.args.get("query")
    if query:
        where.append("t.name LIKE ?")
        params.append(f"%{query}%")
    name = request.args.get("transfer_name")
    if name:
        where.append("t.name = ?")
        params.append(name)

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = db.execute(
        f"SELECT COUNT(*) FROM transfers t {where_clause}", params
    ).fetchone()[0]

    rows = db.execute(
        f"""SELECT t.*, a.name as agreement_name, a.provider, a.study_ids as agr_study_ids
            FROM transfers t
            LEFT JOIN agreements a ON t.agreement_id = a.id
            {where_clause}
            ORDER BY t.sent_at DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset]
    ).fetchall()

    items = []
    for r in rows:
        items.append(_format_transfer(r))

    total_pages = max(1, (total + limit - 1) // limit)
    return jsonify({
        "transfers": {
            "items": items,
            "meta": {
                "totalItems": total,
                "itemCount": len(items),
                "itemsPerPage": limit,
                "totalPages": total_pages,
                "currentPage": page
            },
            "links": {
                "first": f"/transfers?limit={limit}",
                "next": f"/transfers?page={page+1}&limit={limit}" if page < total_pages else "",
                "last": f"/transfers?page={total_pages}&limit={limit}"
            }
        },
        "error": None
    })


@app.route("/transfers/<transfer_id>", methods=["GET"])
@require_auth
def get_transfer(transfer_id):
    db = get_db()
    row = db.execute(
        """SELECT t.*, a.name as agreement_name, a.provider, a.study_ids as agr_study_ids
           FROM transfers t
           LEFT JOIN agreements a ON t.agreement_id = a.id
           WHERE t.id = ? OR t.name = ?""",
        (transfer_id, transfer_id)
    ).fetchone()
    if not row:
        return jsonify({"metadata": None, "error": f"Transfer not found: {transfer_id}"}), 404

    result = _format_transfer(row)
    if request.args.get("include_files", "false").lower() == "true":
        files = db.execute(
            "SELECT * FROM transfer_files WHERE transfer_id = ?", (row["id"],)
        ).fetchall()
        result["files"] = [dict(f) for f in files]
    return jsonify({"metadata": result, "error": None})


@app.route("/agreements/<agreement_id>", methods=["GET"])
@require_auth
def get_agreement(agreement_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM agreements WHERE id = ?", (agreement_id,)
    ).fetchone()
    if not row:
        return jsonify({"metadata": None, "error": f"Agreement not found: {agreement_id}"}), 404
    return jsonify({
        "metadata": {
            "id": row["id"],
            "agreementName": row["name"],
            "provider": row["provider"],
            "studyIds": json.loads(row["study_ids"]),
            "approvalStatus": row["approval_status"]
        },
        "error": None
    })


@app.route("/transfers", methods=["POST"])
@require_auth
def create_transfer():
    data = request.get_json() or {}
    transfer_id = str(uuid.uuid4())
    name = data.get("name", f"Transfer-{transfer_id[:8]}")
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO transfers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (transfer_id, name, data.get("agreement_id"), "NOT_STARTED",
         "NOT_STARTED", "NOT_STARTED", 0, 0, now, now, 0, "[]")
    )
    db.commit()
    return jsonify({"id": transfer_id, "name": name, "status": "NOT_STARTED"}), 201


def _format_transfer(row):
    return {
        "id": row["id"],
        "transferName": row["name"],
        "transferStatus": row["status"],
        "qcStatus": row["qc_status"],
        "routingStatus": row["routing_status"],
        "totalFiles": row["total_files"],
        "sizeBytes": row["size_bytes"],
        "sentAt": row["sent_at"],
        "updatedAt": row["updated_at"],
        "isTest": bool(row["is_test"]),
        "studyIds": json.loads(row["study_ids"] or "[]"),
        "agreement": {
            "agreementName": row["agreement_name"] or "",
            "provider": row["provider"] or "",
            "studyIds": json.loads(row["agr_study_ids"] or "[]")
        } if row["agreement_name"] else None
    }




@app.route("/reset", methods=["POST"])
def reset():
    import os
    db_path = os.environ.get("DB_PATH", "/tmp/transfer_api.db")
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
    return jsonify({"status": "ok", "service": "transfer-api"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8001")), debug=False)
