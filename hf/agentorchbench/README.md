---
license: cc-by-4.0
language:
- en
pretty_name: AgentOrchBench
size_categories:
- n<1K
task_categories:
- question-answering
- text-generation
tags:
- agents
- llm-agents
- tool-use
- function-calling
- api
- silent-failures
- robustness
- guardrails
- benchmark
configs:
- config_name: count
  data_files:
  - split: test
    path: data/count.jsonl
  default: true
- config_name: lists
  data_files:
  - split: test
    path: data/lists.jsonl
- config_name: retail
  data_files:
  - split: test
    path: data/retail.jsonl
- config_name: workflows
  data_files:
  - split: test
    path: data/workflows.jsonl
- config_name: faults
  data_files:
  - split: test
    path: data/faults.jsonl
- config_name: catalog
  data_files:
  - split: test
    path: data/catalog.jsonl
---

# AgentOrchBench

**A benchmark of silent API failures for LLM agents.**

An API can fail silently. It ignores part of a request, such as a misnamed filter, and still
returns HTTP 200 with a plausible but wrong result. Nothing in the response shows the fault, so
agents report the wrong number. AgentOrchBench measures how often agents fall for this and whether
a guard can stop them.

Every task has an exact answer. Each count task also lists what the API returns under each silent
fault, so you can see which faults change the answer and which ones an exact check can detect.

- **Code, services and the SilentGuard harness:** https://github.com/Sajib-006/silentguard
- **Paper:** *Invariant-Aware Harness Enforcement to Protect LLM Agents from Silent API Failures*

## Configs

| Config | Rows | Contents |
|---|---|---|
| `count` (default) | 36 | Count questions with 2 to 3 conditions, the main suite of the paper |
| `lists` | 30 | Questions whose answer is the list of matching record IDs |
| `retail` | 30 | Count questions on a second domain (retail orders) |
| `workflows` | 100 | Multi-step workflows across eight services |
| `faults` | 8 | The silent fault types, the API bug each is modeled on, and whether the exact count check sees it |
| `catalog` | 400 | The synthetic catalog records the count and list questions are asked about |

```python
from datasets import load_dataset

tasks = load_dataset("Sajib-006/AgentOrchBench", "count", split="test")
print(tasks[0]["prompt"], tasks[0]["answer"])
```

## Fields (count and retail)

| Field | Meaning |
|---|---|
| `task_id` | Task identifier |
| `prompt` | The question given to the agent. The agent must finish with `count=<n>` |
| `answer` | The exact correct count |
| `conditions` | The equality conditions in the question (`field`, `value`) |
| `num_conditions` | 2 or 3 |
| `fault_effects` | For each fault: the count the faulty API returns, whether it changes the answer, and whether the exact count check fires |

## Silent faults

| Fault | API behavior | Modeled on | Exact count check |
|---|---|---|---|
| `drop_last` | drops the last filter | a misnamed filter | fires |
| `cap_total` | reports at most 5 matches | page size returned as the total | fires |
| `or_semantics` | joins conditions with OR | a faulty query builder | fires |
| `drop_field` | ignores any filter on one field | an unindexed field | fires |
| `ignore_negation` | drops NOT from a condition | no NOT support | false alarm |
| `off_by_one` | adds one to the count | a range bug | fires |
| `prefix` | matches by prefix | loose matching | cannot see |
| `consistent_lie` | lies the same way on every query | an adversarial API | cannot see |

The first six are the fault types of the main results. The last two keep the count identity true
by design and test the limits of exact checks.

## How to run agents on it

The questions are answered through local services that you start from the code repository. A
faulted service still answers HTTP 200, so the agent sees nothing unusual:

```python
from agentorchbench import CatalogClient, load_tasks, score, start_services

with start_services("bio") as svc:
    api = CatalogClient(svc)
    api.set_fault("drop_last")
    for task in load_tasks("count"):
        answer = my_agent(task.prompt, tool=api.count)
        print(task.id, score(task, answer))   # correct, wrong, unknown or no_answer
```

Report wrong answers under faults, correct answers on fault-free tasks, and abstentions
("cannot be determined") separately. An agent that always abstains is never wrong but never useful.

## Main results

Across 7 open models, unguarded agents report a wrong count in 74.0% of faulted runs. Checklists,
reviewer models and LLM judges leave over 70% wrong. SilentGuard, which tests each result against
exact invariants and withholds values that break them, leaves 1.7% wrong on the fault types its
checks target, with no loss on fault-free tasks. See the paper for all methods, models and limits.

## Data

All records are synthetic. Values are built from public dataset names (PBMC 3k, Allen Brain Atlas,
GEO) and generic labels. No personal, patient or company data is included.

## Citation

```bibtex
@article{dip2026silentguard,
  title  = {Invariant-Aware Harness Enforcement to Protect LLM Agents from Silent API Failures},
  author = {Dip, Sajib Acharjee and Ouyang, Brian and Motlhabi, Lala},
  year   = {2026}
}
```
