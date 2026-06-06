from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HistoryEvent:
    """One event captured by a Claude Code hook and stored in History DB."""

    id: int | None = None  # DB auto-increment
    session_id: str = ""
    hook_event_name: str = ""  # "PostToolUse" | "UserPromptSubmit" | ...
    tool_name: str | None = None
    tool_input_json: str | None = None
    tool_output_json: str | None = None
    transcript_path: str | None = None
    dedup_hash: str = ""
    created_at: str = ""
    processed: int = 0  # 0=pending, 1=processing, 2=done

    @classmethod
    def from_dict(cls, d: dict) -> HistoryEvent:
        return cls(
            id=d.get("id"),
            session_id=d.get("session_id", ""),
            hook_event_name=d.get("hook_event_name", ""),
            tool_name=d.get("tool_name"),
            tool_input_json=d.get("tool_input_json"),
            tool_output_json=d.get("tool_output_json"),
            transcript_path=d.get("transcript_path"),
            dedup_hash=d.get("dedup_hash", ""),
            created_at=d.get("created_at", ""),
            processed=d.get("processed", 0),
        )


@dataclass
class PipelineStatus:
    """Result of a pipeline run."""

    events_processed: int = 0
    traces_created: int = 0
    errors: list[str] = field(default_factory=list)
    last_run: str = ""  # ISO timestamp
