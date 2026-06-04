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

---

## 待解决

### 高优先级（阻断真实使用）

- [ ] **skill_analyze 工具绑定（#P-2）** — 分解出的 step 默认 `tool: "echo"`，未根据任务描述推断需要的工具类型。应标注每个 step 需要的工具/脚本并生成占位 command。
- [ ] **Skill 分层管理（#P-5）** — 当前 skill 扁平存储在 skills/ 单一目录，SkillDefinition 无 parent/category/namespace 字段。需支持目录嵌套、层级命名空间、skill_list 按层级筛选。
- [ ] **Skill 运行时复用（#P-6）** — Step.tool 只能引用 Python 函数或 Shell 命令，不能调用另一个 Skill。需支持 step 中引用其他 skill ID，运行时嵌套执行，被调用 skill 的输入/输出通过 input_mapping 和 $steps 引用串联。
- [ ] **Skill 功能去重（#P-7）** — TF-IDF 只做 query→skill 搜索，不比较 skill 间相似度；优化器 _detect_composition_opportunities 为空实现。需支持跨 skill 功能重叠检测、相似度报告、去重建议。

### 中优先级（体验缺口）

- [ ] **DAG 条件分支（H-1）** — 纯 DAG 不支持 `if step A fails → route to step B`。v2 加 `condition` 边。
- [ ] **优化器后台自动应用（H-6，需文件锁保护）** — 当前为被动触发，低风险优化可自动应用但需防竞态。
- [ ] **TF-IDF 索引缓存（M-2）** — 当前每次查询重新构建矩阵，skill 增长后需内存缓存 + 失效机制。

### 低优先级（工程健壮性）

- [ ] **版本管理（M-5）** — 当前只有 .backup 文件。v2 做版本化文件名 + 回滚。
- [ ] **认证模型（M-8）** — 当前只绑 127.0.0.1。v2 加 token 认证。
- [ ] **测试覆盖扩展** — 补 skill_analyze/decomposer 单元测试，补 TraceStore 集成测试，补 optimizer 分析正确性测试。
- [ ] **MCP Inspector 测试流程** — 补充手动调试工具的操作文档。

## 工具清单（16 个 MCP 工具）

```
skill_list        skill_get         skill_create       skill_update
skill_delete      skill_analyze     skill_execute      skill_search
skill_compose     trace_get         trace_list         trace_errors
optimizer_analyze optimizer_apply   optimizer_status
```

---

## 关键文件索引

| 文件 | 用途 |
|------|------|
| `.mcp.json` | 项目级 MCP server 定义 |
| `.claude/settings.json` | MCP 服务器启用配置 |
| `docs/architecture-plan.md` | 完整架构设计 + 决策记录 |
| `docs/DEVELOPMENT.md` | 本文件，当前开发状态 |
| `CLAUDE.md` | Claude Code 会话指引 |
