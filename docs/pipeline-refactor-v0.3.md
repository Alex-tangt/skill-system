# Pipeline Refactor v0.3: Transcript-Native Recording & LLM Analysis

> 基于 OpenSpace 深度调研 + Claude Code Transcript 机制验证 + 多轮架构讨论的 pipeline 重构方案。
> 核心变化：hook-event 碎片化提取 → transcript 原生录制 + 实时分段 + LLM 后置分析。
> 模块重组：Analyzer + Optimizer 合并（共享上下文），Validator 独立（输入输出分离）。

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
3. **分析与进化共享上下文** — 同一 segment 的上下文复用，不产生信息断层
4. **验证与优化分离** — 验证器输入输出独立，不需要 execution 上下文
5. **实时触发 + 链式上下文** — 下一条用户消息即触发，LLM 自主沿链扩展
6. **分层隔离** — Layer 0 和 Layer 1 数据物理隔离，Meta 优化不进实时循环

### 模块重组逻辑

```
旧设计:
  Data Pipeline (regex extract) → Analyzer → Optimizer → (Validator embedded)
  问题: Analyzer → Optimizer 之间有信息断层（JSON 摘要丢失上下文）

新设计:
  Segmenter → Analyzer-Evolver (共享上下文, 两阶段 LLM) → Validator (独立)
            ↑                                    ↑
        Metric Monitor (信号源)              Meta Optimizer (后台, 低频)
```

| 模块 | 旧设计 | 新设计 | 理由 |
|------|--------|--------|------|
| **Segmenter** | 不存在（hook 按 session group） | 新模块，确定性的 | transcript → segment 链表 |
| **Analyzer** | 独立，产出 JSON 摘要 | 合并进 Analyzer-Evolver Phase A | 上下文不丢失 |
| **Optimizer** | 独立，读 JSON 摘要 | 合并进 Analyzer-Evolver Phase B | 共享上下文，不重复加载 |
| **Validator** | 嵌在 Optimizer 里 | **独立模块** | I/O 独立，不需要 execution 上下文 |
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
        ┌────────────────────┴────────────────────┐
        │                                         │
   Task 1: Segment Watcher (实时)           Metric Monitor (独立协程)
        │                                   纯 SQL, 零 LLM 调用
        ▼                                   扫描 SkillRecord 健康指标
   SegmentStore                             发现异常 → 推入分析队列
   (segments 表)
        │
        │ 新 segment? prev 补全?
        ▼
   Task 2: Analyzer-Evolver (异步, 可并发)
   ┌──────────────────────────────────────┐
   │ Phase A: 分析                         │
   │   输入: Segment (execution + skills)  │
   │   输出: SkillJudgment[] + Suggestion[]│
   │                                      │
   │ Phase B: 进化                         │
   │   输入: Phase A 输出 + 原 skill 内容   │
   │   输出: SkillPatch (可应用的 diff)     │
   │                                      │
   │   上下文共享, LLM 调用两次             │
   └──────────────┬───────────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
        ▼                   ▼
   analysis_traces     SkillPatch
   (分析 LLM 自身记录)    │
                  │
                  ▼
   Task 3: Validator (独立, 无 execution 上下文)
   ┌──────────────────────────────────────┐
   │ Layer 1: 机械检查 (linter 化)         │
   │   - frontmatter 合法性               │
   │   - 危险模式检测                     │
   │   - diff 合理性检查                   │
   │                                      │
   │ Layer 2: 语义检查 (LLM, 低频)         │
   │   - 修改是否解决了问题？              │
   │   - 是否引入新歧义/错误？             │
   │                                      │
   │ 输出: pass / reject / needs_review    │
   └──────────────┬───────────────────────┘
                  │ pass
                  ▼
            Apply Patch → 新版本 SKILL.md
                  │
                  ▼
            SkillRecord 更新 (质量计数器)

┌─────────────────────────────────────────────────────────────────┐
│ Task 4: Meta Signal Detector (后台, 低频)                         │
│                                                                  │
│ • 周期性扫描 analysis_traces 表                                    │
│ • 检测信号：格式错误率 > 阈值、技能退化、用户纠正                     │
│ • 触发分析 skill 优化 → 修改分析 skill 的 SKILL.md                  │
│ • 不进实时循环 — 这是一次 prompt 修改, 不是新 segment               │
└─────────────────────────────────────────────────────────────────┘
```

## 三、触发策略：实时 + 链式

```
新 User Message 到达
  │
  ├── 创建 Segment₃
  ├── Segment₃ 的创建意味着 Segment₂ 的「完成」— 用户已开始说下一件事
  ├── Segment₂ 立即可分析（不需要等 session 结束）
  │
  └── Segment₃ 是链上最后一个节点
        → 没有 next，无法判断边界
        → 等待：下一条 user message 到达（最常见, 秒-分钟级）
        → 兜底：session 结束（最后一段）
