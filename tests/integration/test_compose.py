from __future__ import annotations

import pytest
from skill_engine.retrieval.retriever import compose_skills
from skill_engine.engine.dag_executor import DAGExecutor
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria


class TestComposeIntegration:
    def test_compose_two_skills_step_count(self, skill_store):
        """Composing two skills produces correct step count with prefixes."""
        s1 = SkillDefinition(
            id="comp-a", name="Comp A",
            steps=[
                StepDefinition(id="extract", name="Extract", tool="echo",
                               input_mapping={"message": "$input.text"},
                               success_criteria=Criteria(type="always")),
            ],
        )
        s2 = SkillDefinition(
            id="comp-b", name="Comp B",
            steps=[
                StepDefinition(id="transform", name="Transform", tool="echo",
                               input_mapping={},
                               success_criteria=Criteria(type="always")),
            ],
        )
        skill_store.save(s1)
        skill_store.save(s2)

        composed = compose_skills("Pipeline", ["comp-a", "comp-b"], skill_store)
        assert len(composed.steps) == 2
        assert composed.steps[0].id == "_s0_extract"
        assert composed.steps[1].id == "_s1_transform"

    def test_compose_preserves_terminal_dependency(self, skill_store):
        """Skill 2's root steps depend on Skill 1's terminal steps."""
        s1 = SkillDefinition(
            id="first", name="First",
            steps=[
                StepDefinition(id="init", name="Init", tool="echo",
                               input_mapping={},
                               success_criteria=Criteria(type="always")),
            ],
        )
        s2 = SkillDefinition(
            id="second", name="Second",
            steps=[
                StepDefinition(id="process", name="Process", tool="echo",
                               input_mapping={},
                               success_criteria=Criteria(type="always")),
            ],
        )
        skill_store.save(s1)
        skill_store.save(s2)

        composed = compose_skills("Chain", ["first", "second"], skill_store)
        # second's process step should depend on first's init
        assert "_s0_init" in composed.steps[1].depends_on

    @pytest.mark.asyncio
    async def test_composed_skill_executes(self, tool_registry, skill_store):
        """A composed skill can be executed successfully."""
        s1 = SkillDefinition(
            id="exec-a", name="Exec A",
            steps=[
                StepDefinition(id="step1", name="Step 1", tool="echo",
                               input_mapping={"message": "$input.text"},
                               success_criteria=Criteria(type="always")),
            ],
        )
        s2 = SkillDefinition(
            id="exec-b", name="Exec B",
            steps=[
                StepDefinition(id="step2", name="Step 2", tool="echo",
                               depends_on=[], input_mapping={},
                               success_criteria=Criteria(type="always")),
            ],
        )
        skill_store.save(s1)
        skill_store.save(s2)

        composed = compose_skills("ExecPipeline", ["exec-a", "exec-b"], skill_store)
        # Save the composed skill
        skill_store.save(composed)

        executor = DAGExecutor(tool_registry)
        result = await executor.execute(composed, {"text": "hello"})
        assert result["status"] == "succeeded"

    def test_compose_missing_skill_raises_value_error(self, skill_store):
        """Composing with a nonexistent skill raises ValueError."""
        with pytest.raises(ValueError, match="Skill not found"):
            compose_skills("Bad", ["nonexistent-id"], skill_store)
