# Skill-System

A microkernel-based skill management platform for AI agents. Skills follow the **Agent Skills open standard** (SKILL.md), executed by the native Claude Code mechanism. Skill-System provides metadata management, a plugin architecture, and a data pipeline for trace extraction and optimization.

## What is this?

Skill-System is an MCP (Model Context Protocol) server that manages AI agent skills:

- **Open standard** — Skills use the [Agent Skills](https://agentskills.io) format (`SKILL.md`), compatible with Claude Code, Cursor, Copilot, and other tools
- **Microkernel architecture** — Core handles metadata + plugin coordination; plugins (data pipeline, optimizer) are independent MCP servers
- **Hook-based tracing** — Claude Code hooks capture LLM context (messages + CoT); a data pipeline extracts structured traces
- **Extensible** — Strategy interfaces (`BaseExtractor`, `BaseDedup`, `BaseTrigger`, `BasePlugin`) with MVP implementations, swappable without changing callers

Runs as a stdio MCP server consumable by any MCP client.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Start the kernel MCP server
python -m skill_engine.kernel.server
```

Configure your MCP client (`.mcp.json`):

```json
{
  "mcpServers": {
    "skill-system": {
      "command": "python3",
      "args": ["-m", "skill_engine.kernel.server"],
      "env": {
        "SKILL_ENGINE_SKILLS_DIR": "./skills",
        "SKILL_ENGINE_TRACES_DB": "./traces/traces.db",
        "SKILL_ENGINE_PLUGINS_CONFIG": "./plugins.yaml"
      }
    }
  }
}
```

Enable Claude Code hooks for trace capture (`.claude/settings.json`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "command": "python3 skills/../src/skill_engine/hooks/capture.py"
      }
    ]
  }
}
```

## Architecture

### Data Flow

```
Claude Code Session
  ├── Native skill mechanism (execution)
  └── Hooks (PostToolUse / UserPromptSubmit)
        │
        ▼
      capture.py ──► History DB ──► Data Pipeline Plugin ──► Trace DB
                                         │
                                         └── (future) Optimizer Plugin
```

### Core Components

| Component | Role |
|---|---|
| `kernel/server.py` | MCP server with 16 tools (skill CRUD, trace queries, plugin management, pipeline) |
| `kernel/skill_store.py` | File-system CRUD for SKILL.md files, auto `.backup` |
| `kernel/trace_store.py` | SQLite (WAL mode, aiosqlite) with v0.2 schema |
| `kernel/plugin_manager.py` | Plugin lifecycle (loads `plugins.yaml`) |
| `plugins/data_pipeline/` | Extracts structured traces from raw LLM context |
| `hooks/capture.py` | Zero-dependency script for Claude Code hook events |

### MCP Tools (16 total)

**Skill CRUD:** `skill_list`, `skill_get`, `skill_create`, `skill_update`, `skill_delete`

**Search:** `skill_search`

**Tracing:** `trace_get`, `trace_list`, `trace_errors`

**Plugins:** `plugin_list`, `plugin_health`, `plugin_config`

**Pipeline:** `pipeline_run`, `pipeline_status`

### Plugin System

Plugins implement `kernel/plugin_interface.py::BasePlugin`. Configured in `plugins.yaml`:

```yaml
plugins:
  data-pipeline:
    name: data-pipeline
    type: internal
    module: skill_engine.plugins.data_pipeline.plugin
    description: Extract structured traces from raw LLM context
    config:
      history_db_path: ./traces/history.db
```

### Extensibility

Four strategy interfaces with swappable implementations:

| Interface | MVP | Upgrade Path |
|-----------|-----|--------------|
| `BaseExtractor` | Regex (3 extractors) | LLM-based extraction |
| `BaseDedup` | SHA256 exact match | Semantic / SimHash dedup |
| `BaseTrigger` | Manual (`pipeline_run`) | Cron / event-driven |
| `BasePlugin` | Internal module | External MCP server |

## Skills Directory

Skills follow the Agent Skills standard. Each skill is a directory with a `SKILL.md`:

```
skills/
├── hello-world/
│   ├── SKILL.md
│   └── scripts/echo.py
├── pdf-to-markdown/
│   ├── SKILL.md
│   └── scripts/extract.py
└── dev-diary/
    ├── SKILL.md
    └── scripts/diary.py
```

Example `SKILL.md`:

```markdown
---
name: hello-world
description: A simple demonstration skill that echoes input text.
license: MIT
---
# Hello World

## Workflow
1. Echo the input text
2. Return the echoed result

## Input
- `text`: Any string to echo
```

## Development

```bash
pip install -e ".[dev]"                       # install with dev deps
python -m pytest tests/ -v                    # run all tests (61 tests)
python -m skill_engine.kernel.server          # start kernel MCP server
```

## Documentation

- [architecture-v0.2.md](docs/architecture-v0.2.md) — v0.2 architecture direction
- [DEVELOPMENT.md](docs/DEVELOPMENT.md) — development tracker
- [CLAUDE.md](CLAUDE.md) — guidance for Claude Code sessions

## License

MIT — see [LICENSE](LICENSE).
