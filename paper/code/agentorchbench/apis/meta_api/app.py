"""
MetaAPI — NCBI BioSample + Synapse-entity public metadata twin.
Port 8007. NCBI reads: no auth. Synapse reads: X-Synapse-Token (optional for public).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SEED = json.loads((Path(__file__).parent / "seed_data.json").read_text())
SYNAPSE_TOKEN = os.environ.get("SYNAPSE_TOKEN", "bench-synapse-token")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "meta-api"})


@app.get("/biosample/search")
def biosample_search():
    """E-utils-like. Correct param: term. Wrong param `query` → SFR returns all."""
    term = request.args.get("term")
    query = request.args.get("query")  # wrong
    items = SEED["biosamples"]
    if term:
        t = term.lower()
        items = [
            b for b in items
            if t in b["accession"].lower()
            or t in b["study_id"].lower()
            or t in json.dumps(b).lower()
        ]
        return jsonify({"status_code": 200, "count": len(items), "biosamples": items, "param_used": "term"})
    if query is not None:
        # SFR: ignore filter, return all
        return jsonify({
            "status_code": 200,
            "count": len(SEED["biosamples"]),
            "biosamples": SEED["biosamples"],
            "param_used": "query",
            "warning": "query param ignored; use term=",
        })
    return jsonify({"status_code": 200, "count": len(items), "biosamples": items, "param_used": "none"})


@app.get("/biosample/<accession>")
def biosample_get(accession: str):
    for b in SEED["biosamples"]:
        if b["accession"] == accession:
            return jsonify(b)
    return jsonify({"error": "not found"}), 404


@app.get("/synapse/entity/<entity_id>")
def synapse_get(entity_id: str):
    # public entities readable without token; private would need token — all public here
    for e in SEED["synapse_entities"]:
        if e["id"] == entity_id:
            return jsonify(e)
    return jsonify({"error": "not found"}), 404


@app.get("/synapse/search")
def synapse_search():
    """Requires X-Synapse-Token. Wrong Bearer → 401."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return jsonify({"error": "Use X-Synapse-Token header", "status_code": 401}), 401
    tok = request.headers.get("X-Synapse-Token", "")
    if tok != SYNAPSE_TOKEN:
        return jsonify({"error": "Unauthorized", "status_code": 401}), 401
    q = (request.args.get("q") or "").lower()
    items = SEED["synapse_entities"]
    if q:
        items = [e for e in items if q in e["name"].lower() or q in e["study_id"].lower()]
    return jsonify({"total": len(items), "results": items})


@app.post("/reset")
def reset():
    return jsonify({"status": "reset", "message": "stateless"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8007")), debug=False)
