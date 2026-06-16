# Development Tracker

## 已完成

### 第一阶段：MVP 核心（2026-06-03）

- [x] **1A 基础层** — 项目脚手架、数据模型（SkillDefinition/StepDefinition/Criteria/Trace）、SkillStore（文件系统 CRUD + .backup）、TraceStore（SQLite WAL + aiosqlite）、ToolRegistry、builtin_tools/echo
- [x] **1B MCP 骨架** — server.py、stdio 传输、skill_list/get/create/update/delete 5 个 CRUD 工具
- [x] **1C DAG 执行引擎** — Kahn 拓扑排序、分层并行（asyncio.Semaphore 限流）、asyncio.wait_for 超时、重试 + exponential backoff、sync/async 双模式、resolver（$input/$steps 点号路径）、validator（JSON Schema 入口校验）、criteria（成功/失败评估）
- [x] **1D 追踪系统** — Tracer 集成到 DAGExecutor、trace_get/trace_list/trace_errors 3 个 MCP 工具、execution_traces + step_traces 两张表
- [x] **1E 检索与组合** — TF-IDF 搜索器（SkillRetriever）、skill_compose（临时定义 + 预览 → skill_create 持久化）、skill_search/compose 2 个 MCP 工具
- [x] **1F 优化器（被动模式）** — TraceAnalyzer（5 类模式检测）、OptimizerAgent（无后台扫描，需显式调用）、optimizer_analyze/apply/status 3 个 MCP 工具
- [x] **1G 测试与文档** — 32 个测试（28 单元 + 4 集成）全部通过、CLAUDE.md、hello-world + code-review 示例 skill
- [x] **架构评审修复** — 22 个缺陷中 8 个 MVP 关键缺陷已修（H-2/H-3/H-4/H-5/M-1/M-3/M-6/M-7）
- [x] **MCP 配置修正** — settings.json → .mcp.json 项目级加载方案
- [x] **真实场景验证** — skill_execute + trace_get 在 Claude Code 中端到端通过
- [x] **计划文件迁移** — 从 ~/.claude/plans/ 移至 docs/architecture-plan.md（项目内可追踪）

### 第二阶段：模块化创建（2026-06-03）

- [x] **8 AI 分解引擎** — decompose_task()：自然语言 → 模块化 skill DAG、子步骤识别、依赖推断（串行/并行）、中英文混合分割支持
- [x] **skill_analyze MCP 工具** — 任务描述 → 可审阅的 skill 定义预览，modularity_notes 标注并行机会

### dev-diary Skill 开发（2026-06-04）