```

| 条件 | 行为 |
|------|------|
| 新 User Message 到达，上一个 segment 的 next 被补全 | **立即触发分析** |
| Segment 是链上最后一个，session 未结束 | **等待** |
| Session 结束，最后一个 segment 仍无 next | **兜底触发** |

不需要超时、不需要撤销、不需要计时器。绝大多数 segment 在秒-分钟级获得 next。

## 四、Phase 1: Segmentation（确定性的）

### 4.1 数据源

```
路径: ~/.claude/projects/<sanitized_cwd>/<CLAUDE_CODE_SESSION_ID>.jsonl
推导: sanitized_cwd = PWD.replace('/', '-')
环境变量: CLAUDE_CODE_SESSION_ID (已在 MCP Server 进程中可用)
```

### 4.2 Transcript 消息类型

| 类型 | 识别方式 | 处理 |
|------|---------|------|
| **用户消息** | `type == "user"` 且 **无** `toolUseResult` | → Segment 边界 |
| **工具结果** | `type == "user"` 且 **有** `toolUseResult` | → 当前 Segment 的 execution |
| **Assistant** | `type == "assistant"` | → 当前 Segment 的 execution |
| **attachment** (skill_listing) | `attachment.type == "skill_listing"` | → skills_available |
| **attachment** (其他) | — | → 忽略 |
| **file-history-snapshot** | `type == "file-history-snapshot"` | → files_modified |
| **queue-operation / last-prompt** | — | → 忽略 |

### 4.3 数据结构

```python
@dataclass
class Segment:
    id: str                          # UUID
    session_id: str                  # Claude Code session ID
    user_msg: str                    # 用户输入原文
    user_msg_index: int              # 在 transcript 中的序号
    execution_json: str              # JSON array, 截断后的 TranscriptEntry 列表
    prev_id: str | None
    next_id: str | None
    stats_json: str                  # SegmentStats
    skills_available: str            # JSON array of skill names
    files_modified: str              # JSON array of file paths

@dataclass
class SegmentStats:
    tool_count: int
    tool_types: dict[str, int]       # {"shell": 5, "mcp": 3}
    iteration_count: int
    status: str                      # "success" | "error" | "incomplete"
    skills_referenced: list[str]
    total_chars: int
    started_at: float
    finished_at: float
```

### 4.4 存储

```sql
CREATE TABLE segments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    transcript_path TEXT NOT NULL,
    user_msg_index INTEGER NOT NULL,
    user_msg TEXT NOT NULL,
    execution_json TEXT NOT NULL,
    prev_id TEXT,
    next_id TEXT,
    stats_json TEXT NOT NULL,
    skills_available TEXT NOT NULL,
    files_modified TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, user_msg_index)
);
```

**execution 完整存到 SQLite。** 不存指针（transcript 可能被清理）。5K-50K 字符/条对 SQLite 不是问题。

### 4.5 分割算法

```python
class Segmenter:
    def __init__(self, reader: TranscriptReader)
    def segment(self) -> list[Segment]:
        """遍历 transcript → 识别用户消息边界 → 收集 execution → 截断 → 统计 → 链表"""
```

## 五、Phase 2: Analyzer-Evolver（分析与进化合并）

### 5.1 为什么合并

旧设计中 Analyzer 产出 JSON 摘要 → Optimizer 读摘要做修改。信息断层：
- Analyzer 看到「agent 在第 3 轮用了 urllib」，但输出摘要时写成「retry 逻辑缺失」
- Optimizer 只拿到「retry 逻辑缺失」，不知道该改 SKILL.md 中哪一步、参数是什么

合并后 Analyzer 和 Evolver 共享同一 segment 上下文。Phase A 分析问题，Phase B 产出修改——同一份上下文，两次 LLM 调用，不高双倍 token。

### 5.2 Phase A: 分析（Analysis）

与旧 Analyzer 相同，优先级预算截断 + 富化 + LLM Agent Loop。

```
Priority 0  CRITICAL  — 用户指令                  → 永不截断
Priority 1  CRITICAL  — 最后一轮 assistant 响应    → 永不截断
Priority 2  HIGH      — 工具调用 + 工具错误        → 配对保留
Priority 3  HIGH      — 中间轮 assistant 推理      → 可截断
Priority 4  MEDIUM    — 工具成功结果               → 尝试保留
Priority 5  LOW       — 系统引导消息               → 溢出丢弃
SKIP        —         — 技能注入文本、冗长 prompt  → 不包含
```

截断阈值：错误 1000、成功 800、参数 500、对话 5000 字/条。

输出：`SkillJudgment[]` + `EvolutionSuggestion[]`（方向，不含具体修改）。

### 5.3 Phase B: 进化（Evolution）

```
输入:
  - Phase A 产出的 EvolutionSuggestion[]
  - 被建议修改的技能的完整 SKILL.md
  - 原 execution（如有歧义可回查，但通常不需要）

