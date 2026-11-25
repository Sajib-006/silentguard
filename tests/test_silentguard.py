"""Tests for the SilentGuard library on an in-memory API. No network needed."""
import itertools

import pytest

from silentguard import Condition, SilentGuard, check_count, check_params, check_records

RECORDS = [{"id": f"r{i}", "a": a, "b": b, "c": c}
           for i, (a, b, c) in enumerate(list(itertools.product("xyz", "uv", "pq")) * 3)]


def api(fault=None):
    calls = {"n": 0}

    def count(conds):
        calls["n"] += 1
        conds = list(conds)
        if fault == "drop_last" and conds:
            conds = conds[:-1]
        if fault == "ignore_b":
            conds = [c for c in conds if c.field != "b"]
        n = sum(all(c.matches(r) for c in conds) for r in RECORDS)
        if fault == "off_by_one":
            n += 1
        if fault == "flaky" and calls["n"] == 1:
            n += 7                                  # wrong only on the very first call
        return n
    return count


Q = [Condition("a", "x"), Condition("b", "u")]
TRUTH = api()(Q)


def test_identity_holds_on_correct_api():
    chk = check_count(api(), Q, TRUTH)
    assert chk.passed and chk.extra_calls == 4


@pytest.mark.parametrize("fault", ["drop_last", "ignore_b", "off_by_one"])
def test_identity_breaks_on_silent_faults(fault):
    f = api(fault)
    chk = check_count(f, Q, f(Q))
    assert chk.broken and chk.failed


@pytest.mark.parametrize("fault", ["drop_last", "ignore_b", "off_by_one"])
def test_guard_withholds_and_gate_rejects(fault):
    f = api(fault)
    g = SilentGuard(f)
    res = g.count(Q)
    assert res.withheld and res.value is None
    assert "withheld" in res.observation()
    assert not g.check_answer(f(Q)).accepted


def test_guard_verifies_correct_api_and_accepts_answer():
    g = SilentGuard(api())
    res = g.count(Q)
    assert res.status == "verified" and res.value == TRUTH
    assert g.check_answer(TRUTH).accepted


def test_recheck_recovers_one_time_fault():
    res = SilentGuard(api("flaky")).count(Q)
    assert res.status == "verified_on_recheck" and res.value == TRUTH


def test_gate_answers_undetermined_after_budget():
    g = SilentGuard(api(), max_rejections=2)
    assert not g.check_answer(999).final
    assert not g.check_answer(998).final
    d = g.check_answer(997)
    assert d.final and d.answer == "cannot be determined"


def test_learned_check_only_warns():
    g = SilentGuard(api(), learned_checks=[lambda conds, v: "looks unusual"])
    res = g.count([])                                # exact check cannot run on no conditions
    assert res.status == "warned" and res.value == len(RECORDS)


def test_record_and_param_checks():
    good = [r for r in RECORDS if all(c.matches(r) for c in Q)]
    assert check_records(good, Q, reported_total=len(good)).consistent
    bad = good + [next(r for r in RECORDS if r["a"] != "x")]
    assert not check_records(bad, Q).consistent
    assert check_params(["status", "tisue"], ["status", "tissue"]) == ["tisue"]


def test_gate_rejects_count_of_a_looser_query():
    g = SilentGuard(api())
    loose = g.count([Condition("a", "x")])          # passes the check, but drops a condition
    full = g.count(Q)
    assert loose.status == "verified" and full.status == "verified"
    assert not g.check_answer(loose.value).accepted
    assert g.check_answer(full.value).accepted
