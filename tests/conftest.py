from __future__ import annotations

import asyncio
import os
import tempfile
import pytest

from skill_engine.storage.skill_store import SkillStore
from skill_engine.storage.trace_store import TraceStore
from skill_engine.models.registry import ToolRegistry
from skill_engine.builtin_tools.echo import echo
from skill_engine.models.skill import SkillDefinition, StepDefinition, Criteria, RetryPolicy


@pytest.fixture
def temp_skills_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def skill_store(temp_skills_dir):
    return SkillStore(temp_skills_dir)


@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def trace_store(temp_db_path):
    ts = TraceStore(temp_db_path)
    asyncio.run(ts.initialize())
    return ts


@pytest.fixture
def tool_registry():
    r = ToolRegistry()
    r.register("echo", echo)
    return r


@pytest.fixture
def sample_skill():
    return SkillDefinition(
        id="test-skill",
        name="Test Skill",
        steps=[
            StepDefinition(
                id="s1",
                name="Step 1",
                tool="echo",
                input_mapping={"message": "$input.text"},
            ),
            StepDefinition(
                id="s2",
                name="Step 2",
                tool="echo",
                depends_on=["s1"],
                input_mapping={"message": "$steps.s1.output.echoed"},
            ),
        ],
    )
