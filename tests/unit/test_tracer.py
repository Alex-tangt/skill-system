from __future__ import annotations

import asyncio
import pytest
from skill_engine.tracing.tracer import Tracer
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria, RetryPolicy


@pytest.fixture
def sample_skill_for_tracer():
    return SkillDefinition(
        id="tracer-test-skill",
        name="Tracer Test Skill",
        steps=[
            StepDefinition(
                id="step1",
                name="Step 1",
                tool="echo",
                input_mapping={"message": "$input.text"},
                success_criteria=Criteria(type="always"),
            ),
        ],
    )


class TestTracer:
    def test_start_trace_creates_trace(self, trace_store, sample_skill_for_tracer):
        """start_trace inserts a trace with correct skill metadata."""
        tracer = Tracer(trace_store)
        trace = asyncio.run(
            tracer.start_trace(sample_skill_for_tracer, {"text": "hello"})
        )
        assert trace.skill_id == "tracer-test-skill"
        assert trace.skill_version == "1.0.0"
        assert trace.status == "running"
        assert trace.input == {"text": "hello"}
        assert trace.run_id is not None
        assert len(trace.run_id) > 0

    def test_start_trace_generates_unique_run_ids(self, trace_store, sample_skill_for_tracer):
        """Each start_trace call generates a different run_id."""
        tracer = Tracer(trace_store)
        trace1 = asyncio.run(
            tracer.start_trace(sample_skill_for_tracer, {})
        )
        trace2 = asyncio.run(
            tracer.start_trace(sample_skill_for_tracer, {})
        )
        assert trace1.run_id != trace2.run_id
        assert trace1.id != trace2.id

    def test_start_step_trace_appends_to_trace(self, trace_store, sample_skill_for_tracer):
        """start_step_trace creates a StepTrace and appends it to the trace."""
        tracer = Tracer(trace_store)
        trace = asyncio.run(
            tracer.start_trace(sample_skill_for_tracer, {})
        )
        step = sample_skill_for_tracer.steps[0]
        step_trace = tracer.start_step_trace(trace, step)
        assert step_trace.step_id == "step1"
        assert step_trace.step_name == "Step 1"
        assert step_trace.trace_id == trace.id
        assert step_trace.status == "pending"
        assert step_trace in trace.step_traces

    def test_finish_step_trace_sets_finished_at(self, trace_store, sample_skill_for_tracer):
        """finish_step_trace sets finished_at and persists to store."""
        tracer = Tracer(trace_store)
        trace = asyncio.run(
            tracer.start_trace(sample_skill_for_tracer, {})
        )
        step = sample_skill_for_tracer.steps[0]
        step_trace = tracer.start_step_trace(trace, step)
        step_trace.status = "succeeded"
        step_trace.output = {"echoed": "hello"}
        asyncio.run(tracer.finish_step_trace(step_trace))
        assert step_trace.finished_at is not None

    def test_finish_trace_sets_finished_at_and_status(self, trace_store, sample_skill_for_tracer):
        """finish_trace sets finished_at, status, and output on the trace."""
        tracer = Tracer(trace_store)
        trace = asyncio.run(
            tracer.start_trace(sample_skill_for_tracer, {})
        )
        trace.status = "succeeded"
        trace.output = {"result": "ok"}
        asyncio.run(tracer.finish_trace(trace))
        assert trace.finished_at is not None
        assert trace.status == "succeeded"

    def test_multiple_step_traces(self, trace_store):
        """A trace can have multiple step traces."""
        skill = SkillDefinition(
            id="multi-step",
            name="Multi Step",
            steps=[
                StepDefinition(id="a", name="A", tool="echo",
                               input_mapping={}, success_criteria=Criteria(type="always")),
                StepDefinition(id="b", name="B", tool="echo",
                               input_mapping={}, success_criteria=Criteria(type="always")),
            ],
        )
        tracer = Tracer(trace_store)
        trace = asyncio.run(tracer.start_trace(skill, {}))
        st1 = tracer.start_step_trace(trace, skill.steps[0])
        st2 = tracer.start_step_trace(trace, skill.steps[1])
        assert len(trace.step_traces) == 2
        assert st1.step_id == "a"
        assert st2.step_id == "b"
