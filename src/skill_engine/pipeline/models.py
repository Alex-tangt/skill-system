from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SegmentStats:
    """Mechanical statistics computed during segmentation (no LLM involved)."""

    tool_count: int = 0
    tool_types: dict[str, int] = field(default_factory=dict)
    iteration_count: int = 0
    status: str = "unknown"
    skills_referenced: list[str] = field(default_factory=list)
    total_chars: int = 0
    started_at: float | None = None
    finished_at: float | None = None

    def to_json(self) -> str:
        return json.dumps({
            "tool_count": self.tool_count, "tool_types": self.tool_types,
            "iteration_count": self.iteration_count, "status": self.status,
            "skills_referenced": self.skills_referenced, "total_chars": self.total_chars,
            "started_at": self.started_at, "finished_at": self.finished_at,
        })

    @classmethod
    def from_json(cls, data: str) -> SegmentStats:
        d = json.loads(data) if isinstance(data, str) else data
        return cls(
            tool_count=d.get("tool_count", 0), tool_types=d.get("tool_types", {}),
            iteration_count=d.get("iteration_count", 0), status=d.get("status", "unknown"),
            skills_referenced=d.get("skills_referenced", []), total_chars=d.get("total_chars", 0),
            started_at=d.get("started_at"), finished_at=d.get("finished_at"),
        )


@dataclass
class Segment:
    """One user message and its complete execution context."""

    id: str
    session_id: str
    user_msg: str
    user_msg_index: int
    execution_json: str = "[]"
    prev_id: str | None = None
    next_id: str | None = None
    stats_json: str = "{}"
    skills_available: str = "[]"
    files_modified: str = "[]"
    created_at: str = ""

    @property
    def has_next(self) -> bool:
        return self.next_id is not None

    @property
    def has_prev(self) -> bool:
        return self.prev_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "session_id": self.session_id,
            "user_msg": self.user_msg, "user_msg_index": self.user_msg_index,
            "execution_json": self.execution_json,
            "prev_id": self.prev_id, "next_id": self.next_id,
            "stats_json": self.stats_json, "skills_available": self.skills_available,
            "files_modified": self.files_modified, "created_at": self.created_at,
        }


# --- Analysis output (Phase A: natural language diagnosis, not JSON) ------


@dataclass
class ExecutionAnalysis:
    """Phase A output: natural language diagnosis.

    No intermediate JSON — just a diagnosis that feeds directly into
    Phase B's prompt as context.
    """

    segment_id: str
    diagnosis: str = ""           # Natural language analysis of execution
    error_summary: list[str] = field(default_factory=list)  # Mechanical extraction
    analyzed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "diagnosis": self.diagnosis,
            "error_summary": self.error_summary,
            "analyzed_at": self.analyzed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionAnalysis:
        return cls(
            segment_id=d.get("segment_id", ""),
            diagnosis=d.get("diagnosis", ""),
            error_summary=d.get("error_summary", []),
            analyzed_at=d.get("analyzed_at", ""),
        )


# --- Evolution output (Phase B) -------------------------------------------


@dataclass
class SkillPatch:
    """A concrete, applicable modification to a skill directory."""

    skill_id: str
    patch_type: str                # "full" | "diff" | "patch"
    content: str                   # The actual patch text
    change_summary: str = ""       # One-line description

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id, "patch_type": self.patch_type,
            "content": self.content, "change_summary": self.change_summary,
        }


# --- Validator output ------------------------------------------------------


@dataclass
class ValidateResult:
    """Result of validating a skill patch."""

    verdict: str = "fail"          # "pass" | "fail"
    reason: str = ""
    failed_cases: list[str] = field(default_factory=list)
    suggestion: str = ""
