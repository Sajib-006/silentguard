# Invariant-Aware Harness Enforcement to Protect LLM Agents from Silent API Failures

Code for the paper *Invariant-Aware Harness Enforcement to Protect LLM Agents from Silent API Failures*
(Sajib Acharjee Dip, Brian Ouyang, Lala Motlhabi).

An API can fail silently. It ignores part of a request, such as a misnamed filter, and still returns
HTTP 200 with a plausible but wrong result. SilentGuard is a harness for LLM agents that tests each
tool result against invariants that any correct API must satisfy (for example, the count with a
condition plus the count with its negation must equal the count without it). It withholds values that
break an exact invariant and warns on values that break a learned check.

This repository contains AgentOrchBench (eight local services and a retail domain with synthetic
data, tasks and fault injectors), the SilentGuard harness and the baselines, the real-API study, our
ToolMaze and tau-bench arms, the prediction log and the scripts that build every table and figure.

## Repository layout

| Path | Contents |
|---|---|
| `code/agentorchbench/apis/` | The eight AgentOrchBench services and the retail domain (Flask), with synthetic seed data and fault injectors (`catalog_api/blind_faults.py`, `catalog_api/FAULTS.md`) |
| `code/benchmark/tasks/` | Task files (`hd100_v7.jsonl`, `adv_d7_v*.jsonl`, `retail_v1.jsonl`, `rec_v1.jsonl`) |
| `code/eval_v7/` | Agent runner, SilentGuard (`d7_harness.py`, `d7_records.py`, `executor.py`), baselines, model providers and the experiment matrix |
| `code/e3/` | Real-API probes and the real-API agent study |
| `code/external/toolmaze/`, `code/external/tau/` | Arms for ToolMaze and tau-bench |
| `reeval/` | Analysis scripts, result summaries (`paper_results/`), the prediction log (`EXTERNAL_PLAN.md`) and the code hashes (`*.sha256`) |

## Installation

Python 3.10. The run scripts expect a virtual environment named `env` at the repository root.

```bash
python3.10 -m venv env
env/bin/pip install -r requirements.txt
cp .env.example code/.env   # then fill in the keys for the providers you use
```

ToolMaze and tau-bench are installed from their authors' repositories when you run those arms.

## Reproducing

Start the service stack for a worker (worker 1 serves the eight bio services, worker 5 the retail domain):

```bash
bash code/eval_v7/stack.sh up 1
bash code/eval_v7/stack.sh up 5 retail
env/bin/python code/eval_v7/selftest_inject.py --worker 1   # checks the fault injectors
```

Run an experiment stage across workers (stages are listed in `code/eval_v7/run_matrix.py`):

```bash
cd code/eval_v7
../../env/bin/python run_matrix.py --stage silentguard --workers 1 --retail-workers 5 --dry-run
../../env/bin/python run_matrix.py --stage silentguard --workers 1 --retail-workers 5
```

Run files are written under `runs/`. The runs refuse to start if a frozen file no longer matches its
hash in `reeval/*.sha256`.

Build the tables and figures from the run files:

```bash
env/bin/python reeval/paper_tables_data.py
env/bin/python reeval/paper_tables_render.py reeval/paper_results/table_data.json out/
env/bin/python reeval/paper_figures.py reeval/paper_results/table_data.json out/figures/
```

The real-API study sends read-only requests to public APIs, about one per second:

```bash
cd code/e3
../../env/bin/python e3_real_apis.py --out ../../reeval/e3_real_apis.json
../../env/bin/python e3c_real_agents.py --threads 4 --arms none,schema,sgp --out ../../runs/matrix_e3c
```

The GPT-5.6 runs on AgentOrchBench and the Nemotron-3-Ultra control arms were run on Genentech's
internal computing platform with the same code, tasks, seeds and grader. Their per-arm counts are in
`reeval/paper_results/frontier_runs.json` and `reeval/paper_results/nemotron_external.json`.
