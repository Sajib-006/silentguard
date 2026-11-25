"""Phase 2 additive patch (2026-11-24, reeval/PHASE2_PLAN.md). Run from code/eval_v7 on the server.

Adds one remediation value (advisory_num: the every-position verdict plus the failed check's numbers) and three
stages (ctl5, mix5, mix5seen). No existing arm, prompt, check or stage is changed. Backups: *.bak_pre_phase2.
"""
import shutil
from pathlib import Path


def patch(name, edits):
    p = Path(name)
    s = p.read_text()
    if "Phase 2 (2026-11-24)" in s:
        print(name, "already patched"); return
    shutil.copy(p, name + ".bak_pre_phase2")
    for old, new in edits:
        assert s.count(old) == 1, f"{name}: anchor found {s.count(old)} times: {old[:60]!r}"
        s = s.replace(old, new)
    p.write_text(s)
    print(name, "patched")


patch("executor.py", [
    ('V3_ARMS = {"advisory_hint", "advisory_nonum", "enforce2", "repair3", "sgp"}',
     'V3_ARMS = {"advisory_hint", "advisory_nonum", "enforce2", "repair3", "sgp", "advisory_num"}'),
    ('''                elif d7_remediation == "advisory_nonum":
                    # isolates the number leak: same v3 verdict as the hint arm, no numbers, no hint, no gate
                    if v["ran"]:
                        rec["full_body"] = rec["full_body"] + "\\n\\n" + v["text"]
''',
     '''                elif d7_remediation == "advisory_nonum":
                    # isolates the number leak: same v3 verdict as the hint arm, no numbers, no hint, no gate
                    if v["ran"]:
                        rec["full_body"] = rec["full_body"] + "\\n\\n" + v["text"]
                elif d7_remediation == "advisory_num":
                    # Phase 2 (2026-11-24) control: the same v3 verdict, plus the numbers of the first failed check
                    if v["ran"]:
                        extra = ""
                        if failed and v.get("detail") and v.get("failed_at"):
                            dd = v["detail"][v["failed_at"][0]]
                            extra = (f" Check numbers: reported count {v['n']} + count with {dd['field']} negated "
                                     f"{dd['negated_count']} = {v['n'] + dd['negated_count']}, but the count without "
                                     f"that condition is {dd['without_count']}.")
                        rec["full_body"] = rec["full_body"] + "\\n\\n" + v["text"] + extra
'''),
])

patch("runner.py", [
    ('"advisory_hint", "advisory_nonum", "enforce2", "repair3", "rec_enforce", "rec_repair", "sgp", "rec_sgp"],',
     '"advisory_hint", "advisory_nonum", "enforce2", "repair3", "rec_enforce", "rec_repair", "sgp", "rec_sgp",\n'
     '                             "advisory_num"],   # Phase 2 (2026-11-24)'),
])

patch("run_matrix.py", [
    ('# every model must go through every one of these, with identical settings\n',
     '''# Phase 2 (2026-11-24), reeval/PHASE2_PLAN.md. Additive stages, same tasks, seeds and settings as final5.
BLIND6 = ["blind_analyzed_text_match", "blind_null_negation_drop", "blind_partial_shard_timeout",
          "blind_tag_join_fanout", "blind_keyset_cursor_ties", "blind_lossy_bitmap_no_recheck"]
P2_ARMS = {"none": [],
           "advisory_nonum_abstain": ["--d7-harness", "--d7-remediation", "advisory_nonum", "--baseline", "skeptic"],
           "enforce2": ["--d7-harness", "--d7-remediation", "enforce2"],
           "sgp": ["--d7-harness", "--d7-remediation", "sgp"]}
# ctl5: the missing control, full check with numbers shown plus permission to abstain
STAGES["ctl5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-advisory_num_abstain", ["--catalog", "large", "--projection", "count_only",
                                                  "--sfr-mode", "off" if fam == "honest" else fam,
                                                  "--d7-harness", "--d7-remediation", "advisory_num",
                                                  "--baseline", "skeptic"])
              for fam in F5_FAULTS]}
# mix5: four arms on the six blind fault types
STAGES["mix5"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only", "--sfr-mode", fam] + a)
              for fam in BLIND6 for arm, a in P2_ARMS.items()]}
# mix5seen: the same four arms on the seen faults and clean tasks (for models served on a new setup)
STAGES["mix5seen"] = {
    "conditions": ["A"], "domains": ["bio"], "seeds": 2, "task_file": "adv5",
    "cells": [(f"f5-{fam}-{arm}", ["--catalog", "large", "--projection", "count_only",
                                   "--sfr-mode", "off" if fam == "honest" else fam] + a)
              for fam in F5_FAULTS for arm, a in P2_ARMS.items()]}
# every model must go through every one of these, with identical settings
'''),
])
