"""Validator — Optimizer's internal testing tool.

Provides three tools registered as both LLM-callable tools (for Optimizer)
and MCP tools (for human use):

  - validate_patch(patch, skill_id, old_content) → ValidateResult
  - add_test_case(skill_id, input_desc, expected_behavior) → None
  - run_test_suite(skill_id) → list of test cases + recent run results

Has its own SQLite database (validator.db) for persistence of test
cases and validation run history.  Test cases accumulate over time,
preventing overfitting to any single execution error.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, List, Optional

import logging

logger = logging.getLogger(__name__)

# --- Dataclasses -----------------------------------------------------------


@dataclass
class ValidateResult:
    """Result of a patch validation."""
    verdict: str = "fail"         # "pass" | "fail"
    reason: str = ""
    failed_cases: list[str] = field(default_factory=list)
    suggestion: str = ""


@dataclass
class ValidatorTestCase:
    """One test case for a skill."""
    id: str
    skill_id: str
    input_desc: str               # What scenario does this test cover?
    expected_behavior: str         # What should the skill do?
    source: str = "execution_error"  # "execution_error" | "manual" | "skill_create"
    created_at: str = ""


# --- DB Retry --------------------------------------------------------------


def _db_retry(max_retries: int = 5, initial_delay: float = 0.1, backoff: float = 2.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (sqlite3.OperationalError, sqlite3.DatabaseError):
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
                    delay *= backoff
        return wrapper
    return decorator


# --- Safety patterns -------------------------------------------------------

_DANGEROUS_PATTERNS = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
     "prompt-injection: attempts to override instructions"),
    (re.compile(r"(?:api[_-]?key|password|secret|token|credential)\s*[:=]\s*['\"]?\w{8,}", re.IGNORECASE),
     "credential-leak: appears to contain credentials"),
    (re.compile(r"eval\s*\(|exec\s*\(|__import__\s*\(|subprocess\.", re.IGNORECASE),
     "dangerous-code: potentially unsafe execution"),
]

_REQUIRED_FRONTMATTER = {
    "name": re.compile(r"^name:\s*(.+)$", re.MULTILINE),
    "description": re.compile(r"^description:\s*(.+)$", re.MULTILINE),
}


# --- Validator --------------------------------------------------------------


class Validator:
    """Optimizer's testing toolkit.

    Usage as LLM tool (inside Optimizer):
        result = await validator.validate_patch(patch, skill_id, old_content)

    Usage as MCP tool (human):
        pipeline_validator_add_case(skill_id, input_desc, expected_behavior)
    """

    def __init__(self, db_path: str = "./traces/validator.db") -> None:
        self.db_path = db_path

    # --- Lifecycle -----------------------------------------------------------

    async def initialize(self) -> None:
        import asyncio as _asyncio
        await _asyncio.to_thread(self._init_sync)

    @_db_retry()
    def _init_sync(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS validator_test_cases (
                    id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    input_desc TEXT NOT NULL,
                    expected_behavior TEXT NOT NULL,
                    source TEXT DEFAULT 'execution_error',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS validator_runs (
                    id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    patch_hash TEXT,
                    verdict TEXT NOT NULL,
                    failed_cases TEXT DEFAULT '[]',
                    reason TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_vtc_skill
                ON validator_test_cases(skill_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_vr_skill
                ON validator_runs(skill_id, created_at)
            """)
            conn.commit()
        finally:
            conn.close()

    # --- Tool: validate_patch -----------------------------------------------

    async def validate_patch(
        self,
        patch_content: str,
        skill_id: str,
        old_content: str = "",
        error_summary: list[str] | None = None,
    ) -> ValidateResult:
        """Validate a skill patch.

        Layer 1 (mechanical): frontmatter, safety, format.
        Layer 2 (regression): check against accumulated test cases.

        Args:
            patch_content: The raw patch text (SEARCH/REPLACE or FULL format).
            skill_id: Target skill identifier.
            old_content: Current SKILL.md content (for frontmatter check).
            error_summary: Execution errors the patch should address.

        Returns:
            ValidateResult with verdict and failure details.
        """
        error_summary = error_summary or []

        # L1: Mechanical checks
        l1 = self._l1_check(patch_content, old_content)
        if l1.verdict == "fail":
            return l1

        # L2: Regression test (test cases + error coverage)
        l2 = await self._l2_check(patch_content, skill_id, error_summary)
        if l2.verdict == "fail":
            return l2

        return ValidateResult(verdict="pass")

    @staticmethod
    def _l1_check(patch_content: str, old_content: str) -> ValidateResult:
        """Mechanical checks: empty, dangerous, frontmatter, size."""
        if not patch_content or not patch_content.strip():
            return ValidateResult(
                verdict="fail", reason="Patch content is empty",
            )

        # For FULL format: check frontmatter
        if "*** Begin Files" in patch_content or "*** File:" in patch_content:
            for field, pattern in _REQUIRED_FRONTMATTER.items():
                if not pattern.search(patch_content):
                    return ValidateResult(
                        verdict="fail",
                        reason=f"Missing required frontmatter field: {field}",
                    )

        # Safety
        for pattern, description in _DANGEROUS_PATTERNS:
            if pattern.search(patch_content):
                return ValidateResult(
                    verdict="fail", reason=f"Safety: {description}",
                )

        # Size sanity
        if len(patch_content) < 30:
            return ValidateResult(
                verdict="fail", reason="Patch suspiciously short (< 30 chars)",
            )

        # Identical check (FULL format)
        if old_content and patch_content.strip() == old_content.strip():
            return ValidateResult(
                verdict="fail", reason="Patch is identical to original",
            )

        return ValidateResult(verdict="pass")

    async def _l2_check(
        self,
        patch_content: str,
        skill_id: str,
        error_summary: list[str],
    ) -> ValidateResult:
        """Regression check against accumulated test cases + error coverage.

        Checks:
          1. Does the patch address at least one of the error_summary items?
          2. Does the patch break any existing test case?
        """
        import asyncio as _asyncio

        cases = await _asyncio.to_thread(self._get_cases_sync, skill_id)
        patch_lower = patch_content.lower()
        failed: list[str] = []

        # 1. Error coverage: does the patch address execution errors?
        if error_summary:
            covered = 0
            for err in error_summary:
                # Extract key terms from the error
                key_terms = _extract_key_terms(err)
                if any(term.lower() in patch_lower for term in key_terms):
                    covered += 1

            if covered == 0 and len(error_summary) > 0:
                return ValidateResult(
                    verdict="fail",
                    reason=f"Patch does not address any of the {len(error_summary)} execution errors. "
                           f"Errors: {error_summary[0][:100]}...",
                    failed_cases=[f"error_coverage:{e[:80]}" for e in error_summary[:3]],
                    suggestion="Ensure the patch addresses at least one execution error. "
                               "If this error is no longer relevant, call add_test_case to document why.",
                )

        # 2. Test case regression: check existing test cases
        for case in cases:
            expected_terms = _extract_key_terms(case["expected_behavior"])
            # If the old expected behavior is contradicted or removed by the patch,
            # and not explicitly replaced with a better version
            if expected_terms:
                old_matches = all(
                    term.lower() in (case.get("old_behavior", "") or "").lower()
                    for term in expected_terms
                ) if case.get("old_behavior") else True

                if not old_matches and not any(
                    term.lower() in patch_lower for term in expected_terms
                ):
                    failed.append(
                        f"test_case:{case['input_desc'][:60]}"
                    )

        if failed:
            return ValidateResult(
                verdict="fail",
                reason=f"Patch breaks {len(failed)} existing test case(s)",
                failed_cases=failed,
                suggestion="Review the patch to ensure existing test cases still pass. "
                           "If the test case is obsolete, remove it first.",
            )

        return ValidateResult(verdict="pass")

    # --- Record validation run ----------------------------------------------

    async def _record_run(self, skill_id: str, patch_content: str,
                          result: ValidateResult) -> None:
        import asyncio as _asyncio
        await _asyncio.to_thread(self._record_run_sync, skill_id, patch_content, result)

    @_db_retry()
    def _record_run_sync(self, skill_id: str, patch_content: str,
                         result: ValidateResult) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            patch_hash = hashlib.sha256(patch_content.encode()).hexdigest()[:16]
            conn.execute(
                """INSERT INTO validator_runs (id, skill_id, patch_hash, verdict, failed_cases, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), skill_id, patch_hash,
                 result.verdict, json.dumps(result.failed_cases), result.reason),
            )
            conn.commit()
        finally:
            conn.close()

    # --- Tool: add_test_case ------------------------------------------------

    async def add_test_case(
        self,
        skill_id: str,
        input_desc: str,
        expected_behavior: str,
        source: str = "execution_error",
    ) -> str:
        """Add a test case. Returns the new test case ID.

        Called by Optimizer when it encounters a new error pattern,
        or by humans via MCP tool.
        """
        import asyncio as _asyncio
        case_id = str(uuid.uuid4())
        await _asyncio.to_thread(
            self._add_case_sync, case_id, skill_id, input_desc, expected_behavior, source,
        )
        logger.info(f"Validator test case added: {skill_id} — {input_desc[:60]}")
        return case_id

    @_db_retry()
    def _add_case_sync(self, case_id: str, skill_id: str, input_desc: str,
                       expected_behavior: str, source: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO validator_test_cases (id, skill_id, input_desc, expected_behavior, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (case_id, skill_id, input_desc, expected_behavior, source),
            )
            conn.commit()
        finally:
            conn.close()

    # --- Tool: run_test_suite -----------------------------------------------

    async def run_test_suite(self, skill_id: str) -> list[dict]:
        """Return all test cases + recent runs for a skill."""
        import asyncio as _asyncio
        return await _asyncio.to_thread(self._run_suite_sync, skill_id)

    @_db_retry()
    def _run_suite_sync(self, skill_id: str) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Test cases
            cases = conn.execute(
                "SELECT * FROM validator_test_cases WHERE skill_id = ? ORDER BY created_at DESC",
                (skill_id,),
            ).fetchall()

            # Recent runs
            runs = conn.execute(
                "SELECT * FROM validator_runs WHERE skill_id = ? ORDER BY created_at DESC LIMIT 10",
                (skill_id,),
            ).fetchall()

            return {
                "skill_id": skill_id,
                "test_cases": [dict(r) for r in cases],
                "recent_runs": [dict(r) for r in runs],
            }
        finally:
            conn.close()

    # --- Test case retrieval ------------------------------------------------

    @_db_retry()
    def _get_cases_sync(self, skill_id: str) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM validator_test_cases WHERE skill_id = ?",
                (skill_id,),
            )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()


# --- Helpers ----------------------------------------------------------------


def _extract_key_terms(text: str) -> list[str]:
    """Extract meaningful key terms from an error description.

    Strips common noise words and punctuation, returns distinct terms.
    """
    noise = {"the", "a", "an", "is", "was", "are", "were", "be", "been",
             "has", "have", "had", "does", "did", "will", "would", "could",
             "should", "may", "might", "can", "shall", "to", "of", "in",
             "for", "on", "with", "at", "by", "from", "as", "or", "and",
             "not", "no", "but", "if", "then", "else", "when", "that",
             "this", "these", "those", "it", "its"}

    # Split on non-alphanumeric, filter short and noise words
    words = re.findall(r"[a-zA-Z0-9_./-]{2,}", text.lower())
    return list(dict.fromkeys(
        w for w in words if w not in noise and len(w) > 2
    ))[:5]  # Up to 5 distinct key terms
