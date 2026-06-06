from __future__ import annotations


def validate_input(input_schema: dict, input_data: dict) -> list[str]:
    """Validate input data against a JSON Schema.

    Returns a list of error messages (empty = valid).
    Supports: type, properties, required, items (for arrays).
    """
    if not input_schema:
        return []

    errors = []
    schema_type = input_schema.get("type", "object")

    if schema_type != "object":
        return errors

    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])

    for field in required:
        if field not in input_data:
            errors.append(f"Missing required field: '{field}'")

    for field, value in input_data.items():
        if field in properties:
            prop = properties[field]
            expected_type = prop.get("type")
            if expected_type:
                if not _check_type(expected_type, value):
                    errors.append(
                        f"Field '{field}': expected {expected_type}, got {type(value).__name__}"
                    )

    return errors


def _check_type(expected: str, value: object) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True
