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
_pipeline: Any = None  # Old DataPipelinePlugin instance (deprecated, kept for compat)
_new_pipeline: Any = None  # AnalyzerEvolverRunner instance
_pipeline_store: Any = None  # PipelineStore instance
_segment_watcher: Any = None  # SegmentWatcher instance


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
    """Lazy-init the old data pipeline (kept for trace_get/trace_list/trace_errors)."""
    global _pipeline
    if _pipeline is None:
        from skill_engine.plugins.data_pipeline.plugin import DataPipelinePlugin
        _pipeline = DataPipelinePlugin({
            "history_db_path": os.environ.get("SKILL_ENGINE_HISTORY_DB", "./traces/history.db"),
            "trace_db_path": os.environ.get("SKILL_ENGINE_TRACES_DB", "./traces/traces.db"),
        })
        asyncio.run(_pipeline.initialize())
    return _pipeline


def get_new_pipeline():
    """Lazy-init the v0.3 transcript-native pipeline."""
    global _new_pipeline, _pipeline_store, _segment_watcher
    if _new_pipeline is None:
        from skill_engine.pipeline.llm_client_impl import AnthropicLLMClient
        from skill_engine.pipeline.pipeline_store import PipelineStore
        from skill_engine.pipeline.analyzer_evolver import AnalyzerEvolverRunner
        from skill_engine.pipeline.segment_watcher import SegmentWatcher

        db_path = os.environ.get(
            "SKILL_ENGINE_PIPELINE_DB",
            "./traces/pipeline.db",
        )
        model = os.environ.get("ANTHROPIC_MODEL")

        _pipeline_store = PipelineStore(db_path)
        asyncio.run(_pipeline_store.initialize())

        llm = AnthropicLLMClient(model=model)
        validator = get_validator()

        # Shared queue for watcher → runner communication
        analysis_queue = asyncio.Queue()

        _new_pipeline = AnalyzerEvolverRunner(
            llm_client=llm,
            segment_store=_pipeline_store.segments,
            skill_store=get_skill_store(),
            pipeline_store=_pipeline_store,
            validator=validator,
            analysis_queue=analysis_queue,
            model=model,
        )

        _segment_watcher = SegmentWatcher(
            store=_pipeline_store.segments,
            analysis_queue=analysis_queue,
        )

    return _new_pipeline, _pipeline_store, _segment_watcher


def get_validator():
    """Lazy-init the Validator (shared by pipeline and MCP tools)."""
    from skill_engine.pipeline.validator import Validator

    db_path = os.environ.get(
        "SKILL_ENGINE_VALIDATOR_DB",
        "./traces/validator.db",
    )
    validator = Validator(db_path)
    asyncio.run(validator.initialize())
    return validator


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


# ── Pipeline v0.3 tools ──


@mcp.tool(
    name="pipeline_segments",
    description="List all segments for the current or specified session.",
)
def pipeline_segments(session_id: str = "") -> str:
    _, store, _ = get_new_pipeline()

    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not sid:
        return json.dumps({"error": "No session_id provided and CLAUDE_CODE_SESSION_ID not set"})

    try:
        rows = asyncio.run(store.segments.get_by_session(sid))
    except Exception as e:
        return json.dumps({"error": str(e)})

    return json.dumps([
        {
            "id": r["id"][:8] + "...",
            "user_msg": r["user_msg"][:120],
            "user_msg_index": r["user_msg_index"],
            "has_next": r.get("next_id") is not None,
            "created_at": r.get("created_at", ""),
        }
        for r in rows
    ], ensure_ascii=False)


@mcp.tool(
    name="pipeline_segment_get",
    description="Get a specific segment by ID, including execution trace.",
)
def pipeline_segment_get(segment_id: str) -> str:
    _, store, _ = get_new_pipeline()

    try:
        row = asyncio.run(store.segments.get(segment_id))
    except Exception as e:
        return json.dumps({"error": str(e)})

    if not row:
        return json.dumps({"error": f"Segment not found: {segment_id}"})

    return json.dumps({
        "id": row["id"],
        "session_id": row.get("session_id", ""),
        "user_msg": row["user_msg"],
        "user_msg_index": row["user_msg_index"],
        "stats": json.loads(row.get("stats_json", "{}")),
        "execution": json.loads(row.get("execution_json", "[]")),
        "prev_id": row.get("prev_id"),
        "next_id": row.get("next_id"),
        "skills_available": json.loads(row.get("skills_available", "[]")),
    }, ensure_ascii=False)


@mcp.tool(
    name="pipeline_analyze",
    description="Run Phase A analysis on a specific segment. Returns analysis results.",
)
def pipeline_analyze(segment_id: str) -> str:
    runner, store, _ = get_new_pipeline()

    try:
        analysis, patches = asyncio.run(runner.analyze_and_evolve(segment_id))
    except Exception as e:
        return json.dumps({"error": str(e)})

    if analysis is None:
        return json.dumps({"error": "Segment not found"})

    return json.dumps({
        "task_completed": analysis.task_completed,
        "execution_note": analysis.execution_note,
        "skill_judgments": [j.to_dict() for j in analysis.skill_judgments],
        "evolution_suggestions": [s.to_dict() for s in analysis.evolution_suggestions],
        "tool_issues": analysis.tool_issues,
        "patches_produced": len(patches),
    }, ensure_ascii=False)


@mcp.tool(
    name="pipeline_watch",
    description="Start watching the current session transcript for new segments.",
)
def pipeline_watch(session_id: str = "") -> str:
    _, _, watcher = get_new_pipeline()

    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not sid:
        return json.dumps({"error": "No session_id available"})

    asyncio.create_task(watcher.watch(sid))

    return json.dumps({
        "status": "watching",
        "session_id": sid,
    })


# ── Validator tools ──


@mcp.tool(
    name="pipeline_validator_add_case",
    description="Add a test case to the validator for a specific skill. Test cases are used to validate future skill patches.",
)
def pipeline_validator_add_case(
    skill_id: str,
    input_desc: str,
    expected_behavior: str,
    source: str = "manual",
) -> str:
    validator = get_validator()
    try:
        case_id = asyncio.run(
            validator.add_test_case(skill_id, input_desc, expected_behavior, source)
        )
        return json.dumps({"status": "ok", "case_id": case_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool(
    name="pipeline_validator_cases",
    description="List all test cases and recent validation runs for a skill.",
)
def pipeline_validator_cases(skill_id: str) -> str:
    validator = get_validator()
    try:
        data = asyncio.run(validator.run_test_suite(skill_id))
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Entry point ──


def main():
    """Start the kernel MCP server."""
    # Auto-start watcher if session is active
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if session_id:
        _, _, watcher = get_new_pipeline()
        asyncio.create_task(watcher.watch(session_id))
        import logging
        logging.getLogger(__name__).info(
            f"Pipeline watcher auto-started for session {session_id[:8]}..."
        )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
