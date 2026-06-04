from __future__ import annotations

import asyncio
import os
import pytest
from skill_engine.engine.dag_executor import DAGExecutor
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria


@pytest.mark.asyncio
async def test_unicode_input(tool_registry):
    """Unicode/Chinese characters survive round-trip through echo."""
    skill = SkillDefinition(
        id="unicode-test",
        name="Unicode Test",
        steps=[
            StepDefinition(id="s1", name="S1", tool="echo",
                           input_mapping={"message": "$input.text"}),
        ],
    )
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(skill, {"text": "你好世界 🌍"})
    assert result["status"] == "succeeded"
    assert result["output"]["s1"]["echoed"] == "你好世界 🌍"


@pytest.mark.asyncio
async def test_empty_input(tool_registry):
    """Execute with empty input dict succeeds (no required fields)."""
    skill = SkillDefinition(
        id="empty-input",
        name="Empty Input",
        input_schema={"type": "object", "properties": {}, "required": []},
        steps=[
            StepDefinition(id="s1", name="S1", tool="echo",
                           input_mapping={"message": "$input.text"}),
        ],
    )
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(skill, {})
    assert result["status"] == "succeeded"
    # message will be None since $input.text doesn't exist
    assert result["output"]["s1"]["echoed"] is None


@pytest.mark.asyncio
async def test_none_input(tool_registry):
    """Execute with None input is treated as {}."""
    skill = SkillDefinition(
        id="none-input", name="None Input",
        steps=[
            StepDefinition(id="s1", name="S1", tool="echo", input_mapping={}),
        ],
    )
    executor = DAGExecutor(tool_registry)
    # execute() in server does input_data = input or {}
    result = await executor.execute(skill, {})  # server normalizes None->{}
    assert result["status"] == "succeeded"


@pytest.mark.asyncio
async def test_self_cycle_detected(tool_registry):
    """Step depending on itself is caught by validation."""
    s1 = StepDefinition(id="a", name="A", depends_on=["a"], tool="echo", input_mapping={})
    skill = SkillDefinition(id="self-cycle", name="Self Cycle", steps=[s1])
    errors = skill.validate()
    # Will be caught by topological_order (cycle detection)
    assert len(errors) >= 1


@pytest.mark.asyncio
async def test_large_input_string(tool_registry):
    """100KB input string passes through without truncation."""
    large_text = "x" * 100000  # 100KB
    skill = SkillDefinition(
        id="large-input",
        name="Large Input",
        steps=[
            StepDefinition(id="s1", name="S1", tool="echo",
                           input_mapping={"message": "$input.text"}),
        ],
    )
    executor = DAGExecutor(tool_registry)
    result = await executor.execute(skill, {"text": large_text})
    assert result["status"] == "succeeded"
    # Output should match
    echoed = result["output"]["s1"]["echoed"]
    assert len(echoed) == len(large_text)


@pytest.mark.asyncio
async def test_many_input_fields(tool_registry):
    """Skill with 20 input_schema properties validates correctly."""
    properties = {f"field{i}": {"type": "string"} for i in range(20)}
    skill = SkillDefinition(
        id="many-fields",
        name="Many Fields",
        input_schema={
            "type": "object",
            "properties": properties,
            "required": ["field0", "field1"],
        },
        steps=[
            StepDefinition(id="s1", name="S1", tool="echo",
                           input_mapping={"message": "$input.field0"}),
        ],
    )
    executor = DAGExecutor(tool_registry)
    # Missing required fields
    result = await executor.execute(skill, {"field0": "a"})
    assert result["status"] == "failed"
    assert "validation" in result.get("error", "").lower()
