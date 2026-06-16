# Pipeline Refactor v0.3: Transcript-Native Recording & LLM Analysis

> 基于 OpenSpace 深度调研 + Claude Code Transcript 机制验证 + 多轮架构讨论的 pipeline 重构方案。
> 核心变化：hook-event 碎片化提取 → transcript 原生录制 + 实时分段 + LLM 后置分析。
> 模块重组：Analyzer + Optimizer 合并（共享上下文），Validator 注册为 Optimizer 的内部工具（即改即测）。

## 一、现状与问题

### v0.2 当前 pipeline

```
Claude Code Session
  │
  ├── Native skill mechanism (skill execution)
  └── PostToolUse hooks → capture.py (stdin JSON)
        │
        ▼
      history.db (SQLite, raw events)
        │
        │ pipeline_run (manual trigger)
        ▼
      Data Pipeline Plugin
        ├── SHA256Dedup (exact dedup)
        ├── Extractor Chain (regex-based, 3 extractors)
        └── Build ExecutionTrace + StepTrace
              │
              ▼
            traces.db (SQLite WAL)
              │
              ▼ (future, not implemented)
          Optimizer Plugin
            reads traces.db → suggests SKILL.md improvements
```

### 根本问题

| # | 问题 | 根因 |
|---|------|------|
| 1 | **信息碎片化** | Hook 每次只捕获单个 tool_call 的 I/O，丢失 agent 推理链、因果关系、技能上下文 |
| 2 | **时序丢失** | 多个 hook 事件按 `created_at` 排序不可靠 |
| 3 | **链路脆弱** | capture.py → history.db → extractors → traces.db，任意环节失败静默丢数据 |
| 4 | **分析天花板低** | Regex 提取器只能做表面匹配，无法回答为什么/怎么样 |
| 5 | **手动触发** | `pipeline_run` 需人工调用 |
| 6 | **信息断层** | Data Pipeline 产出 JSON 摘要 → Optimizer 基于摘要做建议，丢失原始上下文 |

## 二、新架构概述

### 核心原则

1. **录制层只管无损记录** — Claude Code 内置 transcript，不分析、不拆分、不提取
2. **分析全部交给 LLM** — 从完整对话中提取可操作的洞察
3. **分析与进化共享上下文** — 同一 segment 的上下文复用
4. **Validator 是 Optimizer 的工具** — 注册为 LLM 可用工具 + MCP 工具，不独立成为阶段
5. **实时触发 + 链式上下文** — 下一条用户消息即触发，LLM 自主沿链扩展
6. **分层隔离** — Layer 0 和 Layer 1 数据物理隔离，Meta 优化不进实时循环

### 模块重组逻辑

```
旧设计:
  Data Pipeline (regex extract) → Analyzer → Optimizer → (Validator embedded)
  问题: Analyzer → Optimizer 之间有信息断层（JSON 摘要丢失上下文）

新设计:
  Segmenter → Analyzer-Evolver (Phase A 诊断 → Phase B 内嵌 validate→fix 循环)
            ↑                                    ↑
        Metric Monitor (信号源)              Meta Optimizer (后台, 低频)
```

| 模块 | 旧设计 | 新设计 | 理由 |
|------|--------|--------|------|
| **Segmenter** | 不存在（hook 按 session group） | 新模块，确定性的 | transcript → segment 链表 |
| **Analyzer** | 独立，产出 JSON 摘要 | 合并进 Analyzer-Evolver Phase A，产出自然语言诊断 | JSON 对 LLM-to-LLM 通信无增益 |
| **Optimizer** | 独立，读 JSON 摘要 | 合并进 Analyzer-Evolver Phase B，内嵌 validate_patch 工具循环 | 共享上下文，即改即测 |
| **Validator** | 嵌在 Optimizer 里 | **Optimizer 的内部工具**（MCP Tool + LLM Tool），独立 test_cases DB | Optimizer 调它获得反馈，避免过拟合 |
| **Metric Monitor** | 不存在 | 独立协程，纯 SQL | 信号源，不重复分析逻辑 |
| **Meta Optimizer** | 不存在 | 独立后台任务 | 扫描 analysis_traces，优化分析 skill |

### 新 pipeline 全景

