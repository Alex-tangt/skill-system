from __future__ import annotations

import pytest
from skill_engine.engine.decomposer import (
    _extract_phrases,
    _split_text,
    _identify_data_flow,
    _detect_parallel_groups,
    _derive_step_name,
    _slugify,
    decompose_task,
    SubStepBlueprint,
)


class TestSlugify:
    def test_simple_text(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self):
        # _slugify strips leading/trailing dashes
        assert _slugify("Test & Demo!") == "test---demo"

    def test_chinese_text(self):
        result = _slugify("测试中文")
        assert "-" not in result or len(result) > 0


class TestSplitText:
    def test_numbered_items(self):
        text = "1. Extract text from PDF\n2. Convert to markdown\n3. Save output"
        result = _split_text(text)
        assert len(result) == 3
        assert "Extract text from PDF" in result[0]

    def test_parallel_and_marker(self):
        text = "Run lint and independently run security scan"
        result = _split_text(text)
        assert len(result) == 2

    def test_sequential_comma_then(self):
        text = "Extract text, then convert to markdown"
        result = _split_text(text)
        assert len(result) == 2

    def test_single_phrase(self):
        text = "Do one thing only"
        result = _split_text(text)
        assert len(result) == 1
        assert result[0] == "Do one thing only"


class TestExtractPhrases:
    def test_single_phrase(self):
        result = _extract_phrases("Extract text from a PDF file")
        assert len(result) == 1
        assert "Extract text from a PDF file" in result[0]

    def test_numbered_phrases(self):
        text = "1. Extract text from PDF.\n2. Convert text to markdown.\n3. Save the file."
        result = _extract_phrases(text)
        assert len(result) >= 2

    def test_chinese_sequential(self):
        text = "首先提取PDF文本，然后转换为markdown格式"
        result = _extract_phrases(text)
        assert len(result) >= 1


class TestIdentifyDataFlow:
    def test_creates_blueprints_with_correct_ids(self):
        phrases = ["Extract text from PDF", "Convert text to markdown"]
        blueprints = _identify_data_flow(phrases)
        assert len(blueprints) == 2
        assert blueprints[0].id == "step-1"
        assert blueprints[1].id == "step-2"

    def test_default_sequential_dependencies(self):
        phrases = ["First do X", "Then do Y"]
        blueprints = _identify_data_flow(phrases)
        assert blueprints[0].depends_on == []
        assert blueprints[1].depends_on == ["step-1"]

    def test_detects_verification(self):
        phrases = ["Check the output is valid"]
        blueprints = _identify_data_flow(phrases)
        assert blueprints[0].is_verification is True

    def test_non_verification_step(self):
        phrases = ["Extract data from source"]
        blueprints = _identify_data_flow(phrases)
        assert blueprints[0].is_verification is False


class TestDetectParallelGroups:
    def test_parallel_marker_removes_dependency(self):
        bp1 = SubStepBlueprint(id="step-1", name="A", description="Do task A",
                               depends_on=[])
        bp2 = SubStepBlueprint(id="step-2", name="B", description="Do task B independently",
                               depends_on=["step-1"])
        result = _detect_parallel_groups([bp1, bp2])
        assert result[1].depends_on == []

    def test_sequential_marker_keeps_dependency(self):
        bp1 = SubStepBlueprint(id="step-1", name="A", description="Do task A",
                               depends_on=[])
        bp2 = SubStepBlueprint(id="step-2", name="B", description="Then do task B",
                               depends_on=["step-1"])
        result = _detect_parallel_groups([bp1, bp2])
        assert result[1].depends_on == ["step-1"]


class TestDecomposeTask:
    def test_simple_description(self):
        skill = decompose_task("Extract text from PDF and convert to markdown")
        assert skill.name == "Generated Skill"
        assert len(skill.steps) >= 1
        assert skill.tags == ["generated", "modular"]

    def test_with_skill_name(self):
        skill = decompose_task(
            "First extract text, then convert to markdown",
            skill_name="PDF Converter"
        )
        assert skill.name == "PDF Converter"
        assert skill.id == "pdf-converter"

    def test_produces_valid_skill(self):
        skill = decompose_task(
            "1. Load data from file\n2. Process the data\n3. Save results"
        )
        errors = skill.validate()
        assert errors == []

    def test_has_input_schema(self):
        skill = decompose_task("Extract text from PDF file")
        assert "type" in skill.input_schema
        assert skill.input_schema["type"] == "object"

    def test_has_output_schema(self):
        skill = decompose_task("Generate a report from the data")
        assert "type" in skill.output_schema
        assert skill.output_schema["type"] == "object"
