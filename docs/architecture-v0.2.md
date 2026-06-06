# Skill-System Architecture v0.2

> 基于 v0.1.0 架构评审 + 多轮需求讨论产生的架构方向。本文档是下一步详细设计和重构方案的输入，不是最终实现方案。

## 核心决策

| # | 决策 | 结论 |
|---|------|------|
| 1 | Skill 元数据标准 | **Agent Skills 开放标准**（[agentskills.io/specification](https://agentskills.io/specification)），即 SKILL.md（YAML frontmatter + Markdown body + scripts/references/assets） |
| 2 | Skill Engine 定位 | **微内核** — 负责隔离、通信、调度。不再是 skill 运行时 |
| 3 | Skill 执行 | 委托给**原生 skill 机制**（Claude Code 原生 skill 视为特殊 plugin） |
| 4 | Trace 数据源 | **Claude Code hook 截取 LLM 上下文**（messages + CoT），不依赖自身执行产生 trace |
| 5 | 数据采集 | v0.2.0 仅适配 Claude Code（`*` hooks），接口设计预留多厂商扩展 |
| 6 | Trace 提取 | 数据流水线异步处理：原始上下文 → 去重 → 结构化 trace。提取方案待测试（正则 → ML → 小 LLM → 云端 LLM） |
| 7 | 优化器 | 被动分析 trace 数据，产出优化建议，有独立测试/审核机制。**当前开发重点不在这** |
| 8 | 插件化 | 方向 A — 各组件（trace 提取、优化器等）为独立 MCP Server，Skill Engine 微内核做协调 |
| 9 | 编排方案 | SKILL.md body 中引用其他 skill（本地按引用工作），导出时展开替换真实 skill |
| 10 | 原子/业务 skill 分层 | 预想方案，未成熟，待后续实践验证 |

## 与 v0.1.0 的关键差异

| 维度 | v0.1.0（当前代码） | v0.2（方向） |
|------|-------------------|-------------|
| Skill 定义格式 | 自定义 YAML（SkillDefinition dataclass） | Agent Skills 开放标准（SKILL.md） |
| Skill 执行 | 自建 DAG executor，直接执行 | 委托原生 skill 机制 |
| Trace 来源 | 自己执行产生 trace | Hook 截取 LLM 上下文 |
| Skill Engine 角色 | Skill 运行时（执行 + 追踪 + 优化） | 微内核（管理 + 协调 + 分析） |
| 优化器 | 分析自己的 trace | 分析外部采集的上下文数据 |
| 封闭性 | 封闭生态，skill 离开即不可用 | 基于开放标准，skill 可跨工具使用 |

## 整体架构图（概念）

```
┌─────────────────────────────────────────────────────────┐
│                      LLM / Agent 应用                    │
│   (Claude Code, Cursor, etc.)                           │
│   ┌──────────────┐  ┌──────────────────────────────┐    │
│   │ 原生 Skill   │  │ Hook (截取上下文)              │    │
│   │ 机制 (执行)   │  │  → 实时历史数据库              │    │
│   └──────┬───────┘  └──────────────┬───────────────┘    │
└──────────┼─────────────────────────┼────────────────────┘
           │                         │
           │ 委托执行                 │ 原始上下文数据
           │                         │
           ▼                         ▼
┌─────────────────────────────────────────────────────────┐
│                   Skill Engine (微内核)                   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │              内核 (Core)                          │    │
│  │  • Skill 元数据管理 (CRUD on SKILL.md)            │    │
│  │  • 插件注册 / 发现 / 生命周期                      │    │
│  │  • 事件路由 / 调度                                 │    │
│  │  • 隔离边界 (插件间无直接依赖)                      │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────┐     │
│  │ Trace    │  │ Optimizer│  │ Data Pipeline      │     │
│  │ Store    │  │ Plugin   │  │ Plugin              │     │
│  │ (SQLite) │  │ (MCP)    │  │ (MCP)               │     │
│  │          │  │          │  │ 上下文 → trace 提取  │     │
│  └──────────┘  └──────────┘  └────────────────────┘     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Skill 元数据模型（Agent Skills 标准）

### 目录结构

```
skill-name/
├── SKILL.md          # 必须：YAML frontmatter + Markdown body
├── scripts/          # 可选：可执行代码
├── references/       # 可选：参考文档
└── assets/           # 可选：静态资源
```

### SKILL.md Frontmatter

| 字段 | 必须 | 约束 |
|------|------|------|
| `name` | 是 | ≤64 字符，小写字母/数字/连字符，匹配目录名 |
| `description` | 是 | ≤1024 字符，描述做什么 + 何时使用 |
| `license` | 否 | 许可证名称 |
| `compatibility` | 否 | ≤500 字符，环境要求 |
| `metadata` | 否 | 任意 key-value |
| `allowed-tools` | 否 | 预批准工具列表（实验性） |

### 渐进式加载

| 层级 | 内容 | Token 预算 | 加载时机 |
|------|------|-----------|---------|
| L1 | name + description | ~100 tokens | 启动时始终加载 |
| L2 | SKILL.md body | ≤5000 tokens | skill 触发时加载 |
| L3 | scripts/references/assets | 按需 | 显式引用时加载 |

## 数据流水线（概念）

```
Hook 截取 LLM 上下文
  │
  ▼
实时历史数据库（去重）
  │
  ▼
Data Pipeline Plugin (MCP)
  │  从原始上下文提取结构化 trace
  │  提取方案：正则 → ML → 小 LLM → 云端 LLM（成本由低到高，测试后选择）
  │
  ▼
Trace 数据库 (SQLite)
  │
  ▼
Optimizer Plugin (MCP)
  │  分析 trace 模式 → 产出优化建议
  │  有独立测试/审核机制
  │
  ▼
Skill Engine Core
    应用优化 → 修改 SKILL.md
```

## 待解决的问题（TODO）

以下问题需要在详细设计阶段逐一敲定，目前仅记录方向：

### 高优先级

- [ ] **微内核 API/接口设计** — 插件如何注册？事件如何路由？通信协议（MCP 工具调用 vs 事件总线 vs gRPC）？
- [ ] **数据流水线具体形态** — 实时流式 vs 批量处理？各组件间数据交换格式？
- [ ] **现有代码去留决策** — DAG executor、YAML skill store、optimizer/analyzer 中哪些保留/重写/废弃？
- [ ] **Skill Store 重设计** — 从自定义 YAML 格式迁移到 SKILL.md 标准格式的读写

### 中优先级

- [ ] **数据采集适配器接口** — 为 Claude Code hook 设计，预留 Cursor/Copilot/Codex 扩展点
- [ ] **Trace 数据结构** — 从 LLM 上下文中能提取出什么字段？与 v0.1.0 的 ExecutionTrace/StepTrace 差异多大？
- [ ] **编排引用语法** — SKILL.md body 中如何引用其他 skill？导出时展开替换的具体规则？
- [ ] **Hook 脚本形态** — `*` hook 如何部署？需不需要 Skill Engine 生成/管理 hook 配置？

### 低优先级（后期）

- [ ] **优化器设计细化** — 输入是 trace（非结构化 → 结构化后的），输出是 SKILL.md 修改建议，审核机制具体形态？
- [ ] **原子 skill 与业务 skill 分层** — 理论方案验证 + 工程实践
- [ ] **多厂商适配** — Cursor、Copilot、Codex 等数据采集适配器

## 参考

- [Agent Skills Specification](https://agentskills.io/specification)
- [Anthropic: How to create custom skills](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills)
- [Claude Code plugin development](https://github.com/anthropics/claude-code/blob/main/plugins/plugin-dev/)
- 项目文档：[architecture-v0.1.md](architecture-v0.1.md)（原始架构评审）、[architecture-plan.md](architecture-plan.md)（v0.1.0 详细设计）
