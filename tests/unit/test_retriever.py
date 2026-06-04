from __future__ import annotations

import pytest
from skill_engine.retrieval.retriever import (
    _tokenize,
    SkillRetriever,
    compose_skills,
)
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria


def _make_skill(skill_id, name, description="", tags=None, input_props=None, output_props=None, steps=None):
    return SkillDefinition(
        id=skill_id,
        name=name,
        description=description,
        tags=tags or [],
        input_schema={"type": "object", "properties": input_props or {}},
        output_schema={"type": "object", "properties": output_props or {}},
        steps=steps or [],
    )


class TestTokenize:
    def test_splits_to_lowercase_words(self):
        result = _tokenize("Hello World! Python3, testing.")
        assert "hello" in result
        assert "world" in result
        assert "python3" in result
        assert "testing" in result

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_special_chars_only(self):
        assert _tokenize("!@#$%") == []


class TestSkillRetriever:
    def test_search_empty_store(self, skill_store):
        """Search on an empty store returns []."""
        retriever = SkillRetriever(skill_store)
        results = retriever.search("anything")
        assert results == []

    def test_search_returns_relevant_skills(self, skill_store):
        """Search returns skills matching the query."""
        store = skill_store
        store.save(_make_skill("pdf-extract", "PDF Extractor",
                                description="Extract text from PDF files",
                                tags=["pdf", "text"]))
        store.save(_make_skill("image-resize", "Image Resizer",
                                description="Resize and optimize images",
                                tags=["image", "resize"]))
        retriever = SkillRetriever(store)
        results = retriever.search("pdf text extraction")
        assert len(results) >= 1
        assert results[0][0].id == "pdf-extract"

    def test_search_respects_top_k(self, skill_store):
        """Search respects top_k parameter."""
        for i in range(5):
            skill_store.save(_make_skill(
                f"skill-{i}", f"Skill {i}",
                description=f"A skill for task number {i}",
                tags=["test"],
            ))
        retriever = SkillRetriever(skill_store)
        results = retriever.search("skill task", top_k=3)
        assert len(results) <= 3

    def test_search_no_matches(self, skill_store):
        """Search with no matching tokens returns empty."""
        skill_store.save(_make_skill("pdf", "PDF Tool", description="PDF processing"))
        retriever = SkillRetriever(skill_store)
        results = retriever.search("zzz_nonexistent_term_zzz")
        assert results == []


class TestComposeSkills:
    def test_compose_two_skills(self, skill_store):
        """Compose two skills into a pipeline."""
        s1 = SkillDefinition(
            id="skill-a", name="Skill A",
            steps=[
                StepDefinition(id="s1", name="Step 1", tool="echo",
                               input_mapping={}, success_criteria=Criteria(type="always")),
            ],
        )
        s2 = SkillDefinition(
            id="skill-b", name="Skill B",
            steps=[
                StepDefinition(id="s1", name="Step 1", tool="echo",
                               input_mapping={}, success_criteria=Criteria(type="always")),
            ],
        )
        skill_store.save(s1)
        skill_store.save(s2)
        composed = compose_skills("My Pipeline", ["skill-a", "skill-b"], skill_store)
        assert composed.name == "My Pipeline"
        assert composed.id == "my-pipeline"
        # Steps should be prefixed
        assert len(composed.steps) == 2
        assert composed.steps[0].id == "_s0_s1"
        assert composed.steps[1].id == "_s1_s1"

    def test_compose_dependency_wiring(self, skill_store):
        """Terminal step of skill-0 becomes dependency of root steps of skill-1."""
        s1 = SkillDefinition(
            id="skill-a", name="Skill A",
            steps=[
                StepDefinition(id="extract", name="Extract", tool="echo",
                               input_mapping={}, success_criteria=Criteria(type="always")),
            ],
        )
        s2 = SkillDefinition(
            id="skill-b", name="Skill B",
            steps=[
                StepDefinition(id="transform", name="Transform", tool="echo",
                               input_mapping={}, success_criteria=Criteria(type="always")),
            ],
        )
        skill_store.save(s1)
        skill_store.save(s2)
        composed = compose_skills("Pipeline", ["skill-a", "skill-b"], skill_store)
        # transform step should depend on extract (root step with no prior deps gets wired)
        assert composed.steps[1].depends_on == ["_s0_extract"]

    def test_compose_missing_skill_raises(self, skill_store):
        """Composing with a nonexistent skill_id raises ValueError."""
        with pytest.raises(ValueError, match="Skill not found"):
            compose_skills("Bad Pipeline", ["nonexistent-skill"], skill_store)

    def test_compose_with_output_mappings(self, skill_store):
        """Output mappings are added to the composed input_schema."""
        s1 = SkillDefinition(
            id="skill-a", name="Skill A",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            steps=[
                StepDefinition(id="s1", name="Step 1", tool="echo",
                               input_mapping={"message": "$input.text"},
                               success_criteria=Criteria(type="always")),
            ],
        )
        skill_store.save(s1)
        composed = compose_skills(
            "Pipeline", ["skill-a"], skill_store,
            output_mappings={"format": "markdown"},
        )
        assert "format" in composed.input_schema.get("properties", {})
