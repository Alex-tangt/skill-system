# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Skill-System is a microkernel-based skill management platform for AI agents. Skills follow the **Agent Skills open standard** (SKILL.md). Skill-System provides:

- **Skill metadata management** — CRUD on SKILL.md files with `.backup` safety
- **Plugin architecture** — Extensible via BasePlugin interface (internal + external MCP servers)
- **Pipeline v0.3** — Transcript-native recording → real-time segmentation → LLM analysis → skill evolution (analysis & evolution share context; validation is independent)
- **Trace storage** — SQLite WAL mode with v0.2 schema (LLM context fields)

Runs as a stdio MCP server consumable by Claude Code or any MCP client.

## Commands

```bash
pip install -e ".[dev]"                          # install with dev deps
python -m pytest tests/ -v                       # run all tests (61)
python -m skill_engine.kernel.server             # start kernel MCP server (stdio)
```

## Architecture (v0.3)

### Directory structure

```
src/skill_engine/
├── kernel/                    # Microkernel core
│   ├── server.py              #   MCP server (20 tools: 16 v0.2 + 4 v0.3)
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
├── pipeline/                  # Pipeline v0.3 (NEW — 16 files)
│   ├── transcript_reader.py   #   Claude Code transcript JSONL lazy reader
│   ├── models.py              #   Segment, ExecutionAnalysis, SkillPatch, etc.
│   ├── segment_store.py       #   Segment SQLite persistence + chain traversal
│   ├── segmenter.py           #   User-message segmentation + priority truncation
│   ├── segment_watcher.py     #   Task 1: real-time transcript watcher
│   ├── llm_client.py          #   LLMClient protocol + built-in analysis tools
│   ├── llm_client_impl.py     #   Anthropic-compatible client (stdlib, zero deps)
│   ├── analysis_prompt.py     #   Phase A prompt builder
│   ├── analysis_runner.py     #   Phase A: LLM agent loop analysis
│   ├── evolution_runner.py    #   Phase B: analysis → concrete patch
│   ├── analyzer_evolver.py    #   Task 2: Phase A+B orchestrator
│   ├── validator.py           #   Task 3: dual-layer validation (L1 mech + L2 LLM)
│   ├── metric_monitor.py      #   Independent signal source (pure SQL)
│   ├── meta_signal_detector.py #  Task 4: analysis skill self-optimization
│   └── pipeline_store.py      #   Unified DB: segments+analyses+traces+records
├── plugins/
│   └── data_pipeline/         # Data Pipeline Plugin (old, retained for compat)
│       ├── plugin.py          #   DataPipelinePlugin
│       ├── extractors.py      #   BaseExtractor + 3 regex implementations
│       ├── dedup.py           #   BaseDedup + SHA256
│       └── models.py          #   HistoryEvent, PipelineStatus
└── hooks/
    └── capture.py             # Zero-dependency Claude Code hook script (old, retained)
plugins.yaml                   # Plugin configuration (declarative)
```

### Data flow (v0.3)

```
Claude Code Session
  │
  │  Transcript JSONL (auto-generated, no hooks needed)
  │  ~/.claude/projects/<sanitized_cwd>/<session_id>.jsonl
  │
  ├── Segment Watcher (real-time: next user msg → prev segment ready)
  │     │
  │     ▼
  │   SegmentStore (SQLite: segments table)
  │     │
  │     ▼
  │   Analyzer-Evolver (shared context, two LLM phases)
  │     ├── Phase A: Analyze execution → SkillJudgment[] + EvolutionSuggestion[]
  │     └── Phase B: Generate concrete patch → SkillPatch
  │     │
  │     ├──► execution_analyses table (persisted results)
  │     ├──► analysis_traces table (LLM self-recording, for Meta Signal Detector)
  │     └──► Validator (L1 mechanical check → L2 LLM semantic check → pass/reject)
  │           │
  │           ▼ pass
  │         Apply Patch → new SKILL.md version
  │
  ├── Metric Monitor (pure SQL, scans skill_records health → pushes alerts to queue)
  └── Meta Signal Detector (low-freq, scans analysis_traces → optimizes analysis skill)
```

