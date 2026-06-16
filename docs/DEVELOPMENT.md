# Development Tracker

## 已完成

### 第一阶段：MVP 核心（2026-06-03）

- [x] **1A 基础层** — 项目脚手架、数据模型、SkillStore、TraceStore、ToolRegistry
- [x] **1B MCP 骨架** — server.py、stdio 传输、5 个 CRUD 工具
- [x] **1C DAG 执行引擎** — Kahn 拓扑排序、分层并行、重试 + backoff
- [x] **1D 追踪系统** — Tracer、trace_get/list/errors 3 个工具
- [x] **1E 检索与组合** — TF-IDF SkillRetriever、skill_search/compose
- [x] **1F 优化器（被动模式）** — TraceAnalyzer、OptimizerAgent
- [x] **1G 测试与文档** — 32 个测试、CLAUDE.md、示例 skill

### 第二阶段：模块化创建（2026-06-03）

- [x] **8 AI 分解引擎** — decompose_task()：自然语言 → DAG
- [x] **skill_analyze MCP 工具** — 任务 → 可审阅的 skill 预览

### dev-diary Skill 开发（2026-06-04）

- [x] **Dev-Diary Skill** (#P-4) — diary.py + SKILL.md + CLAUDE.md。端到端验证通过。

### 第三阶段：工具执行闭环修复（2026-06-04）

- [x] **#P-1 DAG 引擎支持 shell 命令模式** — step.tool 不在 registry → 当作命令执行
- [x] **PDF-to-Markdown Skill** — 端到端验证通过
- [x] **#P-3 skill_import 方向明确** — 对齐 Agent Skills 开放标准
- [x] **工具注册闭环（#P-1）** — tool_register MCP 工具

### Skill 触发链路修复（2026-06-04）

- [x] **#P-8 Skill 触发链路断裂** — 两套系统互通
- [x] **#P-3 skill_import 实现** — SKILL.md 解析 + YAML wrapper

### v0.2 架构方向讨论 + 重构（2026-06-06）

- [x] **架构方向重新评估** — 10 项关键决策。详见 [architecture-v0.2.md](architecture-v0.2.md)
- [x] **Phase 0-4 v0.2 重构完成** (#P-9) — 188→61 测试，自定义 YAML DAG → SKILL.md 标准，微内核（16 MCP 工具）+ Data Pipeline Plugin + capture.py

---

### Pipeline v0.3: OpenSpace 调研 + 架构设计（2026-06-08）

- [x] **OpenSpace 深度调研** — evolver/patch/store/conversation_formatter/quality/registry。提炼 8 类模式。写入 [openspace-architecture-insights.md](openspace-architecture-insights.md)
- [x] **Transcript 机制验证** — 路径推导、消息分类、387 条消息/20 user messages 验证正确
- [x] **架构讨论** — 实时触发（链式 segment）、Analyzer+Optimizer 合并、Validator 独立、分层隔离、Meta 信号驱动
- [x] **架构文档** — [pipeline-refactor-v0.3.md](pipeline-refactor-v0.3.md)

### Pipeline v0.3: 代码实现（2026-06-08）

- [x] **基础设施** — transcript_reader + models（8 dataclass）+ segment_store
- [x] **Segmentation** — segmenter（优先级截断）+ segment_watcher（Task 1）
- [x] **Analyzer-Evolver** — llm_client（协议 + 实现）+ analysis_prompt/runner + evolution_runner + analyzer_evolver
- [x] **Validator + Monitor** — validator（双层验证）+ metric_monitor（纯 SQL）+ meta_signal_detector（Task 4）
- [x] **Pipeline DB** — pipeline_store（6 表：segments/execution_analyses/analysis_traces/skill_records/validator_test_cases/validator_runs）
- [x] **Server 集成** — 4+2 MCP 工具、旧 pipeline 保留兼容

### Validator-as-Tool 重构（2026-06-08）

- [x] **Validator 设计修正** — 从独立阶段→Optimizer 内部工具（validate_patch/add_test_case/run_test_suite）
- [x] **分析输出简化** — 删除中间 JSON Schema，Phase A→自然语言诊断，+1440/-1918 行
- [x] **分析 skill** — `skills/pipeline-analyzer/SKILL.md` v1.0.0，自动注入为 prompt
- [x] **Validator 种子** — 5 基线测试用例
- [x] **Bootstrap 脚本** — `scripts/bootstrap_pipeline.py`（--dry-run/--seed-only）

### 项目完成冲刺（2026-06-08）

- [x] **Phase 1: 稳定性** — 增量 Segmenter、Watcher 自动启动、LLM 3 次重试、+19 tests（80 total）
- [x] **Phase 2: Skill 生态** — hello-world scripts/、进化 E2E（模拟错误→诊断→DIFF patch）完整闭环
- [x] **Phase 3: 调试面板** — `dashboard.py`（Flask, 6 routes）
- [x] **Phase 4: 清理 + README** — README.md + README-zh.md v0.3 同步
- [x] **Phase 5: 验证** — 80 tests pass

---

## 当前状态

| 指标 | 数值 |
|------|------|
| Python 文件 | 37 |
| Tests | 80 |
| MCP Tools | 22 |
| Skills | 7（含 pipeline-analyzer） |
| DB 表 | 6 |

```
启动: python3 -m skill_engine.kernel.server       # MCP server
引导: python3 scripts/bootstrap_pipeline.py        # Segment + Analyze
面板: python3 -m skill_engine.dashboard --port 7788  # Flask dashboard
测试: python -m pytest tests/ -v                   # 80 tests
```

---

## 下一步

- [ ] **旧组件归档** — 废弃 capture.py/history.db/extractors.py/data_pipeline plugin
- [ ] **生产监控** — 积累 analysis_traces，触发 Meta Signal Detector
- [ ] **更多 Skills** — git-status/run-tests/markdown-stats 的 scripts/ 补全
- [ ] **Dashboard 增强** — 版本 DAG 图（Mermaid.js）、Skill 详情页

---

## 工具清单

```
v0.2 保留（9）:
skill_list  skill_get  skill_create  skill_update  skill_delete
skill_search  trace_get  trace_list  trace_errors

v0.2 新增（5）:
plugin_list  plugin_health  plugin_config  pipeline_run  pipeline_status

v0.3 新增（6）:
pipeline_segments  pipeline_segment_get  pipeline_analyze  pipeline_watch
pipeline_validator_add_case  pipeline_validator_cases

v0.1 移除（7）:
skill_execute  skill_analyze  skill_compose  skill_import
optimizer_analyze  optimizer_apply  optimizer_status
```

---

## 关键文件索引

| 文件 | 用途 |
|------|------|
| `src/skill_engine/kernel/server.py` | MCP Server（22 工具） |
| `src/skill_engine/pipeline/` | **v0.3 pipeline**（16 文件） |
| `src/skill_engine/dashboard.py` | **调试面板**（Flask, 6 routes） |
| `src/skill_engine/plugins/data_pipeline/` | Data Pipeline Plugin（旧） |
| `skills/pipeline-analyzer/SKILL.md` | **分析 meta-skill** |
| `scripts/bootstrap_pipeline.py` | **一键引导脚本** |
| `docs/pipeline-refactor-v0.3.md` | **v0.3 架构方案** |
| `docs/openspace-architecture-insights.md` | OpenSpace 源码洞察 |
| `docs/architecture-v0.2.md` | v0.2 架构方向 |
| `docs/DEVELOPMENT.md` | 本文件 |
| `CLAUDE.md` | Claude Code 会话指引 |
