from __future__ import annotations

import pytest
from skill_engine.engine.dag_executor import DAGExecutor
from skill_engine.tracing.tracer import Tracer
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria, RetryPolicy


@pytest.mark.asyncio
async def test_full_trace_cycle(tool_registry, trace_store, sample_skill):
    tracer = Tracer(trace_store)
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(sample_skill, {"text": "integration"}, tracer=tracer)

    assert result["status"] == "succeeded"
    run_id = result["run_id"]

    trace = await trace_store.get_trace(run_id)
    assert trace is not None
    assert trace["status"] == "succeeded"
    assert len(trace["steps"]) == 2
    assert trace["steps"][0]["status"] == "succeeded"
    assert trace["steps"][1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_error_trace_capture(tool_registry, trace_store):
    tracer = Tracer(trace_store)
    executor = DAGExecutor(tool_registry)

    skill = SkillDefinition(
        id="failing",
        name="Failing Skill",
        steps=[
            StepDefinition(id="s1", name="S1", tool="nonexistent", input_mapping={}),
        ],
    )
    result = await executor.execute(skill, {}, tracer=tracer)
    assert result["status"] == "failed"

    errors = await trace_store.get_error_traces(skill_id="failing")
    assert len(errors) >= 1
    assert errors[0]["step_name"] == "S1"


@pytest.mark.asyncio
async def test_retry_and_backoff(tool_registry, trace_store):
    tracer = Tracer(trace_store)
    executor = DAGExecutor(tool_registry)

    skill = SkillDefinition(
        id="retry-test",
        name="Retry Test",
        steps=[
            StepDefinition(
                id="s1",
                name="S1",
                tool="echo",
                input_mapping={"message": "$input.x"},
                success_criteria=Criteria(type="output_match", expected={"echoed": "impossible"}),
                failure_criteria=Criteria(type="exception"),
                retry=RetryPolicy(max_attempts=3, backoff="fixed", backoff_base_seconds=0.01),
            ),
        ],
    )
    result = await executor.execute(skill, {"x": "hello"}, tracer=tracer)
    assert result["status"] == "failed"

    trace = await trace_store.get_trace(result["run_id"])
    assert trace is not None
    assert trace["steps"][0]["retry_count"] >= 2


@pytest.mark.asyncio
async def test_parallel_execution(tool_registry, trace_store):
    tracer = Tracer(trace_store)
    executor = DAGExecutor(tool_registry)

    skill = SkillDefinition(
        id="parallel-test",
        name="Parallel Test",
        steps=[
            StepDefinition(id="a", name="A", tool="echo", input_mapping={"message": "$input.x"}),
            StepDefinition(id="b", name="B", tool="echo", input_mapping={"message": "$input.y"}),
            StepDefinition(id="c", name="C", tool="echo", depends_on=["a", "b"],
                           input_mapping={"message": "$steps.a.output.echoed"}),
        ],
    )
    result = await executor.execute(skill, {"x": "foo", "y": "bar"}, tracer=tracer)
    assert result["status"] == "succeeded"
