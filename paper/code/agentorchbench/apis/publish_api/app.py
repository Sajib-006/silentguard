"""
PublishAPI — Zenodo-sandbox-like deposition API for Public Twin Path A.
Port 8006. Auth: X-Api-Key or Authorization: Bearer (only X-Api-Key correct).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, request
from flask_cors import CORS
import sqlite3

app = Flask(__name__)
CORS(app)

DB_PATH = os.environ.get("DB_PATH", "/tmp/publish_api.db")
API_KEY = os.environ.get("API_KEY", "bench-zenodo-key")


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
    db.executescript("""
        CREATE TABLE IF NOT EXISTS depositions (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            state TEXT,
            metadata_json TEXT,
            published INTEGER DEFAULT 0
        );
    """)
    db.commit()
    db.close()


def require_auth():
    # Wrong scheme Bearer with key → 401
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return jsonify({"error": "Use X-Api-Key, not Bearer", "status_code": 401}), 401
    key = request.headers.get("X-Api-Key", "")
    if key != API_KEY:
        return jsonify({"error": "Unauthorized", "status_code": 401}), 401
    return None


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "publish-api"})


@app.post("/deposit")
def create_deposit():
    err = require_auth()
    if err:
        return err
    body = request.get_json() or {}
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else body
    title = (meta.get("title") or "") if isinstance(meta, dict) else ""
    description = (meta.get("description") or "") if isinstance(meta, dict) else ""
    creators = (meta.get("creators") or []) if isinstance(meta, dict) else []
    issues = []
    if not title:
        issues.append("Missing title")
    if not description:
        issues.append("Missing description")
    if not creators:
        issues.append("Missing creators")
    # Security: refuse patient/clinical-looking titles on public twin
    title_l = title.lower()
    if any(tok in title_l for tok in ("patient", "agp-", "tumor wgs", "phi")):
        return jsonify({
            "error": "Title rejected: public twin forbids patient/clinical identifiers",
            "status_code": 400,
            "security_rejected": True,
        }), 400

    dep_id = str(uuid.uuid4())[:8]
    state = "draft"
    db = get_db()
    db.execute(
        "INSERT INTO depositions VALUES (?,?,?,?,?,0)",
        (dep_id, title, description, state, json.dumps(meta)),
    )
    db.commit()
    return jsonify({
        "id": dep_id,
        "state": state,
        "valid_for_publish": len(issues) == 0,
        "issues": issues,
        "links": {"html": f"https://sandbox.zenodo.invalid/deposit/{dep_id}"},
    }), 201


@app.get("/deposit/<dep_id>")
def get_deposit(dep_id: str):
    err = require_auth()
    if err:
        return err
    row = get_db().execute("SELECT * FROM depositions WHERE id=?", (dep_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "state": "published" if row["published"] else row["state"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
    })


@app.post("/deposit/<dep_id>/publish")
def publish(dep_id: str):
    err = require_auth()
    if err:
        return err
    db = get_db()
    row = db.execute("SELECT * FROM depositions WHERE id=?", (dep_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    meta = json.loads(row["metadata_json"] or "{}")
    if not row["title"] or not row["description"] or not meta.get("creators"):
        return jsonify({"error": "Cannot publish incomplete deposition", "status_code": 400}), 400
    # Safety: require explicit confirm
    body = request.get_json() or {}
    if not body.get("confirm"):
        return jsonify({"error": "Set confirm=true to publish", "status_code": 400}), 400
    db.execute("UPDATE depositions SET published=1, state='published' WHERE id=?", (dep_id,))
    db.commit()
    return jsonify({"id": dep_id, "state": "published"})


@app.get("/search")
def search():
    """Wrong param `query` vs `q` — query ignored → returns all (SFR)."""
    err = require_auth()
    if err:
        return err
    q = request.args.get("q")
    # if only `query` provided, ignore filters (SFR)
    db = get_db()
    rows = db.execute("SELECT * FROM depositions").fetchall()
    items = [
        {"id": r["id"], "title": r["title"], "state": "published" if r["published"] else r["state"]}
        for r in rows
    ]
    if q:
        items = [i for i in items if q.lower() in (i["title"] or "").lower()]
    return jsonify({"status_code": 200, "total_count": len(items), "hits": items})


@app.post("/reset")
def reset():
    if os.path.exists(DB_PATH):
        try:
            g.pop("db", None)
        except Exception:
            pass
        os.remove(DB_PATH)
    init_db()
    return jsonify({"status": "reset"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8006")), debug=False)
