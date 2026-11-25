# External benchmarks: does SilentGuard beat published methods outside our own suite?

Written 2026-09-25, BEFORE any external run. Append-only after the first model call.

## Why
Reviewers can say our method only works on the benchmark we built. So we test it on
benchmarks built by other people, with their fault injection and their graders, against
published methods.

## Datasets
1. **ToolMaze** (Zhu et al. 2026, arXiv 2606.05806; github.com/Zhudongsheng75/ToolMaze;
   HF dongsheng/ToolMaze). 400 workflows (C1-C4), 270 tools, 5 modes. We use the silent
   modes P3 (wrong content once) and P4 (wrong content every time), plus P0 (no fault) to
   measure false alarms. Their own deterministic trace judge (no LLM judge).
   Published TSR on P3/P4 is 2-47% for frontier models, so there is headroom.
2. **tau-bench retail** with the silent read faults defined by Outcome Monitors
   (status out of set; broken payment conservation on `get_order_details`). State-based
   reward. Built second, after ToolMaze works.

## Methods (every arm = same model, same settings, same tasks)
| arm | source | what changes |
|---|---|---|
| vanilla | Zhu et al. 2026 (ToolMaze "w/o hint") | standard prompt |
| hint | Zhu et al. 2026 (ToolMaze "w/ hint"); also Sun et al. 2024 "disclaimer" | fault-aware prompt |
| checklist | Sun et al. 2024, *Tools Fail* (EMNLP) | hint + a check list for each tool result (adapted from numbers to records) |
| reviewer | Ta, Zhu, Shayandeh 2026, *Reinforced Agent* (GEM@ACL) | hint + a separate reviewer checks each tool call before it runs, up to 5 loops (same port as Outcome Monitors used) |
| om | Panthi & Abdelfattah 2026, *Outcome Monitors* (arXiv 2608.19303) | hint + mined contracts + nonbinding receipt with recovery tools |
| **sg (ours)** | this paper | hint + the SAME detector as `om`, but a binding response: failed values are withheld (never shown), the call is reported as a failed verification with recovery tools, and after 2 failed checks on one tool the agent is told the value cannot be verified and must not be guessed |

`om` and `sg` share the detector byte for byte, so the `sg - om` contrast isolates the
response design (withhold + loud failure vs. advisory), which Outcome Monitors list as
"hard guard" but never test.

Detector = our port of Outcome Monitors Algorithm 2 (echo, positivity, categorical domain,
affine, date order, plus field presence/type/unexpected-field/explicit-error), mined from
the P0 nominal traces in the task files, 5-fold cross-fit by workflow so no workflow sees
contracts mined from itself. The categorical name lexicon is ours (theirs is not public).
Port check: their audit reports 79.6% recall / 1.39% clean false-positive rate per tool
outcome on C3/C4; we report ours next to it before any model run.

## Test set (fixed before any run, seed 20260925)
20 workflows per (C1..C4 x P3,P4) cell = 160 faulted episodes, plus 10 per C in P0 = 40
clean episodes. 200 episodes per arm per model. 13-model roster, roster.SETTINGS
(temperature 0, max_tokens 2000); max 25 agent rounds (ToolMaze default).
Prompt-based tool calling for every model (same interface for all 13).

## Predictions (written before running)
- P1. On P3+P4, sg > om > hint > vanilla in TSR, model-level (13 models) sign-flip test.
- P2. The sg - om gap is larger on P4 (persistent) than on P3, because advisory leaves the
  wrong data usable and agents keep going.
- P3. reviewer is within 5 points of hint (Outcome Monitors saw +3.75 for their port).
- P4. On P0, sg costs at most 5 points vs hint (false alarms turn into aborts).
- P5. checklist is within 5 points of hint.
If P1 fails for sg vs om, we report it as is.

## Detector port check (offline, no model calls; 2026-09-25)
`tm_audit.py`, 5-fold cross-fit by workflow, per tool outcome:
- C3/C4: 88.5% of silent faults flagged, 1.6% of clean outcomes flagged (Outcome Monitors
  report 79.6% / 1.39%). All complexities: 88.7% / 1.7%. So our port of their detector is at
  least as sensitive as theirs, with a similar false-alarm rate.
