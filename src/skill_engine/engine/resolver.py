from __future__ import annotations

import re
from functools import reduce

# Matches $input.x.y or $steps.<id>.output or $steps.<id>.output.x.y
REF_PATTERN = re.compile(
    r'\$input\.([\w.]+)'
    r'|\$steps\.(\w+)\.output(?:\.([\w.]+))?'
)


def resolve_input(
    mapping: dict[str, str],
    skill_input: dict,
    step_outputs: dict[str, object],
) -> dict:
    """Resolve input_mapping templates against actual data.

    Supported references:
      $input.field.path       — access skill input
      $steps.<id>.output      — access full output of upstream step
      $steps.<id>.output.path — access nested field of upstream output

    Short $steps.field (without step id) is NOT supported.
    """
    resolved = {}
    for key, template in mapping.items():
        resolved[key] = _resolve_value(template, skill_input, step_outputs)
    return resolved


def _resolve_value(template: str, skill_input: dict, step_outputs: dict[str, object]) -> object:
    if not isinstance(template, str):
        return template

    stripped = template.strip()
    match = REF_PATTERN.fullmatch(stripped)
    if match:
        input_path, step_id, output_path = match.groups()
        if input_path:
            return _get_nested(skill_input, input_path)
        elif step_id:
            if step_id not in step_outputs:
                raise KeyError(
                    f"Reference '$steps.{step_id}.output': step '{step_id}' has no output yet. "
                    f"Available outputs: {list(step_outputs.keys())}"
                )
            output = step_outputs[step_id]
            if output_path:
                return _get_nested(output, output_path)
            return output

    # String interpolation: replace each reference inline
    result = template
    for m in REF_PATTERN.finditer(template):
        full = m.group(0)
        input_path, step_id, output_path = m.groups()
        if input_path:
            val = _get_nested(skill_input, input_path)
        elif step_id:
            output = step_outputs.get(step_id)
            if output_path and output is not None:
                val = _get_nested(output, output_path)
            else:
                val = output
        else:
            val = None
        if isinstance(val, str):
            result = result.replace(full, val, 1)
        elif m.start() == 0 and m.end() == len(template.strip()):
            return val
    return result


def _get_nested(data: object, path: str) -> object:
    """Access nested dict by dot-separated path."""
    if data is None:
        return None
    keys = path.split(".")
    try:
        return reduce(lambda d, k: d[k] if isinstance(d, dict) else getattr(d, k, None), keys, data)
    except (KeyError, TypeError, AttributeError):
        return None
