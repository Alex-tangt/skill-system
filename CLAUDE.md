# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Skill-System is a microkernel-based skill management platform for AI agents. Skills follow the **Agent Skills open standard** (SKILL.md) and are executed by the native Claude Code skill mechanism. Skill-System provides:

- **Skill metadata management** — CRUD on SKILL.md files with `.backup` safety (via `skill_store.py`)
- **Plugin architecture** — Extensible via BasePlugin interface (internal + external MCP servers)
- **Data pipeline** — Claude Code hooks capture LLM context → structured traces → optimizer (future)
- **Trace storage** — SQLite WAL mode (traces/traces.db) + History DB (traces/history.db)

Runs as a stdio MCP server consumable by Claude Code or any MCP client.

## Commands

```bash
pip install -e ".[dev]"                          # install with dev deps
python -m pytest tests/ -v                       # run all tests (61)
python -m skill_engine.kernel.server             # start kernel MCP server (stdio)
```

## Architecture (v0.2)

### Directory structure

```
src/skill_engine/
├── kernel/                    # Microkernel core
│   ├── server.py              #   MCP server (3 tools: skill_list/get/search)
│   ├── skill_store.py         #   SKILL.md CRUD + .backup (filesystem)
│   ├── trace_store.py         #   SQLite WAL trace storage (2 tables)
│   ├── plugin_manager.py      #   Plugin lifecycle (loads plugins.yaml). KERNEL_API_VERSION lives here.
│   ├── plugin_interface.py    #   BasePlugin ABC (api_version, initialize, health_check, shutdown, list_mcp_tools, call_tool)
│   ├── plugin_registry.py     #   PluginHandle registry
│   ├── retriever.py           #   TF-IDF skill search
│   ├── validator.py           #   JSON Schema input validation
│   └── models/
│       ├── skill_metadata.py  #   SKILL.md frontmatter + body model
│       └── trace.py           #   ExecutionTrace / StepTrace (v0.2 fields)
├── plugins/
│   └── data_pipeline/         # Data Pipeline Plugin
│       ├── plugin.py          #   DataPipelinePlugin (standalone class, called directly)
│       ├── extractors.py      #   BaseExtractor + 3 implementations (SkillTrigger, InputOutput, Error)
│       ├── dedup.py           #   BaseDedup + SHA256
│       └── models.py          #   HistoryEvent, PipelineStatus
└── hooks/
    └── capture.py             # Zero-dependency Claude Code hook script
plugins.yaml                   # Plugin configuration (declarative)
```

### Data flow

```
Claude Code Session
  ├── Native skill mechanism (execution)
  └── PostToolUse / UserPromptSubmit hooks
        │
        ▼
      capture.py  ──►  History DB (SQLite, traces/history.db)
        │                    │
        │                    │  pipeline.run() (direct Python call, NOT MCP tool)
        │                    ▼
        │              Data Pipeline Plugin
        │                ├── SHA256 dedup (history + trace level)
        │                ├── BaseExtractor chain (3 extractors)
        │                ├── Append logic (same session → same ExecutionTrace)
        │                └── Build ExecutionTrace + StepTrace
        │                    │
        │                    ▼
        │              Trace DB (SQLite, traces/traces.db)
        │
        └── Future: Optimizer Plugin reads Trace DB → suggests SKILL.md improvements
```

### Kernel MCP server (3 tools)

The kernel exposes a minimal 3-tool LLM interface. CRUD operations exist in `skill_store.py` but are not MCP tools.

**Read (3):** `skill_list`, `skill_get`, `skill_search`

Skill CRUD (`skill_create`/`skill_update`/`skill_delete`) and trace queries (`trace_get`/`trace_list`/`trace_errors`) are available via `skill_store.py` and `trace_store.py` — call them directly in Python if needed.

### Pipeline invocation

`pipeline_run` is NOT an MCP tool. Invoke directly:

```bash
python3 -c "
import asyncio
from skill_engine.plugins.data_pipeline.plugin import DataPipelinePlugin
dp = DataPipelinePlugin({
    'history_db_path': './traces/history.db',
    'trace_db_path': './traces/traces.db',
})
asyncio.run(dp.initialize())
result = asyncio.run(dp.run(limit=100))
print(result)
"
```

Append behavior: multiple `run()` calls on the same session append new steps to the existing ExecutionTrace (matched by session_id from history_events). Step-level dedup uses `context_ref` (= `history_events.dedup_hash`).

### Plugin system

Plugins implement `kernel/plugin_interface.py::BasePlugin`:
- `api_version` — Must match `plugin_manager.KERNEL_API_VERSION` ("0.2")
- `initialize()` / `health_check()` / `shutdown()` — Lifecycle
- `list_mcp_tools()` / `call_tool()` — MCP tool exposure

Two modes:
- **Internal** — Imported as Python module, shares kernel process. Crash = kernel crash.
- **External** — Independent MCP server subprocess, connected via `ClientSession`.

Configured in `plugins.yaml` at project root. Loaded by `plugin_manager.py`.

Note: `DataPipelinePlugin` is called directly from `server.py::get_pipeline()`, NOT via the plugin system. It does NOT implement `BasePlugin`.

### Extensibility (Open for extension)

Strategy interfaces with MVP concrete implementations:

| Interface | MVP | Swappable for |
|-----------|-----|---------------|
| `BaseExtractor` | 3 extractors (SkillTrigger, InputOutput, Error) | LLM-based extraction |
| `BaseDedup` | SHA256 exact match | Semantic/simhash dedup |
| `BasePlugin` | Internal module | External MCP server |

Note: `BaseTrigger` exists only as an interface concept; `triggers.py` was removed. Invoke pipeline manually.

### Skill format

Skills follow the **Agent Skills open standard** (agentskills.io):

```
skills/<name>/
├── SKILL.md          # YAML frontmatter + Markdown body
├── scripts/          # Executable code
├── references/       # Documentation
└── assets/           # Static resources
```

SKILL.md frontmatter: `name` (required, ≤64 chars), `description` (required, ≤1024 chars), `license`, `compatibility`, `metadata`, `allowed-tools`.

## Dev Diary

Project development diary at `docs/DEVELOPMENT.md`. Managed via diary script:

```bash
python3 skills/dev-diary/scripts/diary.py \
  --file docs/DEVELOPMENT.md \
  --operation add \
  --title "Task title" \
  --priority high \
  --description "Task description"

# Other operations: done, list, update
```

## Conventions

- Every `.py` file needs `from __future__ import annotations` (Python 3.10 compat for `X | None` syntax).
- MCP tool functions return JSON strings.
- Skill saves automatically create `.backup` of the previous SKILL.md.
- TraceStore uses `PRAGMA journal_mode=WAL` for concurrent reads.
- Hook scripts (`capture.py`) must be zero-dependency — stdlib only.
- No `sqlite3` CLI — query DBs with `python3 -c "import sqlite3; ..."`.

## Known issues

- `tool_output_json` is always `{}` — PostToolUse hook stdin does not include `tool_result`. `capture.py:109` defaults to `hook_input.get("tool_result", {})`. Tool output is only available in the transcript JSONL file.
- `execution_traces.llm_model` is always None — hook doesn't capture model info.
- Trace DB timestamps are REAL (unix epoch), not ISO strings.
- `SkillTriggerExtractor` only matches `skills/<name>/scripts/` paths in tool input — it does NOT match `skills/<name>/SKILL.md`. `InputOutputExtractor` catches everything else.
