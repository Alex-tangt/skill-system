from __future__ import annotations

import pytest
from skill_engine.engine.resolver import resolve_input


def test_resolve_input_reference():
    mapping = {"message": "$input.text"}
    result = resolve_input(mapping, {"text": "hello"}, {})
    assert result == {"message": "hello"}


def test_resolve_steps_reference():
    mapping = {"msg": "$steps.s1.output.echoed"}
    result = resolve_input(mapping, {}, {"s1": {"echoed": "world"}})
    assert result == {"msg": "world"}


def test_resolve_steps_full_output():
    mapping = {"data": "$steps.s1.output"}
    result = resolve_input(mapping, {}, {"s1": {"a": 1, "b": 2}})
    assert result == {"data": {"a": 1, "b": 2}}


def test_resolve_nested_input():
    mapping = {"target": "$input.deeply.nested.field"}
    result = resolve_input(mapping, {"deeply": {"nested": {"field": "found"}}}, {})
    assert result == {"target": "found"}


def test_resolve_missing_input_returns_none():
    mapping = {"x": "$input.missing"}
    result = resolve_input(mapping, {}, {})
    assert result == {"x": None}


def test_resolve_nonexistent_step_raises():
    mapping = {"x": "$steps.nonexistent.output"}
    with pytest.raises(KeyError, match="nonexistent"):
        resolve_input(mapping, {}, {})


def test_resolve_multiple_mappings():
    mapping = {
        "a": "$input.x",
        "b": "$steps.s1.output.y",
    }
    result = resolve_input(mapping, {"x": "X"}, {"s1": {"y": "Y"}})
    assert result == {"a": "X", "b": "Y"}


def test_string_interpolation():
    """Reference embedded in a larger string gets substituted."""
    mapping = {"msg": "Hello $input.name!"}
    result = resolve_input(mapping, {"name": "World"}, {})
    assert result == {"msg": "Hello World!"}


def test_multiple_refs_in_one_string():
    """Multiple references in a single string are all resolved."""
    mapping = {"full": "$input.first $input.last"}
    result = resolve_input(mapping, {"first": "John", "last": "Doe"}, {})
    assert result == {"full": "John Doe"}


def test_unicode_ref_values():
    """Unicode characters in reference values are preserved."""
    mapping = {"msg": "$input.text"}
    result = resolve_input(mapping, {"text": "你好世界"}, {})
    assert result == {"msg": "你好世界"}


def test_deep_missing_intermediate_key():
    """Missing intermediate key in nested path returns None."""
    mapping = {"x": "$input.a.b.c"}
    result = resolve_input(mapping, {"a": {"not_b": 1}}, {})
    assert result == {"x": None}


def test_non_string_template_passthrough():
    """Non-string mapping values are passed through unchanged."""
    mapping = {"count": 42}
    result = resolve_input(mapping, {}, {})
    assert result == {"count": 42}
