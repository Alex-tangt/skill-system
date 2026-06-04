from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

import asyncio

from skill_engine.storage.skill_store import SkillStore
from skill_engine.storage.trace_store import TraceStore
from skill_engine.models.registry import ToolRegistry
from skill_engine.models.skill import SkillDefinition
from skill_engine.builtin_tools.echo import echo
from skill_engine.engine.dag_executor import DAGExecutor
from skill_engine.tracing.tracer import Tracer
from skill_engine.retrieval.retriever import SkillRetriever, compose_skills
from skill_engine.storage.skill_store import skill_to_dict
from skill_engine.optimizer.analyzer import TraceAnalyzer
from skill_engine.optimizer.agent import OptimizerAgent
from skill_engine.engine.decomposer import decompose_task

mcp = FastMCP("skill-engine")

_trace_store: TraceStore | None = None


def get_skill_store() -> SkillStore:
    skills_dir = os.environ.get("SKILL_ENGINE_SKILLS_DIR", "./skills")
    return SkillStore(skills_dir)


def get_trace_store() -> TraceStore:
    global _trace_store
    if _trace_store is None:
        traces_db = os.environ.get("SKILL_ENGINE_TRACES_DB", "./traces/traces.db")
        _trace_store = TraceStore(traces_db)
    return _trace_store


def get_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("echo", echo)
    return registry


# ── Skill CRUD tools ──


@mcp.tool(
    name="skill_list",
    description="List all available skills. Returns id, name, description, version, and tags for each skill.",
)
def skill_list(tag: str | None = None, limit: int = 50) -> str:
    store = get_skill_store()
    skills = store.list_all()
    if tag:
        skills = [s for s in skills if tag in s.tags]
    skills = skills[:limit]
    result = [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "version": s.version,
            "tags": s.tags,
        }
        for s in skills
    ]
    return json.dumps(result)


@mcp.tool(
    name="skill_get",
    description="Get the full definition of a skill by ID, including all steps and their configurations.",
)
def skill_get(skill_id: str) -> str:
    store = get_skill_store()
    skill = store.get(skill_id)
    if not skill:
        skill = store.get_by_name(skill_id)
    if not skill:
        return json.dumps({"error": f"Skill not found: {skill_id}"})
    return json.dumps(skill_to_dict(skill))


@mcp.tool(
    name="skill_create",
    description="Create a new skill. Provide a full skill definition as a JSON object matching the skill schema.",
)
def skill_create(definition: dict[str, Any]) -> str:
    store = get_skill_store()
    try:
        skill = _dict_to_skill(definition)
    except Exception as e:
        return json.dumps({"error": f"Invalid skill definition: {e}"})
    errors = skill.validate()
    if errors:
        return json.dumps({"error": "Skill validation failed", "validation_errors": errors})
    existing = store.get(skill.id)
    if existing:
        return json.dumps({"error": f"Skill '{skill.id}' already exists. Use skill_update to modify it."})
    store.save(skill)
    # Auto-generate SKILL.md skeleton for Claude Code discovery
    _generate_skill_md(store.skills_dir, skill)
    return json.dumps({"status": "created", "skill_id": skill.id})


@mcp.tool(
    name="skill_update",
    description="Update an existing skill. Provide the full updated definition. A .backup of the previous version is saved automatically.",
)
def skill_update(skill_id: str, definition: dict[str, Any]) -> str:
    store = get_skill_store()
    existing = store.get(skill_id)
    if not existing:
        return json.dumps({"error": f"Skill not found: {skill_id}"})
    try:
        skill = _dict_to_skill(definition)
        skill.id = skill_id
    except Exception as e:
        return json.dumps({"error": f"Invalid skill definition: {e}"})
    errors = skill.validate()
    if errors:
        return json.dumps({"error": "Skill validation failed", "validation_errors": errors})
    store.save(skill)
    return json.dumps({"status": "updated", "skill_id": skill_id})


