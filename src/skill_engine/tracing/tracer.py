from __future__ import annotations

import uuid
import time
import asyncio

from skill_engine.models.trace import ExecutionTrace, StepTrace
from skill_engine.models.skill import SkillDefinition, StepDefinition
from skill_engine.storage.trace_store import TraceStore


class Tracer:
    def __init__(self, trace_store: TraceStore):
        self.store = trace_store

    async def start_trace(self, skill: SkillDefinition, input_data: dict) -> ExecutionTrace:
        trace = ExecutionTrace(
            id=str(uuid.uuid4()),
            skill_id=skill.id,
            skill_version=skill.version,
            run_id=str(uuid.uuid4()),
            input=input_data,
        )
        await self.store.insert_trace(trace)
        return trace

    def start_step_trace(self, trace: ExecutionTrace, step: StepDefinition) -> StepTrace:
        step_trace = StepTrace(
            id=str(uuid.uuid4()),
            trace_id=trace.id,
            step_id=step.id,
            step_name=step.name,
            started_at=time.time(),
        )
        trace.step_traces.append(step_trace)
        return step_trace

    async def finish_step_trace(self, step_trace: StepTrace) -> None:
        step_trace.finished_at = time.time()
        await self.store.upsert_step_trace(step_trace)

    async def finish_trace(self, trace: ExecutionTrace) -> None:
        trace.finished_at = time.time()
        await self.store.update_trace(trace)
