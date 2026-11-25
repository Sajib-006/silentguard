# Reproducing the paper

This folder holds the exact code, tasks, predictions and analysis scripts behind the paper
*Invariant-Aware Harness Enforcement to Protect LLM Agents from Silent API Failures*. Each file
in `reeval/*.sha256` records the versions that were frozen before one planned stage. Files that
changed between stages have one hash per stage, and the files here are the final versions.

| Path | Contents |
|---|---|
| `code/agentorchbench/apis/` | The eight AgentOrchBench services and the retail domain (Flask), with synthetic seed data and fault injectors |
| `code/benchmark/tasks/` | Task files used in the paper |
| `code/eval_v7/` | Agent runner, SilentGuard (`d7_harness_v3.py`, `d7_records.py`, `executor.py`), baselines, model providers and the experiment matrix |
| `code/e3/` | Real-API probes and the real-API agent study |
| `code/external/toolmaze/`, `code/external/tau/` | Arms for ToolMaze and tau-bench |
| `reeval/` | Analysis scripts, result summaries (`paper_results/`), the prediction log (`EXTERNAL_PLAN.md`) and the code hashes |

## Installation

Python 3.10. The run scripts expect a virtual environment named `env` inside this folder.

```bash
cd paper
python3.10 -m venv env
env/bin/pip install -r requirements.txt
cp .env.example code/.env     # then fill in the keys for the providers you use
```

ToolMaze and tau-bench are installed from their authors' repositories when you run those arms.

## Running experiments

Start a service stack (worker 1 serves the eight bio services, worker 5 the retail domain), check
the fault injectors, and run a stage of the experiment matrix (stages are listed in
`code/eval_v7/run_matrix.py`):

```bash
bash code/eval_v7/stack.sh up 1
bash code/eval_v7/stack.sh up 5 retail
env/bin/python code/eval_v7/selftest_inject.py --worker 1
cd code/eval_v7
../../env/bin/python run_matrix.py --stage silentguard --workers 1 --retail-workers 5 --dry-run
../../env/bin/python run_matrix.py --stage silentguard --workers 1 --retail-workers 5
```

Run files are written under `runs/`.

The real-API study sends read-only requests to public APIs, about one per second:

```bash
cd code/e3
../../env/bin/python e3_real_apis.py --out ../../reeval/e3_real_apis.json
../../env/bin/python e3c_real_agents.py --threads 4 --arms none,schema,sgp --out ../../runs/matrix_e3c
```

## Second round of runs (agents on unseen fault types, control arm, real APIs with no documentation)

The plan and predictions, written before these runs, are in `reeval/PHASE2_PLAN.md` (hash recorded in
`reeval/EXTERNAL_PLAN.md`). The patch that adds the control arm and the stages is
`code/eval_v7/patch_phase2.py`; the frozen versions are in `reeval/CONFIRM_PHASE2.sha256` and
`reeval/CONFIRM_E3N.sha256`.

```bash
cd code/eval_v7
../../env/bin/python run_matrix.py --stage mix5 --workers 1,2,3 --only <model>       # unseen fault types
../../env/bin/python run_matrix.py --stage mix5seen --workers 1,2,3 --only <model>   # seen faults and clean
../../env/bin/python run_matrix.py --stage ctl5 --workers 1,2,3 --only <model>       # warning with numbers
cd ../e3
../../env/bin/python e3n_natural.py --check                                          # no model call
../../env/bin/python e3n_natural.py --only <model> --arms none,schema --repeats 2 --out ../../runs/matrix_e3n
../../env/bin/python e3n_replay.py --rows ../../runs/matrix_e3n
```

Gemma-3-27B, Phi-4 and Llama-3.3-70B were served with vLLM (provider entry pointing at a local
OpenAI-compatible server), gpt-oss-120b through the same endpoint as the first round.

The run logs of this round, and the first-round rows the new table compares against, are in
`runs_release/phase2_and_reference_runs.tar.gz`. To rebuild the table from them:

```bash
mkdir -p runs && tar xzf runs_release/phase2_and_reference_runs.tar.gz -C runs
python3 reeval/phase2_analysis.py           # writes reeval/paper_results/phase2_stats.json
```

## Rebuild every table from the released logs

`runs_release/` holds the run logs of both rounds (`round1_runs.tar.gz`, `phase2_and_reference_runs.tar.gz`).
With Python 3 and the packages in `requirements.txt`:

```bash
bash reeval/rebuild_tables.sh
```

This recomputes `reeval/paper_results/table_data.json` and `phase2_stats.json` from the raw runs. We checked that
both come out identical to the committed files. Two inputs are counts only, because those runs were made on a
company platform whose logs cannot be released: the GPT-5.6 runs on AgentOrchBench and the Nemotron-3-Ultra
control arms (`reeval/paper_results/frontier_runs.json`, `nemotron_external.json`). The ToolMaze repeated-action
counts come from full traces that are too large to release (`reeval/paper_results/repeated_actions.json`).

## Tables and figures

```bash
env/bin/python reeval/paper_tables_data.py
env/bin/python reeval/paper_tables_render.py reeval/paper_results/table_data.json out/
env/bin/python reeval/paper_figures.py reeval/paper_results/table_data.json out/figures/
```

The GPT-5.6 runs on AgentOrchBench and the Nemotron-3-Ultra control arms were run on Genentech's
internal computing platform with the same code, tasks, seeds and grader. Their per-arm counts are in
`reeval/paper_results/frontier_runs.json` and `reeval/paper_results/nemotron_external.json`.