@mcp.tool(
    name="skill_delete",
    description="Delete a skill definition by ID.",
)
def skill_delete(skill_id: str) -> str:
    store = get_skill_store()
    ok = store.delete(skill_id)
    if not ok:
        return json.dumps({"error": f"Skill not found: {skill_id}"})
    return json.dumps({"status": "deleted", "skill_id": skill_id})


@mcp.tool(
    name="skill_analyze",
    description="Analyze a natural language task description and generate a modular skill definition. "
                "Decomposes the task into independently verifiable sub-steps with dependencies, "
                "input/output schemas, and success criteria. Returns a draft skill definition "
                "ready for review — use skill_create to persist it.",
)
def skill_analyze(description: str, name: str | None = None) -> str:
    skill_name = name or ""
    skill = decompose_task(description, skill_name)
    errors = skill.validate()
    if errors:
        return json.dumps({
            "status": "warning",
            "message": "Generated skill has validation issues that were auto-resolved",
            "validation_errors": errors,
            "definition": skill_to_dict(skill),
        })
    return json.dumps({
        "status": "preview",
        "message": "Modular skill definition generated. Review the decomposition and use skill_create to persist.",
        "modularity_notes": {
            "principle": "Each step has independent success/failure criteria",
            "dependency_model": "Steps are chained only when output of one is input to another",
            "parallel_opportunities": [
                s.id for s in skill.steps if not s.depends_on
            ],
        },
        "definition": skill_to_dict(skill),
    })


# ── Skill execution ──


@mcp.tool(
    name="skill_execute",
    description="Execute a skill by name or ID. Runs its DAG of sub-steps in dependency order. Set sync=false for async execution — returns a run_id immediately for polling via trace_get.",
)
async def skill_execute(skill_id: str, input: dict[str, Any] | None = None, sync: bool = True) -> str:
    store = get_skill_store()
    skill = store.get(skill_id)
    if not skill:
        skill = store.get_by_name(skill_id)
    if not skill:
        return json.dumps({"error": f"Skill not found: {skill_id}"})

    input_data = input or {}
    registry = get_tool_registry()
    trace_store = get_trace_store()
    await trace_store.initialize()
    tracer = Tracer(trace_store)
    executor = DAGExecutor(registry)
    result = await executor.execute(skill, input_data, sync=sync, tracer=tracer)
    return json.dumps(result)


# ── Trace tools ──


@mcp.tool(
    name="trace_get",
    description="Get the full execution trace for a specific run, including all step-level traces with inputs, outputs, errors, and timing.",
)
async def trace_get(run_id: str) -> str:
    ts = get_trace_store()
    await ts.initialize()
    trace = await ts.get_trace(run_id)
    if not trace:
        return json.dumps({"error": f"Trace not found for run_id: {run_id}"})
    return json.dumps(trace, default=str)


@mcp.tool(
    name="trace_list",
    description="List recent execution traces, filterable by skill_id and status.",
)
async def trace_list(skill_id: str | None = None, status: str | None = None, limit: int = 20) -> str:
    ts = get_trace_store()
    await ts.initialize()
    traces = await ts.list_traces(skill_id=skill_id, status=status, limit=limit)
    return json.dumps(traces, default=str)


@mcp.tool(
    name="trace_errors",
    description="Get failed traces with full step-level error details. Shows exactly which step failed, with what input, and what error occurred.",
)
async def trace_errors(skill_id: str | None = None, limit: int = 10) -> str:
    ts = get_trace_store()
    await ts.initialize()
    errors = await ts.get_error_traces(skill_id=skill_id, limit=limit)
    return json.dumps(errors, default=str)


# ── Retrieval & composition tools ──


