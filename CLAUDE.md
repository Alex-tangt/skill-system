# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Skill Engine is an MCP server that manages AI agent skills — creating, executing, tracing, optimizing, and composing them. Skills are YAML-defined DAGs of sub-steps. Runs as a stdio MCP server consumable by Claude Code, Cursor, or any MCP client.

## Commands

```bash
pip install -e ".[dev]"                 # install with dev deps
python -m pytest tests/ -v              # run all tests
python -m pytest tests/unit/test_dag_executor.py -v  # single test file
python -m skill_engine.server           # start MCP server (stdio)
```

## Skill 执行规则

本项目所有 skill 通过 Skill Engine 统一执行：

- **发现**: Skill 工具自动发现 `skills/*/SKILL.md`，由 `.claude-plugin/plugin.json` 注册
- **执行**: **必须使用 `skill_execute` MCP 工具**。禁止直接调用 `skills/*/scripts/*.py`
- **原因**: skill_execute 自动提供输入校验 + 超时/重试 + SQLite 追踪。绕过即无 trace，优化器不可用
- **导入外部 skill**: 通过 `skill_import` 纳入管理后，同样走 skill_execute 执行

## Dev Diary Skill

项目开发日记，通过 `skill_execute` 执行 (skill_id=`dev-diary`)：

```bash
skill_execute dev-diary --input '{"operation": "add", "title": "...", "priority": "high"}'
skill_execute dev-diary --input '{"operation": "done", "title": "...", "description": "解决方案"}'
skill_execute dev-diary --input '{"operation": "list", "filter": "all"}'
skill_execute dev-diary --input '{"operation": "update", "title": "...", "priority": "medium"}'
```

日记存储在 `docs/DEVELOPMENT.md`，由 `skills/dev-diary.yaml` (DAG) + `skills/dev-diary/scripts/diary.py` (脚本) 支撑。

## Architecture

### Data flow

Skills are YAML files (`skills/{id}.yaml`) loaded by `SkillStore`. Execution goes through `DAGExecutor`:

```
skill_execute MCP tool
  → SkillStore.get(skill_id)
  → DAGExecutor.execute(skill, input, tracer=Tracer(trace_store))
    → validator.validate_input  (JSON Schema check at entry)
    → skill.topological_order   (Kahn's algorithm, detects cycles)
    → group by level → asyncio.gather with Semaphore(max_concurrency)
    → per step: resolver.resolve_input → tool call with asyncio.wait_for(timeout) → criteria.evaluate_success
    → on failure: recursively _skip_downstream
    → Tracer writes execution_traces + step_traces to SQLite (WAL mode, aiosqlite)
```

### Reference system (`engine/resolver.py`)

Step `input_mapping` supports exactly two reference forms:
- `$input.x.y` — access skill input
- `$steps.<step_id>.output.z` — access upstream step output

Short `$steps.x` without step ID is rejected. Resolver uses `functools.reduce` for dotted-path traversal — no jsonpath dependency.

### Retry semantics

A step retries only when an **exception** or **timeout** occurs. If the tool runs successfully but output doesn't match `success_criteria`, the retry loop continues — `failure_criteria` is not consulted. Known tool (`Unknown tool: X`) is a hard failure with no retry.

### Skill composition (`retrieval/retriever.py`)

`compose_skills` creates a temporary (non-persisted) SkillDefinition by concatenating DAGs. Step IDs are prefixed (`_s0_`, `_s1_`) to avoid collisions. Terminal steps of skill N become dependencies of skill N+1's root steps. The result is returned for preview; `skill_create` must be called separately to persist.

### Optimizer (passive mode)

No background scanning. `optimizer_analyze` runs on-demand, queries trace data for 4 patterns (failure hotspots, timeouts, retry gaps, validation gaps), returns ranked `OptimizationRecommendation` objects. `optimizer_apply` patches the skill YAML, bumps minor version, saves `.backup`.

### Tool model

`ToolRegistry` maps step `tool` names to Python callables. The only built-in is `echo`. Real skills register their own tools (HTTP calls, code exec, etc.) here before execution.

### Conventions

- Every `.py` file needs `from __future__ import annotations` (Python 3.10 compat for `X | None` syntax).
- MCP tool functions return JSON strings. Async tools must `await trace_store.initialize()` before use.
- Skill saves automatically create `.backup` of the previous YAML.
