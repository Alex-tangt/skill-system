from __future__ import annotations

import asyncio
import os
import tempfile
import pytest
from skill_engine.optimizer.agent import OptimizerAgent
from skill_engine.optimizer.analyzer import TraceAnalyzer, OptimizationRecommendation
from skill_engine.storage.trace_store import TraceStore
from skill_engine.storage.skill_store import SkillStore
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria, RetryPolicy


@pytest.fixture
def agent_skill_store():
    with tempfile.TemporaryDirectory() as d:
        yield SkillStore(d)


@pytest.fixture
def agent_trace_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = TraceStore(path)
    asyncio.run(store.initialize())
    yield store
    if os.path.exists(path):
        os.unlink(path)


def _make_test_skill():
    return SkillDefinition(
        id="test-skill",
        name="Test Skill",
        version="1.0.0",
        steps=[
            StepDefinition(
                id="s1",
                name="Step 1",
                tool="echo",
                input_mapping={"message": "$input.text"},
                success_criteria=Criteria(type="always"),
                failure_criteria=Criteria(type="exception"),
                retry=RetryPolicy(max_attempts=1, backoff="none", backoff_base_seconds=1.0),
                timeout_seconds=60,
            ),
        ],
    )


def _make_recommendation(rec_id="rec-001", skill_id="test-skill", rec_type="timeout",
                         severity="medium", affected_step_ids=None, suggested_change=None,
                         confidence=0.8):
    return OptimizationRecommendation(
        id=rec_id,
        skill_id=skill_id,
        type=rec_type,
        severity=severity,
        description="Test recommendation",
        affected_step_ids=affected_step_ids or ["s1"],
        suggested_change=suggested_change or {"timeout_seconds": 120},
        confidence=confidence,
        evidence={"test": True},
    )


class TestOptimizerAgent:
    def test_get_recommendations_empty(self, agent_trace_store, agent_skill_store):
        """No recommendations before analyze."""
        analyzer = TraceAnalyzer(agent_trace_store)
        agent = OptimizerAgent(agent_trace_store, agent_skill_store, analyzer)
        recs = agent.get_recommendations()
        assert recs == []

    def test_get_recommendations_filtered_by_skill_id(self, agent_trace_store, agent_skill_store):
        """get_recommendations filters by skill_id."""
        analyzer = TraceAnalyzer(agent_trace_store)
        agent = OptimizerAgent(agent_trace_store, agent_skill_store, analyzer)
        agent._recommendations = {
            "rec-1": _make_recommendation("rec-1", skill_id="skill-a"),
            "rec-2": _make_recommendation("rec-2", skill_id="skill-b"),
        }
        recs = agent.get_recommendations(skill_id="skill-a")
        assert len(recs) == 1
        assert recs[0].skill_id == "skill-a"

    def test_get_recommendations_excludes_applied(self, agent_trace_store, agent_skill_store):
        """Applied recommendations are excluded."""
        analyzer = TraceAnalyzer(agent_trace_store)
        agent = OptimizerAgent(agent_trace_store, agent_skill_store, analyzer)
        rec = _make_recommendation("rec-1")
        rec.applied = True
        agent._recommendations = {"rec-1": rec}
        recs = agent.get_recommendations()
        assert recs == []

    @pytest.mark.asyncio
    async def test_apply_timeout(self, agent_trace_store, agent_skill_store):
        """Applying a timeout recommendation updates the step and bumps version."""
        skill = _make_test_skill()
        agent_skill_store.save(skill)
        analyzer = TraceAnalyzer(agent_trace_store)
        agent = OptimizerAgent(agent_trace_store, agent_skill_store, analyzer)
        rec = _make_recommendation(
            "rec-timeout", skill_id="test-skill", rec_type="timeout",
            suggested_change={"timeout_seconds": 120},
        )
        agent._recommendations = {rec.id: rec}
        result = await agent.apply("rec-timeout")
        assert result["status"] == "applied"
        # Verify skill was updated
        updated = agent_skill_store.get("test-skill")
        assert updated.version == "1.0.1"
        assert updated.steps[0].timeout_seconds == 120
        assert rec.applied is True

    @pytest.mark.asyncio
    async def test_apply_retry_policy(self, agent_trace_store, agent_skill_store):
        """Applying a retry_policy recommendation updates retry settings."""
        skill = _make_test_skill()
        agent_skill_store.save(skill)
        analyzer = TraceAnalyzer(agent_trace_store)
        agent = OptimizerAgent(agent_trace_store, agent_skill_store, analyzer)
        rec = _make_recommendation(
            "rec-retry", skill_id="test-skill", rec_type="retry_policy",
            suggested_change={
                "retry": {"max_attempts": 3, "backoff": "exponential", "backoff_base_seconds": 2},
            },
        )
        agent._recommendations = {rec.id: rec}
        result = await agent.apply("rec-retry")
        assert result["status"] == "applied"
        updated = agent_skill_store.get("test-skill")
        assert updated.steps[0].retry.max_attempts == 3
        assert updated.steps[0].retry.backoff == "exponential"

    @pytest.mark.asyncio
    async def test_apply_unknown_recommendation(self, agent_trace_store, agent_skill_store):
        """Applying an unknown recommendation returns an error."""
        analyzer = TraceAnalyzer(agent_trace_store)
        agent = OptimizerAgent(agent_trace_store, agent_skill_store, analyzer)
        result = await agent.apply("nonexistent-id")
        assert "error" in result

    def test_last_scan_updated_after_analyze(self, agent_trace_store, agent_skill_store):
        """analyze() updates last_scan timestamp."""
        analyzer = TraceAnalyzer(agent_trace_store)
        agent = OptimizerAgent(agent_trace_store, agent_skill_store, analyzer)
        assert agent.last_scan is None
        asyncio.run(agent.analyze(skill_id="nonexistent", min_samples=100))
        assert agent.last_scan is not None