@mcp.tool(
    name="skill_search",
    description="Search for skills matching a natural language task description. Returns ranked results with relevance scores.",
)
def skill_search(query: str, top_k: int = 5) -> str:
    store = get_skill_store()
    retriever = SkillRetriever(store)
    results = retriever.search(query, top_k=top_k)
    output = [
        {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "tags": skill.tags,
            "score": round(score, 4),
        }
        for skill, score in results
    ]
    return json.dumps(output)


@mcp.tool(
    name="skill_compose",
    description="Compose multiple skills into a new pipeline. Skills execute in sequence. Returns a preview definition — use skill_create to persist it.",
)
def skill_compose(name: str, skill_ids: list[str], output_mappings: dict | None = None, tags: list[str] | None = None) -> str:
    store = get_skill_store()
    try:
        composed = compose_skills(name, skill_ids, store, output_mappings, tags)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    errors = composed.validate()
    if errors:
        return json.dumps({"error": "Composed skill validation failed", "validation_errors": errors})

    return json.dumps({
        "status": "preview",
        "message": "Composed skill definition ready for review. Use skill_create to persist.",
        "definition": skill_to_dict(composed),
    })


# ── Optimizer tools ──


@mcp.tool(
    name="optimizer_analyze",
    description="Analyze execution traces to find failure patterns and optimization opportunities. Returns ranked recommendations.",
)
async def optimizer_analyze(skill_id: str | None = None, min_samples: int = 5) -> str:
    ts = get_trace_store()
    await ts.initialize()
    optimizer = _get_optimizer()
    recommendations = await optimizer.analyze(skill_id=skill_id, min_samples=min_samples)
    result = [
        {
            "id": r.id,
            "skill_id": r.skill_id,
            "type": r.type,
            "severity": r.severity,
            "description": r.description,
            "affected_step_ids": r.affected_step_ids,
            "suggested_change": r.suggested_change,
            "confidence": round(r.confidence, 4),
            "evidence": r.evidence,
        }
        for r in recommendations
    ]
    return json.dumps(result)


@mcp.tool(
    name="optimizer_apply",
    description="Apply a specific optimization recommendation, updating the skill definition. A .backup is saved automatically.",
)
async def optimizer_apply(recommendation_id: str) -> str:
    ts = get_trace_store()
    await ts.initialize()
    optimizer = _get_optimizer()
    # Must run analyze first to populate recommendations
    if recommendation_id not in optimizer._recommendations:
        return json.dumps({"error": f"Recommendation '{recommendation_id}' not found. Run optimizer_analyze first."})
    result = await optimizer.apply(recommendation_id)
    return json.dumps(result, default=str)


@mcp.tool(
    name="optimizer_status",
    description="Get the current optimizer state: list of pending recommendations.",
)
def optimizer_status(skill_id: str | None = None) -> str:
    # Create a lightweight instance just to read stored recommendations
    optimizer = _get_optimizer()
    recs = optimizer.get_recommendations(skill_id=skill_id)
    result = [
        {
            "id": r.id,
            "skill_id": r.skill_id,
            "type": r.type,
            "severity": r.severity,
            "description": r.description,
            "confidence": round(r.confidence, 4),
            "applied": r.applied,
        }
        for r in recs
    ]
    return json.dumps(result)


def _dict_to_skill(d: dict) -> SkillDefinition:
    from skill_engine.models.skill import StepDefinition, Criteria, RetryPolicy

    steps = []
    for s in d.get("steps", []):
        success = s.get("success_criteria", {"type": "always"})
        failure = s.get("failure_criteria")
        retry = s.get("retry", {})
        steps.append(StepDefinition(
            id=s["id"],
            name=s.get("name", s["id"]),
            description=s.get("description", ""),
            tool=s.get("tool", ""),
            depends_on=s.get("depends_on", []),
            input_mapping=s.get("input_mapping", {}),
            success_criteria=Criteria(
                type=success.get("type", "always"),
                path=success.get("path"),
                expected=success.get("expected"),
            ),
            failure_criteria=Criteria(
                type=failure["type"],
                path=failure.get("path"),
                expected=failure.get("expected"),
            ) if failure else None,
            retry=RetryPolicy(
                max_attempts=retry.get("max_attempts", 1),
                backoff=retry.get("backoff", "none"),
                backoff_base_seconds=retry.get("backoff_base_seconds", 1.0),
            ),
            timeout_seconds=s.get("timeout_seconds", 60),
        ))

    return SkillDefinition(
        id=d["id"],
        name=d.get("name", d["id"]),
        version=d.get("version", "1.0.0"),
        description=d.get("description", ""),
        tags=d.get("tags", []),
        timeout_seconds=d.get("timeout_seconds", 300),
        max_concurrency=d.get("max_concurrency", 10),
        input_schema=d.get("input_schema", {}),
        output_schema=d.get("output_schema", {}),
        steps=steps,
    )


