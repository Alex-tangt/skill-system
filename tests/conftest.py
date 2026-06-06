from __future__ import annotations

import asyncio
import os
import tempfile
import pytest

from skill_engine.kernel.skill_store import SkillStore
from skill_engine.kernel.trace_store import TraceStore
from skill_engine.kernel.plugin_registry import PluginRegistry
from skill_engine.kernel.models.skill_metadata import SkillMetadata


@pytest.fixture
def temp_skills_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


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
