"""Run SilentGuard against every AgentOrchBench fault, with no model in the loop.

Starts the bio services locally, switches on each silent fault, and asks every count task once
with a plain call and once through SilentGuard. A plain call reports a wrong number under most
faults. SilentGuard either verifies the right number or withholds the value.

    python examples/02_benchmark_faults.py            # takes about a minute
"""
import os

from agentorchbench import CatalogClient, PAPER_FAULTS, load_tasks, start_services
from silentguard import Condition, SilentGuard

tasks = load_tasks("count")
print(f"{len(tasks)} count tasks\n")
print(f"{'fault':16s} {'plain wrong':>12s} {'guard wrong':>12s} {'guard withheld':>15s}")

with start_services("bio", worker=int(os.environ.get("AOB_WORKER", 0))) as svc:
    api = CatalogClient(svc)
    for fault in ["off"] + PAPER_FAULTS:
        api.set_fault(fault, catalog="large")
        plain_wrong = guard_wrong = withheld = 0
        for t in tasks:
            conds = [Condition(c["field"], c["value"]) for c in t.conditions]
            plain = api.count(conds)
            res = SilentGuard(api.count).count(conds)
            plain_wrong += plain != t.answer
            withheld += res.withheld
            guard_wrong += (not res.withheld) and res.value != t.answer
        n = len(tasks)
        print(f"{fault:16s} {plain_wrong / n:12.0%} {guard_wrong / n:12.0%} {withheld / n:15.0%}")
