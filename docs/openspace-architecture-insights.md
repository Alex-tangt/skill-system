# OpenSpace Architecture Insights

> 2026-06-08 深度阅读 OpenSpace 源码后的架构笔记。
> 记录值得 Skill-System 借鉴的设计模式、算法细节和工程决策。
> 不是对比文档，是提取可复用模式的参考手册。

## 一、核心架构决策

### 1.1 单体设计 vs 微内核

OpenSpace 采用单体 `OpenSpace` 类而非微内核架构。所有组件（recording、analysis、evolution、quality）深度集成，共享同一个 SQLite 数据库和 LLM client。

**值得参考的点：** 深度集成使得「录制→分析→进化」闭环可以在不经过 MCP JSON 序列化的情况下直接传递数据（对象引用而非 JSON），降低了组件间通信开销。

**Skill-System 的选择：** 我们保持微内核 + 插件隔离，但 pipeline 内部组件（segmenter → analyzer）之间应该直接传递对象，不需要走 MCP 工具调用。

### 1.2 SQLite 作为唯一持久化层

OpenSpace 只用 SQLite（不开其他数据库），skill store、quality store、analysis store 共享同一个 DB 文件但不同表。配合 WAL 模式实现读写并发。

**关键细节：** 有一个 `_db_retry` 装饰器处理「database is locked」瞬态错误，指数退避 5 次（0.1s → 1.6s）。

```python
def _db_retry(max_retries=5, initial_delay=0.1, backoff=2.0):
    # Catches OperationalError, retries with exponential backoff
```

## 二、录制与分割

### 2.1 三层录制架构

```
RecordingManager (单例)
├── TrajectoryRecorder     → traj.jsonl (工具执行轨迹)
├── ActionRecorder         → agent_actions.jsonl (Agent 推理决策)
└── LLM Client Wrapping    → conversations.jsonl (LLM 对话流)
```

**关键设计决策：**
- `RecordingManager` 是全局单例，`_global_instance` 类变量维护
- 录制产物按 task 目录组织：`logs/recordings/<task_id>_<timestamp>/`
- conversations.jsonl 采用 setup + iteration delta 模式（不重复存储 system prompt）
- conversation 录制支持多 agent_name（GroundingAgent, ExecutionAnalyzer, SkillEvolver），通过 `agent_name` 字段区分不同 pipeline 阶段的对话

### 2.2 录制触发机制

两种方式并存：

1. **LLM Client 包装** — `register_to_llm(llm_client)` 动态替换 `complete()` 方法，拦截返回的 `tool_results`
2. **BaseTool 回调** — 每个 `BaseTool.execute()` 完成后主动调用 `record_tool_execution()`

**启示：** 我们不依赖 hook，直接读 transcript，比这两种方式都简单。但 `agent_name` 字段的思路值得借鉴 — 如果未来 pipeline 有多个分析阶段，可以用它区分不同阶段的对话。

## 三、对话格式化（prompt 构建核心）

### 3.1 优先级预算截断

这是 Phase 2 最值得直接借鉴的组件。核心思想：**不是简单截尾，而是给消息打优先级，在预算内最大化信息密度。**

```
Priority 0  CRITICAL  — 用户指令                  → 永不被截断
Priority 1  CRITICAL  — 最后一轮 assistant 响应    → 永不被截断
Priority 2  HIGH      — 工具调用 + 工具错误        → 配对保留
Priority 3  HIGH      — 中间轮 assistant 推理      → 可截断到首行
Priority 4  MEDIUM    — 工具成功结果               → 尝试保留，有 truncatable_to
Priority 5  LOW       — 系统引导消息               → 溢出时丢弃
SKIP        —         — 技能注入文本、冗长 system prompt → 不包含
```

**截断策略（`_assemble_with_budget`）：**

```
① 计算 Priority ≤ 3 的总大小 → essential_chars
② 如果 essential_chars ≤ budget:
    全部 Priority ≤ 3 + 按时间顺序加 P4-P5，直到预算耗尽
③ 如果 essential_chars > budget (极端情况):
    保留 P0-1 完整 → P2 按比例分配 → P3 只取首行
```

**每条消息携带 `truncatable_to` 字段：** 当空间不足时，不是直接丢弃，而是截断到最小版本：
- 工具结果（有内嵌摘要）：`truncatable_to=500`
- 工具结果（无摘要）：`truncatable_to=300`
- 系统消息：`truncatable_to=150`