输出:
  - SkillPatch: 具体 diff/patch（FULL/DIFF/PATCH 三格式自动检测，借鉴 OpenSpace）
  - change_summary: 一句话描述
```

Phase B 的 prompt 专注于「基于分析结论，产出可应用的修改」。不受 execution 噪音干扰（Phase A 已经完成了从噪音中提取信号）。

### 5.4 上下文扩展示例

```
LLM 收到 Segment₂ 的 Phase A 分析请求

Segment₂.user_msg = "加上重试和日志"
Segment₂.prev → Segment₁.user_msg = "帮我写一个爬虫"

LLM 调用: traverse_chain("prev", 1)
→ 返回 Segment₁ 的完整 execution

LLM: "Segment₁ 中 agent 用 urllib 写了基础爬虫，但 retry 逻辑在 Segment₂ 才加上。
      crawler-template 技能没有预先指导 retry 设计 → 建议 FIX"

→ Phase B 输入: EvolutionSuggestion(type=FIX, target=crawler-template, direction="补 retry 指导")
→ Phase B 输出: SkillPatch(SEARCH: "Step 2: 发送请求" → REPLACE: "Step 2: 检查 requests 是否可用, 否则用 urllib. 对 HTTPError 429/5xx 自动重试")
```

## 六、Validator（独立模块）

### 6.1 为什么独立

验证的输入不需要 execution 上下文：

```
输入:
  - 旧 skill SKILL.md
  - SkillPatch (diff/patch)
  - change_summary
  - 前后指标对比 (completion_rate, error_rate)

输出:
  - pass / reject / needs_review
  - 风险标记
```

这是一个比较任务，不需要知道「当时 agent 在第 3 轮想了什么」。独立后可以单独测试、单独迭代。

### 6.2 双层验证

```
Layer 1: 机械检查 (确定性, 零 token)
  ├── SKILL.md frontmatter 格式合法性
  ├── 目录/文件名规范
  ├── 危险模式检测 (prompt injection, credential leak)
  └── diff 合理性 (是否为空/超大/全删)

Layer 2: 语义检查 (LLM, 低频)
  ├── 修改是否真正解决了提出的问题？
  ├── 修改是否引入新歧义/错误？
  └── 修改是否符合技能原始定位？

触发 Layer 2 的条件:
  - Layer 1 通过 + (新技能上线后指标下降 | 涉及 critical_tools | 人工标记需审核)
```

## 七、Metric Monitor（独立信号源）

### 7.1 为什么独立

Metric Monitor 是纯 SQL 查询，零 LLM 调用。它只是一个信号源——发现异常后推入 Analyzer-Evolver 的分析队列，而不是重复实现分析逻辑。

```
Metric Monitor (独立协程):
  1. 扫描 SkillRecord:
     - completion_rate 低于阈值
     - fallback_rate 高于阈值
     - applied_rate 突然下降
  2. 发现异常 → 构造虚拟分析请求:
     user_msg = "Metric alert: skill X completion_rate dropped from 0.8 to 0.3"
     execution = (空, 因为是纯指标异常)
  3. 推入分析队列 → Analyzer-Evolver 正常处理
```

### 7.2 三种触发来源统一入口

```
触发来源:
  ① 用户对话 (segment next 补全)     → 分析队列
  ② Metric Monitor (指标异常)        → 分析队列
  ③ Tool Quality (工具退化, 未来)     → 分析队列

三个信号源, 一个 Analyzer-Evolver 管道
```

## 八、异步设计：四个独立协程

```
Task 1: Segment Watcher (实时)
  - 监听 transcript 新 user message
  - 解析 segment → 写入 SegmentStore
  - prev 补全 → 推入分析队列

