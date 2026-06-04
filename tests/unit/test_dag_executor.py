from __future__ import annotations

import asyncio
import pytest
from skill_engine.engine.dag_executor import (
    DAGExecutor,
    _group_by_level,
    _backoff_delay,
    _skip_downstream,
)
from skill_engine.models.skill import (
    SkillDefinition,
    StepDefinition,
    NodeStatus,
    Criteria,
    RetryPolicy,
)


def test_group_by_level_linear():
    s1 = StepDefinition(id="a", name="A", tool="echo", depends_on=[])
    s2 = StepDefinition(id="b", name="B", tool="echo", depends_on=["a"])
    s3 = StepDefinition(id="c", name="C", tool="echo", depends_on=["b"])
    steps = [s1, s2, s3]
    order = steps
    levels = _group_by_level(steps, order)
    assert len(levels) == 3
    assert [s.id for s in levels[0]] == ["a"]
    assert [s.id for s in levels[1]] == ["b"]
    assert [s.id for s in levels[2]] == ["c"]


def test_group_by_level_parallel():
    s1 = StepDefinition(id="a", name="A", tool="echo", depends_on=[])
    s2 = StepDefinition(id="b", name="B", tool="echo", depends_on=[])
    s3 = StepDefinition(id="c", name="C", tool="echo", depends_on=["a", "b"])
    steps = [s1, s2, s3]
    order = [s1, s2, s3]
    levels = _group_by_level(steps, order)
    assert len(levels) == 2
    assert len(levels[0]) == 2
    assert len(levels[1]) == 1


@pytest.mark.asyncio
async def test_execute_valid(tool_registry, sample_skill):
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(sample_skill, {"text": "hello"})
    assert result["status"] == "succeeded"
    assert result["output"]["s2"] == {"echoed": "hello"}


@pytest.mark.asyncio
async def test_execute_validation_failure(tool_registry, sample_skill):
    sample_skill.input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(sample_skill, {})
    assert result["status"] == "failed"
    assert "Input validation failed" in result["error"]


@pytest.mark.asyncio
async def test_execute_invalid_command(tool_registry):
    skill = SkillDefinition(
        id="bad",
        name="Bad",
        steps=[StepDefinition(id="s1", name="S1", tool="nonexistent_command_xyz", input_mapping={})],
    )
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(skill, {})
    assert result["status"] == "failed"
    assert "Command failed" in result["error"]


@pytest.mark.asyncio
async def test_execute_async_mode(tool_registry, sample_skill):
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(sample_skill, {"text": "async"}, sync=False)
    assert result["status"] == "running"
    assert result["run_id"]

    await asyncio.sleep(0.1)
    task = executor._running_tasks.get(result["run_id"])
    if task:
        await task


class TestBackoffDelay:
    def test_exponential_backoff(self):
        """Exponential backoff: base * 2^attempt."""
        retry = RetryPolicy(max_attempts=3, backoff="exponential", backoff_base_seconds=1.5)
        assert _backoff_delay(retry, 0) == 1.5   # 1.5 * 2^0
        assert _backoff_delay(retry, 1) == 3.0   # 1.5 * 2^1
        assert _backoff_delay(retry, 2) == 6.0   # 1.5 * 2^2

    def test_fixed_backoff(self):
        """Fixed backoff always returns base."""
        retry = RetryPolicy(max_attempts=3, backoff="fixed", backoff_base_seconds=0.5)
        assert _backoff_delay(retry, 0) == 0.5
        assert _backoff_delay(retry, 1) == 0.5
        assert _backoff_delay(retry, 5) == 0.5

    def test_none_backoff(self):
        """No backoff always returns 0.0."""
        retry = RetryPolicy(max_attempts=1, backoff="none")
        assert _backoff_delay(retry, 0) == 0.0
        assert _backoff_delay(retry, 10) == 0.0


class TestSkipDownstream:
    def test_skip_direct_child(self):
        """Failing step A skips its direct depending step B."""
        steps = [
            StepDefinition(id="a", name="A", depends_on=[], tool="echo", input_mapping={}),
            StepDefinition(id="b", name="B", depends_on=["a"], tool="echo", input_mapping={}),
        ]
        statuses = {"a": NodeStatus.FAILED, "b": NodeStatus.PENDING}
        _skip_downstream("a", steps, statuses)
        assert statuses["b"] == NodeStatus.SKIPPED

    def test_skip_transitive(self):
        """Failing step A skips B, which then skips C."""
        steps = [
            StepDefinition(id="a", name="A", depends_on=[], tool="echo", input_mapping={}),
            StepDefinition(id="b", name="B", depends_on=["a"], tool="echo", input_mapping={}),
            StepDefinition(id="c", name="C", depends_on=["b"], tool="echo", input_mapping={}),
        ]
        statuses = {"a": NodeStatus.FAILED, "b": NodeStatus.PENDING, "c": NodeStatus.PENDING}
        _skip_downstream("a", steps, statuses)
        assert statuses["b"] == NodeStatus.SKIPPED
        assert statuses["c"] == NodeStatus.SKIPPED

    def test_does_not_skip_succeeded(self):
        """Already succeeded steps are not affected."""
        steps = [
            StepDefinition(id="a", name="A", depends_on=[], tool="echo", input_mapping={}),
            StepDefinition(id="b", name="B", depends_on=["a"], tool="echo", input_mapping={}),
        ]
        statuses = {"a": NodeStatus.FAILED, "b": NodeStatus.SUCCEEDED}
        _skip_downstream("a", steps, statuses)
        assert statuses["b"] == NodeStatus.SUCCEEDED

    def test_does_not_skip_unrelated(self):
        """Steps not depending on the failed step are unaffected."""
        steps = [
            StepDefinition(id="a", name="A", depends_on=[], tool="echo", input_mapping={}),
            StepDefinition(id="b", name="B", depends_on=["a"], tool="echo", input_mapping={}),
            StepDefinition(id="c", name="C", depends_on=[], tool="echo", input_mapping={}),
        ]
        statuses = {"a": NodeStatus.FAILED, "b": NodeStatus.PENDING, "c": NodeStatus.PENDING}
        _skip_downstream("a", steps, statuses)
        assert statuses["b"] == NodeStatus.SKIPPED
        assert statuses["c"] == NodeStatus.PENDING  # Unrelated


class TestTopologicalOrder:
    def test_cycle_detection(self, tool_registry):
        """A cyclic dependency raises a DAG validation error."""
        s1 = StepDefinition(id="a", name="A", depends_on=["b"], tool="echo", input_mapping={})
        s2 = StepDefinition(id="b", name="B", depends_on=["a"], tool="echo", input_mapping={})
        skill = SkillDefinition(id="cycle", name="Cycle", steps=[s1, s2])
        executor = DAGExecutor(tool_registry)
        result = asyncio.run(executor.execute(skill, {}))
        assert result["status"] == "failed"
        assert "Cycle detected" in result["error"]

    def test_unknown_dependency(self, tool_registry):
        """Depending on a nonexistent step raises a DAG validation error."""
        s1 = StepDefinition(id="a", name="A", depends_on=["nonexistent"], tool="echo", input_mapping={})
        skill = SkillDefinition(id="bad-dep", name="Bad Dep", steps=[s1])
        executor = DAGExecutor(tool_registry)
        # validate() catches this
        errors = skill.validate()
        # validate() catches this in both the explicit check AND topological_order()
        assert len(errors) >= 1
        assert "unknown step" in errors[0].lower() or "nonexistent" in errors[0]