### 3.2 内嵌摘要提取

Shell agent 结果中常有 `Execution Summary (N steps):` 块，这比原始输出更浓缩、更有价值。`_extract_embedded_summary` 用正则提取这些自生成的摘要，作为 Priority 3 而非 Priority 4 处理。

**启示：** 在 prompt 构建时，寻找和提升「高信息密度」的内容块，降级「冗余」内容。这不只是截断，而是质量感知。

### 3.3 截断阈值配置

```python
TOOL_ERROR_MAX_CHARS    = 1000   # 错误保留较多（前几行就能定位问题）
TOOL_SUCCESS_MAX_CHARS  = 800    # 成功保留较少（预期内的，不需要全文）
TOOL_ARGS_MAX_CHARS     = 500    # 参数太长说明调用方式有问题
TOOL_SUMMARY_MAX_CHARS  = 1500   # 内嵌摘要保留较多（信息密度高）
```

## 四、LLM 输出处理

### 4.1 三格式 patch 自动检测

解决 LLM 修改技能文件时输出格式不稳定的问题：

```
检测优先级（按结构标记）：
  ① "*** Begin Patch"  → PATCH (多文件 diff)
  ② "*** Begin Files"  → FULL  (多文件 envelope)
  ③ "*** File:" 行首标记 → FULL  (裸多文件，需 ≥2 个或后续有内容)
  ④ "<<<<<<< SEARCH"   → DIFF  (单文件 SEARCH/REPLACE)
  ⑤ 无标记              → FULL  (单文件完整内容，fallback)
```

**启示：** 永远不要强制 LLM 输出特定格式。接受多种格式、自动检测、统一处理——这是让 LLM 参与代码修改的工程最佳实践。

### 4.2 模糊匹配链（6 层降级）

LLM 输出的 SEARCH 块与文件原文常有微小差异。6 层降级匹配：

```python
REPLACER_CHAIN = [
    ("simple",                  simple_replacer),              # Exact match
    ("line_trimmed",            line_trimmed_replacer),        # Per-line strip
    ("block_anchor",            block_anchor_replacer),        # 首尾行锚定 + Levenshtein 中段
    ("whitespace_normalized",   whitespace_normalized_replacer), # 压缩所有空白
    ("indentation_flexible",    indentation_flexible_replacer),  # 移除公共缩进
    ("trimmed_boundary",        trimmed_boundary_replacer),    # 整个 block strip
]
```

**关键细节：**
- Levenshtein 只用于 Level 3 的多候选歧义消除（避免全文计算）
- 每个 replacer 是 generator，yield 候选匹配；调用方用 `str.find()` 验证
- 单候选时相似度阈值 = 0（宽松），多候选时阈值 = 0.3

### 4.3 Skill ID 纠错

LLM 在生成进化建议时经常把 skill_id 的 hex 后缀搞错（`61f694bc` → `61f694cb`）。OpenSpace 有一个 `_correct_skill_ids()` 函数：

- 对每个 LLM 输出的 skill_id，在已知 ID 集合中找同名前缀 + 编辑距离 ≤ 4 的匹配
- 如果候选 ID 很多（>20），收紧编辑距离到 ≤ 2
- 有唯一匹配 → 静默修正；有歧义 → 保留原文，由 evolver warning

**启示：** LLM 处理 UUID/hex 时错误率很高，但修复成本极低（编辑距离计算）。这是一个可以用 20 行代码解决的实际问题。

## 五、进化系统

### 5.1 三种进化模式

```
FIX:     原地修复 → 同目录、同 name、新 skill_id、新 version
DERIVED: 增强/特化 → 新目录、新 name、引用 parent_skill_ids
CAPTURED: 全新创建 → 新目录、新 name、无 parent
```

### 5.2 三种触发来源

```
① Post-analysis:  ExecutionAnalyzer 产出 EvolutionSuggestion
② Tool degradation: ToolQualityManager 检测到工具成功率下降
③ Metric monitor: 周期性扫描 SkillRecord 健康指标
     (applied_rate / completion_rate / fallback_rate)
```

三种触发都产生 `EvolutionContext` → 进入同一个 `evolve()` 管道。

### 5.3 Evolution 安全机制

