from __future__ import annotations

from skill_engine.engine.criteria import evaluate_success, evaluate_failure
from skill_engine.models.skill import Criteria


def test_always_success():
    assert evaluate_success(Criteria(type="always"), None) is True


def test_exception_none_success():
    assert evaluate_success(Criteria(type="exception_none"), "any output") is True


def test_output_match_success():
    assert evaluate_success(Criteria(type="output_match", expected={"status": "ok"}), {"status": "ok"}) is True


def test_output_match_failure():
    assert evaluate_success(Criteria(type="output_match", expected={"status": "ok"}), {"status": "error"}) is False


def test_exception_failure():
    assert evaluate_failure(Criteria(type="exception"), "some error") is True


def test_timeout_failure():
    assert evaluate_failure(Criteria(type="timeout"), "Step timed out after 60s") is True
    assert evaluate_failure(Criteria(type="timeout"), "normal error") is False


def test_output_mismatch_failure_match():
    """output_mismatch matches when error contains 'criteria not met'."""
    assert evaluate_failure(Criteria(type="output_mismatch"), "criteria not met: expected X got Y") is True


def test_output_mismatch_failure_no_match():
    """output_mismatch does not match unrelated errors."""
    assert evaluate_failure(Criteria(type="output_mismatch"), "connection refused") is False


def test_output_match_with_none_expected():
    """output_match with None expected returns True."""
    assert evaluate_success(Criteria(type="output_match", expected=None), "anything") is True


def test_output_match_with_list_expected():
    """output_match with list expected checks exact equality."""
    assert evaluate_success(Criteria(type="output_match", expected=["a", "b"]), ["a", "b"]) is True
    assert evaluate_success(Criteria(type="output_match", expected=["a", "b"]), ["a"]) is False


def test_output_match_dict_partial():
    """output_match with dict expected performs partial (subset) match."""
    assert evaluate_success(Criteria(type="output_match", expected={"a": 1}), {"a": 1, "b": 2}) is True