- [x] **Dev-Diary Skill 开发** (#P-4) — diary.py (Python 核心脚本, 解析/序列化 + add/done/list/update 四操作) + dev-diary.yaml (Skill Engine DAG, shell 命令模式) + SKILL.md (Claude Code /dev-diary 命令) + CLAUDE.md (技能文档章节)。经端到端验证：四操作均通过 shell 命令和 Skill Engine DAG 执行测试，32 个原有测试无回归。

### 第三阶段：工具执行闭环修复（2026-06-04）

- [x] **#P-1 DAG 引擎支持 shell 命令模式** — `_execute_step` 中当 `tool_registry.get()` 找不到时，不再报 "Unknown tool"，改为 `_run_command` 执行 shell 命令。支持 `{varname}` 占位符替换 input_mapping 解析值。**问题来源**：真实场景中用户想创建 PDF 提取 skill，但 ToolRegistry 只有 `echo`，无法执行任何实质任务。**方案**：step.tool 不在 registry → 当作命令执行，用户只需在 skill YAML 中写 `python3 scripts/xxx.py --input {pdf_path}` 即可，无需改源码。
- [x] **PDF-to-Markdown Skill 端到端验证** — 按 Agent Skills 标准目录结构（`SKILL.md` + `scripts/extract_pdf.py`）创建 skill，skill-engine YAML 的 step.tool 通过命令模式调用脚本。在真实 PDF 文件上执行成功（4 页/2445 字符，同名 .md 生成）。
- [x] **#P-3 skill_import 方向明确** — 通过对齐 Agent Skills 开放标准（agentskills/agentskills），确定 skill-engine 的差异化价值在 DAG 执行 + 追踪 + 优化，而 skill 定义层应遵循标准 SKILL.md 格式。标准目录结构的三层（SKILL.md 元数据/指令 + scripts/ 脚本 + references/assets/ 资源）与 skill-engine 的 DAG 模型方向一致。
- [x] **工具注册闭环（#P-1）** — 在另一对话中完成：新增 tool_register MCP 工具，允许用户注册 Python 函数/脚本为运行时工具，解决了 ToolRegistry 只有 echo 的瓶颈。

### Skill 触发链路修复（2026-06-04）

- [x] **Skill 触发链路断裂：skill_execute 未被自然触发** (#P-8) — 诊断：两套系统不互通 → 架构决策：全面专用 → 实现：.claude-plugin/plugin.json + skill_create 自动 SKILL.md + skill_import + optimizer 单例修复。skills/ 下 3 skill 均 SKILL.md+YAML 齐全，188 测试无回归。
- [x] **skill_import 实现（#P-3）** — skill_import MCP 工具已实现：解析 SKILL.md frontmatter + body → 提取 workflow 步骤 → 扫描 scripts/ → 生成 YAML wrapper → 保存到 skills/imported/。同时 skill_create 自动生成 SKILL.md 骨架。

### v0.2 架构方向讨论（2026-06-06）

- [x] **架构方向重新评估** — 多轮深入问答，确定 v0.1.0 封闭 YAML-DAG 模型不可持续，Skill Engine 应转型为基于 Agent Skills 开放标准的微内核。详见 [architecture-v0.2.md](architecture-v0.2.md)。
- [x] **核心决策对齐** — 10 项关键决策已拍板：元数据标准(SKILL.md)、定位(微内核)、执行(委托原生机制)、trace 来源(Hook 截取 LLM 上下文)、采集适配(仅 Claude Code + 预留扩展)、插件化(独立 MCP Server)、编排方案(SKILL.md body 引用 + 导出展开) 等。
- [x] **architecture-v0.2.md 文档化** — 方向文档写入 `docs/architecture-v0.2.md`，包含架构图、数据流水线概念、待解决问题清单。

### v0.2 重构（2026-06-06）

- [x] **现有代码去留决策** — Phase 0 完成：17 个旧模块 → KEEP 5 + REWRITE 5 + DISCARD 6 + MOVE 1。迁移文档见 docs/architecture-v0.2.md。
- [x] **Skill Store 重设计** — kernel/skill_store.py 完成：从 skills/{id}.yaml → skills/{name}/SKILL.md。基于 SkillMetadata（YAML frontmatter + Markdown body），保留 .backup 模式。
- [x] **微内核 API/接口设计** — Phase 1 完成：kernel/server.py（16 MCP 工具）+ kernel/plugin_manager.py（插件生命周期）+ kernel/plugin_interface.py（BasePlugin 抽象基类 + api_version 协商）。plugins.yaml 声明式配置。
- [x] **数据流水线具体形态** — Phase 2 完成：hooks/capture.py（零依赖 hook 脚本 → History DB）+ plugins/data_pipeline/（DataPipelinePlugin + 4 个抽象基类 BaseExtractor/BaseDedup/BaseTrigger + 3 个 MVP 实现）。pipeline_run 手动触发。
- [x] **Phase 0-4 v0.2 重构完成** (#P-9) — 从 188 测试/20 秒 → 61 测试/0.48 秒。17 个旧模块 → 12 个新模块。建立 kernel/ 微内核（16 MCP 工具）+ plugins/data_pipeline/（4 个抽象基类）+ hooks/capture.py（零依赖 hook 脚本）。旧 engine/models/optimizer/storage/tracing/retrieval/builtin_tools 全部移除。自定义 YAML DAG → Agent Skills SKILL.md 开放标准。

---

### Pipeline v0.3 重构（2026-06-08）

- [x] **OpenSpace 深度调研** — 完整阅读 OpenSpace 源码（evolver/patch/store/conversation_formatter/quality/registry），提炼 8 类可借鉴设计模式。写入 [openspace-architecture-insights.md](openspace-architecture-insights.md)。
- [x] **Transcript 机制验证** — 发现 Claude Code 内置 transcript JSONL（`~/.claude/projects/<sanitized_cwd>/<session_id>.jsonl`），验证可通过 `CLAUDE_CODE_SESSION_ID` + `PWD` 推导路径，387 条消息/20 user messages 分类正确。
- [x] **架构设计讨论** — 多轮深入讨论：实时触发策略（链式 segment）、分析与进化合并（共享上下文）、验证器独立（I/O 分离）、分层隔离阻断递归、Meta 信号驱动优化。
- [x] **架构文档** — [pipeline-refactor-v0.3.md](pipeline-refactor-v0.3.md) 完整架构方案，含模块重组逻辑、异步协程设计、迁移计划。
- [x] **Step 1: 基础设施** — `transcript_reader.py`（JSONL 惰性读取 + 消息类型分类）+ `models.py`（Segment/SegmentStats/ExecutionAnalysis/SkillPatch 等 8 个 dataclass）+ `segment_store.py`（SQLite CRUD + 链表遍历）。
- [x] **Step 2: Segmentation** — `segmenter.py`（按 user message 切分 + 优先级预算截断，借鉴 OpenSpace）+ `segment_watcher.py`（Task 1 协程，监听 transcript 实时创建 segment）。
- [x] **Step 3: Analyzer-Evolver** — `llm_client.py`（LLMClient 协议 + 内置分析工具定义）+ `analysis_prompt.py`（Phase A prompt 模板）+ `analysis_runner.py`（Phase A LLM agent loop）+ `evolution_runner.py`（Phase B 三格式 patch 生成）+ `analyzer_evolver.py`（Phase A+B 编排 + 持久化 + skill_record 更新）。
- [x] **Step 4: Validator + Metric Monitor** — `validator.py`（双层验证：L1 机械检查 + L2 LLM 语义检查）+ `metric_monitor.py`（纯 SQL 信号源，扫描 skill_records 健康指标）。
- [x] **Step 5: Meta Signal Detector** — `meta_signal_detector.py`（Task 4 低频后台，检测分析 skill 退化信号：格式错误率/低信号/退化，触发优化）。
- [x] **LLM Client 实现** — `llm_client_impl.py`（Anthropic 兼容 API，stdlib urllib 零新依赖，DeepSeek v4-pro 实测连通）。
- [x] **Pipeline DB** — `pipeline_store.py`（统一 DB：segments + execution_analyses + analysis_traces + skill_records 四表）。
- [x] **Server 集成** — `server.py` 新增 4 个 MCP 工具（pipeline_segments/pipeline_segment_get/pipeline_analyze/pipeline_watch），旧 DataPipelinePlugin 保留兼容。
- [x] **E2E 验证** — 真实 segment → Phase A 分析 → 产出 EvolutionSuggestion + SkillPatch → 持久化到 DB。61 个已有测试零回归。

### Validator-as-Tool 重构 + 生产就绪（2026-06-08）

- [x] **Validator 设计修正** — Validator 从独立 Pipeline 阶段改为 Optimizer（Phase B）的内部工具。提供 `validate_patch`/`add_test_case`/`run_test_suite` 三个工具。Optimizer 通过 `generate → validate → fix` 循环即改即测，避免过拟合。
- [x] **分析输出简化** — 删除中间 JSON Schema（SkillJudgment/EvolutionSuggestion/ToolIssue 等），Phase A 输出自然语言诊断，直接作为 Phase B prompt 前缀。简化代码 +1440/-1918 行。
- [x] **分析 skill 创建** — `skills/pipeline-analyzer/SKILL.md` v1.0.0，定义分析方法、输出格式、质量标准。AnalysisPromptBuilder 自动加载并注入为 system prompt。
- [x] **Validator 种子数据** — 5 个基线测试用例（dev-diary/hello-world/run-tests/markdown-stats/git-status）。
- [x] **Server 修复** — 共享队列（watcher + runner 同一 asyncio.Queue）+ Validator 注入 + 新增 2 个 MCP 工具（`pipeline_validator_add_case`/`pipeline_validator_cases`）。
- [x] **Bootstrap 脚本** — `scripts/bootstrap_pipeline.py`：一键初始化 DB、种子 Validator、分段 transcript、分析 tool-using segments。
- [x] **生产审计** — 6/6 检查项通过（Segmenter、SegmentStore、PipelineStore 六表、Validator、LLM Client、Phase A 真实分析）。

### 当前状态

**Pipeline v0.3 生产就绪。** 启动方式：
```bash
# 初始化 + 分析当前 session
python3 scripts/bootstrap_pipeline.py

# 或在 MCP Server 中调用
pipeline_watch           # 开始实时监听
pipeline_analyze <id>    # 分析指定 segment
```

### 项目完成冲刺（2026-06-08 晚间）

- [x] **Phase 1: Pipeline 稳定性** — 增量 Segmenter（`segment_from`）、Watcher 自动启动、LLM 3 次重试、19 个新测试（80 total）。
- [x] **Phase 2: Skill 生态 + 进化验证** — hello-world scripts/、进化 E2E：模拟错误 → Phase A 诊断 → Phase B DIFF patch。完整闭环。
- [x] **Phase 3: 调试面板** — `dashboard.py`（Flask, 6 routes）：Dashboard 首页、Segments 列表/详情、Skills、Validator。
- [x] **Phase 4: 清理 + README** — README.md + README-zh.md 同步 v0.3 架构、20 tools、bootstrap 脚本。CLAUDE.md 已知问题更新。

### 当前状态

**项目可对外展示。** 80 tests pass。Evolution 闭环已验证。Dashboard 可启动。

### 下一步

- [ ] **生产监控** — 在实际使用中观察分析质量，积累 analysis_traces 数据。
- [ ] **Meta Signal Detector 首次运行** — 积累 20+ analysis traces 后触发分析 skill 优化。
- [ ] **旧组件归档** — 新 pipeline 稳定后废弃 capture.py/history.db/extractors.py。

---

## 待解决

### 高优先级（阻断真实使用）

- [x] ~~**真实场景数据测试** (#P-10)~~ — Pipeline v0.3 E2E 验证已完成：transcript → segment → Phase A 分析 → EvolutionSuggestion + SkillPatch → 持久化。

### 中优先级（体验缺口）

- [ ] **数据采集适配器接口** — 为 Claude Code `*` hook 设计适配器，同时预留 Cursor/Copilot/Codex 扩展点。Hook 脚本由 Skill-System 提供。
- [ ] **Trace 数据结构** — 从 LLM 上下文（messages + CoT）能提取什么字段？与 v0.1.0 的 ExecutionTrace/StepTrace 差异多大？需设计新的 trace schema。
- [ ] **编排引用语法** — SKILL.md body 中如何引用其他 skill（本地按引用工作），导出时展开替换的具体规则。
- [ ] **Hook 脚本部署方案** — `*` hook 如何由 Skill Engine 生成/管理？是否需要 Skill Engine 干预用户的 `.claude/settings.json`？

### 低优先级（工程健壮性）

- [ ] **优化器设计细化** — 输入从自产 trace 变为外部采集的上下文分析结果，审核机制具体形态待定。
- [ ] **原子 skill 与业务 skill 分层** — 理论方案验证 + 工程实践，目前仅为预想。
- [ ] **多厂商数据采集适配** — Cursor、Copilot、Codex 等，采集接口扩展性验证。
- [ ] **版本管理** — 对 SKILL.md 的版本追踪（利用 `metadata.version` 字段 或 git-based）。
- [ ] **认证模型** — MCP server 对外暴露时的 token 认证。
- [ ] **测试覆盖扩展** — 随重构更新测试体系，补数据流水线、trace 提取等新模块的测试。
- [ ] **MCP Inspector 测试流程** — 补充手动调试工具的操作文档。
- [ ] **内核公共 API 层（插件隔离）** (#P-11) — 当前插件直接 import TraceStore/SkillStore，在 1 插件/1 人/1 存储的场景下没必要做抽象。但应：(1) 将 TraceStore 和 SkillStore 标记为内核私有，插件不直接 import；(2) 用薄 wrapper 函数暴露内核能力，以后加隔离只改 wrapper；(3) 第二个插件出现时自动触发此重构。触发条件：出现第二个消费者（另一个插件需要读写 trace/skill）。

## 工具清单

> v0.2 内核 MCP Server（kernel/server.py）已实现 16 个工具。v0.3 新增 4 个 pipeline 工具。

```
v0.2 保留（9）:
skill_list        skill_get         skill_create       skill_update
skill_delete      skill_search      trace_get          trace_list
trace_errors

v0.2 新增（5）:
plugin_list       plugin_health     plugin_config      pipeline_run
pipeline_status

v0.3 新增（4）:
pipeline_segments       pipeline_segment_get
pipeline_analyze        pipeline_watch

v0.1 移除（7）:
skill_execute     skill_analyze     skill_compose      skill_import
optimizer_analyze optimizer_apply   optimizer_status
```

---

## 关键文件索引

| 文件 | 用途 |
|------|------|
| `plugins.yaml` | 插件配置（声明式，内核启动时加载） |
| `src/skill_engine/kernel/server.py` | 内核 MCP Server（16+4 工具） |
| `src/skill_engine/kernel/skill_store.py` | Skill CRUD（SKILL.md 格式） |
| `src/skill_engine/kernel/trace_store.py` | Trace 存储（SQLite WAL + v0.2 schema） |
| `src/skill_engine/kernel/plugin_manager.py` | 插件生命周期管理 |
| `src/skill_engine/pipeline/` | **v0.3 新 pipeline**（16 文件） |
| `src/skill_engine/plugins/data_pipeline/` | Data Pipeline Plugin（旧，保留兼容） |
| `src/skill_engine/hooks/capture.py` | Claude Code hook 脚本（旧，保留兼容） |
| `docs/architecture-v0.1.md` | v0.1.0 架构评审（22 缺陷分析） |
| `docs/architecture-v0.2.md` | v0.2 架构方向 |
| `docs/pipeline-refactor-v0.3.md` | **v0.3 pipeline 架构方案** |
| `docs/openspace-architecture-insights.md` | **OpenSpace 源码洞察** |
| `docs/DEVELOPMENT.md` | 本文件，当前开发状态 |
| `CLAUDE.md` | Claude Code 会话指引 |