- **Confirmation gate** — 进化前检查 suggestion 是否仍然有效（工具状态可能已恢复）
- **Anti-loop guard** — 同一 skill 短时间内不能重复进化
- **Safety checks** — 检测 prompt injection、credential exfiltration 等危险模式
- **Validation** — 进化后验证 SKILL.md 的 frontmatter 合法性

### 5.4 版本 DAG 存储

```python
SkillLineage:
    origin: IMPORTED | CAPTURED | DERIVED | FIXED
    generation: int                              # 根=0, 子=父+1
    parent_skill_ids: [str]                      # 0-N 个
    content_diff: str                            # unified diff (git diff 格式)
    content_snapshot: {relative_path: content}    # 完整目录快照
    change_summary: str                          # LLM 生成的一句话描述
```

**content_diff 规则：**
- 0 parents → add-all diff（所有行都是 `+`）
- 1 parent → 正常 unified diff
- N parents → 空字符串（跨父技能的创造性组合，父 diff 无意义）

## 六、质量监控

### 6.1 双层故障检测

```
Rule-based（确定性）:
  每次工具调用 → ExecutionRecord(success, latency, error_message)
  → recent_success_rate, recent_executions (rolling window)

LLM-based（语义性）:
  分析 LLM 发现 rule-based 漏掉的错误
  → record_llm_tool_issues() → 注入到同一 pipeline
```

**去重逻辑：** 如果 rule-based 已经记录了某工具的全部调用都是错误，LLM 的重复标记会被过滤。

### 6.2 Penalty 驱动的工具排名

```
penalty = f(recent_success_rate):
  success_rate >= 0.8  → penalty = 1.0 (无惩罚)
  success_rate >= 0.5  → penalty = 0.7-1.0
  success_rate < 0.5   → penalty = 0.2-0.5

adjust_ranking(tools_with_scores):
  adjusted_score = semantic_score × penalty
```

### 6.3 级联进化

```
工具 A 的 success_rate 跌到 40%
  → ToolQualityManager 标记 A 为 degraded
  → 查找所有 tool_dependencies 包含 A 的 skill
  → 批量触发 FIX evolution
```

**启示：** 这个机制很优雅——不是等技能出问题再修复，而是主动在依赖项退化时预防性修复。但实现成本高，适合 Phase 3 后期。

## 七、可直接借用的代码模式

### 7.1 DB Retry 装饰器

```python
def _db_retry(max_retries=5, initial_delay=0.1, backoff=2.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (sqlite3.OperationalError, sqlite3.DatabaseError):
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
                    delay *= backoff
        return wrapper
    return decorator
```

### 7.2 编辑距离（紧凑 DP）

```python
def _edit_distance(a: str, b: str) -> int:
    # O(min(m,n)) 空间, O(m*n) 时间
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1, curr[j-1] + 1,
                prev[j-1] + (0 if ca == cb else 1)
            )
        prev = curr
    return prev[-1]
```

### 7.3 JSON 提取（处理 LLM markdown 包裹）

```python
def _extract_json(text: str) -> dict | None:
    # Try code block first
    code_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_match:
        text = code_match.group(1).strip()
    else:
        # Try bare JSON object
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            text = json_match.group()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
```

## 八、设计哲学总结

| OpenSpace 模式 | 核心思想 | 我们采纳程度 |
|---------------|---------|------------|
| Recording-first | 先无损录制，后分析 | ✅ 完全采纳（transcript 方案） |
| Post-execution analysis | 异步后置分析，不阻塞执行 | ✅ 完全采纳 |
| Priority-based truncation | 预算内最大化信息密度 | ✅ Phase 2 直接使用 |
| Multi-format LLM output | 接受多种格式，自动检测 | ✅ Phase 3 参考 |
| Fuzzy match chain | 降级匹配，容忍 LLM 输出偏差 | ✅ Phase 3 参考 |
| Version DAG + snapshot | 每次修改全量快照 | ⚠️ 可简化（存 diff + backup） |
| Tool quality → cascade evolution | 依赖退化 → 预防性修复 | ⚠️ Phase 3 后期 |
| Monolith integration | 深度耦合降低通信成本 | ❌ 不采纳（保持微内核） |
| Cloud skill community | 技能共享市场 | ❌ 不采纳（不在 scope 内） |
