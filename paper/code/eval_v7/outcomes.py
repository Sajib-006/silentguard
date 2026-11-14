"""One definition of what a run's error means, shared by every analysis script.

Getting this wrong silently biases scores, which happened: analysis dropped every run with a
non-abort error, so phi-4's 99 protocol violations were removed instead of counted as wrong,
inflating its score.

Three classes:
  MODEL    the model's own behaviour ended the run — a real outcome, always counted, scored wrong
  HARNESS  our code failed — invalid data; must be re-run, never scored
  PROVIDER the API never let the model answer (credit, quota, transport after retries) — excluded
"""
from __future__ import annotations

MODEL_ERRORS = (
    "aborted",                            # no-progress stop
    "protocol violation",                 # never produced RUN:/ANSWER:
    "empty replies",
    "requested tools under condition F",
)
MODEL_ERROR_SUBSTRINGS = (
    "Input too long",                     # model context window exceeded
    "context length", "maximum context",
)
HARNESS_ERROR_PREFIXES = (
    "ValueError", "UnicodeEncodeError", "UnicodeError", "KeyError", "TypeError", "AttributeError",
)


def classify(row: dict) -> str:
    """'ok' (no error), 'model', 'harness', or 'provider'."""
    e = (row.get("error") or "").strip()
    if not e:
        return "ok"
    if e.startswith(MODEL_ERRORS) or any(s in e for s in MODEL_ERROR_SUBSTRINGS):
        return "model"
    if e.startswith(HARNESS_ERROR_PREFIXES):
        return "harness"
    return "provider"


def scoreable(row: dict) -> bool:
    """Rows that belong in a score: clean runs and model-caused failures (scored as wrong)."""
    return classify(row) in ("ok", "model")
