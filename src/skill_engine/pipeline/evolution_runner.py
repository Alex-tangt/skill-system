"""Phase B: Evolution Runner.

Takes the ExecutionAnalysis (from Phase A) and the original segment
context, then produces concrete SkillPatch objects for each actionable
EvolutionSuggestion.

The key design decision: Phase B shares the same segment context as
Phase A but uses it differently — it focuses on producing an applicable
patch from the analysis conclusions, not on re-analyzing the execution.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from skill_engine.pipeline.models import (
    EvolutionSuggestion,
    ExecutionAnalysis,
    Segment,
    SkillPatch,
)
from skill_engine.pipeline.llm_client import LLMClient
import logging

logger = logging.getLogger(__name__)

# Phase B prompt template
_EVOLUTION_TEMPLATE = """## Evolution Task

You are improving a skill based on post-execution analysis.

**Change Type:** {evolution_type}
**Priority:** {priority}
**Direction:** {direction}

## Analysis Summary

The execution analysis found:
{execution_note}

## Current Skill: {skill_name}

{skill_content}

## Instructions

Produce the specific changes needed to improve this skill. Output the
changes in ONE of these three formats:

### Format 1: FULL (complete file replacement)
```
*** Begin Files
*** File: SKILL.md
(complete new content)
*** End Files
```

### Format 2: SEARCH/REPLACE (single-file targeted changes)
```
<<<<<<< SEARCH
(exact text to find in the file)
=======
(replacement text)
>>>>>>> REPLACE
```

### Format 3: PATCH (multi-file changes)
```
*** Begin Patch
*** Update File: SKILL.md
@@ context line before the change
-old line
+new line
*** End Patch
```

**Guidelines:**
- Be MINIMAL — change only what's necessary to address the analysis findings.
- Preserve the original structure and style of the skill.
- If a section is fine as-is, leave it untouched.
- For SEARCH/REPLACE, ensure the SEARCH block exactly matches the file content
  (including whitespace and indentation).
- For new skills (CAPTURED), use the FULL format with a complete SKILL.md.

Output ONLY the patch content. No explanatory text outside the format markers.
"""

# Single-parent skill section (for FIX and single-parent DERIVED)
_SKILL_CONTENT_MAX_CHARS = 10_000


class EvolutionRunner:
    """Run Phase B: produce SkillPatch from analysis conclusions.

    Args:
        llm_client: LLM client.
        skill_store: For loading skill content from disk.
        model: Override model name.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        skill_store: Any = None,
        model: Optional[str] = None,
    ) -> None:
        self._llm = llm_client
        self._skill_store = skill_store
        self._model = model

    async def evolve(
        self,
        analysis: ExecutionAnalysis,
        segment: Segment,
    ) -> list[SkillPatch]:
        """Produce SkillPatch objects for all actionable suggestions.

        Only processes suggestions with confidence != "low".
        """
        patches: list[SkillPatch] = []

        for suggestion in analysis.evolution_suggestions:
            if not suggestion.is_actionable:
                logger.info(
                    f"Skipping low-confidence suggestion: {suggestion.type} "
                    f"on {suggestion.target_skill_ids}"
                )
                continue

            try:
                patch = await self._evolve_one(suggestion, analysis)
                if patch:
                    patches.append(patch)
            except Exception as e:
                logger.error(
                    f"Evolution failed for {suggestion.type} on "
                    f"{suggestion.target_skill_ids}: {e}"
                )

        return patches

    async def _evolve_one(
        self,
        suggestion: EvolutionSuggestion,
        analysis: ExecutionAnalysis,
    ) -> Optional[SkillPatch]:
        """Produce a single SkillPatch for one evolution suggestion."""
        target_ids = suggestion.target_skill_ids
        if not target_ids:
            # CAPTURED: no target skill — create from scratch
            skill_name = suggestion.direction.split()[0] if suggestion.direction else "captured-skill"
            skill_content = "(new skill — no existing content)"
        else:
            # FIX or DERIVED: load existing skill content
            skill_name = target_ids[0]
            skill_content = await self._load_skill_content(skill_name)

        if not skill_content:
            logger.warning(f"Could not load skill content for {skill_name}")
            return None

        # Truncate skill content
        if len(skill_content) > _SKILL_CONTENT_MAX_CHARS:
            skill_content = (
                skill_content[:_SKILL_CONTENT_MAX_CHARS]
                + "\n\n... [truncated]"
            )

        # Build and send prompt
        prompt = _EVOLUTION_TEMPLATE.format(
            evolution_type=suggestion.type.upper(),
            priority=suggestion.priority.upper(),
            direction=suggestion.direction,
            execution_note=analysis.execution_note,
            skill_name=skill_name,
            skill_content=skill_content,
        )

        result = await self._llm.complete(
            messages=[{"role": "user", "content": prompt}],
            model=self._model,
        )

        content = result.get("message", {}).get("content", "")
        if not content:
            logger.warning(f"Empty LLM response for evolution of {skill_name}")
            return None

        # Detect patch type
        patch_type = _detect_patch_type(content)

        # Extract target files
        target_files = _extract_target_files(content, patch_type, skill_name)

        return SkillPatch(
            skill_id=skill_name,
            patch_type=patch_type,
            content=content,
            change_summary=suggestion.direction,
            target_files=target_files,
        )

    async def _load_skill_content(self, skill_name: str) -> Optional[str]:
        """Load SKILL.md content for a skill."""
        if self._skill_store:
            try:
                meta = await self._skill_store.get_skill(skill_name)
                if meta:
                    if hasattr(meta, "body"):
                        return meta.body
                    if hasattr(meta, "path"):
                        p = meta.path
                        if hasattr(p, "read_text"):
                            return p.read_text(encoding="utf-8")
            except Exception:
                pass

        # Filesystem fallback
        for skills_dir in [Path("skills"), Path.home() / ".claude" / "skills"]:
            skill_md = skills_dir / skill_name / "SKILL.md"
            if skill_md.exists():
                return skill_md.read_text(encoding="utf-8")
        return None


# --- Patch type detection (parity with OpenSpace patch.py) ------------------


def _detect_patch_type(content: str) -> str:
    """Auto-detect the patch format from LLM output.

    Detection priority (structural markers):
      1. ``*** Begin Patch``  → "patch" (multi-file diff)
      2. ``*** Begin Files``  → "full"  (multi-file envelope)
      3. ``*** File:`` marker → "full"  (bare multi-file)
      4. ``<<<<<<< SEARCH``   → "diff"  (single-file SEARCH/REPLACE)
      5. Default              → "full"  (single-file complete content)
    """
    if "*** Begin Patch" in content:
        return "patch"
    if "*** Begin Files" in content:
        return "full"
    if re.search(r"^\*\*\*\s*File:", content, re.MULTILINE):
        return "full"
    if "<<<<<<< SEARCH" in content:
        return "diff"
    return "full"


def _extract_target_files(
    content: str, patch_type: str, skill_name: str
) -> list[str]:
    """Extract which files the patch targets."""
    if patch_type == "full":
        files = re.findall(r"\*\*\*\s*File:\s*(.+)", content)
        return files if files else ["SKILL.md"]
    elif patch_type == "patch":
        files = re.findall(r"\*\*\*\s*(?:Update|Add|Delete)\s*File:\s*(.+)", content)
        return list(dict.fromkeys(files)) if files else ["SKILL.md"]
    else:
        # DIFF format — always targets SKILL.md
        return ["SKILL.md"]
