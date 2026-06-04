from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class StepTrace:
    id: str
    trace_id: str
    step_id: str
    step_name: str
    started_at: float
    finished_at: float | None = None
    status: str = "pending"
    input: dict | None = None
    output: Any = None
    error: str | None = None
    retry_count: int = 0

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at) * 1000)


@dataclass
class ExecutionTrace:
    id: str
    skill_id: str
    skill_version: str
    run_id: str
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "running"
    input: dict = field(default_factory=dict)
    output: dict | None = None
    error: str | None = None
    parent_run_id: str | None = None
    step_traces: list[StepTrace] = field(default_factory=list)
