# Skill-System

**Transcript-native skill evolution platform for AI agents.** Skills follow the [Agent Skills open standard](https://agentskills.io) (SKILL.md). Pipeline v0.3 reads Claude Code transcripts directly, analyzes agent execution, and evolves skills automatically.

## Highlights

- **Transcript-native** — No hooks needed. Reads Claude Code's built-in transcript JSONL.
- **Real-time analysis** — Segments conversation by user message, analyzes after each task.
- **Self-evolving skills** — FIX (repair), DERIVED (specialize), CAPTURED (extract) evolution modes.
- **Validator with test cases** — Accumulating regression tests prevent overfitting to single errors.
- **Built-in debug dashboard** — Flask web UI for inspecting segments, analyses, skills, and validator.

Runs as a stdio MCP server (20 tools) consumable by Claude Code or any MCP client.

## Quick Start

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v                         # 80 tests
python -m skill_engine.kernel.server               # MCP server (stdio)
python3 scripts/bootstrap_pipeline.py --dry-run    # segment current session
python3 scripts/bootstrap_pipeline.py              # segment + analyze
python3 -m skill_engine.dashboard --port 7788      # debug dashboard
```

### MCP Client Configuration

```json
{
  "mcpServers": {
    "skill-system": {
      "command": "python3",
      "args": ["-m", "skill_engine.kernel.server"],
      "env": {
        "SKILL_ENGINE_SKILLS_DIR": "./skills",
        "SKILL_ENGINE_PIPELINE_DB": "./traces/pipeline.db"
      }
    }
  }
}
```

## Architecture (v0.3)

```
Claude Code Session
  │  Transcript JSONL (auto-generated, no hooks)
  │
  ├── Segment Watcher (real-time: next user msg → prev segment ready)
  ├── Analyzer-Evolver (Phase A: diagnosis → Phase B: patch with validate→fix loop)
  ├── Metric Monitor (pure SQL signal source)
  └── Meta Signal Detector (low-frequency analysis skill optimizer)
```

### MCP Tools (20 total)

| Category | Tools |
|----------|-------|
| Skill CRUD | `skill_list`, `skill_get`, `skill_create`, `skill_update`, `skill_delete` |
| Search | `skill_search` |
| Trace | `trace_get`, `trace_list`, `trace_errors` |
| Plugins | `plugin_list`, `plugin_health`, `plugin_config`, `pipeline_run`, `pipeline_status` |
| Pipeline v0.3 | `pipeline_segments`, `pipeline_segment_get`, `pipeline_analyze`, `pipeline_watch` |
| Validator | `pipeline_validator_add_case`, `pipeline_validator_cases` |

### Key Components

| Component | Role |
|-----------|------|
| `pipeline/transcript_reader.py` | Claude Code transcript JSONL reader |
| `pipeline/segmenter.py` | User-message segmentation + priority-budgeted truncation |
| `pipeline/analyzer_evolver.py` | Phase A (diagnosis) + Phase B (patch with validate→fix) |
| `pipeline/validator.py` | Optimizer's testing toolkit with accumulated test cases |
| `pipeline/pipeline_store.py` | Unified SQLite DB (6 tables) |
| `kernel/server.py` | MCP server entry point |

## Skills Directory

```
skills/
├── pipeline-analyzer/     # Meta-skill: analyzes execution traces
├── hello-world/           # Demo skill with scripts/
├── dev-diary/             # Development diary management
├── git-status/            # Git repository status checker
├── run-tests/             # Test suite runner
├── markdown-stats/        # Markdown file analyzer
└── pdf-to-markdown/       # PDF text extractor
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v                           # run tests
python3 scripts/bootstrap_pipeline.py --dry-run      # verify segmentation
python3 scripts/bootstrap_pipeline.py                # segment + analyze
```

## Documentation

- [pipeline-refactor-v0.3.md](docs/pipeline-refactor-v0.3.md) — Full architecture design
- [openspace-architecture-insights.md](docs/openspace-architecture-insights.md) — OpenSpace patterns
- [DEVELOPMENT.md](docs/DEVELOPMENT.md) — Development tracker
- [CLAUDE.md](CLAUDE.md) — Claude Code session guidance

## License

MIT