```
┌─────────────────────────────────────────────────────────────────┐
│                      Claude Code Session                         │
│                                                                  │
│   Transcript JSONL (自动生成，无需额外 hook)                       │
│   ~/.claude/projects/<sanitized_cwd>/<session_id>.jsonl          │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ 新 User Message 到达 → 实时触发
                             │ (最后一个 segment 等 session 结束时兜底)
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1: Segmentation (确定性的)                                  │
│ ─────────────────────────────                                    │
│ • 按 User Message 划分 Segment                                   │
│ • 构建双向链表 (prev / next)                                      │
│ • 截断 execution + 机械统计                                       │
│ • 写入 SegmentStore (SQLite, 解耦于 transcript)                   │
│                                                                  │
│ Output: Segment (含完整截断后的 execution)                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             │ Segment 创建 → 入分析队列
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 2: Analyzer-Evolver (合并，共享上下文)                       │
│                                                                  │
│ Phase A: 诊断                                                     │
│   输入: execution + skill content                                │
│   输出: 自然语言诊断 (不是 JSON)                                   │
│                                                                  │
│ Phase B: 进化 (内嵌 validate→fix 循环)                             │
│   输入: Phase A 诊断 + 原 skill + error_summary                  │
│   工具:                                                           │
│     validate_patch(patch, skill) → {pass, fail, reason, cases}   │
│     add_test_case(skill, error_pattern)                          │
│     run_test_suite(skill_id)                                     │
│   流程: generate → validate → fail? → fix → validate → pass      │
│   输出: SkillPatch (验证通过的 diff) + change_summary             │
│                                                                  │
│   Side-effect: analysis_traces 记录分析过程                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                       Apply Patch → 新版本 SKILL.md
                             │
                             ▼
                       SkillRecord 更新

┌─────────────────────────────────────────────────────────────────┐
│ 后台: Metric Monitor + Meta Signal Detector                       │
│                                                                  │
│ Metric Monitor (独立协程, 纯 SQL):                                │
│   扫描 SkillRecord 健康指标 → 发现异常 → 推入分析队列              │
│                                                                  │
│ Meta Signal Detector (后台, 低频):                                │
│   扫描 analysis_traces + Validator test_cases 变化               │
│   检测信号 → 优化分析 skill SKILL.md                               │
│   不进实时循环                                                    │
└─────────────────────────────────────────────────────────────────┘
```

### Validator 设计：Optimizer 的内部工具

**核心洞察：** Validator 是 Optimizer 手中的工具，不是 Pipeline 的一个阶段。Optimizer 调用它获得反馈、修正 patch、添加测试用例——和开发者用 `pytest` 一样。

```
Optimizer (Phase B LLM):
  │
  ├── generate_candidate_patch(diagnosis, skill)
  │
  ├── validate_patch(patch, skill_id)
  │     → {verdict: "fail", reason: "Step 3 still assumes requests is installed",
  │         failed_cases: ["sandbox-no-network: pip install fails"]}
  │
  ├── "The sandbox has no network, pip install will also fail.
  │     I need to use urllib as the fallback instead."
  │
  ├── fix patch → validate_patch(patch, skill_id)
  │     → {verdict: "pass"}
  │
  └── output final patch
```

**Validator 内部结构：**

```
Validator (独立 MCP Tool + Optimizer LLM Tool):
├── validator.db (SQLite)
│   ├── test_cases (skill_id, input_desc, expected_behavior, source)
│   └── validation_runs (patch_hash, skill_id, verdict, failed_cases, timestamp)
│
├── 测试用例来源
│   ├── skill_create 时提供的验证数据
│   ├── Optimizer 调用 add_test_case 添加的执行错误
│   └── 人工通过 MCP tool 添加的边界 case
│
├── 验证方法 (L1: 确定性, L2: LLM, L1+L2 都在 Optimizer 的工具调用中执行)
│   ├── L1 机械检查: frontmatter 合法、无危险模式、格式正确
│   └── L2 语义评测: LLM 判定 patch 是否解决根本问题（独立 prompt，非 Optimizer 的 LLM）
│
└── 输出
    └── {verdict, reason, failed_cases: [...], suggestion}
```

**过拟合防护：** test_cases 是累积的。每次执行中出现新错误，Optimizer 通过 `add_test_case` 注册。下次修改同一 skill 时，`validate_patch` 会检查所有历史用例——不只是当前这一个。这就是为什么 Validator 必须有独立数据库。

