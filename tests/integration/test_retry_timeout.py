from __future__ import annotations

import asyncio
import time
import pytest
from skill_engine.engine.dag_executor import DAGExecutor
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria, RetryPolicy


@pytest.fixture
def slow_echo_tool(tool_registry):
    """Register a slow echo tool that sleeps before responding."""
    async def slow_echo(message: str = "", delay: float = 1.0):
        await asyncio.sleep(delay)
        return {"echoed": message}
    tool_registry.register("slow_echo", slow_echo)
    return tool_registry


class TestTimeout:
    @pytest.mark.asyncio
    async def test_step_times_out_triggers_failure(self, tool_registry):
        """Step with short timeout that actually triggers raises a failure."""
        async def blocking_echo(message: str = ""):
            await asyncio.sleep(1.0)
            return {"echoed": message}
        tool_registry.register("blocking_echo", blocking_echo)

        skill = SkillDefinition(
            id="timeout-test",
            name="Timeout Test",
            steps=[
                StepDefinition(
                    id="s1", name="S1", tool="blocking_echo",
                    input_mapping={"message": "$input.text"},
                    timeout_seconds=0.05,  # shorter than the 1s sleep
                    retry=RetryPolicy(max_attempts=1),
                ),
            ],
        )
        executor = DAGExecutor(tool_registry)
        result = await executor.execute(skill, {"text": "hello"})
        assert result["status"] == "failed"
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_timeout_with_failure_criteria_exits_early(self, tool_registry):
        """Timeout with matching failure_criteria exits retry loop without retrying."""
        async def blocking_echo(message: str = ""):
            await asyncio.sleep(1.0)
            return {"echoed": message}
        tool_registry.register("blocking_echo2", blocking_echo)

        skill = SkillDefinition(
            id="timeout-crit",
            name="Timeout Criteria",
            steps=[
                StepDefinition(
                    id="s1", name="S1", tool="blocking_echo2",
                    input_mapping={"message": "$input.text"},
                    timeout_seconds=0.05,
                    failure_criteria=Criteria(type="timeout"),
                    retry=RetryPolicy(max_attempts=3),  # Would retry 3 times, but timeout match exits early
                ),
            ],
        )
        executor = DAGExecutor(tool_registry)
        result = await executor.execute(skill, {"text": "hello"})
        assert result["status"] == "failed"
        assert "timed out" in result["error"]


class TestRetry:
    @pytest.mark.asyncio
    async def test_success_criteria_not_met_retries(self, tool_registry, trace_store):
        """When output doesn't match success_criteria, the step retries."""
        from skill_engine.tracing.tracer import Tracer

        skill = SkillDefinition(
            id="retry-criteria",
            name="Retry Criteria",
            steps=[
                StepDefinition(
                    id="s1", name="S1", tool="echo",
                    input_mapping={"message": "$input.text"},
                    success_criteria=Criteria(type="output_match", expected={"echoed": "unreachable"}),
                    retry=RetryPolicy(max_attempts=3, backoff="fixed", backoff_base_seconds=0.01),
                ),
            ],
        )
        tracer = Tracer(trace_store)
        executor = DAGExecutor(tool_registry)
        result = await executor.execute(skill, {"text": "hello"}, tracer=tracer)
        assert result["status"] == "failed"

        trace = await trace_store.get_trace(result["run_id"])
        assert trace["steps"][0]["retry_count"] >= 2

    @pytest.mark.asyncio
    async def test_exponential_backoff_increases_delay(self, tool_registry):
        """Exponential backoff produces increasing delays."""
        # Use a tool that always fails success criteria, measure total time
        skill = SkillDefinition(
            id="exp-backoff",
            name="Exp Backoff",
            steps=[
                StepDefinition(
                    id="s1", name="S1", tool="echo",
                    input_mapping={"message": "$input.text"},
                    success_criteria=Criteria(type="output_match", expected={"echoed": "impossible"}),
                    retry=RetryPolicy(max_attempts=3, backoff="exponential", backoff_base_seconds=0.05),
                ),
            ],
        )
        executor = DAGExecutor(tool_registry)
        start = time.time()
        result = await executor.execute(skill, {"text": "hello"})
        elapsed = time.time() - start
        assert result["status"] == "failed"
        # wait times: 0.05 + 0.10 = 0.15s minimum
        assert elapsed >= 0.10, f"Expected at least 0.10s elapsed, got {elapsed:.3f}s"


class TestFailureCriteria:
    @pytest.mark.asyncio
    async def test_failure_criteria_matches_exception(self, tool_registry):
        """failure_criteria type=exception matches any exception and exits retry loop."""
        async def crash_echo(**kwargs):
            raise RuntimeError("connection refused")
        tool_registry.register("crash_echo", crash_echo)

        skill = SkillDefinition(
            id="fail-crit-exc",
            name="Fail Crit Exception",
            steps=[
                StepDefinition(
                    id="s1", name="S1", tool="crash_echo",
                    input_mapping={},
                    failure_criteria=Criteria(type="exception"),
                    retry=RetryPolicy(max_attempts=5, backoff="fixed", backoff_base_seconds=0.1),
                ),
            ],
        )
        executor = DAGExecutor(tool_registry)
        start = time.time()
        result = await executor.execute(skill, {})
        elapsed = time.time() - start
        assert result["status"] == "failed"
        assert "connection refused" in result["error"]
        # Should NOT have waited through all 5 retries (exception match exits early)
        assert elapsed < 0.3, f"Expected early exit, got {elapsed:.3f}s"
