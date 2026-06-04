from __future__ import annotations

from skill_engine.engine.validator import validate_input


def test_valid_empty_schema():
    assert validate_input({}, {"anything": 1}) == []


def test_missing_required():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    errors = validate_input(schema, {})
    assert len(errors) == 1
    assert "name" in errors[0]


def test_type_mismatch():
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    errors = validate_input(schema, {"count": "not-a-number"})
    assert len(errors) == 1
    assert "count" in errors[0]


def test_valid_input():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["name"],
    }
    assert validate_input(schema, {"name": "test", "count": 42}) == []


def test_array_type_valid():
    """Array type accepts list values."""
    schema = {"type": "object", "properties": {"tags": {"type": "array"}}}
    assert validate_input(schema, {"tags": ["a", "b"]}) == []


def test_array_type_invalid():
    """Array type rejects non-list values."""
    schema = {"type": "object", "properties": {"tags": {"type": "array"}}}
    errors = validate_input(schema, {"tags": "not-a-list"})
    assert len(errors) == 1
    assert "tags" in errors[0]


def test_boolean_type():
    """Boolean type accepts True/False, rejects strings."""
    schema = {"type": "object", "properties": {"flag": {"type": "boolean"}}}
    assert validate_input(schema, {"flag": True}) == []
    assert validate_input(schema, {"flag": False}) == []
    errors = validate_input(schema, {"flag": "true"})
    assert len(errors) == 1


def test_number_type():
    """Number type accepts int and float."""
    schema = {"type": "object", "properties": {"val": {"type": "number"}}}
    assert validate_input(schema, {"val": 42}) == []
    assert validate_input(schema, {"val": 3.14}) == []


def test_nested_properties():
    """Properties inside nested objects are not validated (shallow only)."""
    schema = {
        "type": "object",
        "properties": {"outer": {"type": "object"}},
    }
    assert validate_input(schema, {"outer": {"inner": "value"}}) == []


def test_empty_required_list():
    """Empty required list produces no errors."""
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": []}
    assert validate_input(schema, {}) == []