## 三、触发策略：实时 + 链式

```
新 User Message 到达
  │
  ├── 创建 Segment₃ → Segment₂ 的 next 补全
  ├── Segment₂ 立即可分析（不需要等 session 结束）
  │
  └── Segment₃ 是链上最后一个
        → 等待：下一条 user message 到达
        → 兜底：session 结束
```

## 四、异步设计：四个独立协程

```
Task 1: Segment Watcher (实时)
  - 监听 transcript 新 user message
  - 解析 segment → 写入 SegmentStore
  - prev 补全 → 推入分析队列

Task 2: Analyzer-Evolver Runner (异步, 可并发)
  - 消费分析队列
  - Phase A: 诊断 → Phase B: 进化 (内嵌 validate→fix 循环)
  - 写入 analysis_traces
  - 失败重试 (最多 3 次)

Task 3: Metric Monitor (后台, 低频)
  - 纯 SQL 扫描 skill_records 健康指标
  - 发现异常 → 构造虚拟请求推入分析队列

Task 4: Meta Signal Detector (后台, 低频)
  - 扫描 analysis_traces + Validator test_cases
  - 检测分析 skill 退化信号
  - 优化分析 skill SKILL.md
```

## 五、分层隔离：阻断无限递归

```sql
-- Layer 0: 用户技能执行 → Segmenter 写入
CREATE TABLE segments (...);

-- Layer 0: 分析结果
CREATE TABLE execution_analyses (...);

-- Layer 0: 技能版本追踪
CREATE TABLE skill_records (...);

-- Validator 独立数据 (Optimizer 工具的内部状态)
CREATE TABLE validator_test_cases (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    input_desc TEXT NOT NULL,
    expected_behavior TEXT NOT NULL,
    source TEXT DEFAULT 'execution_error',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE validator_runs (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    patch_hash TEXT,
    verdict TEXT NOT NULL,
    failed_cases TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Layer 1: 分析 LLM 自身执行 → 仅供 Meta Signal Detector
CREATE TABLE analysis_traces (...);
```

**递归阻断：** Meta Optimizer 的产出 = 分析 skill 的 SKILL.md 文本修改 → 不进入 Segmenter → 不触发新分析。

## 六、数据流总览

```
Claude Code Session → Transcript JSONL
        │
   Task 1 (Segment Watcher)     Metric Monitor (SQL 信号)
        │                             │
        ▼                             │ 指标异常 → 虚拟请求
   SegmentStore                        │
        │                             │
        ├─────────────────────────────┘
        ▼
   Task 2: Analyzer-Evolver
   ┌──────────────────────────────────┐
   │ Phase A: 诊断 (自然语言)          │
   │ Phase B: 进化 (generate→validate→fix) │
   │   tools: validate_patch          │
   │          add_test_case           │
   │          run_test_suite          │
   └──────────────┬───────────────────┘
                  │ final patch (pass)
                  ▼
            Apply Patch → 新版本 SKILL.md

   Task 4: Meta Signal Detector (后台)
   扫描 analysis_traces + validator_runs
   → 优化分析 skill SKILL.md
```

## 七、与 v0.2 架构的差异

| 维度 | v0.2 (当前) | v0.3 (本方案) |
|------|-----------|-------------|
| 数据源 | capture.py hook → history.db | Claude Code 内置 transcript JSONL |
| 切分单位 | Session | Segment (user message + 双向链表) |
| 提取方式 | Regex extractor chain (3) | 无提取层（LLM 直接分析 execution） |
| 分析→进化 | Pipeline → Analyzer → Optimizer (JSON 断层) | Segmenter → Analyzer-Evolver (自然语言诊断，共享上下文) |
| 中间产物 | JSON (ExecutionAnalysis) | 自然语言诊断（不是独立产物，是 Phase B 的 prompt 前缀） |
| Validator | 嵌在 Optimizer / 独立阶段 | **Optimizer 的内部工具**（MCP Tool + LLM Tool），独立 test_cases DB |
| 过拟合防护 | 无 | test_cases 累积 + add_test_case |
| 触发方式 | 手动 `pipeline_run` | 实时 + session 结束兜底 + Metric Monitor 信号 |
| 异步模型 | 同步 | 4 个独立协程 |
| 递归处理 | 不存在 | 分层物理隔离 |
