# Skill Engine — 轻量级通用 Skill 管理引擎

## Context

独立的通用 Skill 引擎，以 MCP server 形态运行，任何 MCP 兼容客户端即插即用。负责 skill 的创建、模块化 DAG 执行、路径追踪、异步优化和检索组合。

## 架构评审结论

资深架构师审查发现 22 个缺陷。MVP 阶段修复 8 个（H-2, H-3, H-4, H-5, M-1, M-3, M-6, M-7），延后 5 个到后续版本（H-1 条件分支, H-6 优化器后台自动应用, M-2 TF-IDF 缓存, M-5 版本管理, M-8 认证模型）。详见文末「延后到后续版本」节。

## 项目结构

```
Skill-System/
├── pyproject.toml
├── src/skill_engine/
│   ├── server.py              # MCP server 入口，工具注册
│   ├── models/
│   │   ├── skill.py           # SkillDefinition, StepDefinition, Criteria
│   │   ├── trace.py           # ExecutionTrace, StepTrace
│   │   └── registry.py        # ToolRegistry（内置工具注册表）
│   ├── engine/
│   │   ├── dag_executor.py    # 拓扑排序、分层并行（Semaphore 限流）、超时、重试
│   │   ├── resolver.py        # $input.x.y / $steps.sid.output.z 点号路径解析
│   │   ├── criteria.py        # 成功/失败标准评估
│   │   └── validator.py       # JSON Schema 输入校验（入口处拦截）
│   ├── storage/
│   │   ├── skill_store.py     # 文件系统 CRUD + .backup 备份
│   │   └── trace_store.py     # SQLite（WAL 模式）+ aiosqlite
│   ├── tracing/
│   │   └── tracer.py          # Tracer 上下文管理器
│   ├── retrieval/
│   │   └── retriever.py       # TF-IDF 搜索 + skill 组合
│   ├── optimizer/
│   │   ├── analyzer.py        # 追踪模式检测（被动触发，不后台扫描）
│   │   └── agent.py           # 管理推荐、应用优化（需显式调用）
│   └── builtin_tools/         # 内置可调用工具（echo, template 等）
├── skills/                    # skill YAML 定义存放目录
├── traces/                    # SQLite 数据库目录
├── tests/
│   ├── unit/
│   │   ├── test_resolver.py
│   │   ├── test_criteria.py
│   │   ├── test_dag_executor.py
│   │   └── ...
│   └── integration/
│       ├── test_executor.py
│       └── test_mcp_tools.py
└── examples/                  # 示例 skill 定义
```

## 核心数据模型

### Skill 定义（YAML 文件）

```yaml
id: "code-review"
name: "Code Review"
version: "1.0.0"
description: "..."
tags: ["code", "review"]
timeout_seconds: 300            # [M-6] 全局超时
max_concurrency: 10             # [M-7] 最大并发 step 数
input_schema:                   # [H-3] JSON Schema，入口校验
  type: object
  properties:
    code:
      type: string
      description: "Source code to review"
    language:
      type: string
  required: ["code"]
output_schema:
  type: object
  properties: {...}

steps:
  - id: "lint"
    tool: "run_linter"
    depends_on: []
    input_mapping:
      code: "$input.code"       # [H-4] 只允许 $input.x.y 或 $steps.<id>.output.z
    success_criteria:
      type: "always"            # always | output_match | exception_none
    failure_criteria:
      type: "exception"         # exception | timeout
    retry:
      max_attempts: 2
      backoff: "exponential"
      backoff_base_seconds: 1
    timeout_seconds: 60         # [M-6] 每步超时，asyncio.wait_for 兑现
```

### 引用解析规则（H-4 修复）

- `$input.x.y` — 访问 skill 顶层输入
- `$steps.<step_id>.output` — 访问某 step 的完整输出
- `$steps.<step_id>.output.x.y` — 访问某 step 输出的嵌套字段

**禁止** `$steps.x` 短路径——必须带 step_id，消除命名空间歧义。解析器用纯 `functools.reduce` 实现（H-5 修复），删掉 jsonpath-ng 依赖。

### 追踪数据（SQLite + WAL）

- `PRAGMA journal_mode=WAL` 启用并发写（M-1 修复）
- 使用 `aiosqlite` 异步访问
- 两张表：`execution_traces`（skill 级）、`step_traces`（step 级）

## MCP 工具（14 个，保持独立）

> M-4（工具过多）：经讨论，独立工具比 action 参数模式更易于 Agent 理解。保持 14 个独立工具。

| 工具名 | 功能 |
|--------|------|
| `skill_execute` | 按 DAG 执行 skill。**`sync=false` 时立即返回 run_id**（H-2 修复），用 `trace_get` 轮询结果 |
| `skill_create` | 创建 skill（完整定义 或 自然语言描述） |
| `skill_update` | 更新 skill（自动创建 .backup） |
| `skill_delete` | 删除 skill |
| `skill_list` | 列出所有 skill（可按 tag 过滤，返回摘要） |
| `skill_get` | 获取 skill 完整定义 |
| `skill_search` | 自然语言搜索 skill（TF-IDF） |
| `skill_compose` | 组合多个 skill 为临时 pipeline（详见下文） |
| `trace_get` | 获取某次运行的完整追踪 |
| `trace_list` | 列出追踪记录（按 skill/status 过滤） |
| `trace_errors` | 查询失败的 step 级错误详情 |
| `optimizer_analyze` | **被动触发**：分析追踪数据，生成优化建议（H-6 修复） |
| `optimizer_apply` | 显式应用某条优化建议 |
| `optimizer_status` | 查看推荐列表及状态 |

