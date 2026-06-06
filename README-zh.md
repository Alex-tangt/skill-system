# Skill-System

基于微内核架构的 AI Agent 技能管理平台。技能遵循 **Agent Skills 开放标准**（SKILL.md），由 Claude Code 原生机制执行。提供元数据管理、插件架构、以及用于 trace 提取与优化的数据流水线。

## 这是什么？

Skill-System 是一个 MCP（Model Context Protocol）服务器，用于管理 AI Agent 技能：

- **开放标准** — 技能采用 [Agent Skills](https://agentskills.io) 格式（`SKILL.md`），兼容 Claude Code、Cursor、Copilot 等工具
- **微内核架构** — 内核负责元数据 + 插件协调；插件（数据流水线、优化器）作为独立 MCP 服务器
- **Hook 驱动追踪** — Claude Code Hook 截取 LLM 上下文（messages + CoT）；数据流水线提取结构化 trace
- **可扩展** — 策略接口（`BaseExtractor`、`BaseDedup`、`BaseTrigger`、`BasePlugin`）配 MVP 实现，切换算法不改调用方

以 stdio MCP 服务器形态运行，任何 MCP 客户端即插即用。

## 快速开始

```bash
# 安装
pip install -e ".[dev]"

# 运行测试
python -m pytest tests/ -v

# 启动内核 MCP 服务器
python -m skill_engine.kernel.server
```

配置 MCP 客户端（`.mcp.json`）：

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

启用 Claude Code Hook 以采集追踪数据（`.claude/settings.json`）：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "command": "python3 src/skill_engine/hooks/capture.py"
      }
    ]
  }
}
```

## 架构

### 数据流

```
Claude Code 会话
  ├── 原生 skill 机制（执行）
  └── Hook（PostToolUse / UserPromptSubmit）
        │
        ▼
      capture.py ──► History DB ──► Data Pipeline Plugin ──► Trace DB
                                         │
                                         └──（未来）Optimizer Plugin
```

### 核心组件

| 组件 | 作用 |
|---|---|
| `kernel/server.py` | MCP 服务器，16 个工具（skill CRUD、trace 查询、插件管理、流水线） |
| `kernel/skill_store.py` | SKILL.md 文件系统 CRUD，自动 `.backup` |
| `kernel/trace_store.py` | SQLite（WAL 模式，aiosqlite），v0.2 schema |
| `kernel/plugin_manager.py` | 插件生命周期（加载 `plugins.yaml`） |
| `plugins/data_pipeline/` | 从原始 LLM 上下文提取结构化 trace |
| `hooks/capture.py` | 零依赖 Claude Code hook 脚本 |

### MCP 工具（16 个）

**Skill CRUD:** `skill_list`、`skill_get`、`skill_create`、`skill_update`、`skill_delete`

**搜索:** `skill_search`

**追踪:** `trace_get`、`trace_list`、`trace_errors`

**插件:** `plugin_list`、`plugin_health`、`plugin_config`

**流水线:** `pipeline_run`、`pipeline_status`

### 插件系统

插件实现 `kernel/plugin_interface.py::BasePlugin`。通过 `plugins.yaml` 配置：

```yaml
plugins:
  data-pipeline:
    name: data-pipeline
    type: internal
    module: skill_engine.plugins.data_pipeline.plugin
    description: 从原始 LLM 上下文提取结构化 trace
    config:
      history_db_path: ./traces/history.db
```

### 可扩展性

四个策略接口，均可替换实现：

| 接口 | MVP | 可升级为 |
|------|-----|---------|
| `BaseExtractor` | 正则（3 个提取器） | LLM 提取 |
| `BaseDedup` | SHA256 精确去重 | 语义 / SimHash 去重 |
| `BaseTrigger` | 手动（`pipeline_run`） | 定时 / 事件驱动 |
| `BasePlugin` | 内部模块 | 外部 MCP 服务器 |

## 技能目录

技能遵循 Agent Skills 标准。每个技能是一个包含 `SKILL.md` 的目录：

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

示例 `SKILL.md`：

```markdown
---
name: hello-world
description: 一个简单的回显技能，用于验证技能系统是否正常工作。
license: MIT
---
# Hello World

## 工作流
1. 回显输入文本
2. 返回回显结果

## 输入
- `text`：任意字符串
```

## 开发

```bash
pip install -e ".[dev]"                       # 安装及开发依赖
python -m pytest tests/ -v                    # 运行全部测试（61 个）
python -m skill_engine.kernel.server          # 启动内核 MCP 服务器
```

## 文档

- [architecture-v0.2.md](docs/architecture-v0.2.md) — v0.2 架构方向
- [DEVELOPMENT.md](docs/DEVELOPMENT.md) — 开发状态追踪
- [CLAUDE.md](CLAUDE.md) — Claude Code 会话指引

## 许可证

MIT — 详见 [LICENSE](LICENSE)。