Task 2: Analyzer-Evolver Runner (异步, 可并发)
  - 消费分析队列
  - Phase A: 分析 → Phase B: 进化 → SkillPatch
  - 写入 analysis_traces + execution_analyses
  - 失败重试 (最多 3 次)

Task 3: Validator (独立, 串行)
  - 消费 SkillPatch 队列
  - Layer 1 机械检查 → Layer 2 语义检查 (低频)
  - pass → 应用 patch + 更新 SkillRecord

Task 4: Meta Signal Detector (后台, 低频)
  - 扫描 analysis_traces
  - 信号检测 (格式错误/退化/纠正)
  - 触发分析 skill 优化
```

## 九、分层隔离：阻断无限递归

### 9.1 数据分层

```sql
-- Layer 0: 用户技能执行 → Segmenter 写入
CREATE TABLE segments (...);

-- Layer 0: 分析+进化结果 → Analyzer-Evolver 写入
CREATE TABLE execution_analyses (...);

-- Layer 0: 技能版本追踪
CREATE TABLE skill_records (...);

-- Layer 1: 分析 LLM 自身执行 → 仅供 Meta Signal Detector 读取
-- Segmenter 不碰这个表
CREATE TABLE analysis_traces (
    id TEXT PRIMARY KEY,
    analysis_id TEXT,
    segment_id TEXT,
    llm_model TEXT,
    prompt_json TEXT,
    response_json TEXT,
    tool_calls_json TEXT,
    tokens_used INTEGER,
    duration_ms INTEGER,
    status TEXT,                 -- success / error / parse_failed
    created_at TEXT
);
```

### 9.2 递归阻断点

```
Meta Optimizer 的产出 = 分析 skill 的 SKILL.md 文本修改
→ 不是新 segment
→ 不进入 Segmenter
→ 不触发新一轮分析
→ 修改在下一次 Analyzer-Evolver 调用时生效
```

**核心规则：只有 Layer 0（用户 Agent 执行）的数据进入 segment 表。分析过程产物不走同一 pipeline。**

### 9.3 分析 skill 冷启动

| 策略 | 做法 |
|------|------|
| **种子数据** | 手动 3-5 个标准案例，人工标注结果 |
| **保守模式** | 不确定时标 low confidence → 不触发进化 → 积累为 Meta 优化信号 |
| **格式优先** | 初始只优化格式正确性，语义判断随数据积累提升 |

## 十、数据流总览

```
┌──────────────────────────────────────────────────────────┐
│                  Claude Code Session                       │
│  Transcript JSONL (source of truth, 只读)                 │
└────────────────────┬─────────────────────────────────────┘
                     │
        ┌────────────┴──────────────┐
        │                           │
   Task 1 (实时)                Metric Monitor
   Segment Watcher              (纯 SQL 信号源)
        │                           │
        ▼                           │ 指标异常 → 虚拟请求
   SegmentStore                     │
   (segments 表)                    │
        │                           │
        │ 新 segment?                │
        ├─────────────┬─────────────┘
        ▼             ▼
   ┌──────────────────────────────────────┐
   │ Task 2: Analyzer-Evolver (共享上下文)  │
   │                                      │
   │ Phase A: 分析 ──→ SkillJudgment[]     │
   │                   EvolutionSuggestion[]│
   │ Phase B: 进化 ──→ SkillPatch          │
   │                                      │
   │ Side-effects:                        │
   │   analysis_traces (分析过程记录)       │
   │   execution_analyses (分析结果)        │
   └──────────────┬───────────────────────┘
                  │ SkillPatch
                  ▼
   ┌──────────────────────────────────────┐
   │ Task 3: Validator (独立)              │
   │                                      │
   │ Layer 1: 机械检查 (linter)            │
   │ Layer 2: 语义检查 (LLM, 低频)         │
   │                                      │
   │ 输出: pass / reject / needs_review    │
   └──────────────┬───────────────────────┘
                  │ pass
                  ▼
            Apply Patch → 新版本 SKILL.md
                  │
                  ▼
            SkillRecord 更新

   ┌──────────────────────────────────────┐
   │ Task 4: Meta Signal Detector (后台)   │
   │                                      │
   │ 扫描 analysis_traces                  │
   │ 检测格式错误/退化/纠正信号             │
   │ → 修改分析 skill 的 SKILL.md           │
   │ → 不进实时循环                        │
   └──────────────────────────────────────┘
