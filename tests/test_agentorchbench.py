"""Tests for task loading and scoring (no services needed)."""
from agentorchbench import SUITES, load_tasks, score


def test_suites_load_with_answers():
    sizes = {name: len(load_tasks(name)) for name in SUITES}
    assert sizes == {"count": 36, "lists": 30, "retail": 30, "workflows": 100}
    for t in load_tasks("count") + load_tasks("retail"):
        assert isinstance(t.answer, int) and t.conditions


def test_scoring_matches_paper_grader():
    t = load_tasks("count")[0]
    assert score(t, f"ANSWER: count={t.answer}") == "correct"
    assert score(t, t.answer) == "correct"
    assert score(t, f"ANSWER: count={t.answer + 3}") == "wrong"
    assert score(t, "ANSWER: count cannot be determined") == "unknown"
    assert score(t, "") == "no_answer"


def test_list_scoring():
    t = load_tasks("lists")[0]
    assert score(t, "ids=" + ",".join(t.answer)) == "correct"
    assert score(t, "ids=" + ",".join(t.answer[:-1])) == "wrong"
