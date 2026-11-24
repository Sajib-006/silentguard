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

## Tables and figures

```bash
env/bin/python reeval/paper_tables_data.py
env/bin/python reeval/paper_tables_render.py reeval/paper_results/table_data.json out/
env/bin/python reeval/paper_figures.py reeval/paper_results/table_data.json out/figures/
```

The GPT-5.6 runs on AgentOrchBench and the Nemotron-3-Ultra control arms were run on Genentech's
internal computing platform with the same code, tasks, seeds and grader. Their per-arm counts are in
`reeval/paper_results/frontier_runs.json` and `reeval/paper_results/nemotron_external.json`.
