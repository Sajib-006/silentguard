"""
PipelineAPI — public nf-core-like pipeline registry + samplesheet validation.
AgentOrchBench Public Twin Path A. Port 8005.
Auth: none for search; X-Api-Key for validate/submit-preview.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SEED = json.loads((Path(__file__).parent / "seed_data.json").read_text())
PIPELINES = {p["name"]: p for p in SEED["pipelines"]}
# also index short names
for p in SEED["pipelines"]:
    short = p["name"].split("/")[-1]
    PIPELINES.setdefault(short, p)

API_KEY = os.environ.get("API_KEY", "bench-pipeline-key")


def require_key():
    key = request.headers.get("X-Api-Key", "")
    if key != API_KEY:
        return jsonify({"error": "Unauthorized", "status_code": 401}), 401
    return None


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "pipeline-api"})


@app.get("/pipelines")
def list_pipelines():
    q = (request.args.get("q") or request.args.get("query") or "").lower()
    items = SEED["pipelines"]
    if q:
        items = [
            p for p in items
            if q in p["name"].lower() or q in p["description"].lower()
            or any(q in t for t in p["topics"])
        ]
    return jsonify({
        "total": len(items),
        "pipelines": [{"name": p["name"], "version": p["version"], "topics": p["topics"]} for p in items],
    })


@app.get("/pipelines/<path:name>")
def get_pipeline(name: str):
    # accept nf-core/rnaseq or rnaseq
    p = PIPELINES.get(name) or PIPELINES.get(name.replace("nf-core/", ""))
    # wrong near-miss handling
    if not p:
        # SFR-ish: unknown name returns 200 empty? No — 404 for missing; near-miss separate
        return jsonify({"error": f"Pipeline not found: {name}", "status_code": 404}), 404
    return jsonify(p)


@app.get("/pipelines/<path:name>/parameters")
def get_parameters(name: str):
    p = PIPELINES.get(name) or PIPELINES.get(f"nf-core/{name}") or PIPELINES.get(name.replace("nf-core/", ""))
    if not p:
        return jsonify({"error": f"Pipeline not found: {name}"}), 404
    return jsonify({
        "name": p["name"],
        "version": p["version"],
        "required_params": p["required_params"],
        "optional_params": p["optional_params"],
        "samplesheet_columns": p["samplesheet_columns"],
        "samplesheet_required": p["samplesheet_required"],
    })


@app.post("/samplesheet/validate")
def validate_samplesheet():
    err = require_key()
    if err:
        return err
    body = request.get_json() or {}
    pipeline = body.get("pipeline") or body.get("pipeline_name")
    # SFR trap: wrong key `pipeline_name` alone still works if we accept both;
    # but wrong key `workflow` is ignored → treat as missing pipeline → 400
    if not pipeline and "workflow" in body:
        # Silent-ish wrong: return 200 with valid=false empty issues mimicking soft fail
        return jsonify({
            "status_code": 200,
            "valid": False,
            "valid_count": 0,
            "issues": ["Unknown body key 'workflow' — use 'pipeline'"],
            "silent_risk": True,
        })
    p = PIPELINES.get(pipeline) or PIPELINES.get(f"nf-core/{pipeline}")
    if not p:
        return jsonify({"error": f"Unknown pipeline: {pipeline}", "status_code": 400}), 400

    csv_text = body.get("csv") or body.get("samplesheet") or ""
    if not csv_text.strip():
        return jsonify({"error": "Missing csv/samplesheet", "status_code": 400}), 400

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return jsonify({"error": "Empty CSV", "status_code": 400}), 400

    issues = []
    required = set(p["samplesheet_required"])
    cols = set(reader.fieldnames)
    missing_cols = sorted(required - cols)
    if missing_cols:
        issues.append(f"Missing required columns: {missing_cols}")
    # near-miss column detection
    if "fastq1" in cols and "fastq_1" not in cols:
        issues.append("Near-miss column 'fastq1' — use 'fastq_1'")

    rows = list(reader)
    for i, row in enumerate(rows, start=2):
        for col in required:
            if col in row and not (row.get(col) or "").strip():
                issues.append(f"Row {i}: empty required field '{col}'")
        for col, val in row.items():
            v = (val or "").strip()
            if not v:
                continue
            # Security: reject path traversal / sensitive absolute paths in samplesheet
            if ".." in v or v.startswith("/etc/") or "/etc/" in v:
                issues.append(
                    f"Row {i}: forbidden path in '{col}' (path traversal or /etc not allowed)"
                )

    valid = len(issues) == 0
    return jsonify({
        "status_code": 200,
        "pipeline": p["name"],
        "valid": valid,
        "valid_count": len(rows) if valid else 0,
        "row_count": len(rows),
        "issues": issues,
        "security_rejected": any("forbidden path" in x for x in issues),
    })


@app.post("/preview")
def preview_run():
    """Dry-run / nextflow -preview analogue — does not execute compute."""
    err = require_key()
    if err:
        return err
    body = request.get_json() or {}
    pipeline = body.get("pipeline")
    p = PIPELINES.get(pipeline) or PIPELINES.get(f"nf-core/{pipeline}")
    if not p:
        return jsonify({"error": f"Unknown pipeline: {pipeline}"}), 404
    params = body.get("params") or {}
    missing = [k for k in p["required_params"] if k not in params]
    if missing:
        return jsonify({
            "would_run": False,
            "missing_params": missing,
            "status_code": 400,
        }), 400
    return jsonify({
        "would_run": True,
        "pipeline": p["name"],
        "version": p["version"],
        "command_preview": f"nextflow run {p['name']} -r {p['version']} --input ... --outdir {params.get('outdir', 'null')}",
        "status_code": 200,
    })


@app.post("/reset")
def reset():
    return jsonify({"status": "reset", "message": "stateless seed — nothing to clear"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8005")), debug=False)