- Recall by fault type: 82% (statistical inconsistency) to 94% (logic violation).

## tau-bench protocol (fixed before test runs)
- 50 of the 115 retail test tasks, `random.Random(20260718)`, excluding tasks 5 and 17
  (seen in a plumbing pilot). Faults on `get_order_details`, every call, text only:
  `status` -> "in transit" (not in the database's status set); `conserv` -> first payment
  amount x1.15 (breaks net payment = item total). Plus `clean`.
- Contracts mined from orders.json exactly as Outcome Monitors describe: status and
  transaction-type sets, positive item prices, net payment = item total for non-cancelled
  orders (holds for 898/898 such orders).
- User simulator: gpt-4.1-mini, temperature 0, same for every arm and model.
- Agent: prompt-based tool calls, tau-bench wiki as policy, max 30 steps.

## Design log (dev data only; append-only)
- v0 (plumbing pilot, ToolMaze dev workflows + tau test tasks 5,17, gpt-oss-120b /
  gpt-4o-mini): found (a) gpt-oss-120b sometimes returns an empty reply -> added the same
  one-line nudge the main harness uses, all arms; (b) ToolMaze's own prompt-tool parser cuts
  nested JSON -> our parser; (c) SG hid real error messages -> SG now passes explicit errors
  through unchanged (they are already loud); (d) SG said "failed, stop" on the first flag ->
  agents quit instead of retrying; (e) on tau, SG framed the whole call as an error, so the
  agent quit a task that never needed the withheld payment fields.
- v1 (this version): SG withholds only the failed values and says which ones; the rest is
  released; "retry/switch if you need them", then "stop if still unverifiable" after 2 flags.
  No `error` key. Same text on both datasets (`sg_message`, `sg_envelope`).
- Test runs start only after the v1 dev pilot, with `tm_arms.py` and `tau_arms.py` hashed.

## Predictions for tau-bench (written before test runs)
- T1. status: om > hint (their paper: +28 on one model). sg >= om - 5.
- T2. conserv: every arm within 5 points of hint (their paper: 0.0).
- T3. clean: om and sg within 3 points of hint.
- v1 dev pilot (2026-09-25; ToolMaze dev workflows, gpt-4o-mini + gpt-4.1; tau dev split
  tasks 0,3,6,9,12,15, gpt-4o-mini): 0 provider errors, 0 SG/OM flags on tau clean runs.
  Added one more harness fix for every arm: ToolMaze final answers start with
  "FINAL ANSWER:", and a reply with neither a tool call nor that marker gets one nudge
  (gpt-4.1 often narrated a plan with no call, which ToolMaze counts as the final answer).
  Dev numbers (not for the paper): ToolMaze P3+P4 mean over 2 models vanilla 16.7, hint 10.4,
  checklist 12.5, reviewer 8.3, om 29.2, sg 39.6; tau faulted (1 model, 12 items) om 8.3, sg 33.3.
- FROZEN after this entry. Hashes in EXTERNAL_FREEZE.sha256, checked at every launch.
- 2026-09-25 14:10, analysis rule set before looking at pass rates by arm: gpt-5.6-terra
  hits HTTP 400 "max_tokens or model output limit reached" on ~13% of ToolMaze episodes
  (spread over all arms, 8-17 per arm). Provider-error rows are rerun by the resume logic;
  a row that still errors after 2 reruns is scored as a failure (the model used up the fixed
  2000-token budget, same for every arm), and the count is reported.
- 2026-09-25 16:00, PROTOCOL CHANGE (user decision, not a method change): OpenAI credit ran
  out at ~13:58, which stopped the tau user simulator (gpt-4.1-mini) for every model. The tau
  user simulator is now gpt-oss-120b on VT ARC (free, US origin), temperature 0, with up to 3
  asks on an empty reply. All tau rows made with the old simulator (~2.5k) are moved to
  runs/ext_tau_old_user_gpt41mini and NOT used; tau restarts from zero so every arm and model
  shares one simulator. Checked on 3 dev tasks (llama4-maverick): stays in role, no made-up
  order ids. Only tau/tau_arms.py changed (simulator lines); the SG/OM code and all arm text
  are unchanged. Old tau hash 4d219c17... replaced in EXTERNAL_FREEZE.sha256.
  OpenAI agent rows (ToolMaze ~1.8k, tau ~4.5k) wait for credit.
- 2026-09-25 16:30, reporting rule (user decision): the ToolMaze main table shows the 7 fully
  run models plus GPT-4.1, GPT-4o, GPT-4o-mini, which stopped at 87-89% when OpenAI credit ran
  out; caption note says so. For those three, each method's rate uses all of its finished runs
  (missing runs are random because job order was shuffled); tests use only fully paired tasks.
  GPT-5.6 Sol/Terra/Luna are left out of the table with a caption note: in the text tool-call
  format they answered "tools are unavailable" instead of calling tools (83% / 37% / 11% of
  valid runs had zero tool calls; every other model is at or below 1.7%). Found by reading
  traces after an interim table showed Sol at 11% even on clean tasks (its per-method rates
  had been seen in that interim table). Table script: code/external/make_ext_table.py
  -> reeval/tab_ext_toolmaze.tex. tau has no OpenAI rows (old-simulator rows were discarded).
- 2026-09-25 16:45, reporting rule replaced (user decision): the ToolMaze table shows all 13
  models; a model below 1200 finished runs gets the finished share in brackets after its name;
  no caption or paper text about credit or the GPT-5.6 tool-call refusals.

## Study 2 (ToolMaze, new held-out workflows) - written 2026-09-25 before any run
Why: in study 1 the checklist prompt (Sun et al. 2024) beat everything on 4 models, and ours
led Outcome Monitors by only ~2 points. The checklist (the model's own reading) and a hard check
catch different faults, so we test them together, and give Outcome Monitors the same boost.
- Data: 80 new workflows (20 per C1..C4, seed 20260926), never used in study 1 or the dev pilot
  (overlap checked: 0). P3 + P4 for each (160 faulted) + 10 per C in P0 (40 clean).
- Arms: hint, checklist, om, sg (same code as study 1), omcl = checklist prompt + OM receipt,
  sgcl = checklist prompt + SG view. Code: code/external/toolmaze/tm_arms2.py imports the frozen
  tm_arms.py unchanged; its own hash is added to EXTERNAL_FREEZE.sha256.
- Models: the 7 free models (OpenAI has no credit). Same settings as study 1.
Predictions:
- S1. sgcl > checklist and sgcl > sg (model-level, 7 models).
- S2. sgcl >= omcl.
- S3. Replication: sg > om again on these new workflows.
- S4. Clean (P0): sgcl within 3 points of checklist.

## Study 3: SG-V (verify-then-release) - written 2026-09-26 before any test run
Why: study 2 showed the losses for EVERY method are recovery errors after a correct flag (no
retry on a one-time fault, carrying on with bad data, not switching tools), not missed detection.
SG-V moves the first recovery step into the harness, which is the main-paper idea (release only
verified values) applied to recovery.
- SG-V = checklist prompt + shared detector + (1) on a flag, the harness calls the same tool once
  more through the benchmark's own fault layer (logged as a normal tool call); a result that then
  passes is released as verified; (2) if it fails again, failed values are withheld and the agent
  is told to redo the step with a named substitute, or to stop if the task needs the values and no
  substitute exists; (3) no more re-checks on a tool known to fail persistently.
- Control omclr = checklist + Outcome Monitors receipt + the same re-check (1). sgv - omclr
  isolates rules (2)-(3); omclr - omcl isolates the re-check.
- Rejected before any run: "fail closed on badly broken records" (severity does not separate the
  benchmarks: 37% of ToolMaze faults break one field; the tau payment fault breaks two).
- ToolMaze data: dev3 = 24 new workflows (6 per C, seed 20260927) x P3/P4/P0, used only to check
  the mechanics; test3 = 80 new workflows (20 per C, seed 20260928), P3+P4 + 10 per C in P0.
  Overlap with study 1, study 2, dev pilot, dev3: 0 (checked).
- ToolMaze arms: checklist, om, omcl, omclr, sgv. tau arms added to the running tau study (same 50
  tasks, faults, simulator): omcl, omclr, sgv. Code: tm_arms3.py, tau_arms3.py (import the frozen
  files unchanged); hashes added to EXTERNAL_FREEZE.sha256 before test3.
- Models: 7 free models.
Predictions:
- V1. ToolMaze silent faults: sgv > omcl, sgv > checklist, sgv > om (model-level, 7 models).
- V2. sgv >= omclr (withhold + directed switch adds to the re-check).
- V3. omclr > omcl mostly through P3 (one-time faults); sgv - omclr mostly through P4.
- V4. ToolMaze clean: sgv within 3 points of omcl.
- V5. tau: sgv >= omcl on status and conserv; clean within 3 points.
- 2026-09-26 11:30, dev3 result and PRIMARY declared before test3 (dev data only; 2 models,
  24 workflows): the re-check is the large gain (P3: omclr 70.8 vs omcl 45.8 on both models).
  Withholding after a second flag hurts (sgv P3 58-62, P0 67-71 vs omclr 75-83): the learned
  contracts give false flags that repeat on re-check, so "failed twice" is not proof. Principle:
  the strength of the response must match the precision of the check (exact count identity in the
  main benchmark -> gate; learned contracts -> re-check, then advise).
  PRIMARY method for test3 and tau = arm `omclr`, reported as "Ours (SG-V)": checklist prompt +
  shared detector + harness re-check with verified release + Outcome Monitors' receipt for
  persistent flags (credited). Arm `sgv` (withhold after two flags) is an ablation. Primary
  predictions become: V1' omclr > omcl, checklist, om; V3' gain mostly on P3; V4' clean within 3
  points of omcl; V5' tau omclr >= omcl on status and conserv, clean within 3 points.
- 2026-09-26 14:30, HARNESS FIX (user decision after a diagnosis): some models write several tool
  calls and made-up tool results in one reply (gpt-oss-120b in 85-99% of ToolMaze runs, Phi-4,
  Llama-4-Scout). Only the first call ever ran, but the rest stayed in the history. Fix
  (harness_fix.py, tag first-call-only-v1): a reply with a tool call is stored only up to the end
  of that call. Applied to every model and arm, both benchmarks (fixed.py for ToolMaze,
  tau/tau_fix.py for tau); frozen files are not edited.
  Rerun rule: a run is affected if the fix would change any stored reply (exact check with the
  fix's own function), or its trace cannot be checked. Unaffected runs are unchanged under the
  fix and are kept. Affected (7 free models): ToolMaze ~6.6k runs (gpt-oss 89-96%, Phi-4 95-99%,
  Scout 24-36%, Llama-3.3 7-10%, others 0-1%); tau ~1.3k (gpt-oss 90%, Phi-4 44%, others 0.5-6%).
  Unfixed rows -> results_prefix_unfixed.jsonl, traces -> traces_prefix_unfixed/ (kept, not used).
  Found after seeing study 3 interim numbers for gpt-oss (its per-arm rates were seen).
- 2026-09-26 15:00, OpenAI rows (cannot be rerun, no credit): checked with a stricter "material
  change" test (the fix removes something other than spaces or backticks; the first test also
  counted whitespace around a closing code fence, which only over-selects free-model reruns and
  is harmless there). Materially affected: gpt-4o-mini 3, gpt-4o 1, luna 4, sol 22, terra 39,
  gpt-4.1 0. These 69 rows are moved to results_prefix_unfixed.jsonl too, so every reported
  number comes from runs that are valid under the fixed harness; they rerun when credit returns.

## Study 4: SG-P (precision-gated) - written 2026-09-26 before any test4 run
Why: studies 1-3 show withholding wins on tau status (checks mined from 1000 database records)
while advising wins on ToolMaze (checks mined from 2-57 examples, median 9), and the main
benchmark's exact count identity favours the hard gate. One rule covers all three.
- SG-P = SG-V (checklist + detector + harness re-check with verified release) + on a persistent
  flag: if every failed check is exact (rule-of-succession false-alarm estimate 1/(n+2) <= 1%,
  n = clean examples the check held on) -> withhold the failed values; else -> Outcome Monitors
  receipt with the data visible. The 1% cutoff was chosen after seeing studies 1-3, which is why
  study 4 uses only data never touched before.
- Expected behaviour (checked offline, no model calls): ToolMaze never reaches n >= 98 (max 57),
  so SG-P acts like omclr there; every tau database check has n >= 898, so SG-P acts like sgv.
  omclr and sgv stay in as arms, so the duplicate pair also measures run-to-run noise.
- Data: ToolMaze test4 = 100 workflows never used before (25 per C, seed 20260929), P3+P4 + 10 per
  C in P0 (240 episodes/arm). tau test4 = 50 retail test tasks not in the first tau sample and
  not pilot-touched (seed 20260929), status/conserv/clean (150 episodes/arm). Overlap 0 (checked).
- Arms: checklist, om, omcl, omclr, sgv, sgp. 7 free models. Harness fix on (first-call-only-v1).
  Code: tm_arms4.py, tau_arms4.py (hashes added before launch). tau path checked on 2 tau DEV tasks.
Predictions:
- P4-1. ToolMaze silent faults: sgp > omcl, checklist, om (model-level, 7 models).
- P4-2. tau status: sgp > omcl and > om; tau conserv: sgp >= omcl - 3.
- P4-3. Pooled over both benchmarks, sgp is the best arm on faulted episodes.
- P4-4. Clean (ToolMaze P0 and tau clean): sgp within 4 points of the best arm.

## Final method locked + confirmatory experiments A-C - written 2026-09-28 before any run
- SG-P locked (reeval/METHOD_FINAL.md, reeval/METHOD_FINAL.sha256). No method change after this.
- tau-bench: user decision to leave it out of the main paper (it ties the best published methods).
  Recommendation recorded: one appendix line, since its predictions were written in advance.
- A. Blind-retry control on ToolMaze (study 3 + study 4 workflows, P3/P4/P0): `blindr` = checklist
  + OM receipt + EVERY call made twice, second result released, no check deciding. Code
  tm_arms5.py --exp A. Prediction A1: sgp > blindr on P4 (blind retry releases the same wrong
  data again, with no verified/unverified label); A2: sgp >= blindr - 2 on P3; A3: blindr costs
  about 2x tool calls and repeats every write.
- B. Loud faults (ToolMaze P1/P2) on the same workflows, arms checklist, om, omcl, sgp.
  Prediction B1: sgp within 3 points of omcl (SG-P passes loud errors through with the receipt).
- C. AgentOrchBench final5 fault set, arm `sgp` (runner --d7-remediation sgp: checklist prompt, exact
  every-position count check, one re-query, withhold on a second failure, enforce2 answer gate),
  7 free models, compared with the existing gate2/enforce2/none rows. Smoke test on 2 tasks
  (maverick, drop_last): count withheld after re-check, model abstained. Prediction C1: sgp wrong-
  answer rate within 5 points of enforce2; C2: sgp correct rate >= gate2 - 5.
- Analysis (no new runs): pooled ToolMaze studies 3+4 (14 model-study pairs) for ours vs checklist
  + OM, reported as secondary (not planned before those studies ran); clean-cost split.
- 2026-09-28 17:10, transport change (not a method change): VT ARC allows 200 requests/min per
  account and many rows were hitting HTTP 429 (retried, but slow and some rows failed). Added a
  shared, file-locked limiter in code/eval_v7/providers.py (_vtarc_slot, VTARC_RPM=180) so every
  process on the server shares one budget; backup providers.py.bak_pre_rate. Restarted aob_abc and
  aob_sweep so all processes use it (resume logic keeps finished rows; E6d untouched).

## Experiments E (retail SG-P), F (real-API SG-P), D (published methods in AgentOrchBench) - 2026-09-28, before any run
All on the 7 free models, locked SG-P unchanged; harness edits are additive (backups *.bak_pre_sgp,
*.bak_pre_d; hashes in reeval/CONFIRM_ABC.sha256).
- E. SG-P on the retail domain of AgentOrchBench (stage sgp_r5, 5 fault types), compared with the
  existing retail5 arms. Prediction E1: SG-P wrong answers within 5 points of enforce2; clean within 5.
- F. SG-P on real public APIs (e3c arm `sgp`: checklist + spec check + ignored-parameter probe with one
  re-check and withholding + checked-answer gate), docs A and G, 4 APIs. Prediction F1: with wrong docs,
  SG-P wrong <= min(guard, schema) + 3 and correct >= schema - 5 (it contains both checks).
- D. Published methods inside AgentOrchBench (stage pubd5, final5 faults): checklist (Sun 2024),
  reviewer (Reinforced Agent port, same model, <= 5 loops), Outcome Monitors (contracts mined with the
  same miner from clean responses of OTHER tasks, 5-fold by task, replayed from final5 honest/no-guard
  requests; their nonbinding advisory text). Prediction D1: all three stay above 40% wrong under the
  count faults (none of them has an exact count check); D2: SG-P has fewer wrong answers than each on
  7/7 models; D3: Outcome Monitors' mined contracts fire on few count faults (they check shape, not the
  count), so its wrong rate is within 10 points of no guard.
- 2026-09-29, retry controls on AgentOrchBench (stage retry5, final5 faults, 7 free models), written
  before the run: `retry2` = every request sent twice, only the second response released (the exact
  mirror of ToolMaze's blind retry, experiment A); `requery` = the existing arm (same query re-sent,
  both counts shown). Why: blind retry beat SG-P on ToolMaze one-time faults (60.4 vs 47.6), because
  ToolMaze corrupts only the first call; AgentOrchBench faults are persistent. Prediction R1: retry2
  wrong answers within 5 points of no guard (74.0); R2: requery within 10 points of no guard; R3: SG-P
  (1.7) has fewer wrong answers than both on 7/7 models.
- 2026-09-29, blind-retry robustness, written before the run: ToolMaze P3 with the same fault lasting
  the first TWO calls to the victim tool instead of one (tm_arms6.py; tasks, faults and judge otherwise
  unchanged), study 3+4 workflows (180), arms omcl / blindr / sgp, 7 free models. Prediction BR1:
  blind retry loses most of its P3 advantage (within 5 points of omcl); BR2: sgp >= blindr.
  Also run: blind retry on real public APIs (e3c arm retry2). Prediction BR3: retry2 wrong answers
  with wrong docs within 5 points of no guard.

## 2026-09-30: result of the two-call fault test (tm_arms6, p3x2)

Prediction BR1 (blind retry loses its lead when the one-time fault lasts two calls) FAILED. On all 7 free
models blind retry still has the highest P3 success (about 0.56 to 0.70), SG-P is second (about 0.47 to 0.57)
and Checklist + OM is last. Framing in the paper: blind retry is a good fix for short faults on calls with no
side effects; SG-P is the safer choice when faults last or calls change state (repeated actions). Runs: Gemma
and Phi-4 on VT ARC (self-hosted vLLM); Scout, Maverick, Nemotron and Llama-3.3 on HF billed to the user's own
account with a hard cap (Llama-3.3 pinned to novita because its ARC vLLM start failed). Real-API blind retry
(e3c retry2) finished for all 7 models.

## 2026-10-01: SG-P on list answers (stage rec9_sgp), written before any run
Additive arm rec_sgp (executor/runner/run_matrix, backups *.bak_pre_recsgp, hashes reeval/CONFIRM_RECSGP.sha256):
the rec9 record checks (d7_records, unchanged), one re-check of a failed result with the same request, a result
that fails twice is withheld (records and total removed), the rec_enforce answer gate, and the SG-P result checklist.
Same 30 list tasks and 12 fault settings as rec9, 1 seed. Models: the 6 free models other than Nemotron-3-Ultra
(left out for cost: about 17 USD on its only HF provider); comparisons use the same 6 models for every arm.
Llama-3.3-70B on the novita backend (rec9 used scaleway) to stay under the 10 USD budget.
Predictions: L1: rec_sgp wrong lists within 3 points of rec_enforce on seen and on unseen faults (the faults
repeat, so the re-check rarely changes the outcome). L2: rec_sgp has fewer wrong lists than no guard and than
auto-repair on seen and unseen faults (6-model means). L3: clean (honest) correct within 5 points of no guard.
- 2026-10-01, before any run: also withhold returned_count (the size of the failed list) in rec_sgp; hashes re-recorded.

## 2026-10-01: published detectors with blocking power (stage block5, review item), written before any run
Additive baselines judge_block and om_block (backups *.bak_pre_block, hashes reeval/CONFIRM_BLOCK.sha256): the
same gpt-oss-120b judge as judge_check (sees the harness consistency queries) or the same Outcome Monitors
contracts as om, but a flagged result is withheld and the final count must come from a result the detector
accepted (the enforce2 gate). AgentOrchBench final5 faults, 36 tasks, 2 seeds, 6 free models (no Nemotron, cost).
Predictions: K1: judge_block has more wrong answers than SilentGuard on 6/6 models, because the judge also
accepts many faulted results. K2: om_block stays within 10 points of om on wrong answers, because the contracts
rarely fire on count faults. K3: judge_block clean correct is at least 10 points below no guard (false alarms).

## 2026-10-01: judge with blocking but without the harness evidence (stage block5p), written before any run
Additive baseline judge_block_plain (code/eval_v7/patch_plain.py, applied only after block5 finishes; backups
*.bak_pre_plain; hashes reeval/CONFIRM_PLAIN.sha256): judge_block with the consistency-query evidence removed,
so the same gpt-oss-120b judge sees only the task, the request filters and the API response. Same blocking and
answer gate. AgentOrchBench final5 faults, 36 tasks, 1 seed (time), 6 free models.
Prediction P1: judge_block_plain has at least 20 points more wrong answers than judge_block (6-model means),
because without the extra queries a silently wrong count has nothing to contradict it.
Prediction P2: judge_block_plain clean correct within 10 points of judge_block.

## 2026-10-02: typed tool calls on real APIs (review item #3), written before any run
code/e3/e3c_typed.py (imports the locked e3c and e3c_more unchanged; hashes reeval/CONFIRM_TYPED.sha256). The agent
calls a typed tool whose strict schema lists the API documented parameters; unknown parameters are rejected, as
strict function calling would. Arms typed and typed_sgp (typed plus SG-P checks). 9 APIs, 5 tasks, docs A and G,
7 free models. Predictions: T1: typed wrong (docs G, 9 APIs pooled) within 5 points of the schema arm. T2: on NCBI,
UniProt and Europe PMC (filters inside a query string) typed leaves more than 30 percent wrong. T3: typed_sgp has
no more wrong answers than typed on at least 6 of 7 models.
- before the run: the harness also accepts the {name, parameters} wrapper of native function calling (smoke test showed models use it); hashes re-recorded.

## 2026-10-02: crossed evidence-by-enforcement test with one judge (review round 2), written before any run
Additive arm judge_advise_plain (code/eval_v7/patch_cross.py; backups *.bak_pre_cross; hashes
reeval/CONFIRM_CROSS.sha256): the same gpt-oss-120b judge, advisory only, without the harness consistency queries.
With judge_check (evidence, advise), judge_block_plain (no evidence, block; its second seed added here) and
judge_block (evidence, block) this gives a fully crossed 2x2 with one detector, on the same 6 models and 2 seeds.
Also advisory_abstain: the count-check warning plus the caution prompt that explicitly allows answering that the
count cannot be determined. Stage cross5 (6 models, AgentOrchBench final5, 2 seeds) and block5p seeds 1 to 2.
Predictions: X1: judge_advise_plain wrong within 10 points of no guard (6-model means). X2: in the crossed 2x2, the
evidence-and-block cell has at least 40 points fewer wrong answers than each of the other three cells. X3:
advisory_abstain has fewer wrong answers than advisory (warning alone) but more than 20 percent wrong.

## 2026-10-04: warning + permission to abstain with the full check (stage permit5), written before any run
Round-3 review found that the advisory_abstain arm used the older last-position check (d7 v1), so its 11.9 percent
wrong is mostly the ignore-field fault that check misses (44.0 percent there, about 5 on the other faults), while
SilentGuard uses the every-position check (v3). New arm advisory_nonum_abstain: the v3 warning (advisory_nonum, the
same detector as SilentGuard, already run alone in final5) plus the caution prompt that allows answering that the
count cannot be determined. Only run_matrix.py changes (new stage; hashes reeval/CONFIRM_PERMIT.sha256). Same 6
models, final5 faults, 2 seeds.
Predictions: Y1: wrong on ignore field falls below 15 percent. Y2: overall wrong between 3 and 8 percent (6-model
mean). Y3: more wrong answers than SilentGuard on at least 4 of the 6 models.

## 2026-10-05: wrong value FORMAT on a real API (reviewer concern: the real-API gain is the spec check), written before any run
Free probes first (no model): reeval/e3f_badvalue_probe.json (a typo in a filter value is silently ignored by Europe
PMC, GitHub search and iNaturalist; the count identity or the ignored-parameter probe fired on 15 of 15, no false
alarm) and reeval/e3g_identity_replay.json (on the final queries of the existing sgp runs the count identity would
catch only 8 of 60 wrong answers and raise 0 false alarms on 49 correct ones; most remaining wrong answers are a
different, self-consistent query). So no identity is added to the agent harness. New agent study with the FROZEN
harness: code/e3/e3i_badformat.py registers one task set, inat_d2 (iNaturalist, date filter d2). Docs G give the
value format DD/MM/YYYY, which the API silently ignores when the day is above 12 (it returns the unfiltered count);
docs A give YYYY-MM-DD. The parameter name is valid, so the spec check passes. 5 tasks, 7 free models, arms none,
schema and sgp with docs G, and none with docs A. Task check (--check) passed for all 5 tasks before the run.
Hashes: reeval/CONFIRM_BADFORMAT.sha256.
Predictions (7-model means): V1: no guard with wrong docs is wrong at least 80 percent of the time. V2: the spec
check alone is within 10 points of no guard (wrong). V3: SilentGuard is wrong at most 20 percent of the time and at
least 50 points below the spec check alone. V4: SilentGuard is correct at least 50 percent of the time. V5: no
guard with the right docs is correct at least 85 percent of the time.
- 2026-10-05 result (e3i): Gemma-3-27B could not finish (its Hugging Face provider stopped serving the model during
  the run: 17 provider errors, 3 runs done), so the result is on 6 models. No guard 100.0 wrong, spec check alone 100.0,
  SilentGuard 20.0 wrong / 3.3 correct / 76.7 unknown, right docs 100.0 correct. V1, V2, V5 held; V3 held at its
  boundary (20.0); V4 FAILED (correct 3.3, predicted at least 50): agents abstain, they rarely find the right format.
  Of the 6 wrong answers under SilentGuard, 3 came from a live count that changed by one between the request and the
  probe (so the probe saw a difference and passed the result) and 3 from Nemotron switching to a different valid
  filter (year=) after the withhold.
- 2026-10-06: Gemma-3-27B rerun finished (backend named explicitly as deepinfra; the HF router had stopped auto-selecting
  one although the backend answers). All 7 models: no guard 97.1 wrong, spec check alone 100.0, SilentGuard 20.0 wrong /
  2.9 correct / 77.1 unknown, right docs 88.6 correct. V1, V2, V3 (at its boundary), V5 held; V4 failed. 7 wrong answers
  under SilentGuard: 4 from live-count drift between request and probe, 3 from Nemotron switching to year=.

## 2026-11-24: Phase 2 (review round of 2026-11-24), written before any Phase 2 run
Full plan with predictions Z1-Z3 (stage ctl5), M1-M6 (stage mix5) and the go rule for the real-API pilot e3n:
reeval/PHASE2_PLAN.md (sha256 549bb9315778592c766b18f4238e6717ac3ab51278adaab54d14eb1052eb567b). Free models only: gpt-oss-120b (VT ARC endpoint), Gemma-3-27B, Phi-4,
Llama-3.3-70B (ARC GPUs, vLLM).
Addendum for the e3n pilot added before it ran (replay tolerance, conjunct limit, pilot size). PHASE2_PLAN.md sha256 now 1dcbf3c941f0fce78a79362a10a3e275c0c14b30ea1f54443867112cdffea86d.
