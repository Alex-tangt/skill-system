---
name: dev-diary
description: Manage the project development diary (docs/DEVELOPMENT.md). Add TODO items, mark tasks as done with solutions, list status, and update entries. Use /dev-diary slash commands to interact with the development tracker.
license: MIT
metadata:
  author: skill-engine
  version: "1.0"
---

# Dev Diary Skill

Manage `docs/DEVELOPMENT.md` — the project's development tracker. Use this skill whenever the user asks to record a task, mark work as complete, review project status, or update an existing entry.

## File Format

The diary file (`docs/DEVELOPMENT.md`) has this structure:

```
# Development Tracker

## 已完成
### <Phase Name> (<date>)
- [x] **<Title>** — <description>

## 待解决
### 高优先级（阻断真实使用）
- [ ] **<Title> (<issue-id>)** — <description>
### 中优先级（体验缺口）
- [ ] **<Title> (<issue-id>)** — <description>
### 低优先级（工程健壮性）
- [ ] **<Title> (<issue-id>)** — <description>

---

## 工具清单
...

## 关键文件索引
...
```

IMPORTANT: Preserve the `---` divider, `## 工具清单`, and `## 关键文件索引` sections exactly as-is. Never modify these trailing sections.

## Commands

### `/dev-diary add "<title>" [--priority high|medium|low] [--desc "<description>"] [--id "<issue-id>"]`

Add a new TODO item to the `## 待解决` section.

**Algorithm:**
1. Read `docs/DEVELOPMENT.md`
2. Locate the `## 待解决` section
3. Map `--priority` to the correct subsection:
   - `high` → `### 高优先级（阻断真实使用）`
   - `medium` → `### 中优先级（体验缺口）` (default)
   - `low` → `### 低优先级（工程健壮性）`
4. If `--id` is not provided, generate one by scanning all existing `#P-N` patterns in the file and using `#P-{max+1}`
5. Append: `- [ ] **<title> (<issue-id>)** — <description>` to the end of the subsection's list
6. If `--desc` is empty, use `*(待补充描述)*`
7. Write the file back (create `.backup` first via `shutil.copy2`)
8. Report: what was added, at what priority, with what ID

### `/dev-diary done "<title>" [--solution "<description>"] [--phase "<phase name>"]`

Mark a pending item as completed, moving it from `## 待解决` to `## 已完成`.

**Algorithm:**
1. Read `docs/DEVELOPMENT.md`
2. Search for a line matching `- [ ] **<title>...**` in `## 待解决` (case-insensitive substring match)
3. If multiple matches, list them all and ask the user to disambiguate with a more specific title
4. If not found, report: "未找到标题匹配 '<title>' 的待解决条目"
5. Remove the line from its priority subsection
6. If `--phase` is provided, use it; otherwise use `常规迭代（YYYY-MM-DD）` with today's date
7. Locate or create the phase subsection under `## 已完成`
8. Append: `- [x] **<title>** — <solution>`
9. If `--solution` is not provided, use `*(无详细方案记录)*`
10. Write back and report: what was moved, to which phase

### `/dev-diary list [--filter all|pending|completed]`

Display a structured summary of the development diary.

**Algorithm:**
1. Read and parse `docs/DEVELOPMENT.md`
2. Display counts and groupings:
   - Completed: grouped by phase, with item count per phase
   - Pending: grouped by priority (high → medium → low), with item count per priority
3. Format as a clean markdown summary with bullet points
4. `--filter pending` shows only `## 待解决`
5. `--filter completed` shows only `## 已完成`
6. Default `--filter all` shows both

### `/dev-diary update "<title>" [--new-title "..."] [--priority high|medium|low] [--desc "<new desc>"] [--id "<new-id>"]`

Modify an existing pending entry.

**Algorithm:**
1. Read `docs/DEVELOPMENT.md`
2. Search for the title in `## 待解决` (same matching as `done`)
3. If not found or ambiguous, report accordingly
4. Apply specified changes:
   - `--new-title`: replace the title text
   - `--priority`: move the item to the target priority subsection
   - `--desc`: replace the description after `—`
   - `--id`: replace the issue-id in parentheses
5. Write back and report all changes made

## Error Handling

| Scenario | Response |
|---|---|
| Title not found in pending | "未找到标题匹配 '<title>' 的待解决条目" |
| Multiple title matches | List all candidates with priority and full title, ask for disambiguation |
| `## 待解决` section missing | "文件中未找到 ## 待解决 段落" |
| `## 已完成` section missing | Create it before `## 待解决` |
| Priority subsection missing | Create it with the standard label format |
| File missing entirely | "docs/DEVELOPMENT.md 不存在" |

## Important Rules

- ALWAYS create a `.backup` of `docs/DEVELOPMENT.md` before writing
- ALWAYS preserve the `---` divider and trailing sections (工具清单, 关键文件索引)
- Use `—` (em dash) as the separator between title and description, not `--`
- Pending items use `- [ ]`, completed items use `- [x]`
- Issue IDs in titles use full-width parentheses: `（#P-1）` — but accept half-width `(#P-1)` as well
- When a priority subsection becomes empty after `done`, keep the heading with `*(暂无)*` placeholder
- Report results in Chinese (matching the project language)
