"""AgentOrchBench: a benchmark of silent API failures for LLM agents.

Local services with synthetic data, tasks with exact answers, and switchable silent faults.
A faulted service still answers HTTP 200 with a plausible result, so nothing in the response
shows the fault.

    from agentorchbench import load_tasks, start_services, CatalogClient, score
"""
from .client import CatalogClient
from .faults import CHECK_FIRES, FAULTS, PAPER_FAULTS
from .scoring import score
from .services import Services, start_services
from .tasks import SUITES, Task, load_tasks

__version__ = "0.1.0"
__all__ = ["load_tasks", "Task", "SUITES", "FAULTS", "PAPER_FAULTS", "CHECK_FIRES",
           "start_services", "Services", "CatalogClient", "score"]
