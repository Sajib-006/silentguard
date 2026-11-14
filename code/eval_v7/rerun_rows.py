#!/usr/bin/env python3
"""Re-run rows that must not stay in the data, with each cell's original settings.

Selects rows whose outcome class is in --classes (default provider: an outage or a model the router
briefly could not find) and, with --turbo, Llama-3.3 rows served by the Turbo backend. Removes them
from their file (backup *.bak-rerun-<date>), lists them in runs/rerun_<date>.tsv, then re-runs each
affected cell with runner resume, which fills exactly the removed (task, seed) pairs.

    python rerun_rows.py --dirs matrix_faults,matrix_baselines --turbo --workers 10,12,13 [--dry-run]
"""
from __future__ import annotations
import argparse, glob, json, re, shutil, subprocess, sys, threading, time
from pathlib import Path

A = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(A / "code/eval_v7"))
import outcomes, roster                                      # noqa: E402
TASKS = {"faults": "adv_d7_v2", "baselines": "adv_d7_v2", "gate2v": "adv_d7_v2", "gate2h": "adv_d7_v3",
         "heldout": "adv_d7_v3", "repair": "adv_d7_v4", "retailfaults": "adv_retail_v1", "final5": "adv_d7_v5", "retail5": "adv_retail_v1", "rec9": "rec_v1"}
SEEDS = {"final5": "2", "retail5": "2", "rec9": "1"}                     # every other stage ran 3 seeds


def cell_cmd(fpath, row, worker):
    stage = Path(fpath).name.split("-")[0]
    tag = Path(fpath).name.split("__")[0]
    c = [str(A / "env/bin/python"), str(A / "code/eval_v7/runner.py"),
         "--provider", row["provider"], "--model", row["model"], "--domain", row.get("domain", "bio"),
         "--condition", row["condition"], "--task-file", str(A / f"code/benchmark/tasks/{TASKS[stage]}.jsonl"),
         "--tasks", "all", "--seeds", SEEDS.get(stage, "3"), "--worker", str(worker), "--out", str(Path(fpath).parent),
         "--tag", tag, "--d7", str(row.get("d7_level", 0)), "--sfr-mode", row.get("sfr_mode_requested") or "off"]
    c += roster.as_args()
    for k, flag in (("projection", "--projection"), ("catalog", "--catalog"), ("baseline", "--baseline")):
        if row.get(k):
            c += [flag, row[k]]
    if row.get("d7_harness"):
        c += ["--d7-harness", "--d7-remediation", row.get("d7_remediation") or "advisory"]
    c += ["--g-enforce", row.get("g_enforce") or "block"]
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", required=True)
    ap.add_argument("--classes", default="provider")
    ap.add_argument("--turbo", action="store_true")
    ap.add_argument("--workers", default="10,12,13")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    classes = set(a.classes.split(","))
    stamp = time.strftime("%Y%m%d")
    todo = []                                   # (file, template row, n removed)
    listing = []
    for d in a.dirs.split(","):
        for f in sorted(glob.glob(str(A / "runs" / d / "*.jsonl"))):
            rows = [json.loads(l) for l in open(f)]
            bad = [r for r in rows if outcomes.classify(r) in classes or
                   (a.turbo and "Turbo" in str(r.get("model_id_returned")))]
            if not bad:
                continue
            good = [r for r in rows if r not in bad]
            tmpl = (good or bad)[0]
            todo.append((f, tmpl, len(bad), good))
            listing += [f"{Path(f).name.split('__')[0]}\t{r['task_id']}\t{r['seed']}\t{outcomes.classify(r)}\t{r.get('model_id_returned')}" for r in bad]
    n = sum(t[2] for t in todo)
    print(f"rows to re-run: {n} in {len(todo)} cells")
    for f, _, k, _ in todo:
        print(f"  {k:4d}  {Path(f).name.split('__')[0]}")
    if a.dry_run or not n:
        return
    (A / f"runs/rerun_{stamp}.tsv").write_text("\n".join(listing) + "\n")
    for f, _, _, good in todo:
        shutil.copy(f, f + f".bak-rerun-{stamp}")
        Path(f).write_text("".join(json.dumps(r) + "\n" for r in good))
    workers = [int(w) for w in a.workers.split(",")]
    q, lock = list(todo), threading.Lock()

    def work(w):
        while True:
            with lock:
                if not q:
                    return
                f, tmpl, k, _ = q.pop(0)
            out = subprocess.run(cell_cmd(f, tmpl, w), capture_output=True, text=True)
            tail = [l for l in out.stdout.splitlines() if l.strip()][-1:] or [out.stderr[-150:]]
            with lock:
                print(f"{time.strftime('%H:%M')} w{w} {Path(f).name.split('__')[0]} (+{k}) -> {tail[0][:90]}", flush=True)
    ths = [threading.Thread(target=work, args=(w,)) for w in workers]
    [t.start() for t in ths]; [t.join() for t in ths]
    print("RERUN_DONE", flush=True)


if __name__ == "__main__":
    main()
