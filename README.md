# SilentGuard

**Your agent's API said HTTP 200. The answer was still wrong.**

APIs fail silently. A misnamed filter is ignored, a total is capped at the page size, an AND is
read as OR, and the API still returns HTTP 200 with a plausible number. Agents trust it. In our
tests, 11 of 15 public research APIs answer at least one such mistake this way, with counts up to
691 times too large.

SilentGuard moves the decision from the model to the harness. It tests every tool result against
**invariants that any correct API must satisfy**, re-runs a failing request once, and **withholds**
a value that is proven wrong, so the model never sees it. It needs no extra model call and no
change to the API.

This repository also contains **AgentOrchBench**, a benchmark of silent API failures for LLM agents
with exact answers.

## The idea in one line

For a request with conditions C1..Ck and any position i,

```
count(all conditions) + count(others, and NOT Ci) == count(others)
```

"Ci" and "not Ci" split the matching records into two parts, so a correct API always passes.
An API that drops, misreads or caps a condition breaks the identity, even though each response
looks normal.

## Install

```bash
git clone https://github.com/Sajib-006/silentguard.git
cd silentguard
pip install -e ".[bench]"          # the library alone has no dependencies: pip install -e .
```

## Quickstart

```python
from silentguard import Condition, SilentGuard

guard = SilentGuard(count=my_api_count)        # my_api_count(conditions) -> int | None
result = guard.count([Condition("status", "pass"), Condition("tissue", "tumor")])

result.status         # "verified", "verified_on_recheck", "withheld", "warned" or "unchecked"
result.value          # the count, or None when withheld
result.observation()  # the JSON text to show your agent
guard.check_answer(n) # answer gate: accept a final answer only if the harness verified it
```

Run the 30-second demo (no model, no network):

```bash
python examples/01_quickstart.py
```

```
API that ignores 'tissue'
  plain call returns : 159   (true answer 76)
  SilentGuard        : withheld, value released = None
  failed condition   : tissue=tumor
  final answer  159  : rejected
```

## Use it with your agent

SilentGuard gives you an OpenAI-style tool and a handler for it:

```python
guard = SilentGuard(count=my_api_count)
tools = [guard.tool_schema()]                       # pass to your chat model
observation = guard.call_tool(tool_call.arguments)  # run the guarded tool
decision = guard.check_answer(final_count)          # gate the final answer
if not decision.final:
    messages.append({"role": "user", "content": decision.message})
```

`examples/03_llm_agent.py` runs a full agent loop against any OpenAI-compatible endpoint.

## AgentOrchBench

Local services with synthetic data, tasks with exact answers, and silent faults you can switch on.

| Suite | Tasks | What the agent does |
|---|---|---|
| `count` | 36 | Count records that match 2 to 3 conditions (main suite) |
| `lists` | 30 | Return the IDs of all matching records |
| `retail` | 30 | Count orders in a second domain |
| `workflows` | 100 | Multi-step workflows across eight services |

| Fault | API behavior | Modeled on |
|---|---|---|
| `drop_last` | drops the last filter | a misnamed filter |
| `cap_total` | reports at most 5 | page size returned as the total |
| `or_semantics` | joins conditions with OR | a faulty query builder |
| `drop_field` | ignores any filter on one field | an unindexed field |
| `ignore_negation` | drops NOT | no NOT support |
| `off_by_one` | adds one to the count | a range bug |

```python
from agentorchbench import CatalogClient, load_tasks, score, start_services

with start_services("bio") as svc:          # starts the services locally, no Docker
    api = CatalogClient(svc)
    api.set_fault("drop_last")
    for task in load_tasks("count"):
        answer = my_agent(task.prompt, tool=api.count)
        print(task.id, score(task, answer))  # correct, wrong, unknown or no_answer
```

With no model in the loop, `examples/02_benchmark_faults.py` asks every count task once with a plain
call and once through SilentGuard:

| Fault | Plain call wrong | SilentGuard wrong | SilentGuard withheld |
|---|---|---|---|
| none | 0% | 0% | 0% |
| `drop_last` | 100% | 0% | 100% |
| `cap_total` | 100% | 0% | 100% |
| `or_semantics` | 100% | 0% | 100% |
| `drop_field` | 83% | 0% | 83% |
| `ignore_negation` | 0% | 0% | 100% (false alarm) |
| `off_by_one` | 100% | 0% | 100% |

The tasks are also on the Hugging Face Hub: [Sajib-006/AgentOrchBench](https://huggingface.co/datasets/Sajib-006/AgentOrchBench).

## Results with LLM agents

Across 7 open models on the fault types its checks target, SilentGuard cuts wrong answers from
74.0% to 1.7%, with no loss on fault-free tasks. On three frontier models wrong answers fall from
70.2% to 0.5%. Checklists, reviewer models and LLM judges leave over 70% of answers wrong, because
agents report a flagged count anyway. SilentGuard prevents wrong answers by abstaining, not by
recovering them, and it cannot see a fault that keeps its checks true. The paper reports both.

## Reproducing the paper

The exact code, frozen by hash, is in [`paper/`](paper/README.md).

## Citation

```bibtex
@article{dip2026silentguard,
  title  = {Invariant-Aware Harness Enforcement to Protect LLM Agents from Silent API Failures},
  author = {Dip, Sajib Acharjee and Ouyang, Brian and Motlhabi, Lala},
  year   = {2026}
}
```
