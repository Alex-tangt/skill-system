from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from skill_engine.kernel.skill_store import SkillStore
from skill_engine.kernel.plugin_registry import PluginRegistry
from skill_engine.kernel.plugin_manager import PluginManager
from skill_engine.kernel.retriever import SkillRetriever
from skill_engine.kernel.models.skill_metadata import SkillMetadata

mcp = FastMCP("skill-engine-kernel")

# ── Global state ──
_plugin_manager: PluginManager | None = None


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


# ── Skill CRUD tools ──


@mcp.tool(
    name="skill_list",
    description="List all available skills. Returns id, name, description, version, and tags for each skill.",
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
    name="skill_create",
    description="Create a new skill from a SKILL.md definition.",
)
def skill_create(
    name: str,
    description: str,
    body: str = "",
    version: str = "1.0.0",
    license: str | None = None,
    compatibility: str | None = None,
    metadata: dict[str, str] | None = None,
    tags: str | None = None,
) -> str:
    store = get_skill_store()
    existing = store.get(name)
    if existing:
        return json.dumps({"error": f"Skill '{name}' already exists. Use skill_update to modify."})

    meta = metadata or {}
    if tags:
        meta["tags"] = tags

    skill = SkillMetadata(
        name=name,
        description=description,
        body=body,
        version=version,
        license=license,
        compatibility=compatibility,
        metadata=meta,
    )

    errors = skill.validate()
    if errors:
        return json.dumps({"error": "Validation failed", "validation_errors": errors})

    store.save(skill)
    return json.dumps({"status": "created", "name": name})


@mcp.tool(
    name="skill_update",
    description="Update an existing skill. A .backup is saved automatically.",
)
def skill_update(
    name: str,
    description: str | None = None,
    body: str | None = None,
    version: str | None = None,
    metadata: dict[str, str] | None = None,
) -> str:
    store = get_skill_store()
    skill = store.get(name)
    if not skill:
        return json.dumps({"error": f"Skill not found: {name}"})

    if description is not None:
        skill.description = description
    if body is not None:
        skill.body = body
    if version is not None:
        skill.version = version
    if metadata is not None:
        skill.metadata = {**skill.metadata, **metadata}

    errors = skill.validate()
    if errors:
        return json.dumps({"error": "Validation failed", "validation_errors": errors})

    store.save(skill)
    return json.dumps({"status": "updated", "name": name})


@mcp.tool(
    name="skill_delete",
    description="Delete a skill by name.",
)
def skill_delete(name: str) -> str:
    store = get_skill_store()
    ok = store.delete(name)
    if not ok:
        return json.dumps({"error": f"Skill not found: {name}"})
    return json.dumps({"status": "deleted", "name": name})


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


# ── Trace tools ──


@mcp.tool(
    name="trace_get",
    description="Get the full execution trace for a specific run_id.",
)
async def trace_get(run_id: str) -> str:
    from skill_engine.kernel.trace_store import TraceStore
    traces_db = os.environ.get("SKILL_ENGINE_TRACES_DB", "./traces/traces.db")
    ts = TraceStore(traces_db)
    await ts.initialize()
    trace = await ts.get_trace(run_id)
    if not trace:
        return json.dumps({"error": f"Trace not found: {run_id}"})
    return json.dumps(trace, default=str)


@mcp.tool(
    name="trace_list",
    description="List recent execution traces. Filter by skill_id and status.",
)
async def trace_list(skill_id: str | None = None, status: str | None = None, limit: int = 20) -> str:
    from skill_engine.kernel.trace_store import TraceStore
    traces_db = os.environ.get("SKILL_ENGINE_TRACES_DB", "./traces/traces.db")
    ts = TraceStore(traces_db)
    await ts.initialize()
    traces = await ts.list_traces(skill_id=skill_id, status=status, limit=limit)
    return json.dumps(traces, default=str)


@mcp.tool(
    name="trace_errors",
    description="Get failed traces with step-level error details.",
)
async def trace_errors(skill_id: str | None = None, limit: int = 10) -> str:
    from skill_engine.kernel.trace_store import TraceStore
    traces_db = os.environ.get("SKILL_ENGINE_TRACES_DB", "./traces/traces.db")
    ts = TraceStore(traces_db)
    await ts.initialize()
    errors = await ts.get_error_traces(skill_id=skill_id, limit=limit)
    return json.dumps(errors, default=str)


# ── Plugin management tools ──


@mcp.tool(
    name="plugin_list",
    description="List all registered plugins and their health status.",
)
def plugin_list() -> str:
    pm = get_plugin_manager()
    handles = pm.registry.list_healthy()
    all_plugins = pm.registry.list_plugins()
    result = []
    for name in all_plugins:
        h = pm.registry.get(name)
        if h:
            result.append({
                "name": h.name,
                "version": h.version,
                "description": h.description,
                "health_status": h.health_status,
                "registered_tools": h.registered_tools,
                "type": "external" if h.mcp_server_command else "internal",
            })
    return json.dumps(result)


@mcp.tool(
    name="plugin_health",
    description="Check a specific plugin's health status.",
)
async def plugin_health(name: str) -> str:
    pm = get_plugin_manager()
    handle = pm.registry.get(name)
    if not handle:
        return json.dumps({"error": f"Plugin not found: {name}"})

    # Check health via ping for internal plugins
    plugin = pm.get_plugin(name)
    if plugin:
        try:
            healthy = await plugin.health_check()
            handle.health_status = "healthy" if healthy else "unhealthy"
        except Exception:
            handle.health_status = "unhealthy"
    return json.dumps({"name": name, "health_status": handle.health_status})


@mcp.tool(
    name="plugin_config",
    description="Get or set plugin configuration.",
)
def plugin_config(name: str, config_updates: dict[str, Any] | None = None) -> str:
    pm = get_plugin_manager()
    handle = pm.registry.get(name)
    if not handle:
        return json.dumps({"error": f"Plugin not found: {name}"})

    if config_updates:
        plugin = pm.get_plugin(name)
        if plugin:
            plugin.config = {**plugin.config, **config_updates}
        handle.registered_tools = list(config_updates.keys()) if config_updates else handle.registered_tools
        return json.dumps({"status": "updated", "name": name})

    plugin = pm.get_plugin(name)
    return json.dumps({
        "name": name,
        "config": plugin.config if plugin else {},
    })


# ── Pipeline tools ──


@mcp.tool(
    name="pipeline_run",
    description="Process pending history events into structured execution traces.",
)
async def pipeline_run(limit: int = 100) -> str:
    pm = get_plugin_manager()

    # Lazy-load the data-pipeline plugin if registered
    if not pm.get_plugin("data-pipeline"):
        await pm.load_all()

    return await pm.call_plugin_tool("data-pipeline", "pipeline_run", {"limit": limit})


@mcp.tool(
    name="pipeline_status",
    description="Get the last data pipeline run status.",
)
async def pipeline_status() -> str:
    pm = get_plugin_manager()

    if not pm.get_plugin("data-pipeline"):
        await pm.load_all()

    return await pm.call_plugin_tool("data-pipeline", "pipeline_status", {})


# ── Entry point ──


def main():
    """Start the kernel MCP server and load plugins."""
    pm = get_plugin_manager()
    asyncio.run(pm.load_all())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
