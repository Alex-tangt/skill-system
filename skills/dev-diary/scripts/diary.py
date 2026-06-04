#!/usr/bin/env python3
"""Dev Diary manager — reads and writes docs/DEVELOPMENT.md.

Operations: add, done, list, update

Output: JSON with {returncode, status, message, ...} to stdout.
Exits non-zero on error.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DiaryEntry:
    title: str
    description: str = ""
    issue_id: str | None = None
    completed: bool = False
    phase: str | None = None       # only for completed
    priority: str | None = None     # only for pending (high/medium/low)


@dataclass
class Diary:
    completed_phases: dict[str, list[DiaryEntry]] = field(default_factory=dict)
    pending_by_priority: dict[str, list[DiaryEntry]] = field(default_factory=dict)
    # Raw blocks before/after diary sections (preserved verbatim on write)
    prelude: list[str] = field(default_factory=list)   # lines before ## 已完成
    between: list[str] = field(default_factory=list)   # lines between ## 已完成 and ## 待解决
    postscript: list[str] = field(default_factory=list) # lines after ## 待解决's last child

    # Priority label ↔ key mapping
    PRIORITY_MAP: dict[str, str] = field(default_factory=lambda: {
        "high": "高优先级（阻断真实使用）",
        "medium": "中优先级（体验缺口）",
        "low": "低优先级（工程健壮性）",
    })

    PRIORITY_KEY_FROM_LABEL: dict[str, str] = field(default_factory=lambda: {
        "高优先级（阻断真实使用）": "high",
        "中优先级（体验缺口）": "medium",
        "低优先级（工程健壮性）": "low",
    })


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _priority_key(label: str) -> str | None:
    """Extract priority key from a heading label.

    Handles both full labels like '高优先级（阻断真实使用）' and
    shortened labels like '高优先级' (with or without description suffix).
    """
    # Normalize: strip parenthetical descriptions in both half/full-width forms
    cleaned = re.sub(r"[（(][^）)]*[）)]", "", label).strip()

    keyword_map = {
        "高优先级": "high",
        "中优先级": "medium",
        "低优先级": "low",
    }
    for keyword, key in keyword_map.items():
        if keyword in cleaned:
            return key
    return None


def parse(filepath: Path) -> Diary:
    """Parse DEVELOPMENT.md into a Diary structure."""
    diary = Diary()
    lines = filepath.read_text(encoding="utf-8").splitlines(keepends=False)

    # State machine
    SECTION_NONE = 0
    SECTION_COMPLETED = 1
    SECTION_PENDING = 2
    SECTION_POSTSCRIPT = 3

    section = SECTION_NONE
    current_phase: str | None = None
    current_priority: str | None = None

    prelude: list[str] = []
    between: list[str] = []
    postscript: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Detect major sections
        if stripped.startswith("## 已完成"):
            section = SECTION_COMPLETED
            current_phase = None
            current_priority = None
            continue

        if stripped.startswith("## 待解决"):
            section = SECTION_PENDING
            current_phase = None
            current_priority = None
            continue

        # Detect trailing sections (工具清单, 关键文件索引, --- dividers)
        if section in (SECTION_COMPLETED, SECTION_PENDING) and (
            stripped.startswith("## ") and not stripped.startswith("### ")
        ):
            section = SECTION_POSTSCRIPT

        if section == SECTION_POSTSCRIPT:
            postscript.append(line)
            continue

        # Phase heading under ## 已完成
        if section == SECTION_COMPLETED and stripped.startswith("### "):
            current_phase = stripped[4:].strip()
            current_priority = None
            if current_phase not in diary.completed_phases:
                diary.completed_phases[current_phase] = []
            continue

        # Priority heading under ## 待解决
        if section == SECTION_PENDING and stripped.startswith("### "):
            label = stripped[4:].strip()
            pk = _priority_key(label)
            if pk:
                current_priority = pk
                if current_priority not in diary.pending_by_priority:
                    diary.pending_by_priority[current_priority] = []
            else:
                current_priority = None
            continue

        # Done items: - [x] **Title** — description
        if section == SECTION_COMPLETED and stripped.startswith("- [x] "):
            entry = _parse_entry(stripped, completed=True)
            if entry and current_phase:
                entry.phase = current_phase
                diary.completed_phases.setdefault(current_phase, []).append(entry)
            continue

        # Pending items: - [ ] **Title (ID)** — description
        if section == SECTION_PENDING and stripped.startswith("- [ ] "):
            entry = _parse_entry(stripped, completed=False)
            if entry and current_priority:
                entry.priority = current_priority
                diary.pending_by_priority.setdefault(current_priority, []).append(entry)
            continue

        # Collect prelude / between / postscript lines
        if section == SECTION_NONE:
            prelude.append(line)
        elif section == SECTION_COMPLETED and stripped == "":
            between.append(line)
        elif section == SECTION_PENDING and stripped == "":
            # Lines after pending section (separators)
            pass  # handled below via section transitions
        # Skip empty lines and dividers in the wrong state

    # Determine "between" text: lines between the last phase heading in completed
    # and the ## 待解决 header. We approximate by collecting everything that
    # isn't in a known section.
    diary.prelude = prelude
    diary.between = between
    diary.postscript = postscript

    return diary


_ENTRY_PATTERN = re.compile(
    r"^- \[(x| )\] \*\*(.+?)\*\*(?:\s*\(([^)]+)\))?\s*[—–-]\s*(.*)$"
)


def _parse_entry(line: str, completed: bool) -> DiaryEntry | None:
    m = _ENTRY_PATTERN.match(line)
    if not m:
        return None
    return DiaryEntry(
        title=m.group(2).strip(),
        issue_id=m.group(3).strip() if m.group(3) else None,
        description=m.group(4).strip(),
        completed=completed,
    )


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------

def serialize(diary: Diary) -> str:
    """Serialize Diary back into the DEVELOPMENT.md format."""
    parts: list[str] = []

    # Prelude (title + intro text)
    for line in diary.prelude:
        parts.append(line)

    # Completed section
    parts.append("## 已完成")
    parts.append("")
    for phase, entries in diary.completed_phases.items():
        parts.append(f"### {phase}")
        parts.append("")
        for entry in entries:
            parts.append(_format_entry(entry))
        parts.append("")

    # Pending section
    parts.append("---")
    parts.append("")
    parts.append("## 待解决")
    parts.append("")

    priority_order = ["high", "medium", "low"]
    priority_labels = {
        "high": "高优先级（阻断真实使用）",
        "medium": "中优先级（体验缺口）",
        "low": "低优先级（工程健壮性）",
    }
    for pk in priority_order:
        if pk in diary.pending_by_priority:
            entries = diary.pending_by_priority[pk]
            parts.append(f"### {priority_labels[pk]}")
            parts.append("")
            if entries:
                for entry in entries:
                    parts.append(_format_entry(entry))
            else:
                parts.append("*(暂无)*")
            parts.append("")

    # Postscript (工具清单, 关键文件索引, etc.)
    for line in diary.postscript:
        parts.append(line)

    return "\n".join(parts).rstrip("\n") + "\n"


def _format_entry(entry: DiaryEntry) -> str:
    checkbox = "x" if entry.completed else " "
    id_part = f" ({entry.issue_id})" if entry.issue_id else ""
    return f"- [{checkbox}] **{entry.title}**{id_part} — {entry.description}"


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def _auto_id(diary: Diary) -> str:
    """Generate next issue ID by scanning existing entries (titles and issue_ids)."""
    max_n = 0
    pattern = re.compile(r"#P-(\d+)")

    def scan(entry: DiaryEntry) -> None:
        nonlocal max_n
        for text in (entry.issue_id, entry.title):
            if text:
                m = pattern.search(text)
                if m:
                    max_n = max(max_n, int(m.group(1)))

    for entries in diary.pending_by_priority.values():
        for e in entries:
            scan(e)
    for entries in diary.completed_phases.values():
        for e in entries:
            scan(e)
    return f"#P-{max_n + 1}"


def op_add(diary: Diary, title: str, priority: str, description: str, issue_id: str | None) -> dict:
    """Add a new pending entry."""
    if priority not in ("high", "medium", "low"):
        return {"returncode": 1, "status": "error",
                "message": f"无效的优先级 '{priority}'，有效值: high, medium, low"}

    if issue_id is None:
        issue_id = _auto_id(diary)

    # Check for duplicate issue_id
    all_entries = []
    for entries in diary.pending_by_priority.values():
        all_entries.extend(entries)
    for entries in diary.completed_phases.values():
        all_entries.extend(entries)
    for e in all_entries:
        if e.issue_id and e.issue_id == issue_id:
            return {"returncode": 1, "status": "error",
                    "message": f"ID '{issue_id}' 已存在（标题: {e.title}）"}

    if not description:
        description = "*(待补充描述)*"

    entry = DiaryEntry(
        title=title,
        description=description,
        issue_id=issue_id,
        completed=False,
        priority=priority,
    )

    diary.pending_by_priority.setdefault(priority, []).append(entry)

    return {
        "returncode": 0,
        "status": "ok",
        "message": f"已添加待办: **{title}** ({issue_id}) [{priority}]",
        "entry": {"title": title, "issue_id": issue_id, "priority": priority, "description": description},
    }


def op_done(diary: Diary, title: str, solution: str, phase: str | None) -> dict:
    """Mark a pending entry as done, moving it to completed."""
    # Find by title (case-insensitive substring match)
    candidates: list[tuple[str, DiaryEntry]] = []
    for pk, entries in diary.pending_by_priority.items():
        for e in entries:
            if title.lower() in e.title.lower():
                candidates.append((pk, e))

    if not candidates:
        return {"returncode": 1, "status": "error",
                "message": f"未找到标题匹配 '{title}' 的待解决条目"}

    if len(candidates) > 1:
        matches = [
            f"  - [{pk}] **{e.title}** ({e.issue_id or 'no-id'}) — {e.description[:50]}"
            for pk, e in candidates
        ]
        return {
            "returncode": 1,
            "status": "ambiguous",
            "message": f"找到 {len(candidates)} 个匹配条目，请使用更精确的标题:\n" + "\n".join(matches),
            "candidates": [{"title": e.title, "issue_id": e.issue_id, "priority": pk}
                           for pk, e in candidates],
        }

    priority_key, entry = candidates[0]

    # Remove from pending
    diary.pending_by_priority[priority_key].remove(entry)

    # Default phase
    if phase is None:
        phase = f"常规迭代（{date.today().isoformat()}）"

    if not solution:
        solution = "*(无详细方案记录)*"

    # Move to completed
    entry.completed = True
    entry.description = solution
    entry.phase = phase
    entry.priority = None
    diary.completed_phases.setdefault(phase, []).append(entry)

    return {
        "returncode": 0,
        "status": "ok",
        "message": f"已完成: **{entry.title}** ({entry.issue_id or 'no-id'}) → {phase}",
        "entry": {"title": entry.title, "issue_id": entry.issue_id, "phase": phase, "solution": solution},
    }


def op_list(diary: Diary, filter_mode: str) -> dict:
    """List diary contents as JSON summary."""
    result: dict = {"returncode": 0, "status": "ok"}

    if filter_mode in ("all", "completed"):
        completed_list = []
        for phase, entries in diary.completed_phases.items():
            items = [{"title": e.title, "issue_id": e.issue_id, "description": e.description}
                     for e in entries]
            completed_list.append({"phase": phase, "count": len(items), "items": items})
        result["completed"] = {
            "count": sum(p["count"] for p in completed_list),
            "phases": completed_list,
        }

    if filter_mode in ("all", "pending"):
        pending_list = []
        priority_labels = {
            "high": "高优先级（阻断真实使用）",
            "medium": "中优先级（体验缺口）",
            "low": "低优先级（工程健壮性）",
        }
        for pk in ["high", "medium", "low"]:
            entries = diary.pending_by_priority.get(pk, [])
            items = [{"title": e.title, "issue_id": e.issue_id, "description": e.description}
                     for e in entries]
            pending_list.append({
                "priority": pk,
                "label": priority_labels[pk],
                "count": len(items),
                "items": items,
            })
        result["pending"] = {
            "count": sum(p["count"] for p in pending_list),
            "priorities": pending_list,
        }

    return result


def op_update(diary: Diary, title: str, new_title: str | None,
              priority: str | None, description: str | None, new_id: str | None) -> dict:
    """Update fields of a pending entry."""
    # Find by title
    candidates: list[tuple[str, int, DiaryEntry]] = []
    for pk, entries in diary.pending_by_priority.items():
        for idx, e in enumerate(entries):
            if title.lower() in e.title.lower():
                candidates.append((pk, idx, e))

    if not candidates:
        return {"returncode": 1, "status": "error",
                "message": f"未找到标题匹配 '{title}' 的待解决条目"}

    if len(candidates) > 1:
        matches = [
            f"  - [{pk}] **{e.title}** ({e.issue_id or 'no-id'})"
            for pk, _idx, e in candidates
        ]
        return {
            "returncode": 1,
            "status": "ambiguous",
            "message": f"找到 {len(candidates)} 个匹配条目，请使用更精确的标题:\n" + "\n".join(matches),
        }

    old_pk, idx, entry = candidates[0]

    # Apply changes
    changes = []
    if new_title:
        changes.append(f"标题: '{entry.title}' → '{new_title}'")
        entry.title = new_title
    if description is not None:
        changes.append(f"描述已更新")
        entry.description = description
    if new_id:
        changes.append(f"ID: '{entry.issue_id}' → '{new_id}'")
        entry.issue_id = new_id

    # Priority change requires moving between subsections
    if priority and priority != old_pk:
        changes.append(f"优先级: {old_pk} → {priority}")
        diary.pending_by_priority[old_pk].pop(idx)
        diary.pending_by_priority.setdefault(priority, []).append(entry)
        entry.priority = priority
    elif priority and priority == old_pk:
        pass  # no-op

    if not changes:
        return {"returncode": 0, "status": "ok", "message": "无变更"}

    return {
        "returncode": 0,
        "status": "ok",
        "message": f"已更新 **{entry.title}**: " + "; ".join(changes),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Dev Diary Manager")
    parser.add_argument("--file", required=True, help="Path to DEVELOPMENT.md")
    parser.add_argument("--operation", required=True,
                        choices=["add", "done", "list", "update"])
    parser.add_argument("--title", default="")
    parser.add_argument("--priority", default="medium")
    parser.add_argument("--description", default="")
    parser.add_argument("--issue-id", default=None)
    parser.add_argument("--phase", default=None)
    parser.add_argument("--new-title", default=None)
    parser.add_argument("--new-priority", default=None)
    parser.add_argument("--filter", default="all")
    parser.add_argument("--solution", default=None)

    args = parser.parse_args()

    # Sanitize: the Skill Engine resolver substitutes missing optional fields as "None" (string).
    # Convert these to empty/default values.
    def _clean(v: str | None) -> str | None:
        if v is None or v == "None" or v.strip() == "":
            return None
        return v

    args.title = _clean(args.title) or ""
    args.description = _clean(args.description) or ""
    args.issue_id = _clean(args.issue_id)
    args.phase = _clean(args.phase)
    args.new_title = _clean(args.new_title)
    args.solution = _clean(args.solution)
    args.new_priority = _clean(args.new_priority)
    # priority / new_priority / filter: validate and sanitize
    VALID_PRIORITIES = {"high", "medium", "low"}
    VALID_FILTERS = {"all", "pending", "completed"}

    if args.priority in (None, "None", ""):
        args.priority = "medium"
    elif args.priority not in VALID_PRIORITIES:
        print(json.dumps({"returncode": 1, "status": "error",
                          "message": f"无效的优先级 '{args.priority}'，有效值: high, medium, low"},
                         ensure_ascii=False))
        sys.exit(1)

    if args.new_priority in (None, "None", ""):
        args.new_priority = None
    elif args.new_priority not in VALID_PRIORITIES:
        print(json.dumps({"returncode": 1, "status": "error",
                          "message": f"无效的优先级 '{args.new_priority}'，有效值: high, medium, low"},
                         ensure_ascii=False))
        sys.exit(1)

    if args.filter in (None, "None", ""):
        args.filter = "all"
    elif args.filter not in VALID_FILTERS:
        print(json.dumps({"returncode": 1, "status": "error",
                          "message": f"无效的 filter '{args.filter}'，有效值: all, pending, completed"},
                         ensure_ascii=False))
        sys.exit(1)

    filepath = Path(args.file)

    if not filepath.exists():
        result = {"returncode": 1, "status": "error", "message": f"文件不存在: {args.file}"}
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    # Parse file
    diary = parse(filepath)

    # Execute operation
    op = args.operation
    if op == "add":
        if not args.title:
            result = {"returncode": 1, "status": "error", "message": "add 操作需要 --title"}
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(1)
        result = op_add(diary, args.title, args.priority, args.description, args.issue_id)
    elif op == "done":
        if not args.title:
            result = {"returncode": 1, "status": "error", "message": "done 操作需要 --title"}
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(1)
        solution = args.solution or args.description
        result = op_done(diary, args.title, solution, args.phase)
    elif op == "list":
        result = op_list(diary, args.filter)
    elif op == "update":
        if not args.title:
            result = {"returncode": 1, "status": "error", "message": "update 操作需要 --title"}
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(1)
        result = op_update(diary, args.title, args.new_title,
                           args.new_priority, args.description, args.issue_id)
    else:
        result = {"returncode": 1, "status": "error", "message": f"未知操作: {op}"}

    # Write back on success for mutating operations
    if result["returncode"] == 0 and op in ("add", "done", "update"):
        # Backup
        backup = filepath.with_suffix(filepath.suffix + ".backup")
        shutil.copy2(filepath, backup)
        # Write
        filepath.write_text(serialize(diary), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(result["returncode"])


if __name__ == "__main__":
    main()
