#!/usr/bin/env python3
"""Count AgentOrchBench outcomes straight from run files, with the paper's grader (copied below, no other file needed).

    python count_runs.py <run_dir_or_jsonl> [more ...]  > counts.txt      (also writes count_runs_out.json)

For every model and arm it prints, as whole-number counts:
  faulted runs: wrong / correct / unknown / total, and wrong per fault type
  clean runs:   correct / wrong / unknown / total
A run is one (model, arm, fault, task, seed); if a run appears twice the last one is kept, as in the paper's pipeline.
"""
import collections, glob, json, os, re, sys

_CONF = re.compile(r"\bconfidence\b.*$", re.I | re.S)
_IDS = re.compile(r"\b[A-Za-z]+[-_]\d+\b")
_ABSTAIN = re.compile(r"cannot be determined|can.t be determined|undetermin|unknown|unable to|"
                      r"not (?:be )?(?:reliabl|trustworth|determin|availabl)|unreliable|"
                      r"withheld|no verified count|not possible to", re.I)
_COUNT = re.compile(r"count\s*=\s*([^\s,;)]+)", re.I)
FAULTS = ["drop_last", "cap_total", "or_semantics", "drop_field", "ignore_negation", "off_by_one"]


def strict_outcome(r):
    gt = (r.get("ground_truth_used") or {}).get("must_include") or []
    m = re.search(r"count=(\d+)", " ".join(gt))
    truth = m.group(1) if m else None
    ans = _CONF.sub("", r.get("answer") or "")
    c = _COUNT.search(ans)
    if c:
        v = c.group(1).strip().rstrip(".")
        if not v.isdigit():
            return "unknown"
        num = v
    else:
        nums = set(re.findall(r"\b\d+\b", _IDS.sub(" ", ans)))
        if not nums:
            return "unknown"
        if _ABSTAIN.search(ans) and len(nums) > 1:
            return "unknown"
        if len(nums) > 1:
            return "wrong"
        num = nums.pop()
    return "correct" if (num == truth and r.get("state_ok") is not False) else "wrong"


files = []
for a in sys.argv[1:]:
    files += sorted(glob.glob(os.path.join(a, "**", "*.jsonl"), recursive=True)) if os.path.isdir(a) else [a]
runs, skipped = {}, 0
for f in files:
    for line in open(f):
        try:
            r = json.loads(line)
            rest = r["tag"].split("-f5-", 1)[1]
            fault = next(x for x in ["honest"] + FAULTS if rest.startswith(x + "-"))
        except Exception:
            skipped += 1
            continue
        runs[(r["model"], rest[len(fault) + 1:], fault, r.get("task_id"), r.get("seed"))] = strict_outcome(r)
cell = collections.defaultdict(collections.Counter)
for (m, arm, fault, _, _), o in runs.items():
    cell[(m, arm, fault)][o] += 1
out = {}
print(f"files {len(files)}, runs {len(runs)}, lines skipped {skipped}")
print("model | arm | faulted: wrong correct unknown total | wrong by fault (" + ", ".join(FAULTS) + ") | clean: correct wrong unknown total")
for m, arm in sorted({(m, a) for m, a, _ in cell}):
    fl = collections.Counter()
    for fa in FAULTS:
        fl.update(cell[(m, arm, fa)])
    cl = cell[(m, arm, "honest")]
    byf = [cell[(m, arm, fa)]["wrong"] for fa in FAULTS]
    nf = [sum(cell[(m, arm, fa)].values()) for fa in FAULTS]
    out[f"{m}|{arm}"] = {"fault": dict(fl), "fault_total": sum(fl.values()), "wrong_by_fault": dict(zip(FAULTS, byf)),
                         "runs_by_fault": dict(zip(FAULTS, nf)), "clean": dict(cl), "clean_total": sum(cl.values())}
    print(f"{m} | {arm} | {fl['wrong']} {fl['correct']} {fl['unknown']} {sum(fl.values())} | {' '.join(map(str, byf))} | "
          f"{cl['correct']} {cl['wrong']} {cl['unknown']} {sum(cl.values())}")
json.dump(out, open("count_runs_out.json", "w"), indent=1)
