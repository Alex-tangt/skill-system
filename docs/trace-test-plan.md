# Trace 数据流水线 — 真实场景测试方案

> 目标：通过真实 Claude Code 操作，验证 Hook 采集 → History DB → Pipeline → Trace DB 完整数据流。不写测试程序，由子 agent 自动执行指令并收集数据，最终输出 Markdown 报告供人工审核。

## 1. 测试 Skill（3 个）

| # | Skill 名称 | 真实场景 | 典型触发工具 | 成功标准 |
|---|-----------|---------|-------------|---------|
| S1 | `run-tests` | "帮我跑一下测试" | `Bash(pytest)` | exit code = 0 |
| S2 | `git-status` | "当前分支什么状态" | `Bash(git status/log)` | 有输出，无报错 |
| S3 | `markdown-stats` | "统计这个 md 的结构" | `Read(.md)` + `Bash(wc/grep)` | 返回行数/标题数 |

## 2. 测试矩阵

每条指令独立执行，清除历史影响。

### 2.1 指令清晰度等级

| 等级 | 标签 | 定义 | 示例（S1: run-tests） |
|------|------|------|----------------------|
| L1 | 明确指定 | 直接说"用 xxx skill" | "用 run-tests skill 跑一下单元测试" |
| L2 | 场景描述 | 描述需求但不提 skill 名 | "帮我跑一下项目的测试，看看有没有失败的" |
| L3 | 隐式意图 | 不直接提测试，靠上下文推断 | "刚改了代码，检查下有没有问题" |

### 2.2 测试组合（3×3 = 9 条指令）

| # | Skill | 等级 | 指令 |
|---|-------|------|------|
| 1 | S1 | L1 | "用 run-tests skill 跑 pytest 单元测试" |
| 2 | S1 | L2 | "帮我跑一下项目测试，看看结果" |
| 3 | S1 | L3 | "刚改了代码，检查下有没有破坏什么" |
| 4 | S2 | L1 | "用 git-status skill 看一下当前仓库状态" |
| 5 | S2 | L2 | "当前分支干什么了，改了什么文件" |
| 6 | S2 | L3 | "我昨天到现在干了啥" |
| 7 | S3 | L1 | "用 markdown-stats skill 分析 docs/DEVELOPMENT.md" |
| 8 | S3 | L2 | "帮我看下这个 md 文件的结构" |
| 9 | S3 | L3 | "这个文档写完了吗，还有多少待办" |

## 3. 数据检查点（4 个观测点）

```
指令执行 → [CP1: history.db] → pipeline_run → [CP2: traces.db] → [CP3: trace_get]
                                                    ↓
                                           [CP4: 触发对比矩阵]
```

### CP1：History DB（原始事件采集）

**观测时机**：每条指令执行后

**查询方式**：
```sql
SELECT id, session_id, hook_event_name, tool_name,
       substr(tool_input_json, 1, 100) as input_preview,
       tool_output_json IS NOT NULL AND tool_output_json != '' as has_output,
       dedup_hash, created_at, processed
FROM history_events
ORDER BY created_at DESC LIMIT 20;
```

**观测指标**：
- 指令是否产生了 hook 事件？未产生 = hook 失效
- `tool_name` 是否正确？预期 Bash/Read/Write
- `tool_input_json` 是否包含对应命令？
- `tool_output_json` 是否有内容？空 = capture.py 没抓到输出
- `dedup_hash` 无碰撞

### CP2：Pipeline 执行

**观测时机**：`pipeline_run` 调用后

**查询方式**：
```sql
SELECT COUNT(*) as total,
       SUM(CASE WHEN processed=0 THEN 1 ELSE 0 END) as pending,
       SUM(CASE WHEN processed=2 THEN 1 ELSE 0 END) as done
FROM history_events;

SELECT skill_id, COUNT(*) as step_count
FROM step_traces GROUP BY trace_id;
```

**观测指标**：
- `events_processed` = 新增的事件数？多了/少了 = 问题
- `traces_created` = 新增的 trace 数？同一 session 应追加而非新建
- `processed` 从 0 → 2 了吗？没变 = pipeline 没处理
- step_traces 数量与事件数对应？

### CP3：Trace 结构完整性

**观测时机**：CP2 之后

