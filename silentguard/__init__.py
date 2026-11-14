"""SilentGuard: harness enforcement that protects LLM agents from silent API failures."""
from .guard import AnswerDecision, GuardedCount, SilentGuard, UNDETERMINED
from .invariants import (Condition, CountCheck, RecordCheck, check_count, check_params,
                         check_records)

__version__ = "0.1.0"
__all__ = ["SilentGuard", "GuardedCount", "AnswerDecision", "UNDETERMINED", "Condition",
           "CountCheck", "RecordCheck", "check_count", "check_records", "check_params"]
