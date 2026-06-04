from __future__ import annotations

from skill_engine.models.skill import Criteria, CriteriaType, FailureCriteriaType


def evaluate_success(criteria: Criteria, output: object) -> bool:
    if criteria.type == CriteriaType.ALWAYS.value:
        return True
    if criteria.type == CriteriaType.EXCEPTION_NONE.value:
        return True
    if criteria.type == CriteriaType.OUTPUT_MATCH.value:
        if criteria.expected is None:
            return True
        if isinstance(criteria.expected, dict) and isinstance(output, dict):
            return all(output.get(k) == v for k, v in criteria.expected.items())
        return output == criteria.expected
    return True


def evaluate_failure(criteria: Criteria, error_context: str) -> bool:
    if criteria.type == FailureCriteriaType.EXCEPTION.value:
        return True
    if criteria.type == FailureCriteriaType.TIMEOUT.value:
        lower = error_context.lower()
        return "timeout" in lower or "timed out" in lower
    if criteria.type == FailureCriteriaType.OUTPUT_MISMATCH.value:
        return "criteria not met" in error_context.lower()
    return True
