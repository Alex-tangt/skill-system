from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SegmentStats:
    """Mechanical statistics computed during segmentation (no LLM involved)."""

    tool_count: int = 0
    tool_types: dict[str, int] = field(default_factory=dict)  # {"shell": 5, "mcp": 3}
    iteration_count: int = 0
    status: str = "unknown"  # "success" | "error" | "incomplete"
    skills_referenced: list[str] = field(default_factory=list)
    total_chars: int = 0
    started_at: float | None = None
    finished_at: float | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "tool_count": self.tool_count,
                "tool_types": self.tool_types,
                "iteration_count": self.iteration_count,
                "status": self.status,
                "skills_referenced": self.skills_referenced,
                "total_chars": self.total_chars,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> SegmentStats:
        d = json.loads(data) if isinstance(data, str) else data
        return cls(
            tool_count=d.get("tool_count", 0),
            tool_types=d.get("tool_types", {}),
            iteration_count=d.get("iteration_count", 0),
            status=d.get("status", "unknown"),
            skills_referenced=d.get("skills_referenced", []),
            total_chars=d.get("total_chars", 0),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
        )


@dataclass
class Segment:
    """One user message and its complete execution context.

    Represents a single task boundary: the user said something, and the
    agent executed a sequence of assistant thoughts + tool calls to respond.
    """

    id: str  # UUID
    session_id: str  # Claude Code session ID

    # User boundary
    user_msg: str  # User input text
    user_msg_index: int  # Index of this user message in the transcript

    # Execution (truncated, stored as JSON array of TranscriptEntry-like dicts)
    execution_json: str = "[]"

    # Bidirectional chain
    prev_id: str | None = None
    next_id: str | None = None

    # Mechanical stats (JSON string of SegmentStats)
    stats_json: str = "{}"

    # Context references (JSON arrays)
    skills_available: str = "[]"  # Skill names visible during execution
    files_modified: str = "[]"  # File paths from file-history-snapshot

    # Metadata
    created_at: str = ""  # ISO timestamp

    @property
    def has_next(self) -> bool:
        return self.next_id is not None

    @property
    def has_prev(self) -> bool:
        return self.prev_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "user_msg": self.user_msg,
            "user_msg_index": self.user_msg_index,
            "execution_json": self.execution_json,
            "prev_id": self.prev_id,
            "next_id": self.next_id,
            "stats_json": self.stats_json,
            "skills_available": self.skills_available,
            "files_modified": self.files_modified,
            "created_at": self.created_at,
        }


# --- Analysis output models (Phase A) ---


@dataclass
class SkillJudgment:
    """Per-skill assessment within an execution analysis."""

    skill_id: str
    skill_applied: bool = False  # Was the skill actually used?
    skill_helpful: bool = False  # Did it help or hinder?
    note: str = ""  # Observation

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_applied": self.skill_applied,
            "skill_helpful": self.skill_helpful,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillJudgment:
        return cls(
            skill_id=d.get("skill_id", ""),
            skill_applied=d.get("skill_applied", False),
            skill_helpful=d.get("skill_helpful", False),
            note=d.get("note", ""),
        )


@dataclass
class EvolutionSuggestion:
    """One evolution action suggested by the analysis LLM."""

    type: str  # "fix" | "derived" | "captured"
    target_skill_ids: list[str] = field(default_factory=list)
    direction: str = ""  # What to change or capture
    priority: str = "medium"  # "high" | "medium" | "low"
    confidence: str = "medium"  # "high" | "medium" | "low" — low = skip evolution

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence == "high"

    @property
    def is_actionable(self) -> bool:
        return self.confidence in ("high", "medium")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "target_skill_ids": self.target_skill_ids,
            "direction": self.direction,
            "priority": self.priority,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvolutionSuggestion:
        return cls(
            type=d.get("type", "fix"),
            target_skill_ids=d.get("target_skill_ids", []),
            direction=d.get("direction", ""),
            priority=d.get("priority", "medium"),
            confidence=d.get("confidence", "medium"),
        )


@dataclass
class ExecutionAnalysis:
    """LLM-produced analysis of a single segment execution."""

    task_id: str  # Segment ID
    analyzed_at: str = ""  # ISO timestamp

    task_completed: bool = False
    execution_note: str = ""  # Free-text summary

    skill_judgments: list[SkillJudgment] = field(default_factory=list)
    evolution_suggestions: list[EvolutionSuggestion] = field(default_factory=list)
    tool_issues: list[str] = field(default_factory=list)

    @property
    def has_high_confidence_suggestions(self) -> bool:
        return any(s.is_high_confidence for s in self.evolution_suggestions)

    @property
    def has_actionable_suggestions(self) -> bool:
        return any(s.is_actionable for s in self.evolution_suggestions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "analyzed_at": self.analyzed_at,
            "task_completed": self.task_completed,
            "execution_note": self.execution_note,
            "skill_judgments": [j.to_dict() for j in self.skill_judgments],
            "evolution_suggestions": [s.to_dict() for s in self.evolution_suggestions],
            "tool_issues": self.tool_issues,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExecutionAnalysis:
        return cls(
            task_id=d.get("task_id", ""),
            analyzed_at=d.get("analyzed_at", ""),
            task_completed=d.get("task_completed", False),
            execution_note=d.get("execution_note", ""),
            skill_judgments=[SkillJudgment.from_dict(j) for j in d.get("skill_judgments", [])],
            evolution_suggestions=[EvolutionSuggestion.from_dict(s) for s in d.get("evolution_suggestions", [])],
            tool_issues=d.get("tool_issues", []),
        )


# --- Evolution output models (Phase B) ---


@dataclass
class SkillPatch:
    """A concrete, applicable modification to a skill directory."""

    skill_id: str  # Target skill
    patch_type: str  # "full" | "diff" | "patch"
    content: str  # The actual patch text (LLM output)
    change_summary: str = ""  # One-line description of the change
    target_files: list[str] = field(default_factory=list)  # Which files are affected

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "patch_type": self.patch_type,
            "content": self.content,
            "change_summary": self.change_summary,
            "target_files": self.target_files,
        }


# --- Validator output models ---


@dataclass
class ValidateResult:
    """Result of validating a skill patch."""

    verdict: str  # "pass" | "reject" | "needs_review"
    reason: str = ""
    layer: str = ""  # "l1" (mechanical) | "l2" (semantic)
    risk_flags: list[str] = field(default_factory=list)