### Old data flow (v0.2, retained for compat)

```
Claude Code Session
  └── PostToolUse hooks → capture.py → history.db
        │
        │ pipeline_run (manual trigger)
        ▼
      Data Pipeline Plugin → regex extractors → traces.db
```

### Kernel MCP server (20 tools)

**Skill CRUD (5):** `skill_list`, `skill_get`, `skill_create`, `skill_update`, `skill_delete`
**Search (1):** `skill_search`
**Trace (3):** `trace_get`, `trace_list`, `trace_errors`
**Plugin mgmt (5):** `plugin_list`, `plugin_health`, `plugin_config`, `pipeline_run`, `pipeline_status`
**Pipeline v0.3 (4):** `pipeline_segments`, `pipeline_segment_get`, `pipeline_analyze`, `pipeline_watch`

### Pipeline v0.3 key concepts

- **Segment** — One user message + its full execution context (assistant thoughts + tool calls). Linked in a bidirectional chain via prev_id/next_id.
- **Trigger** — Next user message arrival → prev segment's next is completed → immediately pushed to analysis queue. Last segment waits for session end (fallback). No timers, no polling.
- **Analyzer-Evolver** — Phase A (analysis) and Phase B (evolution/patch) share the same segment context to avoid information loss between analysis conclusions and patch generation.
- **Validator** — Independent of execution context. Input: old SKILL.md + patch + change_summary + metrics. Output: pass/reject/needs_review. Layer 1 is deterministic (linter), Layer 2 is LLM (semantic, low-frequency).
- **Layer isolation** — Only user-agent execution data enters `segments` table. Analysis LLM self-recording goes to `analysis_traces` (separate table). Meta optimization modifies analysis skill's SKILL.md directly — does NOT produce new segments. This breaks infinite recursion.
- **Manual start** — Pipeline does NOT auto-start on MCP server launch. Call `pipeline_watch` explicitly. This is intentional: the analysis skill (SKILL.md for the analysis LLM) hasn't been created yet.

### Plugin system

Plugins implement `kernel/plugin_interface.py::BasePlugin`:
- `api_version` — Must match `plugin_manager.KERNEL_API_VERSION` ("0.2")
- `initialize()` / `health_check()` / `shutdown()` — Lifecycle
- `list_mcp_tools()` / `call_tool()` — MCP tool exposure

Two modes:
- **Internal** — Imported as Python module, shares kernel process. Crash = kernel crash.
- **External** — Independent MCP server subprocess, connected via `ClientSession`.

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
- TraceStore and SegmentStore use `PRAGMA journal_mode=WAL` for concurrent reads.
- Hook scripts (`capture.py`) must be zero-dependency — stdlib only.
- No `sqlite3` CLI — query DBs with `python3 -c "import sqlite3; ..."`.
- Pipeline LLM client (`llm_client_impl.py`) uses stdlib `urllib` — zero additional dependencies.

## Key docs

| File | Content |
|------|---------|
| `docs/pipeline-refactor-v0.3.md` | **v0.3 pipeline architecture** |
| `docs/openspace-architecture-insights.md` | OpenSpace patterns worth borrowing |
| `docs/architecture-v0.2.md` | v0.2 architecture direction |
| `docs/DEVELOPMENT.md` | Development tracker & progress |

## Known issues

- Pipeline is **manual-start**: call `pipeline_watch` to begin monitoring. Analysis skill SKILL.md not yet created.
- Old `capture.py` hook still runs; `tool_output_json` is always `{}` (hook stdin lacks `tool_result`). v0.3 pipeline bypasses this by reading transcript directly.
- `execution_traces.llm_model` is always None — old hook doesn't capture model info.
- Trace DB timestamps are REAL (unix epoch), not ISO strings.
