from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class CriteriaType(Enum):
    ALWAYS = "always"
    OUTPUT_MATCH = "output_match"
    EXCEPTION_NONE = "exception_none"


class FailureCriteriaType(Enum):
    EXCEPTION = "exception"
    OUTPUT_MISMATCH = "output_mismatch"
    TIMEOUT = "timeout"


@dataclass
class RetryPolicy:
    max_attempts: int = 1
    backoff: str = "none"  # "exponential" | "fixed" | "none"
    backoff_base_seconds: float = 1.0


@dataclass
class Criteria:
    type: str  # from CriteriaType or FailureCriteriaType
    path: str | None = None
    expected: Any = None


@dataclass
class StepDefinition:
    id: str
    name: str
    description: str = ""
    tool: str = ""
    depends_on: list[str] = field(default_factory=list)
    input_mapping: dict[str, str] = field(default_factory=dict)
    success_criteria: Criteria = field(default_factory=lambda: Criteria(type="always"))
    failure_criteria: Criteria | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: int = 60


@dataclass
class SkillDefinition:
    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    timeout_seconds: int = 300
    max_concurrency: int = 10
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    steps: list[StepDefinition] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors = []
        step_ids = {s.id for s in self.steps}
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    errors.append(f"Step '{step.id}' depends on unknown step '{dep}'")
        try:
            self.topological_order()
        except ValueError as e:
            errors.append(str(e))
        return errors

    def topological_order(self) -> list[StepDefinition]:
        """Kahn's algorithm. Raises ValueError on cycle."""
        in_degree: dict[str, int] = {s.id: 0 for s in self.steps}
        children: dict[str, list[str]] = {s.id: [] for s in self.steps}
        step_map: dict[str, StepDefinition] = {s.id: s for s in self.steps}

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in in_degree:
                    raise ValueError(f"Step '{step.id}' depends on unknown step '{dep}'")
                in_degree[step.id] += 1
                children[dep].append(step.id)

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        order = []

        while queue:
            sid = queue.pop(0)
            order.append(step_map[sid])
            for child in children[sid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(self.steps):
            raise ValueError("Cycle detected in step dependencies")

        return order

    def terminal_steps(self) -> list[str]:
        """Step IDs that no other step depends on."""
        has_dependent = {dep for s in self.steps for dep in s.depends_on}
        return [s.id for s in self.steps if s.id not in has_dependent]
