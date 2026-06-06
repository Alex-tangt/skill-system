# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Skill-System is a microkernel-based skill management platform for AI agents. Skills follow the **Agent Skills open standard** (SKILL.md) and are executed by the native Claude Code skill mechanism. Skill-System provides:

- **Skill metadata management** — CRUD on SKILL.md files with `.backup` safety
- **Plugin architecture** — Extensible via BasePlugin interface (internal + external MCP servers)
- **Data pipeline** — Claude Code hooks capture LLM context → structured traces → optimizer (future)
- **Trace storage** — SQLite WAL mode with v0.2 schema (LLM context fields)

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
│   ├── server.py              #   MCP server (16 tools)
│   ├── skill_store.py         #   SKILL.md CRUD + .backup
│   ├── trace_store.py         #   SQLite WAL trace storage
│   ├── plugin_manager.py      #   Plugin lifecycle (loads plugins.yaml)
│   ├── plugin_interface.py    #   BasePlugin ABC (api_version negotiation)
│   ├── plugin_registry.py     #   PluginHandle registry
│   ├── retriever.py           #   TF-IDF skill search
│   ├── validator.py           #   JSON Schema input validation
│   └── models/
│       ├── skill_metadata.py  #   SKILL.md frontmatter + body model
│       └── trace.py           #   ExecutionTrace / StepTrace (v0.2 fields)
├── plugins/
│   └── data_pipeline/         # Data Pipeline Plugin (internal)
│       ├── plugin.py          #   DataPipelinePlugin (implements BasePlugin)
│       ├── extractors.py      #   BaseExtractor + 3 MVP implementations
│       ├── dedup.py           #   BaseDedup + SHA256
│       ├── triggers.py        #   BaseTrigger + Manual
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
        │                    │  pipeline_run (MCP tool, manual trigger)
        │                    ▼
        │              Data Pipeline Plugin
        │                ├── BaseDedup (SHA256 dedup)
        │                ├── BaseExtractor chain (3 MVP extractors)
        │                └── Build ExecutionTrace + StepTrace
        │                    │
        │                    ▼
        │              Trace DB (SQLite, traces/traces.db)
        │
        └── Future: Optimizer Plugin reads Trace DB → suggests SKILL.md improvements
```

### Kernel MCP tools (16)

**Skill CRUD (5):** `skill_list`, `skill_get`, `skill_create`, `skill_update`, `skill_delete`
**Search (1):** `skill_search`
**Trace (3):** `trace_get`, `trace_list`, `trace_errors`
**Plugin mgmt (5):** `plugin_list`, `plugin_health`, `plugin_config`, `pipeline_run`, `pipeline_status`

### Plugin system

Plugins implement `kernel/plugin_interface.py::BasePlugin`:
- `api_version` — Must match kernel `KERNEL_API_VERSION` ("0.2")
- `initialize()` / `health_check()` / `shutdown()` — Lifecycle
- `list_mcp_tools()` / `call_tool()` — MCP tool exposure

Two modes:
- **Internal** — Imported as Python module, shares kernel process. Crash = kernel crash.
- **External** — Independent MCP server subprocess, connected via `ClientSession`.

Configured in `plugins.yaml` at project root.

### Extensibility (Open for extension)

Four strategy interfaces with MVP concrete implementations:

| Interface | MVP | Swappable for |
|-----------|-----|---------------|
| `BaseExtractor` | Regex (3 extractors) | LLM-based extraction |
| `BaseDedup` | SHA256 exact match | Semantic/simhash dedup |
| `BaseTrigger` | Manual (pipeline_run) | Cron / event-driven |
| `BasePlugin` | Internal module | External MCP server |

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
