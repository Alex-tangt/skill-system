"""Validator — verifies skill patches before they are applied.

Two-layer architecture:
  Layer 1: Mechanical checks — deterministic, zero LLM cost.
  Layer 2: Semantic checks — LLM-powered, only triggered when needed.

The Validator is deliberately independent of execution context.
It only needs: old skill + patch + change_summary + metrics.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from skill_engine.pipeline.models import SkillPatch, ValidateResult

import logging

logger = logging.getLogger(__name__)

# --- Dangerous patterns -----------------------------------------------------

# Patterns that suggest prompt injection or credential exfiltration
_DANGEROUS_PATTERNS = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
     "prompt-injection: attempts to override instructions"),
    (re.compile(r"<\|im_start\|>|<\|im_end\|>", re.IGNORECASE),
     "prompt-injection: special tokens"),
    (re.compile(r"(?:api[_-]?key|password|secret|token|credential)\s*[:=]\s*['\"]?\w{8,}", re.IGNORECASE),
     "credential-leak: appears to contain credentials"),
    (re.compile(r"eval\s*\(|exec\s*\(|__import__\s*\(|subprocess\.", re.IGNORECASE),
     "dangerous-code: potentially unsafe Python execution"),
]

# Frontmatter fields that MUST be present and valid
_REQUIRED_FRONTMATTER = {
    "name": re.compile(r"^name:\s*(.+)$", re.MULTILINE),
    "description": re.compile(r"^description:\s*(.+)$", re.MULTILINE),
}

# --- Validator ---------------------------------------------------------------


class Validator:
    """Validate SkillPatch objects before they are applied to skill files.

    Args:
        llm_client: Optional — only needed for Layer 2 semantic checks.
        skill_store: For loading skill content.
    """

    def __init__(
        self,
        llm_client: Any = None,
        skill_store: Any = None,
    ) -> None:
        self._llm = llm_client
        self._skill_store = skill_store

    def layer1_check(self, patch: SkillPatch, old_content: str) -> ValidateResult:
        """Layer 1: Mechanical checks. Deterministic, zero LLM cost.

        Checks:
          1. Patch content is non-empty.
          2. Frontmatter validity (for FULL format patches).
          3. Dangerous pattern detection.
          4. Diff size sanity check.
        """
        # Empty check
        if not patch.content or not patch.content.strip():
            return ValidateResult(
                verdict="reject",
                reason="Patch content is empty",
                layer="l1",
            )

        # For FULL format: validate that the new SKILL.md has required frontmatter
        if patch.patch_type == "full":
            for field, pattern in _REQUIRED_FRONTMATTER.items():
                if not pattern.search(patch.content):
                    return ValidateResult(
                        verdict="reject",
                        reason=f"Missing required frontmatter field: {field}",
                        layer="l1",
                    )

        # Dangerous pattern detection
        for pattern, description in _DANGEROUS_PATTERNS:
            if pattern.search(patch.content):
                return ValidateResult(
                    verdict="reject",
                    reason=f"Safety check failed: {description}",
                    layer="l1",
                    risk_flags=[description],
                )

        # Diff size sanity: reject full-file replacements that are suspiciously small
        if patch.patch_type == "full" and len(patch.content) < 50:
            return ValidateResult(
                verdict="reject",
                reason="FULL replacement is suspiciously short (< 50 chars)",
                layer="l1",
            )

        # Diff size sanity: reject if new content is identical to old
        if patch.patch_type == "full" and patch.content.strip() == old_content.strip():
            return ValidateResult(
                verdict="reject",
                reason="FULL replacement is identical to original — no changes",
                layer="l1",
            )

        return ValidateResult(verdict="pass", layer="l1")

    async def layer2_check(
        self,
        patch: SkillPatch,
        old_content: str,
        change_summary: str = "",
        metrics: Optional[dict] = None,
    ) -> ValidateResult:
        """Layer 2: Semantic check. LLM-powered, only triggered when needed.

        Called when:
          - Layer 1 passes but the skill has critical_tools dependencies
          - Previous evolution caused regressions
          - Manual review is requested
        """
        if not self._llm:
            return ValidateResult(
                verdict="pass",
                reason="LLM not available — skipping semantic check",
                layer="l2",
            )

        prompt = f"""## Validation Task

Check if this skill modification is correct and safe.

**Change Summary:** {change_summary}

**Old Content (excerpt):**
{old_content[:3000]}

**Patch Type:** {patch.patch_type}

**Patch Content:**
{patch.content[:5000]}

**Metrics Context:**
{metrics or "No metrics available"}

## Questions

1. Does this change actually address the described problem?
2. Does it introduce any new errors, ambiguities, or risks?
3. Is the change minimal — only modifying what's necessary?

Respond with JSON:
```json
{{
  "verdict": "pass|reject|needs_review",
  "reason": "Brief explanation"
}}
```"""

        try:
            result = await self._llm.complete(
                messages=[{"role": "user", "content": prompt}],
            )
            content = result.get("message", {}).get("content", "")
            # Extract JSON
            import json as _json
            json_match = re.search(r"\{.*\}", content, re.DOTALL) if content else None
            if json_match:
                data = _json.loads(json_match.group())
                return ValidateResult(
                    verdict=data.get("verdict", "needs_review"),
                    reason=data.get("reason", ""),
                    layer="l2",
                )
        except Exception as e:
            logger.warning(f"Layer 2 semantic check failed: {e}")

        return ValidateResult(
            verdict="needs_review",
            reason="Semantic check could not complete",
            layer="l2",
        )

    async def validate(
        self,
        patch: SkillPatch,
        old_content: str = "",
        change_summary: str = "",
        metrics: Optional[dict] = None,
        skip_l2: bool = True,
    ) -> ValidateResult:
        """Run the full validation pipeline: Layer 1 → Layer 2 (optional).

        Args:
            patch: The skill patch to validate.
            old_content: Current SKILL.md content.
            change_summary: Human-readable summary of the change.
            metrics: Optional performance metrics for context.
            skip_l2: If True, only run Layer 1 (default for most patches).
        """
        # Layer 1 always runs
        result = self.layer1_check(patch, old_content)
        if result.verdict != "pass" or skip_l2:
            return result

        # Layer 2: semantic check (only when conditions are met)
        return await self.layer2_check(
            patch, old_content, change_summary, metrics
        )
