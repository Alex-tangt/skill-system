"""Claude Code transcript JSONL reader.

Reads the built-in transcript file that Claude Code writes automatically
to ``~/.claude/projects/<sanitized_cwd>/<session_id>.jsonl``.

Zero dependencies beyond Python stdlib.  Inertly parses JSONL lines
and classifies each entry by type.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


@dataclass
class TranscriptEntry:
    """One line from a Claude Code transcript JSONL file."""

    type: str  # "user" | "assistant" | "attachment" | "file-history-snapshot" | ...
    raw: Dict[str, Any] = field(default_factory=dict)

    # --- Convenience accessors -------------------------------------------------
    @property
    def role(self) -> str:
        """The message role for user/assistant entries."""
        msg = self.raw.get("message", {})
        return msg.get("role", "")

    @property
    def content(self) -> Any:
        """Message content (string or list of content blocks)."""
        msg = self.raw.get("message", {})
        return msg.get("content", "")

    @property
    def content_text(self) -> str:
        """Extract text from content, handling both str and list-of-blocks."""
        c = self.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = []
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(parts)
        return str(c) if c else ""

    @property
    def tool_calls(self) -> list[dict]:
        """Tool use blocks from an assistant message."""
        msg = self.raw.get("message", {})
        if isinstance(msg.get("content"), list):
            return [b for b in msg["content"] if b.get("type") == "tool_use"]
        return []

    @property
    def is_user_message(self) -> bool:
        """True if this is a user-submitted message (not a tool result)."""
        return self.type == "user" and "toolUseResult" not in self.raw

    @property
    def is_tool_result(self) -> bool:
        """True if this is a tool result returned to the assistant."""
        return self.type == "user" and "toolUseResult" in self.raw

    @property
    def is_assistant(self) -> bool:
        return self.type == "assistant"

    @property
    def parent_uuid(self) -> Optional[str]:
        """The uuid of the parent message this entry responds to."""
        return self.raw.get("parentUuid")

    @property
    def uuid(self) -> Optional[str]:
        return self.raw.get("uuid")

    @property
    def timestamp(self) -> Optional[str]:
        return self.raw.get("timestamp")

    @property
    def session_id(self) -> Optional[str]:
        return self.raw.get("sessionId")

    @property
    def attachment_type(self) -> Optional[str]:
        """If this is an attachment, what kind."""
        if self.type != "attachment":
            return None
        att = self.raw.get("attachment", {})
        return att.get("type", "")


class TranscriptReader:
    """Lazy reader for Claude Code transcript JSONL files.

    Usage::

        reader = TranscriptReader("/path/to/session.jsonl")
        for entry in reader.entries():
            if entry.is_user_message:
                print(entry.content_text)
    """

    def __init__(self, transcript_path: str) -> None:
        self._path = Path(transcript_path)
        if not self._path.exists():
            raise FileNotFoundError(f"Transcript not found: {transcript_path}")

    @property
    def path(self) -> Path:
        return self._path

    # --- Static path resolution -----------------------------------------------

    @staticmethod
    def resolve_path(session_id: str, cwd: str | None = None) -> Path:
        """Derive the transcript path from a session ID and working directory.

        Claude Code stores transcripts at::

            ~/.claude/projects/<sanitized_cwd>/<session_id>.jsonl

        where *sanitized_cwd* replaces ``/`` with ``-``.
        """
        if cwd is None:
            cwd = os.getenv("PWD", os.getcwd())
        sanitized = cwd.replace("/", "-")
        base = Path.home() / ".claude" / "projects" / sanitized
        return base / f"{session_id}.jsonl"

    @staticmethod
    def resolve_from_env() -> Path:
        """Resolve the current session transcript from environment variables.

        Uses ``CLAUDE_CODE_SESSION_ID`` and ``PWD``.
        """
        session_id = os.getenv("CLAUDE_CODE_SESSION_ID", "")
        if not session_id:
            raise RuntimeError("CLAUDE_CODE_SESSION_ID not set")
        return TranscriptReader.resolve_path(session_id)

    # --- Iteration ------------------------------------------------------------

    def entries(self) -> Iterator[TranscriptEntry]:
        """Yield all transcript entries in chronological order."""
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield TranscriptEntry(type=raw.get("type", "unknown"), raw=raw)

    def read_all(self) -> list[TranscriptEntry]:
        """Read all entries into memory (for small-to-medium transcripts)."""
        return list(self.entries())

    def user_messages(self) -> Iterator[TranscriptEntry]:
        """Yield only user-submitted messages (not tool results)."""
        for entry in self.entries():
            if entry.is_user_message:
                yield entry

    def range(self, start: int, end: int) -> list[TranscriptEntry]:
        """Read entries in the index range [start, end).

        Note: this scans from the beginning each time.  For repeated
        range access, call :meth:`read_all` and index into the result.
        """
        result: list[TranscriptEntry] = []
        for i, entry in enumerate(self.entries()):
            if i >= end:
                break
            if i >= start:
                result.append(entry)
        return result

    def count_user_messages(self) -> int:
        """Return the total number of user messages in the transcript."""
        return sum(1 for _ in self.user_messages())

    # --- Statistics -----------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Return a quick type-distribution summary."""
        counts: dict[str, int] = {}
        for entry in self.entries():
            counts[entry.type] = counts.get(entry.type, 0) + 1
        return counts
