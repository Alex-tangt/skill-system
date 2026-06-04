from __future__ import annotations

import asyncio
import time
import pytest
from skill_engine.engine.dag_executor import DAGExecutor
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria


@pytest.mark.slow
@pytest.mark.asyncio
async def test_many_parallel_steps(tool_registry):
    """50 parallel steps with max_concurrency=5 run without errors."""
    steps = []
    for i in range(50):
        steps.append(StepDefinition(
            id=f"s{i}", name=f"Step {i}", tool="echo",
            input_mapping={"message": f"$input.msg{i}"},
            success_criteria=Criteria(type="always"),
        ))
    skill = SkillDefinition(
        id="many-parallel",
        name="Many Parallel",
        max_concurrency=5,
        steps=steps,
    )
    # Build input with 50 fields
    input_data = {f"msg{i}": f"hello-{i}" for i in range(50)}
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(skill, input_data)
    assert result["status"] == "succeeded"
    assert len(result["output"]) >= 50


@pytest.mark.slow
@pytest.mark.asyncio
async def test_deep_dependency_chain(tool_registry):
    """20 sequential steps complete in order."""
    steps = []
    for i in range(20):
        depends = [f"s{i-1}"] if i > 0 else []
        steps.append(StepDefinition(
            id=f"s{i}", name=f"Step {i}", tool="echo",
            depends_on=depends,
            input_mapping={"message": "$input.text"},
            success_criteria=Criteria(type="always"),
        ))
    skill = SkillDefinition(
        id="deep-chain",
        name="Deep Chain",
        steps=steps,
    )
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(skill, {"text": "chain"})
    assert result["status"] == "succeeded"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_diamond_dependency(tool_registry):
    """Diamond: A→B, A→C, B+C→D."""
    skill = SkillDefinition(
        id="diamond",
        name="Diamond",
        steps=[
            StepDefinition(id="a", name="A", tool="echo",
                           input_mapping={"message": "$input.text"}),
            StepDefinition(id="b", name="B", tool="echo",
                           depends_on=["a"],
                           input_mapping={"message": "$steps.a.output.echoed"}),
            StepDefinition(id="c", name="C", tool="echo",
                           depends_on=["a"],
                           input_mapping={"message": "$steps.a.output.echoed"}),
            StepDefinition(id="d", name="D", tool="echo",
                           depends_on=["b", "c"],
                           input_mapping={"message": "$steps.b.output.echoed"}),
        ],
    )
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(skill, {"text": "diamond"})
    assert result["status"] == "succeeded"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_many_concurrent_executions(tool_registry):
    """10 independent skill executions run concurrently."""
    skill = SkillDefinition(
        id="concurrent-exec",
        name="Concurrent Exec",
        steps=[
            StepDefinition(id="s1", name="S1", tool="echo",
                           input_mapping={"message": "$input.text"}),
        ],
    )
    executor = DAGExecutor(tool_registry)

    async def run_one(i):
        return await executor.execute(skill, {"text": f"run-{i}"})

    tasks = [run_one(i) for i in range(10)]
    start = time.time()
    results = await asyncio.gather(*tasks)
    elapsed = time.time() - start

    assert all(r["status"] == "succeeded" for r in results)
    # Parallel execution should be faster than serial
    assert elapsed < 1.0, f"Concurrent executions took {elapsed:.2f}s"