### `skill_compose` 语义精确定义（M-3 修复）

1. 接受两个以上 skill_id + 可选的 output_mappings
2. **不持久化**——创建临时的内存 SkillDefinition
3. 返回组合后的完整 DAG 供预览
4. Agent 满意后调用 `skill_create` 持久化
5. 组合规则：skill 按顺序串联，前一个 skill 的终止 step 自动成为后一个 skill 起始 step 的依赖

## 核心引擎设计

### DAG 执行器（H-2, M-6, M-7 修复）

```
skill_execute(skill_id, input, sync=true)
  │
  ├─ [H-3] 入口校验: jsonschema.validate(input, skill.input_schema)
  │     失败 → 立即返回结构化错误 "skill X expects field 'Y'"
  │
  ├─ 拓扑排序 (Kahn) → 分层
  │
  ├─ sync=true:   阻塞执行全部 level，返回最终结果
  └─ sync=false:  创建 asyncio.Task，立即返回 {"run_id": "..."}
                  用户通过 trace_get 轮询结果
```

**每 step 执行流程：**
1. `resolver.resolve(input_mapping)` — 点号路径解析
2. `asyncio.wait_for(tool_fn(**input), timeout=step.timeout_seconds)` — [M-6] 超时保护
3. `criteria.evaluate_success(output)` — 成功判定
4. 不满足 → 重试（exponential backoff）
5. 失败 → `NodeStatus.FAILED` → 递归 SKIP 下游

**并发控制：**
- `asyncio.Semaphore(skill.max_concurrency or 10)` — [M-7] 限制同层并行上限
- 同层 step 在 semaphore 约束下 via `asyncio.gather`

### 追踪系统

- `trace_errors` 一步查询：哪个 step 失败、输入是什么、错误是什么、耗时多少
- 支持 `parent_run_id` 追踪组合 skill 的调用链

### 优化器（H-6 修复）

- **去除后台自动扫描**。改为纯被动模式：
  - Agent 调用 `optimizer_analyze` → 同步分析追踪数据 → 返回推荐列表
  - Agent 调用 `optimizer_apply` → 应用指定推荐 → 自动创建 .backup
- 检测 5 类模式：失败热点、超时模式、可并行化步骤、输入-失败关联、组合机会
- **不做自动应用**。每次修改需 Agent 显式调用

### Skill 检索

- TF-IDF 对 name/description/tags/schema 做关键词搜索
- 内存缓存矩阵，`skill_create/update/delete` 时失效
- 零外部依赖，适合几十到几百 skill 的规模

## 依赖

- `mcp>=1.0.0` — MCP Python SDK
- `pyyaml>=6.0` — YAML 解析
- `aiosqlite>=0.20` — 异步 SQLite（M-1 修复）
- 开发依赖：`pytest`, `pytest-asyncio`

共 **3 个运行时依赖**（删除了 jsonpath-ng）。

## 实施顺序

```
1A. 基础 (Day 1-2):    项目脚手架、数据模型、SkillStore（含 .backup）、TraceStore（WAL + aiosqlite）
1B. MCP 骨架 (Day 2-3): server.py、注册 CRUD 工具
1C. DAG 引擎 (Day 3-5): resolver（点号路径）→ validator（JSON Schema）→ criteria → dag_executor
                        （Semaphore + asyncio.wait_for + sync/async）→ 注册 skill_execute
1D. 追踪 (Day 5-6):     Tracer 集成 → 注册 trace_get/list/errors
1E. 检索 (Day 6-7):     TF-IDF retriever → skill_compose（临时定义 + 预览）→ 注册 skill_search/compose
1F. 优化 (Day 7-9):     analyzer（被动触发）→ agent（无后台扫描）→ 注册 optimizer_analyze/apply/status
1G. 打磨 (Day 9-10):    示例 skill、单元测试 + 集成测试、README
```

关键路径：1A → 1B → 1C → 1D。1E 和 1F 可部分并行。

## 延后到后续版本

| 编号 | 问题 | 说明 |
|------|------|------|
| H-1 | DAG 缺条件分支/循环 | MVP 阶段 skill 足够简单，条件逻辑由调用方 Agent 处理。v2 加 `condition` 边 |
| H-6 | 优化器后台自动应用 | MVP 改为被动触发。v2 加可选的自动低风险优化（需文件锁保护） |
| M-2 | TF-IDF 无索引缓存 | MVP 阶段 skill < 50 个，实时构建 < 10ms。v2 加内存缓存 |
| M-5 | 无版本管理 | MVP 先做 `.backup` 文件。v2 做版本化文件名 + 回滚 |
| M-8 | 无认证模型 | MVP 只绑 127.0.0.1，README 注明。v2 加 token 认证 |

## 验证方式

1. **单元测试**：resolver（点号路径解析、非法短路径拒绝）、criteria、dag_executor（拓扑排序、超时、重试、并发限流）
2. **集成测试**：创建示例 skill，通过 `skill_execute` 执行，用 `trace_get` 验证追踪完整性
3. **MCP 连接测试**：将 server 配置到 Claude Code 的 MCP settings，实际调用工具
4. **错误路径测试**：故意让 step 失败，验证 `trace_errors` 准确定位；传错误 input，验证 schema 校验拦截
5. **同步/异步测试**：`sync=false` 立即返回 run_id，`trace_get` 轮询到结果
