"""An LLM agent on AgentOrchBench, with and without SilentGuard.

Works with any OpenAI-compatible endpoint. Set OPENAI_API_KEY (and OPENAI_BASE_URL for a local or
hosted open model), then:

    python examples/03_llm_agent.py --model gpt-4o-mini --fault drop_last --n 5

The agent gets one tool, ``count_records``. The script runs each task twice: once with a plain tool,
and once with the tool and the final answer going through SilentGuard.
"""
import argparse
import os
import json
import re

from openai import OpenAI

from agentorchbench import CatalogClient, load_tasks, score, start_services
from silentguard import SilentGuard

SYSTEM = ("You answer questions about a data catalog by calling the count_records tool. "
          "Finish with one line: ANSWER: count=<n>, or ANSWER: count cannot be determined.")


def run_task(client, model, task, api, guard_on, max_turns=8):
    guard = SilentGuard(api.count)
    plain_schema = guard.tool_schema()                 # same schema with or without the guard
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task.prompt}]
    for _ in range(max_turns):
        out = client.chat.completions.create(model=model, messages=msgs, tools=[plain_schema])
        m = out.choices[0].message
        msgs.append(m.model_dump(exclude_none=True))
        if m.tool_calls:
            for call in m.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                if guard_on:
                    obs = guard.call_tool(args)
                else:
                    obs = json.dumps({"count": api.count([dict(c) for c in args.get("conditions", [])])})
                msgs.append({"role": "tool", "tool_call_id": call.id, "content": obs})
            continue
        text = m.content or ""
        if not guard_on:
            return text
        found = re.search(r"count\s*=\s*(\d+)", text)
        decision = guard.check_answer(int(found.group(1)) if found else text)
        if decision.final:
            return f"ANSWER: count={decision.answer}"
        msgs.append({"role": "user", "content": decision.message})
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--fault", default="drop_last")
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()
    client = OpenAI()
    tasks = load_tasks("count")[: a.n]
    with start_services("bio", worker=int(os.environ.get("AOB_WORKER", 0))) as svc:
        api = CatalogClient(svc)
        api.set_fault(a.fault, catalog="large")
        for guard_on in (False, True):
            tally = {}
            for t in tasks:
                outcome = score(t, run_task(client, a.model, t, api, guard_on))
                tally[outcome] = tally.get(outcome, 0) + 1
            print(f"{'SilentGuard' if guard_on else 'no guard':12s} {tally}")


if __name__ == "__main__":
    main()
