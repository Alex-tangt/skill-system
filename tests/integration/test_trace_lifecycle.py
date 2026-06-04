from __future__ import annotations

import asyncio
import os
import tempfile
import pytest
from skill_engine.storage.trace_store import TraceStore
from skill_engine.tracing.tracer import Tracer
from skill_engine.engine.dag_executor import DAGExecutor
from skill_engine.models.trace import ExecutionTrace, StepTrace
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria


class TestTraceLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle_succeeded(self, tool_registry, trace_store):
        """Complete lifecycle: insert → execute → update → retrieve."""
        tracer = Tracer(trace_store)
        skill = SkillDefinition(
            id="lifecycle",
            name="Lifecycle Test",
            steps=[
                StepDefinition(id="s1", name="S1", tool="echo",
                               input_mapping={"message": "$input.msg"},
                               success_criteria=Criteria(type="always")),
            ],
        )
        executor = DAGExecutor(tool_registry)
        result = await executor.execute(skill, {"msg": "hello"}, tracer=tracer)
        assert result["status"] == "succeeded"

        trace = await trace_store.get_trace(result["run_id"])
        assert trace is not None
        assert trace["status"] == "succeeded"
        assert trace["skill_id"] == "lifecycle"
        assert trace["finished_at"] is not None
        assert len(trace["steps"]) == 1
        step = trace["steps"][0]
        assert step["status"] == "succeeded"
        assert step["finished_at"] is not None

    @pytest.mark.asyncio
    async def test_failed_lifecycle(self, tool_registry, trace_store):
        """Failed execution is recorded correctly in traces."""
        tracer = Tracer(trace_store)
        skill = SkillDefinition(
            id="fail-lifecycle",
            name="Fail Lifecycle",
            steps=[
                StepDefinition(id="s1", name="S1", tool="nonexistent_command_xyz",
                               input_mapping={}),
            ],
        )
        executor = DAGExecutor(tool_registry)
        result = await executor.execute(skill, {}, tracer=tracer)
        assert result["status"] == "failed"

        trace = await trace_store.get_trace(result["run_id"])
        assert trace["status"] == "failed"
        assert trace["error"] is not None

    @pytest.mark.asyncio
    async def test_list_traces_pagination(self, tool_registry, trace_store):
        """Insert multiple traces and verify pagination works."""
        tracer = Tracer(trace_store)
        executor = DAGExecutor(tool_registry)

        skill = SkillDefinition(
            id="page-test",
            name="Page Test",
            steps=[
                StepDefinition(id="s1", name="S1", tool="echo",
                               input_mapping={"message": "$input.msg"},
                               success_criteria=Criteria(type="always")),
            ],
        )
        for i in range(5):
            await executor.execute(skill, {"msg": f"run-{i}"}, tracer=tracer)

        traces = await trace_store.list_traces(limit=3)
        assert len(traces) == 3

    @pytest.mark.asyncio
    async def test_step_trace_upsert(self, trace_store):
        """Step trace upsert: insert then update."""
        # Simulate what DAG executor does
        trace = ExecutionTrace(
            id="trace-upsert-test",
            skill_id="upsert-skill",
            skill_version="1.0.0",
            run_id="upsert-run",
            input={"x": 1},
        )
        await trace_store.insert_trace(trace)

        # Insert step trace
        st = StepTrace(
            id="st-upsert",
            trace_id="trace-upsert-test",
            step_id="s1",
            step_name="Step 1",
            started_at=1000.0,
            status="running",
        )
        await trace_store.upsert_step_trace(st)

        # Update step trace
        st.status = "succeeded"
        st.finished_at = 1001.0
        st.output = {"result": "ok"}
        await trace_store.upsert_step_trace(st)

        full = await trace_store.get_trace("upsert-run")
        assert len(full["steps"]) == 1
        assert full["steps"][0]["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_trace_with_multiple_steps(self, tool_registry, trace_store):
        """Multi-step trace: all step traces returned."""
        tracer = Tracer(trace_store)
        skill = SkillDefinition(
            id="multi-lifecycle",
            name="Multi Lifecycle",
            steps=[
                StepDefinition(id="a", name="A", tool="echo",
                               input_mapping={"message": "$input.text"}),
                StepDefinition(id="b", name="B", tool="echo",
                               depends_on=["a"],
                               input_mapping={"message": "$steps.a.output.echoed"}),
            ],
        )
        executor = DAGExecutor(tool_registry)
        result = await executor.execute(skill, {"text": "multi"}, tracer=tracer)
        assert result["status"] == "succeeded"

        trace = await trace_store.get_trace(result["run_id"])
        assert len(trace["steps"]) == 2
        assert trace["steps"][0]["step_id"] == "a"
        assert trace["steps"][1]["step_id"] == "b"
        # Both should have input_json
        assert trace["steps"][0]["input_json"] is not None
        assert trace["steps"][1]["input_json"] is not None

    @pytest.mark.asyncio
    async def test_error_trace_retrieval(self, tool_registry, trace_store):
        """get_error_traces returns failed steps with error details."""
        tracer = Tracer(trace_store)
        skill = SkillDefinition(
            id="error-trace-skill",
            name="Error Trace Skill",
            steps=[
                StepDefinition(id="s1", name="S1", tool="echo",
                               input_mapping={"message": "$input.text"},
                               success_criteria=Criteria(type="output_match", expected={"echoed": "impossible"}),
                               retry=skill_engine_retry()),
            ],
        )
        from skill_engine.models.skill import RetryPolicy
        skill.steps[0].retry = RetryPolicy(max_attempts=2, backoff="fixed", backoff_base_seconds=0.01)

        executor = DAGExecutor(tool_registry)
        result = await executor.execute(skill, {"text": "test"}, tracer=tracer)
        assert result["status"] == "failed"

        errors = await trace_store.get_error_traces(skill_id="error-trace-skill")
        assert len(errors) >= 1
        assert errors[0]["step_name"] == "S1"
        assert errors[0]["retry_count"] >= 1


def skill_engine_retry():
    """Helper to avoid circular import issues."""
    from skill_engine.models.skill import RetryPolicy
    return RetryPolicy(max_attempts=1)
