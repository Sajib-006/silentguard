"""
TaskAPI: a task tracking service.
Part of AgentOrchBench.
"""

import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request
from flask_cors import CORS

import os as _os
_EG_DEGRADED = _os.environ.get("EG_MODE", "full") == "degraded"

app = Flask(__name__)
CORS(app)

DB_PATH = os.environ.get("DB_PATH", "/tmp/task_api.db")
SEED_PATH = Path(__file__).parent / "seed_data.json"
API_TOKEN = os.environ.get("API_TOKEN", "bench-task-token")
CREDENTIAL_MODE = os.environ.get("CREDENTIAL_MODE", "encapsulated")

VALID_STATUSES = ["TO DO", "IN PROGRESS", "DONE", "BLOCKED"]
ISSUE_PREFIX = os.environ.get("ISSUE_PREFIX", "BENCH")

_issue_counter = [0]


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
        CREATE TABLE IF NOT EXISTS issues (
            key TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'TO DO',
            assignee TEXT,
            issue_type TEXT DEFAULT 'Task',
            created_at TEXT,
            updated_at TEXT,
            labels TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY,
            issue_key TEXT NOT NULL,
            body TEXT NOT NULL,
            author TEXT DEFAULT 'agent',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS issue_counter (
            id INTEGER PRIMARY KEY,
            value INTEGER DEFAULT 0
        );
    """)
    if db.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0:
        db.execute("INSERT OR IGNORE INTO issue_counter VALUES (1, 0)")
        seed = json.loads(SEED_PATH.read_text())
        max_num = 0
        for issue in seed.get("issues", []):
            db.execute(
                "INSERT OR IGNORE INTO issues VALUES (?,?,?,?,?,?,?,?,?)",
                (issue["key"], issue["summary"], issue.get("description",""),
                 issue.get("status","TO DO"), issue.get("assignee"),
                 issue.get("issue_type","Task"),
                 issue.get("created_at", datetime.now(timezone.utc).isoformat()),
                 issue.get("updated_at", datetime.now(timezone.utc).isoformat()),
                 json.dumps(issue.get("labels",[])))
            )
            for c in issue.get("comments", []):
                db.execute(
                    "INSERT OR IGNORE INTO comments VALUES (?,?,?,?,?)",
                    (c.get("id", str(uuid.uuid4())), issue["key"], c["body"],
                     c.get("author","system"), c.get("created_at", datetime.now(timezone.utc).isoformat()))
                )
            # track max issue number
            num = int(issue["key"].split("-")[-1])
            max_num = max(max_num, num)
        db.execute("UPDATE issue_counter SET value=? WHERE id=1", (max_num,))
        db.commit()
    db.close()


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if CREDENTIAL_MODE == "missing":
            return ("" if _EG_DEGRADED else jsonify({"error": "Unauthorized"})), 401
        token = request.headers.get("X-Api-Token") or request.headers.get("Authorization","").replace("Bearer ","")
        if token != API_TOKEN:
            return jsonify({"error": "Unauthorized — invalid API token"}), 401
        return f(*args, **kwargs)
    return decorated


def _next_key(db):
    db.execute("UPDATE issue_counter SET value = value + 1 WHERE id = 1")
    num = db.execute("SELECT value FROM issue_counter").fetchone()["value"]
    return f"{ISSUE_PREFIX}-{num}"


def _format_issue(row, comments=None):
    return {
        "key": row["key"],
        "fields": {
            "summary": row["summary"],
            "description": row["description"],
            "status": {"name": row["status"]},
            "assignee": {"displayName": row["assignee"]} if row["assignee"] else None,
            "issuetype": {"name": row["issue_type"]},
            "created": row["created_at"],
            "updated": row["updated_at"],
            "labels": json.loads(row["labels"] or "[]"),
            "comment": {"comments": comments or [], "total": len(comments or [])}
        }
    }


@app.route("/issues/<key>", methods=["GET"])
@require_auth
def get_issue(key):
    db = get_db()
    row = db.execute("SELECT * FROM issues WHERE key = ?", (key,)).fetchone()
    if not row:
        return jsonify({"error": f"Issue {key} not found", "ticket": None}), 404
    comments = [dict(c) for c in db.execute(
        "SELECT * FROM comments WHERE issue_key = ? ORDER BY created_at", (key,)
    ).fetchall()]
    return jsonify({"ticket": _format_issue(row, comments), "error": None})


@app.route("/issues", methods=["POST"])
@require_auth
def create_issue():
    data = request.get_json() or {}
    fields = data.get("fields", data)
    summary = fields.get("summary")
    if not summary:
        return jsonify({"error": "summary required"}), 400
    db = get_db()
    key = _next_key(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO issues VALUES (?,?,?,?,?,?,?,?,?)",
        (key, summary, fields.get("description",""), "TO DO",
         fields.get("assignee"), fields.get("issuetype",{}).get("name","Task"),
         now, now, json.dumps(fields.get("labels",[])))
    )
    db.commit()
    return jsonify({"issue_key": key, "key": key, "success": True}), 201


@app.route("/issues/<key>/comments", methods=["GET"])
@require_auth
def get_comments(key):
    db = get_db()
    if not db.execute("SELECT key FROM issues WHERE key=?", (key,)).fetchone():
        return jsonify({"error": f"Issue {key} not found"}), 404
    rows = db.execute(
        "SELECT * FROM comments WHERE issue_key = ? ORDER BY created_at", (key,)
    ).fetchall()
    return jsonify({"comments": [dict(r) for r in rows], "count": len(rows), "error": None})


@app.route("/issues/<key>/comments", methods=["POST"])
@require_auth
def add_comment(key):
    db = get_db()
    if not db.execute("SELECT key FROM issues WHERE key=?", (key,)).fetchone():
        return jsonify({"error": f"Issue {key} not found — cannot add comment"}), 404
    data = request.get_json() or {}
    body = data.get("body") or data.get("comment_text","")
    if not body:
        return jsonify({"error": "body required"}), 400
    comment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO comments VALUES (?,?,?,?,?)",
        (comment_id, key, body, data.get("author","agent"), now)
    )
    db.execute("UPDATE issues SET updated_at=? WHERE key=?", (now, key))
    db.commit()
    return jsonify({"comment": {"id": comment_id, "body": body, "created_at": now}, "success": True}), 201


@app.route("/issues/<key>/transitions", methods=["GET"])
@require_auth
def get_transitions(key):
    transitions = [{"id": str(i), "name": s, "to": {"name": s}}
                   for i, s in enumerate(VALID_STATUSES, 1)]
    return jsonify({"transitions": transitions, "count": len(transitions)})


@app.route("/issues/<key>/transitions", methods=["POST"])
@require_auth
def transition_issue(key):
    data = request.get_json() or {}
    status_name = data.get("status_name") or data.get("transition",{}).get("name")
    db = get_db()
    if not db.execute("SELECT key FROM issues WHERE key=?", (key,)).fetchone():
        return jsonify({"error": f"Issue {key} not found"}), 404
    if status_name not in VALID_STATUSES:
        return jsonify({"error": f"Invalid status. Valid: {VALID_STATUSES}"}), 400
    db.execute("UPDATE issues SET status=?, updated_at=? WHERE key=?",
               (status_name, datetime.now(timezone.utc).isoformat(), key))
    db.commit()
    return jsonify({"success": True, "new_status": status_name})


@app.route("/board", methods=["GET"])
@require_auth
def board():
    db = get_db()
    status = request.args.get("status")
    max_results = min(int(request.args.get("max_results", 50)), 100)
    if status:
        rows = db.execute(
            "SELECT * FROM issues WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, max_results)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM issues ORDER BY created_at DESC LIMIT ?", (max_results,)
        ).fetchall()
    issues = [_format_issue(r) for r in rows]
    return jsonify({"issues": issues, "count": len(issues), "column": status, "error": None})


@app.route("/search", methods=["GET"])
@require_auth
def search():
    jql = request.args.get("jql", "")
    max_results = min(int(request.args.get("maxResults", 50)), 100)
    db = get_db()
    rows = db.execute("SELECT * FROM issues ORDER BY created_at DESC").fetchall()

    # Simple JQL parser
    def matches_jql(row, jql):
        jql_lower = jql.lower().strip()
        if not jql_lower:
            return True
        # status = "X"
        m = re.search(r'status\s*=\s*["\']?([^"\']+)["\']?', jql_lower)
        if m and row["status"].lower() != m.group(1).strip():
            return False
        # text ~ "X"
        m = re.search(r'text\s*~\s*["\']([^"\']+)["\']', jql_lower)
        if m:
            q = m.group(1).lower()
            if q not in (row["summary"] or "").lower() and q not in (row["description"] or "").lower():
                return False
        # created >= -Nd
        m = re.search(r'created\s*>=\s*-(\d+)d', jql_lower)
        if m:
            days = int(m.group(1))
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            try:
                created = datetime.fromisoformat(row["created_at"].replace("Z","+00:00"))
                if created < cutoff:
                    return False
            except Exception:
                pass
        return True

    matched = [_format_issue(r) for r in rows if matches_jql(r, jql)]
    return jsonify({
        "issues": matched[:max_results],
        "count": len(matched),
        "total": len(matched),
        "error": None
    })


@app.route("/reset", methods=["POST"])
def reset():
    import os
    db_path = os.environ.get("DB_PATH", "/tmp/task_api.db")
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
    return jsonify({"status": "ok", "service": "task-api"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8004")), debug=False)
