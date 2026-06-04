# Skill Engine

A dedicated skill execution platform for AI agents. Create, execute, trace, and optimize agent skills — defined as YAML DAGs — with zero friction.

## What is this?

Skill Engine is an MCP (Model Context Protocol) server that manages AI agent skills. Skills are **YAML-defined DAGs** of sub-steps, executed with:

- **Topological ordering** (Kahn's algorithm) with cycle detection
- **Layered parallelism** (concurrent steps within each dependency level)
- **Built-in tracing** — every execution recorded to SQLite for analysis
- **Passive optimizer** — failure pattern detection from trace history, with auto-patching

Runs as a stdio MCP server consumable by Claude Code, Cursor, or any MCP client.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Start the MCP server
python -m skill_engine.server
```

Configure your MCP client (`.mcp.json`):

```json
{
  "mcpServers": {
    "skill-engine": {
      "command": "python3",
      "args": ["-m", "skill_engine.server"],
      "env": {
        "SKILL_ENGINE_SKILLS_DIR": "./skills",
        "SKILL_ENGINE_TRACES_DB": "./traces/traces.db"
      }
    }
  }
}
```

## Architecture

### Data Flow

```
skill_execute MCP tool
  → SkillStore.get(skill_id)
  → DAGExecutor.execute(skill, input, tracer=Tracer(trace_store))
    → validator.validate_input       # JSON Schema validation
    → skill.topological_order        # Kahn's algorithm
    → group by level → asyncio.gather # concurrent execution
    → per step: resolve → execute → evaluate
    → on failure: skip downstream
    → Tracer writes to SQLite
```

### Core Components

| Component | Role |
|---|---|
| `SkillStore` | File-system CRUD for skill YAML definitions, auto `.backup` |
| `DAGExecutor` | Executes skill DAGs with timeout, retry, parallelism |
| `TraceStore` | SQLite (WAL mode, aiosqlite) storage of execution traces |
| `ToolRegistry` | Maps step `tool` names to Python callables |
| `Resolver` | `$input.x.y` and `$steps.<id>.output.z` references |
| `Validator` | JSON Schema validation at DAG entry |
| `Optimizer` | Passive analysis of trace data for failure patterns |

### MCP Tools (19 total)

**Skill CRUD:** `skill_list`, `skill_get`, `skill_create`, `skill_update`, `skill_delete`

**Execution:** `skill_execute`

**Tracing:** `trace_get`, `trace_list`, `trace_errors`

**Composition:** `skill_search`, `skill_compose`, `skill_analyze`

**Optimization:** `optimizer_analyze`, `optimizer_apply`, `optimizer_status`

**Import:** `skill_import`

## Skills Directory

Skills live in `skills/` as YAML definitions. Example:

```yaml
id: hello-world
name: Hello World
version: "1.0.0"
steps:
  - id: greet
    tool: echo
    input_mapping:
      message: "$input.name"
  - id: farewell
    tool: echo
    input_mapping:
      message: "$steps.greet.output.message"
    depends_on: [greet]
```

## Development

```bash
pip install -e ".[dev]"          # install with dev deps
python -m pytest tests/ -v       # run all tests (32 tests)
python -m skill_engine.server    # start MCP server (stdio)
```

## License

MIT — see [LICENSE](LICENSE).