```

## 十一、与 v0.2 架构的差异

| 维度 | v0.2 (当前) | v0.3 (本方案) |
|------|-----------|-------------|
| 数据源 | capture.py hook → history.db | Claude Code 内置 transcript JSONL |
| 切分单位 | Session (按 session_id group) | Segment (按 user message 切分 + 双向链表) |
| 提取方式 | Regex extractor chain (3) | 无提取层（LLM 直接分析 execution） |
| 模块关系 | Pipeline → Analyzer → Optimizer (Analysis 产 JSON 摘要，断层) | Segmenter → Analyzer-Evolver (共享上下文) → Validator (独立) |
| 触发方式 | 手动 `pipeline_run` | 实时（下一条 user message）+ 兜底（session 结束）+ Metric Monitor 信号 |
| 截断策略 | 无 | 优先级预算截断（OpenSpace 模式） |
| 异步模型 | 同步 | 4 个独立协程 |
| 递归处理 | 不存在 | 分层物理隔离，Meta Signal Detector 低频后台 |
| 验证 | 嵌在 Optimizer | 独立 Validator (双层：linter + LLM) |
| 保留组件 | — | dedup.py, SkillStore |
| 废弃组件 | — | capture.py, history.db, extractors.py, data_pipeline plugin, HistoryEvent/PipelineStatus |

## 十二、迁移计划

### Step 1: 基础设施（1-2 天）

- [ ] 实现 `TranscriptReader` — 惰性解析 transcript JSONL
- [ ] 实现消息类型识别（user msg vs tool result vs assistant）
- [ ] 验证路径推导 + 环境变量可用性
- [ ] 实现 SegmentStore（SQLite schema + CRUD）

### Step 2: Segmentation（2-3 天）

- [ ] 实现 `Segmenter`（消息边界识别 + 链表构建）
- [ ] 实现优先级预算截断
- [ ] 实时触发逻辑（Segment Watcher 协程）
- [ ] 单元测试

### Step 3: Analyzer-Evolver（4-5 天）

- [ ] 实现 `AnalysisPromptBuilder`（截断 + 富化 + 模板）
- [ ] 实现 Phase A: Analysis Runner（LLM agent loop + tools）
- [ ] 实现 Phase B: Evolution Runner（共享上下文, 产出 patch）
- [ ] 实现三格式 patch 解析（FULL/DIFF/PATCH 自动检测, 借鉴 OpenSpace）
- [ ] 实现 `analysis_traces` 记录
- [ ] 结构化输出解析 + 重试

### Step 4: Validator + Metric Monitor（2-3 天）

- [ ] 实现 `Validator` Layer 1（机械检查: frontmatter, 安全, diff 合理性）
- [ ] 实现 `Validator` Layer 2（语义检查: LLM, 低频, 独立 prompt）
- [ ] 实现 `Metric Monitor`（纯 SQL 扫描 SkillRecord 健康指标, 推入分析队列）
- [ ] 单元测试

### Step 5: Meta Signal Detector（1-2 天）

- [ ] 实现 `MetaSignalDetector`（扫描 analysis_traces）
- [ ] 信号检测：格式错误率、技能退化、用户纠正
- [ ] 分析 skill 优化流程（修改 SKILL.md）
- [ ] 冷启动策略（种子数据 + 保守模式）

### Step 6: 集成与清理（2-3 天）

- [ ] 新 pipeline 替换旧 `DataPipelinePlugin`
- [ ] 废弃：capture.py, history.db, extractors.py, data_pipeline/plugin.py
- [ ] 保留简化：dedup.py, SkillStore
- [ ] 更新 MCP tools
- [ ] 端到端测试 + 文档更新

## 十三、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Transcript 格式变更 | Segmentation 失效 | 版本检测 + schema 验证 |
| Session 中断 → transcript 不完整 | 分析质量下降 | stats 标 `incomplete` |
| LLM 分析成本过高 | 不可持续 | 优先 `skills_referenced > 0` 的 segment；预算控制 |
| Phase B 产出不可应用的 patch | 浪费分析 token | 三格式自动检测 + 模糊匹配降级 + 失败回退到人工审核 |
| 冷启动分析质量低 | 早期进化不可靠 | 保守模式 + 种子数据；low confidence 不执行 |
| 分析 skill 自身退化 | 分析质量螺旋下降 | Meta Signal Detector 及时检测+修复 |

## 参考

- [OpenSpace Architecture Insights](openspace-architecture-insights.md) — 借鉴的设计模式详解
- [architecture-v0.2.md](architecture-v0.2.md) — v0.2 架构方向