# ── Helpers ──


def _generate_skill_md(skills_dir: str, skill: SkillDefinition) -> str:
    """Generate a SKILL.md skeleton for Claude Code discovery."""
    import os as _os
    skill_dir = _os.path.join(skills_dir, skill.id)
    _os.makedirs(skill_dir, exist_ok=True)

    md_content = f"""---
name: {skill.name}
description: {skill.description or 'No description provided.'}
metadata:
  author: skill-engine
  version: "{skill.version}"
---

# {skill.name}

{skill.description or 'No description provided.'}

## Workflow
<!-- Describe the workflow steps here -->

## Input
<!-- Document input parameters here -->

## Output
<!-- Document expected output here -->
"""
    md_path = _os.path.join(skill_dir, "SKILL.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    return md_path


# ── Skill import ──


@mcp.tool(
    name="skill_import",
    description="Import an external skill from a SKILL.md file path. Parses frontmatter, body workflow, and scripts/ directory to generate a YAML wrapper. The original skill is NOT modified — use skillOverrides in settings.json to disable the original version after verification.",
)
def skill_import(path: str, scope: str = "project") -> str:
    import os as _os
    import re as _re
    from pathlib import Path

    md_path = Path(path)
    if not md_path.exists():
        return json.dumps({"error": f"SKILL.md not found at: {path}"})

    # Parse SKILL.md
    content = md_path.read_text(encoding="utf-8")
    frontmatter = {}
    body_lines: list[str] = []

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    frontmatter[key.strip()] = val.strip().strip('"')
            body_lines = parts[2].strip().split("\n")
    else:
        body_lines = content.strip().split("\n")

    skill_name = frontmatter.get("name", md_path.parent.name)
    skill_desc = frontmatter.get("description", "")

    # Extract workflow steps from body
    steps = _extract_steps_from_md(body_lines)

    # Scan for scripts/ directory
    skill_src_dir = md_path.parent
    scripts_dir = skill_src_dir / "scripts"
    script_files: list[str] = []
    if scripts_dir.is_dir():
        script_files = sorted(f.name for f in scripts_dir.iterdir() if f.suffix == ".py")

    # Generate YAML wrapper
    yaml_steps = []
    for i, step_info in enumerate(steps):
        step_id = f"step-{i + 1}"
        tool_cmd = step_info.get("tool", "")
        # If a scripts/ file matches, use it as the tool command
        if not tool_cmd and script_files:
            matched = [s for s in script_files if s.replace(".py", "") in step_info.get("title", "").lower()]
            if matched:
                tool_cmd = f"python3 {skill_src_dir / 'scripts' / matched[0]}"
            elif i == 0 and script_files:
                tool_cmd = f"python3 {skill_src_dir / 'scripts' / script_files[0]}"

        yaml_steps.append({
            "id": step_id,
            "name": step_info.get("title", f"Step {i + 1}"),
            "description": step_info.get("description", ""),
            "tool": tool_cmd or "echo",
            "depends_on": [f"step-{d}" for d in step_info.get("depends_on", [])],
            "input_mapping": {},
            "success_criteria": {"type": "always"},
            "timeout_seconds": 60,
        })

    # If no steps found, create a single fallback step
    if not yaml_steps:
        if script_files:
            tool_cmd = f"python3 {skill_src_dir / 'scripts' / script_files[0]}"
        else:
            tool_cmd = "echo"
        yaml_steps = [{
            "id": "main",
            "name": "Main",
            "description": "Execute the skill's main logic",
            "tool": tool_cmd,
            "depends_on": [],
            "input_mapping": {},
            "success_criteria": {"type": "always"},
            "timeout_seconds": 120,
        }]

    definition = {
        "id": f"imported-{skill_name}",
        "name": skill_name,
        "version": "1.0.0",
        "description": skill_desc,
        "tags": ["imported"],
        "timeout_seconds": 300,
        "max_concurrency": 1,
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {},
        "steps": yaml_steps,
    }

    # Save the YAML and copy SKILL.md
    store = get_skill_store()
    skill_id = f"imported-{skill_name}"

    if store.get(skill_id):
        return json.dumps({"error": f"Imported skill '{skill_id}' already exists. Delete it first or use a different name."})

    try:
        skill_def = _dict_to_skill(definition)
        skill_def.id = skill_id
    except Exception as e:
        return json.dumps({"error": f"Failed to create skill definition: {e}"})

    errors = skill_def.validate()
    if errors:
        return json.dumps({"error": "Generated skill validation failed", "validation_errors": errors})

    store.save(skill_def)

    # Copy SKILL.md into skills/imported-<name>/
    imported_dir = _os.path.join(store.skills_dir, skill_id)
    _os.makedirs(imported_dir, exist_ok=True)
    imported_md = _os.path.join(imported_dir, "SKILL.md")
    with open(imported_md, "w", encoding="utf-8") as f:
        f.write(content)

    return json.dumps({
        "status": "imported",
        "skill_id": skill_id,
        "skill_name": skill_name,
        "steps_generated": len(yaml_steps),
        "scripts_found": script_files,
        "next": f"Add skillOverrides to .claude/settings.json to disable the original: {{\"skillOverrides\": {{\"{skill_name}\": \"off\"}}}}",
    })


def _extract_steps_from_md(body_lines: list[str]) -> list[dict]:
    """Extract workflow steps from markdown body text."""
    import re as _re
    steps: list[dict] = []
    in_workflow = False
    current_step: dict | None = None

    for line in body_lines:
        stripped = line.strip()

        # Detect workflow section
        if _re.match(r"^#{1,3}\s*(Workflow|Steps|工作流|步骤)", stripped, _re.IGNORECASE):
            in_workflow = True
            continue

        # End of workflow section
        if in_workflow and _re.match(r"^#{1,3}\s+", stripped) and not _re.match(r"^#{1,3}\s*(Workflow|Steps|工作流|步骤)", stripped, _re.IGNORECASE):
            in_workflow = False
            if current_step:
                steps.append(current_step)
                current_step = None
            continue

        if not in_workflow:
            continue

        # Numbered or bullet step: "1. Do X" or "- Do X"
        m = _re.match(r"^(?:\d+[\.\)]\s*|[-*]\s+)(.+)", stripped)
        if m:
            if current_step:
                steps.append(current_step)
            current_step = {"title": m.group(1).strip(), "description": "", "depends_on": []}
        elif current_step and stripped and not stripped.startswith("#"):
            current_step["description"] += (" " if current_step["description"] else "") + stripped

    if current_step:
        steps.append(current_step)

    return steps


# ── Optimizer singleton fix ──

_optimizer: OptimizerAgent | None = None


def _get_optimizer() -> OptimizerAgent:
    global _optimizer
    if _optimizer is None:
        ts = get_trace_store()
        store = get_skill_store()
        analyzer = TraceAnalyzer(ts)
        _optimizer = OptimizerAgent(ts, store, analyzer)
    return _optimizer


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
