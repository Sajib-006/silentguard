# Phase 2 plan and pre-registration (written 2026-11-24, before any Phase 2 run)

Goal: answer the three critiques that all four reviewers shared (review_round_20261124/MERGED_REVIEW_AND_PLAN.md).
Free models only: gpt-oss-120b on the VT ARC endpoint; Gemma-3-27B, Phi-4 and Llama-3.3-70B on ARC GPUs with vLLM
(weights already in /projects/apruden_lab/sajib_aob/hf_cache). Method code stays frozen. New arms and stages are
additive patches with new hash files, as in earlier stages.

Order: P2-01 (smallest), P2-02 (largest), P2-03 pilot (cheap, decides P2-04).

## Rules for all Phase 2 runs
- Same tasks (adv5, 36 count tasks), seeds (2), turn and token limits, prompts and strict grader as the paper.
- VT ARC endpoint: shared limiter at 60 requests per minute (providers.py), at most 4 in flight, backoff on 429/502/503.
- With 4 models, model-level sign tests cannot reach p < 0.125. Primary tests are paired at the run level
  (same model, fault, task, seed), with a 95% bootstrap interval over tasks, plus wins out of 4 models.
- Nothing may be tuned after results. Any analysis added later is labelled post hoc.

## P2-01 (stage ctl5). The missing control: full check, numbers shown, permission to abstain
Question: is a strong warning enough once the agent may abstain, so that withholding adds nothing?
New arm `advisory_num_abstain`: the every-position check (v3), the failed check's numbers shown in the verdict
(reported count, count with the condition negated, count without it), plus the caution prompt that permits
"cannot be determined". Additive patch to executor.py (a new remediation value only).
Runs: 1 arm x (6 seen faults + clean) x 36 tasks x 2 seeds = 504 per model, 4 models.
Compared with existing or re-run arms on the same models: no guard, advisory_nonum_abstain, sgp.
Predictions (4-model means):
- Z1: the new arm leaves at least 5% wrong on faulted runs.
- Z2: SilentGuard has fewer wrong answers than the new arm on paired runs, interval above 0, and on at least 3 of 4 models.
- Z3: the new arm's wrong rate differs by at least 10 points between the best and the worst model.
If Z1 and Z2 both fail, the paper's claim narrows to: with an exact check, numbers shown and permission to abstain,
a warning is as reliable as withholding on these models. That would be reported in the main text.
Sanity gate: on clean tasks the new arm's verdict text must never contain a failure (0 flagged clean runs).

## P2-02 (stage mix5). Mixed-fault main table: agents on the six seen and the six blind fault types
Question: what is the wrong-answer rate with agents when the fault types include ones the check was not built for?
Arms: none, advisory_nonum_abstain (warning + may abstain), enforce2, sgp.
Faults: the six blind fault types in blind_faults.py (hash in BLIND_FAULTS.sha256), written by an author who had not
seen the detector. Runs: 4 arms x 6 blind faults x 72 = 1,728 per model, 4 models.
For the three vLLM models the same 4 arms are also run on the six seen faults and clean tasks (2,016 runs per model),
so that every cell of the mixed table for a model comes from one serving setup. gpt-oss-120b keeps its existing
seen-fault and clean runs (same endpoint as before).
Predictions (4-model means, blind faults unless noted):
- M1: no guard is wrong in at least 50% of runs.
- M2: SilentGuard is wrong in 15% to 35% of runs, and below no guard on 4 of 4 models.
- M3: SilentGuard has fewer wrong answers than warning + may abstain on paired runs (interval above 0).
- M4: on all 12 fault types pooled, SilentGuard's wrong rate is at most one third of no guard's.
- M5 (cost): SilentGuard's correct rate on blind faults is at least 10 points below no guard's.
- M6: Enforce has more wrong answers than SilentGuard on paired runs (interval above 0).
Sanity gates (vLLM models): no-guard wrong rate on seen faults within 10 points of the earlier value for the same
model, and no-guard clean accuracy within 10 points. If a gate fails for a model, its serving setup is checked
before any other number of that model is read.
Kill rule: if SilentGuard is not below no guard on blind faults for 2 or more models, the mixed table is reported
as a limit and the headline stays scoped to seen faults.
The mixed table (12 fault types) becomes the main table. The seen-fault table stays as the by-design reference.

## P2-03 (pilot e3n). Do agents trigger silent failures on real APIs with no edited documentation?
Question: with no author-edited documentation, how often does an unguarded agent get a wrong count from a real API,
and are those wrong counts silent API failures (an ignored parameter, or a query-string term the API did not apply)?
Setting "from memory": the agent gets the API name, the base URL and the task, and no parameter documentation,
as when an agent calls a well-known API from what it knows. Same 9 APIs and 5 tasks each as the paper.
Pilot: gpt-oss-120b only, arms none and spec check, 2 seeds (180 runs). New script e3n_natural.py that imports the
frozen e3c code unchanged.
Then, with no model: replay the final query of every wrong no-guard run and test (a) the spec check, (b) the
ignored-parameter probe, (c) a count identity on query-string conjuncts (N(A AND B) + N(A NOT B) = N(A)).
Go rule for the full study P2-04: no guard is wrong in at least 20% of runs, and at least half of the wrong runs
are caught by (a), (b) or (c) on replay.
No-go: the paper says that with correct or no documentation agents rarely trigger this fault (with the measured
rate), the real-API agent study is described as a stress test with wrong documentation, and no query-string
check is added.

## P2-04 (only if P2-03 says go). Fair real-API study
Arms: none; parameter list given in the prompt (equal information, no check); spec check; SilentGuard; SilentGuard
plus the query-string identity if replay shows it catches at least 25% of the remaining wrong answers with at
most 5% false alarms on correct ones. 4 models, 9 APIs x 5 tasks, 2 seeds. Predictions are written after the pilot
and before the full run.

## Not allowed after seeing results
Changing a check, a prompt, the grader, the task set, the fault set or a prediction. Dropping a model because of its
result. Reporting only the fault types where SilentGuard wins.

## Addendum 2026-11-24, written before the P2-03 pilot ran
- Scripts: code/e3/e3n_natural.py (documentation variant N, frozen e3c code imported unchanged) and
  code/e3/e3n_replay.py (no model). `--check` passed for all 9 APIs before the run.
- Replay identity tolerance: a relative difference of at most 0.1% counts as "holds", because live databases
  change between requests. The exact-equality result is reported next to it.
- The identity is tested on the first 4 top-level AND conjuncts of a query. Queries with a top-level OR or NOT,
  or with an implicit AND (a space), are counted as not testable.
- Pilot size: gpt-oss-120b, arms none and schema, 9 APIs x 5 tasks x 2 repeats = 180 runs.
