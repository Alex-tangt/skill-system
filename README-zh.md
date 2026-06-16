# Skill-System

**基于 Transcript 的技能进化平台。** 技能遵循 [Agent Skills 开放标准](https://agentskills.io)（SKILL.md）。Pipeline v0.3 直接读取 Claude Code transcript，分析 agent 执行过程，自动进化技能。

## 亮点

- **Transcript 原生** — 无需 hook。直接读取 Claude Code 内置 transcript JSONL。
- **实时分析** — 按用户消息分段，每次任务完成后即时分析。
- **技能自我进化** — FIX（修复）、DERIVED（特化）、CAPTURED（提取）三种进化模式。
- **带测试用例的验证器** — 累积回归测试，防止过拟合单个错误。
- **内置调试面板** — Flask Web UI，查看 segments、分析结果、技能、验证器。

以 stdio MCP Server 方式运行（20 个工具），可被 Claude Code 或任何 MCP 客户端消费。

## 快速开始

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v                         # 80 个测试
python -m skill_engine.kernel.server               # 启动 MCP Server
python3 scripts/bootstrap_pipeline.py --dry-run    # 分段当前会话
python3 scripts/bootstrap_pipeline.py              # 分段 + 分析
python3 -m skill_engine.dashboard --port 7788      # 调试面板
```

## 架构（v0.3）

```
Claude Code Session
  │  Transcript JSONL（自动生成，无需 hook）
  │
  ├── Segment Watcher（实时：下一条用户消息 → 前一段可分析）
  ├── Analyzer-Evolver（Phase A: 诊断 → Phase B: patch with validate→fix）
  ├── Metric Monitor（纯 SQL 信号源）
  └── Meta Signal Detector（低频分析 skill 优化）
```

## MCP 工具（20 个）

| 类别 | 工具 |
|------|------|
| Skill CRUD | `skill_list`, `skill_get`, `skill_create`, `skill_update`, `skill_delete` |
| 搜索 | `skill_search` |
| Trace | `trace_get`, `trace_list`, `trace_errors` |
| 插件 | `plugin_list`, `plugin_health`, `plugin_config`, `pipeline_run`, `pipeline_status` |
| Pipeline v0.3 | `pipeline_segments`, `pipeline_segment_get`, `pipeline_analyze`, `pipeline_watch` |
| 验证器 | `pipeline_validator_add_case`, `pipeline_validator_cases` |

## 文档

- [pipeline-refactor-v0.3.md](docs/pipeline-refactor-v0.3.md) — 完整架构设计
- [openspace-architecture-insights.md](docs/openspace-architecture-insights.md) — OpenSpace 设计洞察
- [DEVELOPMENT.md](docs/DEVELOPMENT.md) — 开发日记
- [CLAUDE.md](CLAUDE.md) — Claude Code 会话指引

## License

MIT
