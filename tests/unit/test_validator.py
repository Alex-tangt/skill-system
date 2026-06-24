from __future__ import annotations


def validate_input(schema: dict, data: dict) -> list[str]:
    """Simple JSON Schema input validator (standalone, no external deps)."""
    errors: list[str] = []
    props = schema.get("properties", {})

    for field, field_schema in props.items():
        expected_type = field_schema.get("type")
        if field not in data:
            if field in schema.get("required", []):
                errors.append(f"Missing required field: {field}")
            continue
        value = data[field]
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"Field '{field}' must be a string")
        elif expected_type == "integer" and not isinstance(value, int):
            errors.append(f"Field '{field}' must be an integer")
        elif expected_type == "number" and not isinstance(value, (int, float)):
            errors.append(f"Field '{field}' must be a number")
        elif expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"Field '{field}' must be a boolean")
        elif expected_type == "array" and not isinstance(value, list):
            errors.append(f"Field '{field}' must be an array")
    return errors


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
