"""Phase B: Evolution Runner.

Takes the Phase A diagnosis (natural language) and produces a concrete
SkillPatch.  Uses Validator tools (validate_patch, add_test_case) in
a generate→validate→fix loop (max 3 iterations).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from skill_engine.pipeline.models import ExecutionAnalysis, Segment, SkillPatch
from skill_engine.pipeline.llm_client import LLMClient
from skill_engine.pipeline.validator import Validator

import logging

logger = logging.getLogger(__name__)

_MAX_EVOLUTION_ITERATIONS = 3

_EVOLUTION_TEMPLATE = """## Evolution Task

Improve a skill based on post-execution diagnosis.

**Skill:** {skill_name}

**Current Skill Content:**
{skill_content}

**Diagnosis (from Phase A analysis):**
{diagnosis}

**Execution Errors:**
{error_summary}

## Instructions

Based on the diagnosis above, produce specific changes to improve this skill.
Output in ONE of these formats:

### SEARCH/REPLACE (preferred, for targeted changes):
```
<<<<<<< SEARCH
(exact text to find in the file)
=======
(replacement text)
>>>>>>> REPLACE
```

### FULL (for complete rewrites or new skills):
```
*** Begin Files
*** File: SKILL.md
(complete new content)
*** End Files
```

**Guidelines:**
- Be MINIMAL — change only what the diagnosis identifies as problematic.
- Preserve the original structure and style.
- If the diagnosis mentions specific error messages, make sure your change addresses them.
- Output ONLY the patch. No explanatory text outside the format markers.
"""


class EvolutionRunner:
    """Run Phase B: produce SkillPatch with internal validate→fix loop.

    Args:
        llm_client: LLM client.
        skill_store: For loading skill content.
        validator: Validator instance (provides validate_patch/add_test_case tools).
        model: Override model name.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        skill_store: Any = None,
        validator: Validator | None = None,
        model: Optional[str] = None,
    ) -> None:
        self._llm = llm_client
        self._skill_store = skill_store
        self._validator = validator
        self._model = model

    # --- Main entry point ---------------------------------------------------

    async def evolve(
        self,
        analysis: ExecutionAnalysis,
        segment: Segment,
    ) -> SkillPatch | None:
        """Produce a validated SkillPatch for the skills referenced in the segment.

        If analysis.diagnosis is empty or contains failure markers, returns None.
        """
        if not analysis.diagnosis or "Analysis failed" in analysis.diagnosis:
            logger.warning("Phase B skipped: empty or failed diagnosis")
            return None

        # Get target skill from segment stats
        import json as _json
        stats_data = _json.loads(segment.stats_json)
        skills = stats_data.get("skills_referenced", [])
        if not skills:
            logger.debug("No skills referenced — skipping Phase B")
            return None

        skill_name = skills[0]  # Primary skill
        skill_content = await self._load_skill_content(skill_name)
        if not skill_content:
            logger.warning(f"Could not load skill content for {skill_name}")
            return None

        # Iterative generate → validate → fix loop
        diagnosis = analysis.diagnosis
        error_lines = "\n".join(f"- {e}" for e in analysis.error_summary)

        for iteration in range(_MAX_EVOLUTION_ITERATIONS):
            prompt = _EVOLUTION_TEMPLATE.format(
                skill_name=skill_name,
                skill_content=skill_content[:10000],
                diagnosis=diagnosis,
                error_summary=error_lines or "(none)",
            )

            if iteration > 0:
                prompt += (
                    f"\n\n**Validation feedback from previous attempt:**\n"
                    f"{diagnosis.split(chr(10) + '---VALIDATION---' + chr(10))[-1] if '---VALIDATION---' in diagnosis else ''}"
                    f"\n\nPlease FIX the issues and produce an updated patch."
                )

            try:
                result = await self._llm.complete(
                    messages=[{"role": "user", "content": prompt}],
                    model=self._model,
                )
            except Exception as e:
                logger.error(f"Phase B LLM call failed (iter {iteration}): {e}")
                return None

            patch_content = result.get("message", {}).get("content", "")
            if not patch_content:
                logger.warning("Empty patch from Phase B")
                continue

            patch_type = _detect_patch_type(patch_content)
            patch = SkillPatch(
                skill_id=skill_name,
                patch_type=patch_type,
                content=patch_content,
                change_summary=diagnosis.split("\n")[0][:200],
            )

            # Validate
            if self._validator:
                val_result = await self._validator.validate_patch(
                    patch_content=patch_content,
                    skill_id=skill_name,
                    old_content=skill_content,
                    error_summary=analysis.error_summary,
                )

                if val_result.verdict == "pass":
                    logger.info(
                        f"Phase B: patch validated (iter {iteration + 1})"
                    )
                    return patch

                # Feed validation feedback back to LLM
                diagnosis = (
                    f"{diagnosis}\n\n---VALIDATION---\n"
                    f"Previous patch was REJECTED: {val_result.reason}\n"
                    f"Failed cases: {val_result.failed_cases}\n"
                    f"Suggestion: {val_result.suggestion}"
                )
                logger.info(
                    f"Phase B: validation failed (iter {iteration + 1}): "
                    f"{val_result.reason[:100]}"
                )
            else:
                # No validator — first accepted attempt wins
                return patch

        logger.warning(
            f"Phase B: max iterations ({_MAX_EVOLUTION_ITERATIONS}) reached "
            f"without passing validation"
        )
        return None

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
        for skills_dir in [Path("skills"), Path.home() / ".claude" / "skills"]:
            skill_md = skills_dir / skill_name / "SKILL.md"
            if skill_md.exists():
                return skill_md.read_text(encoding="utf-8")
        return None


# --- Patch type detection --------------------------------------------------


def _detect_patch_type(content: str) -> str:
    if "*** Begin Patch" in content:
        return "patch"
    if "*** Begin Files" in content:
        return "full"
    if re.search(r"^\*\*\*\s*File:", content, re.MULTILINE):
        return "full"
    if "<<<<<<< SEARCH" in content:
        return "diff"
    return "full"
