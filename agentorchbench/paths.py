"""Where the benchmark files live. The services and tasks are the exact files used in the paper."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAPER_CODE = REPO / "paper" / "code"
APIS = PAPER_CODE / "agentorchbench" / "apis"
TASKS = PAPER_CODE / "benchmark" / "tasks"
