"""Phase A analysis prompt builder.

Assembles the LLM prompt for analyzing a segment's execution trace.
Output: natural language diagnosis (not JSON).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from skill_engine.pipeline.models import Segment, SegmentStats

_SKILL_CONTENT_MAX_CHARS = 8_000

_ANALYSIS_TEMPLATE = """## Task Context

**Current Task:** {user_msg}
**Previous Task:** {prev_user_msg}
**Next Task:** {next_user_msg}

## Skills Involved

{skills_section}

## Execution Trace

{execution_trace}

## Instructions

Analyze the execution trace above and produce a DIAGNOSIS of what happened.
Focus on:

1. Was the task completed? If not, what blocked it?
2. For each skill: was it applied? Did it help or cause confusion? Where did it fall short?
3. What specific evidence in the trace supports your conclusions?
   Quote tool call names, error messages, step numbers.

**Output format:** Just write your analysis in natural language. Start with a one-line summary, then provide detailed observations with trace evidence. Do NOT output JSON — this is a prose diagnosis.

Example output:
```
SUMMARY: Task completed but Skill X caused one wasted iteration due to outdated parameter format.

DETAIL:
- Agent followed Step 1-2 correctly.
- At Step 3, agent called `run_shell('curl --retry 3')` which failed with
  "unrecognized option --retry". The skill says to use `--retry` but curl 8.x
  requires `--retries`. Agent corrected to `--retries` in the next iteration.
- Tool issue: shell:run_shell — does not validate command flags before execution.
- SUGGESTION: Fix Skill X Step 3 to use `--retries N --retry-max-time M`.
  Evidence: trace line "unrecognized option --retry" at Step 3.
```
"""


class AnalysisPromptBuilder:
    """Builds the Phase A analysis prompt for a segment.

    Args:
        skill_store: Used to load SKILL.md content.
    """

    def __init__(self, skill_store: Any = None) -> None:
        self._skill_store = skill_store

    async def build(
        self,
        segment: Segment,
        prev_user_msg: str = "(none)",
        next_user_msg: str = "(none)",
    ) -> str:
        stats = SegmentStats.from_json(segment.stats_json)
        skills_section = await self._build_skills_section(stats.skills_referenced)
        execution_entries = json.loads(segment.execution_json)
        execution_trace = self._format_execution(execution_entries)

        return _ANALYSIS_TEMPLATE.format(
            user_msg=segment.user_msg,
            prev_user_msg=prev_user_msg,
            next_user_msg=next_user_msg,
            skills_section=skills_section or "(no skills involved in this task)",
            execution_trace=execution_trace,
        )

    async def _build_skills_section(self, skill_names: list[str]) -> str:
        if not skill_names:
            return ""
        parts: list[str] = []
        for name in skill_names:
            content = await self._load_skill_content(name)
            if not content:
                parts.append(f"### {name}\n(skill content not available)")
                continue
            if len(content) > _SKILL_CONTENT_MAX_CHARS:
                content = content[:_SKILL_CONTENT_MAX_CHARS] + "\n\n... [truncated]"
            parts.append(f"### {name}\n{content}")
        return "\n\n---\n\n".join(parts)

    async def _load_skill_content(self, skill_name: str) -> Optional[str]:
        if self._skill_store:
            try:
                meta = await self._skill_store.get_skill(skill_name)
                if meta:
                    if hasattr(meta, "body"):
                        return meta.body
                    if hasattr(meta, "path") and hasattr(meta.path, "read_text"):
                        return meta.path.read_text(encoding="utf-8")
            except Exception:
                pass
        from pathlib import Path
        for skills_dir in [Path("skills"), Path.home() / ".claude" / "skills"]:
            skill_md = skills_dir / skill_name / "SKILL.md"
            if skill_md.exists():
                return skill_md.read_text(encoding="utf-8")
        return None

    @staticmethod
    def _format_execution(entries: list[dict]) -> str:
        lines: list[str] = []
        for entry in entries:
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
        return "\n".join(lines) if lines else "(no execution trace available)"


def _is_error(text: str) -> bool:
    if not text:
        return False
    head = text[:200].lower()
    return any(kw in head for kw in ("error", "failed", "traceback", "exception"))