**查询方式**：`MCP: trace_get(run_id)` 或
```sql
SELECT et.run_id, et.status, et.context_type, et.llm_model,
       COUNT(st.id) as step_count,
       SUM(CASE WHEN st.status='succeeded' THEN 1 ELSE 0 END) as succeeded,
       SUM(CASE WHEN st.status='failed' THEN 1 ELSE 0 END) as failed
FROM execution_traces et
LEFT JOIN step_traces st ON et.id = st.trace_id
GROUP BY et.id ORDER BY et.started_at DESC LIMIT 10;
```

**观测指标**：
- `context_type = "hook"` 且非空
- `step_count` ≈ hook 事件数（误差 ±2）
- `succeeded` 占比 > 80%
- 每条 step_trace 的 `input/output` 非空

### CP4：触发对比矩阵

**观测时机**：全 9 条指令完成后

**对比维度**：

| 指令 | 等级 | 原生 skill 触发？ | history 事件数 | trace step 数 | 触发质量 |
|------|------|-------------------|---------------|---------------|---------|
| 1 | L1 | ✓ 明确触发 | N | ≈N | 完整 |
| 2 | L2 | ? 取决于 LLM | ? | ? | 待观测 |
| 3 | L3 | ? 取决于上下文 | ? | ? | 最差情况 |

**核心问题**：L1→L2→L3，skill 触发率和 trace 质量如何衰减？这是后续优化 skill description 的量化依据。

## 4. 测试执行流程

### 4.1 准备工作（一次性）

```
1. 确保 skill-system MCP server 已启动
2. 确保 .claude/settings.json hooks 已配置
3. 清空 history.db (备份后删除) 和 traces.db (备份后删除)
4. 创建 3 个 skill 的 SKILL.md
```

### 4.2 逐条执行（每条指令独立，9 轮）

每轮子 agent 的任务：

```
INPUT: 指令文本 + 指令编号 (#1-#9)
STEPS:
  1. 执行该指令（发起工具调用）
  2. 等待工具执行完成
  3. 查询 history.db (CP1) — 记录新增事件
  4. 调用 pipeline_run
  5. 查询 traces.db (CP2) — 记录 pipeline 输出
  6. 调用 trace_get (CP3) — 记录 trace 结构
  7. 输出本轮观测数据（JSON）
OUTPUT: {"round": N, "cp1": {...}, "cp2": {...}, "cp3": {...}}
```

### 4.3 汇总报告

所有轮次完成后，一个子 agent 汇总所有轮次的 JSON 数据，生成 CP4 触发对比矩阵和最终 Markdown 报告。

## 5. 成本控制

| 措施 | 说明 |
|------|------|
| 子 agent 模型 | 使用 `haiku`（成本最低），不做推理只做数据收集 |
| 上下文注入 | 每轮只注入：本轮指令 + 数据库查询命令 + 输出模板。不注入历史轮次数据 |
| 批量 pipeline | 全部指令执行完成后统一跑一次 pipeline_run，而非每条指令跑一次 |
| 汇总报告 | 最后一轮由一个 agent 汇总所有 JSON，输出 Markdown |

## 6. 报告输出模板

```markdown
# Trace 数据流水线测试报告

## 测试概要
- 执行时间: ...
- 指令总数: 9
- Hook 事件总数: N
- Trace 总数: M
- Step 总数: S

## CP1: History DB 采集完整性
| 指令 | 事件数 | tool_name 正确 | 有 output |
|------|--------|---------------|-----------|
| #1 | ... | ... | ... |

## CP2: Pipeline 处理正确性
| pipeline_run | events_processed | traces_created | 备注 |
|-------------|-----------------|----------------|------|
| 第1次 | ... | ... | ... |

## CP3: Trace 结构完整性
| run_id | step_count | succeeded | failed | context_type |
|--------|-----------|-----------|--------|-------------|
| ... | ... | ... | ... | ... |

## CP4: 触发对比矩阵
| 等级 | 指令 | skill触发 | 事件数 | trace完整度 | 评价 |
|------|------|----------|--------|-----------|------|
| L1 | #1 | ✓ | ... | 高 | 基准 |
| L1 | #4 | ... | ... | ... | ... |
| ... | ... | ... | ... | ... | ... |

## 问题发现
- [ ] ...
```

## 7. 待确认事项

- [ ] 3 个 skill 的 SKILL.md 是否需要现在创建？还是测试时由子 agent 用 `skill_create` 动态创建？
- [ ] 是否需要故意注入错误场景（如不存在的文件路径）测试 ErrorExtractor？
- [ ] 报告审核通过后，是否将测试流程固化为可重复执行的脚本？
