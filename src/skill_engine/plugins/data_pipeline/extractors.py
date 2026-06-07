from __future__ import annotations

import json
import time
import uuid
import re
from abc import ABC, abstractmethod

from skill_engine.kernel.models.trace import StepTrace

try:
    from skill_engine.plugins.data_pipeline.models import HistoryEvent
except ImportError:
    HistoryEvent = None  # type: ignore


class BaseExtractor(ABC):
    """Extracts a StepTrace from a raw history event.

    Extractor chain: extractors are sorted by priority (lower = first).
    The first extractor whose can_extract() returns True handles the event.
    """

    name: str = "base"
    priority: int = 100  # Lower = tried first

    @abstractmethod
    def can_extract(self, event: dict) -> bool:
        """Does this extractor know how to handle this event?"""
        ...

    @abstractmethod
    def extract(self, event: dict, trace_id: str) -> StepTrace | None:
        """Extract a StepTrace from the event. Returns None on failure."""
        ...


class SkillTriggerExtractor(BaseExtractor):
    """Detects skill invocations by matching skill scripts/ paths in tool input.

    In v0.2, skills are executed by Claude Code's native mechanism — this
    means running scripts from skills/<name>/scripts/ or reading SKILL.md.
    We detect these patterns from Bash/Read/Write tool input.
    """

    name = "skill-trigger"
    priority = 10

    # Patterns that suggest a skill script or SKILL.md was accessed
    SKILL_SCRIPT_RE = re.compile(r"skills/([\w-]+)/scripts/", re.IGNORECASE)
    SKILL_MD_RE = re.compile(r"skills/([\w-]+)/SKILL\.md", re.IGNORECASE)

    def can_extract(self, event: dict) -> bool:
        tool_input = event.get("tool_input_json") or ""
        return bool(self.SKILL_SCRIPT_RE.search(tool_input) or self.SKILL_MD_RE.search(tool_input))

    def extract(self, event: dict, trace_id: str) -> StepTrace | None:
        tool_input = event.get("tool_input_json", "") or ""
        # Try to extract skill name from the path
        m = self.SKILL_SCRIPT_RE.search(tool_input) or self.SKILL_MD_RE.search(tool_input)
        skill_id = m.group(1) if m else "unknown-skill"

        return StepTrace(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            step_id=skill_id,
            step_name=f"Skill: {skill_id}",
            started_at=time.time(),
            status="succeeded",
            input={"tool_name": event.get("tool_name", ""), "match": tool_input[:200]},
            output=None,
            event_type="tool_call",
            context_ref=event.get("id"),
        )


class InputOutputExtractor(BaseExtractor):
    """Extracts input/output from any tool call with JSON payloads."""

    name = "input-output"
    priority = 20

    def can_extract(self, event: dict) -> bool:
        # Handle any tool with JSON input/output
        return bool(event.get("tool_input_json"))

    def extract(self, event: dict, trace_id: str) -> StepTrace | None:
        tool_name = event.get("tool_name", "unknown") or "unknown"

        input_data = {}
        output_data = None
        try:
            raw = event.get("tool_input_json", "{}") or "{}"
            input_data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            pass

        try:
            raw_out = event.get("tool_output_json", "{}") or "{}"
            output_data = json.loads(raw_out) if isinstance(raw_out, str) else raw_out
        except (json.JSONDecodeError, TypeError):
            pass

        return StepTrace(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            step_id=tool_name,
            step_name=tool_name,
            started_at=time.time(),
            finished_at=time.time(),
            status="succeeded",
            input=input_data,
            output=output_data,
            event_type="tool_call",
            context_ref=event.get("id"),
        )


class ErrorExtractor(BaseExtractor):
    """Detects tool failures and errors in hook events."""

    name = "error"
    priority = 30

    ERROR_KEYWORDS = ["error", "failed", "exception", "timeout", "traceback"]

    def can_extract(self, event: dict) -> bool:
        output = (event.get("tool_output_json") or "").lower()
        return any(kw in output for kw in self.ERROR_KEYWORDS)

    def extract(self, event: dict, trace_id: str) -> StepTrace | None:
        tool_name = event.get("tool_name", "unknown") or "unknown"
        output_str = event.get("tool_output_json") or ""

        # Extract first error line
        error_msg = output_str[:500] if output_str else "Unknown error"

        return StepTrace(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            step_id=tool_name,
            step_name=tool_name,
            started_at=time.time(),
            finished_at=time.time(),
            status="failed",
            error=error_msg,
            event_type="tool_call",
            context_ref=event.get("id"),
        )


def build_extractor_chain(extractors: list[BaseExtractor] | None = None) -> list[BaseExtractor]:
    """Build and sort the extractor chain by priority."""
    if extractors is None:
        extractors = [
            SkillTriggerExtractor(),
            InputOutputExtractor(),
            ErrorExtractor(),
        ]
    return sorted(extractors, key=lambda e: e.priority)
