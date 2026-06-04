from __future__ import annotations

import os
import pytest
from skill_engine.storage.skill_store import SkillStore, skill_to_dict


@pytest.fixture
def real_skills_store():
    """SkillStore pointed at the real skills/ directory."""
    skills_dir = os.path.join(os.path.dirname(__file__), "..", "..", "skills")
    return SkillStore(os.path.abspath(skills_dir))


class TestLoadRealYamlSkills:
    def test_load_hello_world(self, real_skills_store):
        """Can load the hello-world skill from real YAML."""
        skill = real_skills_store.get("hello-world")
        assert skill is not None
        assert skill.name == "Hello World"
        assert len(skill.steps) == 2
        assert skill.steps[0].id == "echo1"
        assert skill.steps[1].id == "echo2"
        assert skill.steps[1].depends_on == ["echo1"]

    def test_load_dev_diary(self, real_skills_store):
        """Can load the dev-diary skill with shell command mode."""
        skill = real_skills_store.get("dev-diary")
        assert skill is not None
        assert skill.name == "Dev Diary Manager"
        assert len(skill.steps) == 1
        step = skill.steps[0]
        # Shell command mode
        assert "python3" in step.tool or "diary.py" in step.tool
        assert "input_schema" in skill_to_dict(skill)
        # Has enum constraints
        assert "operation" in skill.input_schema.get("properties", {})

    def test_load_pdf_to_markdown(self, real_skills_store):
        """Can load the pdf-to-markdown skill with retry config."""
        skill = real_skills_store.get("pdf-to-markdown")
        assert skill is not None
        step = skill.steps[0]
        assert step.retry.max_attempts == 2
        assert step.retry.backoff == "fixed"
        assert step.timeout_seconds == 60


class TestRoundTrip:
    def test_save_and_reload_preserves_data(self, skill_store, temp_skills_dir):
        """Saving a skill and loading it back preserves all fields."""
        from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria, RetryPolicy

        skill = SkillDefinition(
            id="roundtrip",
            name="Roundtrip Test",
            version="1.2.3",
            description="A test for roundtrip fidelity",
            tags=["test", "roundtrip"],
            timeout_seconds=120,
            max_concurrency=5,
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            steps=[
                StepDefinition(
                    id="step1",
                    name="Step 1",
                    description="First step",
                    tool="echo",
                    depends_on=[],
                    input_mapping={"message": "$input.text"},
                    success_criteria=Criteria(type="output_match", expected={"echoed": "ok"}),
                    failure_criteria=Criteria(type="timeout"),
                    retry=RetryPolicy(max_attempts=3, backoff="exponential", backoff_base_seconds=2.0),
                    timeout_seconds=30,
                ),
            ],
        )
        skill_store.save(skill)
        loaded = skill_store.get("roundtrip")
        assert loaded is not None
        assert loaded.name == "Roundtrip Test"
        assert loaded.version == "1.2.3"
        assert loaded.description == "A test for roundtrip fidelity"
        assert loaded.tags == ["test", "roundtrip"]
        assert loaded.timeout_seconds == 120
        assert loaded.max_concurrency == 5
        assert loaded.input_schema["required"] == ["text"]
        assert len(loaded.steps) == 1
        ls = loaded.steps[0]
        assert ls.id == "step1"
        assert ls.tool == "echo"
        assert ls.retry.max_attempts == 3
        assert ls.retry.backoff == "exponential"
        assert ls.retry.backoff_base_seconds == 2.0
        assert ls.timeout_seconds == 30
        assert ls.success_criteria.type == "output_match"
        assert ls.success_criteria.expected == {"echoed": "ok"}
        assert ls.failure_criteria.type == "timeout"

    def test_list_all_includes_real_skills(self, real_skills_store):
        """list_all returns the real skills."""
        skills = real_skills_store.list_all()
        skill_ids = {s.id for s in skills}
        assert "hello-world" in skill_ids
        assert "dev-diary" in skill_ids
        assert "pdf-to-markdown" in skill_ids
