"""Phase A analysis prompt builder.

Assembles the LLM prompt for analyzing a segment's execution trace.
Handles priority-based truncation (via Segmenter), skill content
enrichment, and template assembly.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from skill_engine.pipeline.models import Segment, SegmentStats

# Max characters for skill content in the prompt.
_SKILL_CONTENT_MAX_CHARS = 8_000

# Analysis prompt template
_ANALYSIS_TEMPLATE = """## Task Context

**Current Task:** {user_msg}

**Previous Task (user message):** {prev_user_msg}
**Next Task (user message):** {next_user_msg}

## Skills Involved

{skills_section}

## Execution Trace

{execution_trace}

## Instructions

Analyze the execution trace above and answer the following questions.
Be specific — reference exact tool names, error messages, and step numbers.

1. **Task Completed?** Was the user's request fulfilled?
   - If no: what blocked completion?
   - If partially: what was done and what was missed?

2. **Skill Effectiveness:** For each skill listed above, assess:
   - **Applied correctly?** Did the agent follow the skill's guidance?
     Any deviation?
   - **Helpful or harmful?** Did the skill save time/effort or cause
     confusion/rework?
   - **Missing guidance?** Is there something the skill should have
     covered but didn't?

3. **Improvement Suggestions:** For each skill that needs changes:
   - **FIX:** broken instructions, outdated tool names, wrong parameters
   - **DERIVED:** opportunities to specialize or compose with other skills
   - **CAPTURED:** reusable patterns from this execution worth extracting
     as a new skill

## Available Tools

- `traverse_chain(direction, steps)` — Expand context along the conversation
  chain if the task appears to span multiple segments.
- `read_file(path)` — Read a file from disk to inspect the agent's output.
- `get_skill_content(skill_id)` — Load the full SKILL.md for a skill if the
  truncated version above is insufficient.

**Important:** If the prev/next user messages suggest the task continues
beyond this segment, use `traverse_chain` before drawing conclusions.

## Output

Respond with a JSON object matching this schema:

```json
{{
  "task_completed": true/false,
  "execution_note": "Brief summary of what happened (1-3 sentences)",
  "tool_issues": ["tool_key — description of issue"],
  "skill_judgments": [
    {{
      "skill_id": "skill-name",
      "skill_applied": true/false,
      "skill_helpful": true/false,
      "note": "Observation about this skill"
    }}
  ],
  "evolution_suggestions": [
    {{
      "type": "fix|derived|captured",
      "target_skills": ["skill_id"],
      "direction": "What to change or capture (be specific)",
      "priority": "high|medium|low",
      "confidence": "high|medium|low"
    }}
  ],
  "analyzed_by": "{analyzed_by}"
}}
```

Guidelines:
- `confidence: "low"` if the execution trace is incomplete or ambiguous.
  Low-confidence suggestions are reviewed later, not applied immediately.
- `confidence: "high"` only when the trace clearly shows what went wrong
  and how to fix it.
- Only include skills that were actually referenced or relevant.
- `tool_issues` should use the format `backend:tool_name — description`.
"""


class AnalysisPromptBuilder:
    """Builds the Phase A analysis prompt for a segment.

    Args:
        skill_store: Used to load SKILL.md content.
        budget: Character budget for the execution trace section.
    """

    def __init__(
        self,
        skill_store: Any = None,  # SkillStore or similar
        budget: int = 30_000,  # prompt text budget (not including execution)
    ) -> None:
        self._skill_store = skill_store
        self._budget = budget

    async def build(
        self,
        segment: Segment,
        prev_user_msg: str = "(none)",
        next_user_msg: str = "(none)",
        model_name: str = "pipeline-analyzer",
    ) -> str:
        """Build the complete Phase A analysis prompt.

        Args:
            segment: The segment to analyze.
            prev_user_msg: User message from the previous segment.
            next_user_msg: User message from the next segment.
            model_name: Identifier for the analysis (used in output metadata).
        """
        # Load skill content for referenced skills
        stats = SegmentStats.from_json(segment.stats_json)
        skills_section = await self._build_skills_section(stats.skills_referenced)

        # Load execution trace (already truncated by Segmenter)
        execution_entries = json.loads(segment.execution_json)
        execution_trace = self._format_execution(execution_entries)

        return _ANALYSIS_TEMPLATE.format(
            user_msg=segment.user_msg,
            prev_user_msg=prev_user_msg,
            next_user_msg=next_user_msg,
            skills_section=skills_section or "(no skills involved in this task)",
            execution_trace=execution_trace,
            analyzed_by=model_name,
        )

    async def _build_skills_section(self, skill_names: list[str]) -> str:
        """Load skill content and format for the prompt."""
        if not skill_names:
            return ""

        parts: list[str] = []
        for name in skill_names:
            content = await self._load_skill_content(name)
            if not content:
                parts.append(f"### {name}\n(skill content not available)")
                continue

            # Truncate per-skill content
            if len(content) > _SKILL_CONTENT_MAX_CHARS:
                content = (
                    content[:_SKILL_CONTENT_MAX_CHARS]
                    + f"\n\n... [truncated at {_SKILL_CONTENT_MAX_CHARS} chars]"
                )
            parts.append(f"### {name}\n{content}")

        return "\n\n---\n\n".join(parts)

    async def _load_skill_content(self, skill_name: str) -> Optional[str]:
        """Load SKILL.md content for a skill name.

        Tries the skill_store first, then falls back to filesystem scan.
        """
        # Try skill_store
        if self._skill_store:
            try:
                # SkillStore.get_skill returns SkillMetadata or None
                meta = await self._skill_store.get_skill(skill_name)
                if meta and hasattr(meta, "body"):
                    return meta.body
                if meta and hasattr(meta, "path"):
                    path = meta.path
                    if hasattr(path, "read_text"):
                        return path.read_text(encoding="utf-8")
            except Exception:
                pass

        # Fallback: scan skills/ directories
        from pathlib import Path

        for skills_dir in [
            Path("skills"),
            Path.home() / ".claude" / "skills",
        ]:
            skill_md = skills_dir / skill_name / "SKILL.md"
            if skill_md.exists():
                return skill_md.read_text(encoding="utf-8")

        return None

    @staticmethod
    def _format_execution(entries: list[dict]) -> str:
        """Format execution entries into readable text."""
        lines: list[str] = []
        for entry in entries:
            etype = entry.get("type", "")
            role = entry.get("role", "")
            content = entry.get("content", "")
            tool_calls = entry.get("tool_calls", [])

            if role == "assistant":
                if content:
                    lines.append(f"\n### ASSISTANT:\n{content}\n")
                for tc in tool_calls:
                    name = tc.get("name", "?")
                    inp = json.dumps(tc.get("input", {}), ensure_ascii=False)
                    if len(inp) > 500:
                        inp = inp[:500] + "..."
                    lines.append(f">>> TOOL_CALL: {name}({inp})")

            elif entry.get("is_tool_result"):
                label = "TOOL_ERROR" if _is_error(content) else "TOOL_RESULT"
                if len(content) > 800:
                    content = content[:800] + "..."
                lines.append(f"<<< {label}: {content}")

            elif role == "user" and not entry.get("is_user_message"):
                # Tool result in user-style entry
                if len(content) > 800:
                    content = content[:800] + "..."
                lines.append(f"<<< TOOL_RESULT: {content}")

        return "\n".join(lines) if lines else "(no execution trace available)"


def _is_error(text: str) -> bool:
    if not text:
        return False
    head = text[:200].lower()
    return any(kw in head for kw in ("error", "failed", "traceback", "exception"))
