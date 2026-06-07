from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from skill_engine.kernel.skill_store import SkillStore
from skill_engine.kernel.plugin_registry import PluginRegistry
from skill_engine.kernel.plugin_manager import PluginManager
from skill_engine.kernel.retriever import SkillRetriever

mcp = FastMCP("skill-system-kernel")

# ── Global state ──
_plugin_manager: PluginManager | None = None
_pipeline: Any = None  # DataPipelinePlugin instance


def get_skill_store() -> SkillStore:
    skills_dir = os.environ.get("SKILL_ENGINE_SKILLS_DIR", "./skills")
    return SkillStore(skills_dir)


def get_plugin_manager() -> PluginManager:
    global _plugin_manager
    if _plugin_manager is None:
        registry = PluginRegistry()
        config_path = os.environ.get("SKILL_ENGINE_PLUGINS_CONFIG", "plugins.yaml")
        _plugin_manager = PluginManager(registry, config_path)
    return _plugin_manager


def get_pipeline():
    """Lazy-init the data pipeline (direct Python, not MCP)."""
    global _pipeline
    if _pipeline is None:
        from skill_engine.plugins.data_pipeline.plugin import DataPipelinePlugin
        _pipeline = DataPipelinePlugin({
            "history_db_path": os.environ.get("SKILL_ENGINE_HISTORY_DB", "./traces/history.db"),
            "trace_db_path": os.environ.get("SKILL_ENGINE_TRACES_DB", "./traces/traces.db"),
        })
        asyncio.run(_pipeline.initialize())
    return _pipeline


# ── Skill tools (kernel: read-only, for LLM) ──


@mcp.tool(
    name="skill_list",
    description="List all available skills. Returns name, description, version, and tags for each skill.",
)
def skill_list(tag: str | None = None, limit: int = 50) -> str:
    store = get_skill_store()
    skills = store.list_all()
    if tag:
        skills = [s for s in skills if tag in s.metadata.get("tags", "").split()]
    skills = skills[:limit]
    result = [
        {
            "name": s.name,
            "description": s.description,
            "version": s.version,
            "tags": s.metadata.get("tags", "").split() if s.metadata.get("tags") else [],
        }
        for s in skills
    ]
    return json.dumps(result)


@mcp.tool(
    name="skill_get",
    description="Get the full definition of a skill by name, including body and metadata.",
)
def skill_get(name: str) -> str:
    store = get_skill_store()
    skill = store.get(name) or store.get_by_name(name)
    if not skill:
        return json.dumps({"error": f"Skill not found: {name}"})
    return json.dumps({
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "body": skill.body,
        "license": skill.license,
        "compatibility": skill.compatibility,
        "metadata": skill.metadata,
        "allowed_tools": skill.allowed_tools,
    })


@mcp.tool(
    name="skill_search",
    description="Search for skills matching a natural language query.",
)
def skill_search(query: str, top_k: int = 5) -> str:
    store = get_skill_store()
    retriever = SkillRetriever(store)
    results = retriever.search(query, top_k=top_k)
    output = [
        {
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "score": round(score, 4),
        }
        for skill, score in results
    ]
    return json.dumps(output)


# ── Entry point ──


def main():
    """Start the kernel MCP server (3 tools for LLM)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
