"""Segmenter — splits a transcript into user-message-delimited Segments.

Each segment represents one user task: the user's message + the complete
execution context (assistant thoughts, tool calls, tool results) until
the next user message.

Segments are linked into a bidirectional chain via prev_id / next_id.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from skill_engine.pipeline.transcript_reader import TranscriptEntry, TranscriptReader
from skill_engine.pipeline.models import Segment, SegmentStats

# --- Truncation budget constants -----------------------------------------------

DEFAULT_BUDGET = 80_000  # characters

# Per-content-type truncation limits (borrowed from OpenSpace)
TOOL_ERROR_MAX_CHARS = 1000
TOOL_SUCCESS_MAX_CHARS = 800
TOOL_ARGS_MAX_CHARS = 500
ASSISTANT_TEXT_MAX_CHARS = 5000

# Skill path patterns (for detecting skill references)
SKILL_PATH_RE = re.compile(r"skills?/([\w-]+)/", re.IGNORECASE)
SKILL_MD_RE = re.compile(r"skills?/([\w-]+)/SKILL\.md", re.IGNORECASE)


class Segmenter:
    """Split a transcript into task-level Segments with priority-based truncation.

    Args:
        reader: TranscriptReader for the transcript.
        budget: Max total characters for the execution_json in each segment.
    """

    def __init__(self, reader: TranscriptReader, budget: int = DEFAULT_BUDGET) -> None:
        self._reader = reader
        self._budget = budget

    # --- Main segmentation entry point ---------------------------------------

    def segment(self) -> list[Segment]:
        """Segment the transcript and return a linked list of Segments.

        Returns an empty list if no user messages are found.
        """
        # Collect all entries into memory for index-based access
        entries = self._reader.read_all()
        if not entries:
            return []

        # Find boundaries: indices of user messages (not tool results)
        boundaries = [i for i, e in enumerate(entries) if e.is_user_message]
        if not boundaries:
            return []

        segments: list[Segment] = []
        session_id = entries[0].session_id or "unknown"
        transcript_path = str(self._reader.path)

        for bi, boundary_idx in enumerate(boundaries):
            user_entry = entries[boundary_idx]
            # Execution range: from boundary+1 to next boundary (or end)
            start_exec = boundary_idx + 1
            if bi + 1 < len(boundaries):
                end_exec = boundaries[bi + 1]
            else:
                end_exec = len(entries)

            execution_entries = entries[start_exec:end_exec]

            # Collect context from attachment entries within this segment's range
            skills_available = self._collect_skills_available(
                entries, boundary_idx, end_exec
            )
            files_modified = self._collect_files(entries, boundary_idx, end_exec)

            # Compute stats
            stats = self._compute_stats(execution_entries, skills_available)
            skills_referenced_str = json.dumps(stats.skills_referenced)

            # Truncate and serialize execution
            truncated = self._truncate(execution_entries)
            execution_json = json.dumps(
                [self._entry_to_dict(e) for e in truncated], ensure_ascii=False
            )

            segment = Segment(
                id=str(uuid.uuid4()),
                session_id=session_id,
                user_msg=user_entry.content_text,
                user_msg_index=bi,
                execution_json=execution_json,
                stats_json=stats.to_json(),
                skills_available=json.dumps(skills_available),
                files_modified=json.dumps(files_modified),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            segments.append(segment)

        # Build bidirectional chain
        for i, seg in enumerate(segments):
            if i > 0:
                seg.prev_id = segments[i - 1].id
            if i < len(segments) - 1:
                seg.next_id = segments[i + 1].id

        return segments

    # --- Skill detection -----------------------------------------------------

    @staticmethod
    def _collect_skills_available(
        entries: list[TranscriptEntry], start: int, end: int
    ) -> list[str]:
        """Extract skill names from attachment entries in the range."""
        skills: list[str] = []
        seen: set[str] = set()
        for i in range(start, end):
            entry = entries[i]
            if entry.type == "attachment" and entry.attachment_type == "skill_listing":
                # Skill listing is plain text; extract skill names
                # Format: "- skill-name: description"
                content = entry.raw.get("attachment", {}).get("content", "")
                for match in re.finditer(r"-\s+([\w-]+):", content):
                    name = match.group(1)
                    if name not in seen:
                        skills.append(name)
                        seen.add(name)
        return skills

    @staticmethod
    def _collect_files(
        entries: list[TranscriptEntry], start: int, end: int
    ) -> list[str]:
        """Extract modified file paths from file-history-snapshot entries."""
        files: list[str] = []
        seen: set[str] = set()
        for i in range(start, end):
            entry = entries[i]
            if entry.type == "file-history-snapshot":
                snapshot = entry.raw.get("snapshot", {})
                # Snapshot is a dict of {path: state}
                for path in snapshot:
                    if isinstance(path, str) and path not in seen:
                        files.append(path)
                        seen.add(path)
        return files

    # --- Mechanical statistics -----------------------------------------------

    @staticmethod
    def _compute_stats(
        execution_entries: list[TranscriptEntry],
        skills_available: list[str],
    ) -> SegmentStats:
        """Compute SegmentStats without any LLM involvement."""
        tool_count = 0
        tool_types: dict[str, int] = {}
        iteration_count = 0
        skills_referenced: list[str] = []
        seen_skills: set[str] = set()
        total_chars = 0
        started_at: Optional[float] = None
        finished_at: Optional[float] = None
        has_error = False
        is_incomplete = False

        for entry in execution_entries:
            # Track timing from timestamps
            ts = entry.timestamp
            if ts:
                # Approximate: first and last timestamp
                if started_at is None:
                    started_at = _parse_timestamp(ts)

            # Count tool calls
            if entry.is_assistant:
                tcs = entry.tool_calls
                if tcs:
                    # Categorize tool types by name pattern
                    for tc in tcs:
                        name = tc.get("name", "unknown")
                        tool_count += 1
                        backend = _infer_backend(name)
                        tool_types[backend] = tool_types.get(backend, 0) + 1

                        # Check for skill references in tool calls
                        _check_skill_ref(name, skills_available, seen_skills,
                                        skills_referenced)

                        args = tc.get("input", {})
                        if isinstance(args, dict):
                            args_str = json.dumps(args)
                            _check_skill_ref(args_str, skills_available,
                                             seen_skills, skills_referenced)

            # Count iterations (one assistant message with new thinking = one iteration)
            if entry.is_assistant and entry.content_text:
                iteration_count += 1

            # Check for errors
            if entry.is_tool_result:
                result_text = entry.content_text
                if _is_error_text(result_text):
                    has_error = True

            # Track text length
            text = entry.content_text
            if text:
                total_chars += len(text)

        # Determine status
        if has_error:
            status = "error"
        elif is_incomplete:
            status = "incomplete"
        else:
            status = "success"

        return SegmentStats(
            tool_count=tool_count,
            tool_types=tool_types,
            iteration_count=iteration_count,
            status=status,
            skills_referenced=skills_referenced,
            total_chars=total_chars,
            started_at=started_at,
            finished_at=finished_at,
        )

    # --- Priority-based truncation -------------------------------------------

    def _truncate(self, entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
        """Apply priority-based budget truncation.

        Priorities (lower = more important, borrowed from OpenSpace):
          0 — User instruction (CRITICAL) — but we already stripped this out,
              it's stored as Segment.user_msg separately.
          1 — Final assistant response (CRITICAL)
          2 — Tool calls + tool errors (HIGH, kept together)
          3 — Non-final assistant reasoning (HIGH)
          4 — Tool success results (MEDIUM)
          5 — System messages (LOW)

        Strategy:
          1. Include all priority ≤ 3 entries in full.
          2. Add priority 4-5 entries until budget is exhausted.
          3. If priority ≤ 3 already exceeds budget, keep 1-2 in full,
             truncate 3, drop 4-5.
        """
        if not entries:
            return entries

        # Assign priorities
        total_entries = len([e for e in entries if e.is_assistant and e.content_text])
        last_assistant_idx: Optional[int] = None
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].is_assistant:
                last_assistant_idx = i
                break

        prioritized: list[Tuple[int, TranscriptEntry]] = []
        for i, entry in enumerate(entries):
            priority = self._assign_priority(
                entry, i, last_assistant_idx, total_entries
            )
            prioritized.append((priority, entry))

        # Calculate essential chars (priority ≤ 3)
        essential_chars = sum(
            len(e.content_text) for p, e in prioritized if p <= 3
        )

        if essential_chars <= self._budget:
            # Room for lower-priority entries
            used = essential_chars
            result: list[TranscriptEntry] = []
            skipped = 0
            for p, e in prioritized:
                if p <= 3:
                    result.append(e)
                elif used + len(e.content_text) + 1 <= self._budget:
                    result.append(e)
                    used += len(e.content_text) + 1
                else:
                    skipped += 1
            return result

        # Essential content exceeds budget — progressive truncation
        return self._truncate_essential(prioritized)

    def _truncate_essential(
        self, prioritized: list[Tuple[int, TranscriptEntry]]
    ) -> list[TranscriptEntry]:
        """Emergency truncation when essential content exceeds budget."""
        result: list[TranscriptEntry] = []
        used = 0

        # Pass 1: priority 1 (final assistant) — keep full
        for p, e in prioritized:
            if p == 1:
                result.append(e)
                used += len(e.content_text) + 1

        remaining = self._budget - used

        # Pass 2: priority 2 (tool calls + errors) — budget-allocated
        p2_entries = [(p, e) for p, e in prioritized if p == 2]
        if p2_entries:
            per_entry_budget = max(400, remaining // (len(p2_entries) + 1))
            for p, e in p2_entries:
                text = e.content_text
                if len(text) > per_entry_budget:
                    # Rebuild entry with truncated content text
                    e = self._truncate_entry_text(e, per_entry_budget)
                if used + len(e.content_text) + 1 <= self._budget:
                    result.append(e)
                    used += len(e.content_text) + 1

        # Pass 3: priority 3 (non-final assistant) — first-line only
        p3_entries = [(p, e) for p, e in prioritized if p == 3]
        for p, e in p3_entries:
            first_line = e.content_text.split("\n", 1)[0][:200]
            if used + len(first_line) + 1 > self._budget:
                break
            truncated = self._truncate_entry_text(e, 200)
            result.append(truncated)
            used += len(truncated.content_text) + 1

        return result

    @staticmethod
    def _truncate_entry_text(entry: TranscriptEntry, max_len: int) -> TranscriptEntry:
        """Return a copy of *entry* with content text truncated to *max_len*."""
        raw_copy = dict(entry.raw)
        msg_copy = dict(raw_copy.get("message", {}))
        content = entry.content_text
        if len(content) > max_len:
            msg_copy["content"] = content[:max_len] + "... [truncated]"
            raw_copy["message"] = msg_copy
        return TranscriptEntry(type=entry.type, raw=raw_copy)

    @staticmethod
    def _assign_priority(
        entry: TranscriptEntry,
        index: int,
        last_assistant_idx: Optional[int],
        total_assistant_messages: int,
    ) -> int:
        """Assign a priority (0-5) to a transcript entry."""
        if entry.is_assistant:
            if index == last_assistant_idx:
                return 1  # Final assistant — CRITICAL
            # Tool calls: flagged as part of priority 2
            if entry.tool_calls:
                return 2
            return 3  # Non-final assistant — HIGH

        if entry.is_tool_result:
            if _is_error_text(entry.content_text):
                return 2  # Tool error — HIGH
            # Check for embedded summary
            if _has_embedded_summary(entry.content_text):
                return 3  # Summary-bearing result — HIGH
            return 4  # Normal success result — MEDIUM

        # System messages, attachments, etc.
        return 5  # LOW

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _entry_to_dict(entry: TranscriptEntry) -> dict[str, Any]:
        """Serialize a TranscriptEntry for JSON storage."""
        return {
            "type": entry.type,
            "role": entry.role,
            "content": entry.content_text,
            "tool_calls": [
                {"name": tc.get("name", ""), "input": tc.get("input", {})}
                for tc in entry.tool_calls
            ],
            "timestamp": entry.timestamp,
            "is_user_message": entry.is_user_message,
            "is_tool_result": entry.is_tool_result,
            "is_assistant": entry.is_assistant,
        }


# --- Internal helpers ---------------------------------------------------------

def _infer_backend(tool_name: str) -> str:
    """Infer the backend type from a tool name."""
    shell_tools = {"shell_agent", "read_file", "write_file", "list_dir", "run_shell",
                   "Bash", "Read", "Write", "Edit", "NotebookEdit"}
    if tool_name in shell_tools:
        return "shell"
    if tool_name.startswith("mcp__"):
        # mcp__plugin__server__tool → extract server name
        parts = tool_name.split("__")
        if len(parts) >= 3:
            return f"mcp:{parts[2]}"
        return "mcp"
    if "gui" in tool_name.lower():
        return "gui"
    if any(kw in tool_name.lower() for kw in ("web", "browser", "fetch", "search")):
        return "web"
    return "other"


def _check_skill_ref(
    text: str,
    available: list[str],
    seen: set[str],
    result: list[str],
) -> None:
    """Check if *text* references any known skill and track it."""
    for match in SKILL_PATH_RE.finditer(text):
        name = match.group(1)
        if name not in seen:
            result.append(name)
            seen.add(name)
    # Also check exact name matches
    for skill_name in available:
        if skill_name in text and skill_name not in seen:
            result.append(skill_name)
            seen.add(skill_name)


def _is_error_text(text: str) -> bool:
    """Detect if a text (tool result) represents an error."""
    if not text:
        return False
    head = text[:200].lower()
    return (
        text.startswith("[ERROR]")
        or text.startswith("ERROR")
        or "error" in head[:50]
        or "task failed" in head
        or "connection refused" in head
        or "timed out" in head
        or "traceback" in head
    )


def _has_embedded_summary(text: str) -> bool:
    """Check if a tool result contains a self-generated execution summary."""
    return bool(re.search(r"Execution Summary \(\d+ steps?\):", text))


def _parse_timestamp(ts: str) -> Optional[float]:
    """Parse an ISO timestamp string into a Unix timestamp."""
    try:
        from datetime import datetime as dt
        return dt.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None
