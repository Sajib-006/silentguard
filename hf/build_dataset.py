"""Build the AgentOrchBench dataset for the Hugging Face Hub from the exact task files of the paper.

    python hf/build_dataset.py          # writes hf/agentorchbench/data/*.jsonl

Each config has one split, ``test``. The dataset card is hf/agentorchbench/README.md.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "paper" / "code" / "benchmark" / "tasks"
SEED = ROOT / "paper" / "code" / "agentorchbench" / "apis" / "catalog_api" / "seed_data_large.json"
OUT = Path(__file__).resolve().parent / "agentorchbench" / "data"

FAULT_TEXT = {
    "drop_last": ("drops the last filter", "a misnamed filter"),
    "cap_total": ("reports at most 5 matches", "page size returned as the total"),
    "or_semantics": ("joins conditions with OR instead of AND", "a faulty query builder"),
    "drop_field": ("ignores any filter on one field", "an unindexed field"),
    "ignore_negation": ("drops NOT from a condition", "no NOT support"),
    "off_by_one": ("adds one to the count", "a range bug"),
    "prefix": ("matches by prefix instead of equality", "loose matching"),
    "consistent_lie": ("lies the same way on every query", "an adversarial API"),
}
CHECK = {"drop_last": "fires", "cap_total": "fires", "or_semantics": "fires", "drop_field": "fires",
         "ignore_negation": "false alarm", "off_by_one": "fires", "prefix": "cannot see",
         "consistent_lie": "cannot see"}
MAIN = {"drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"}


def rows(name):
    return [json.loads(l) for l in open(TASKS / name) if l.strip()]


def count_answer(gt):
    for s in gt.get("must_include") or []:
        if s.startswith("count="):
            return int(s.split("=", 1)[1])
    raise ValueError(gt)


def effects(r):
    return [{"fault": f, "count_under_fault": v.get("n"), "changes_answer": bool(v.get("changes_answer")),
             "check_fires": bool(v.get("v3_fires"))} for f, v in sorted((r.get("fault_sim") or {}).items())]


def conds(r):
    return [{"field": c["field"], "value": str(c["value"])} for c in r.get("conds") or []]


def write(name, items):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{name}.jsonl", "w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    print(f"{name:10s} {len(items):4d} rows")


write("count", [{"task_id": r["task_id"], "domain": "bio", "prompt": r["prompt"],
                 "answer": count_answer(r["ground_truth"]), "conditions": conds(r),
                 "num_conditions": r["k_conditions"], "fault_effects": effects(r)}
                for r in rows("adv_d7_v5.jsonl")])
write("retail", [{"task_id": r["task_id"], "domain": "retail", "prompt": r["prompt"],
                  "answer": count_answer(r["ground_truth"]), "conditions": conds(r),
                  "num_conditions": r["k_conditions"], "fault_effects": effects(r)}
                 for r in rows("adv_retail_v1.jsonl")])
write("lists", [{"task_id": r["task_id"], "domain": "bio", "prompt": r["prompt"],
                 "answer_ids": sorted(r["ground_truth"]["ids"]), "conditions": conds(r),
                 "num_conditions": r["k_conditions"]} for r in rows("rec_v1.jsonl")])
write("workflows", [{"task_id": r["task_id"], "category": r.get("category"), "prompt": r["prompt"],
                     "must_include": [str(x) for x in (r["ground_truth"].get("must_include") or [])],
                     "notes": r["ground_truth"].get("notes", "")} for r in rows("hd100_v7.jsonl")])
write("faults", [{"fault": f, "api_behavior": a, "modeled_on": m, "count_check": CHECK[f],
                  "in_main_results": f in MAIN} for f, (a, m) in FAULT_TEXT.items()])
write("catalog", [{"item_id": a["item_id"], "item_type": a["item_type"], "uri": a["s3_uri"],
                   "tags": a["tags"], **{k: str(v) for k, v in a["json_metadata"].items()}}
                  for a in json.load(open(SEED))["assets"]])
