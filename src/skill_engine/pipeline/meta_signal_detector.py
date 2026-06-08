"""Meta Signal Detector — monitors analysis quality and optimizes the
analysis skill itself.

This is Task 4 of the pipeline: a low-frequency background task that
scans ``analysis_traces`` for signals of analysis skill degradation,
then modifies the analysis skill's SKILL.md when problems are found.

Crucially, this does NOT produce new segments — it directly modifies
the analysis skill file.  This breaks the recursion loop.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)

# How often to scan for signals
DEFAULT_SCAN_INTERVAL = 600  # seconds (10 minutes)

# Thresholds
FORMAT_ERROR_RATE_MAX = 0.3  # If > 30% analyses fail to parse, fix format prompt
CONSECUTIVE_LOW_SIGNAL = 5  # If 5+ consecutive analyses have no suggestions
DEGRADATION_WINDOW = 20  # Look at the last N analyses for degradation checks


def _db_retry(max_retries: int = 3, initial_delay: float = 0.1, backoff: float = 2.0):
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


@dataclass
class MetaSignal:
    """A signal that the analysis skill needs optimization."""
    signal_type: str  # "format_error_rate" | "low_confidence_surge" | "no_suggestions" | "regression"
    description: str
    evidence: dict
    timestamp: str


class MetaSignalDetector:
    """Detect analysis skill degradation and trigger optimization.

    Args:
        db_path: Path to the SQLite database with analysis_traces table.
        analysis_skill_path: Path to the analysis skill's SKILL.md file.
        llm_client: LLM client for optimizing the analysis skill (optional).
        scan_interval: Seconds between scans.
    """

    def __init__(
        self,
        db_path: str,
        analysis_skill_path: str | None = None,
        llm_client: Any = None,
        scan_interval: float = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        self._db_path = db_path
        self._analysis_skill_path = analysis_skill_path
        self._llm = llm_client
        self._scan_interval = scan_interval
        self._running: bool = False

    # --- Main loop -----------------------------------------------------------

    async def start(self) -> None:
        """Start periodic signal scanning."""
        self._running = True
        logger.info(
            f"MetaSignalDetector started (interval={self._scan_interval}s)"
        )
        while self._running:
            try:
                signals = await self.scan()
                for signal in signals:
                    await self.handle_signal(signal)
            except Exception as e:
                logger.error(f"MetaSignalDetector scan error: {e}")
            await asyncio.sleep(self._scan_interval)

    def stop(self) -> None:
        self._running = False

    async def scan(self) -> list[MetaSignal]:
        """Run one scan cycle. Returns signals detected."""
        import asyncio as _asyncio
        return await _asyncio.to_thread(self._scan_impl)

    # --- Signal detection ----------------------------------------------------

    @_db_retry()
    def _scan_impl(self) -> list[MetaSignal]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        signals: list[MetaSignal] = []
        now = datetime.now(timezone.utc).isoformat()

        try:
            # Check if analysis_traces table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_traces'"
            )
            if not cursor.fetchone():
                logger.debug("analysis_traces table not found — skipping scan")
                return []

            # 1. Format error rate
            rows = conn.execute("""
                SELECT status, COUNT(*) as cnt
                FROM analysis_traces
                WHERE created_at > datetime('now', ?)
                GROUP BY status
            """, (f"-{DEGRADATION_WINDOW // 60} minutes",)).fetchall()

            total = sum(r["cnt"] for r in rows)
            if total > 0:
                parse_failed = sum(r["cnt"] for r in rows if r["status"] in ("error", "parse_failed"))
                error_rate = parse_failed / total
                if error_rate > FORMAT_ERROR_RATE_MAX:
                    signals.append(MetaSignal(
                        signal_type="format_error_rate",
                        description=(
                            f"Format error rate is {error_rate:.0%} "
                            f"({parse_failed}/{total} analyses failed to parse), "
                            f"exceeding threshold of {FORMAT_ERROR_RATE_MAX:.0%}"
                        ),
                        evidence={"error_rate": error_rate, "total": total, "failed": parse_failed},
                        timestamp=now,
                    ))

            # 2. Consecutive low-signal analyses
            rows = conn.execute("""
                SELECT response_json, status
                FROM analysis_traces
                WHERE created_at > datetime('now', ?)
                ORDER BY created_at DESC
                LIMIT ?
            """, (f"-{DEGRADATION_WINDOW // 60} minutes", CONSECUTIVE_LOW_SIGNAL)).fetchall()

            if len(rows) >= CONSECUTIVE_LOW_SIGNAL:
                all_low_signal = True
                for row in rows:
                    if row["status"] != "success":
                        continue
                    try:
                        resp = json.loads(row["response_json"] or "{}")
                        suggestions = resp.get("evolution_suggestions", [])
                        if suggestions:
                            all_low_signal = False
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass

                if all_low_signal:
                    signals.append(MetaSignal(
                        signal_type="no_suggestions",
                        description=(
                            f"Last {CONSECUTIVE_LOW_SIGNAL} analyses produced "
                            f"no improvement suggestions — analysis may be too conservative"
                        ),
                        evidence={"consecutive_count": CONSECUTIVE_LOW_SIGNAL},
                        timestamp=now,
                    ))

        finally:
            conn.close()

        if signals:
            logger.info(
                f"MetaSignalDetector: {len(signals)} signal(s) detected: "
                f"{[s.signal_type for s in signals]}"
            )

        return signals

    # --- Signal handling -----------------------------------------------------

    async def handle_signal(self, signal: MetaSignal) -> None:
        """Handle a detected signal by optimizing the analysis skill."""
        if not self._analysis_skill_path:
            logger.warning(
                f"Signal detected but no analysis_skill_path configured: "
                f"{signal.signal_type}"
            )
            return

        skill_path = Path(self._analysis_skill_path)
        if not skill_path.exists():
            logger.warning(f"Analysis skill path not found: {skill_path}")
            return

        if not self._llm:
            logger.info(
                f"Signal detected ({signal.signal_type}) but no LLM client "
                f"available — logging for manual review"
            )
            return

        logger.info(
            f"Optimizing analysis skill due to signal: {signal.signal_type}"
        )

        try:
            old_content = skill_path.read_text(encoding="utf-8")
            prompt = self._build_optimization_prompt(
                signal, old_content, str(skill_path)
            )

            result = await self._llm.complete(
                messages=[{"role": "user", "content": prompt}],
            )
            new_content = result.get("message", {}).get("content", "")

            if not new_content or len(new_content) < 100:
                logger.warning("Meta optimizer produced empty/short response")
                return

            # Extract just the SKILL.md content (strip markdown fences if present)
            import re
            fence_match = re.search(
                r"```(?:markdown)?\s*\n(.*?)\n```", new_content, re.DOTALL
            )
            if fence_match:
                new_content = fence_match.group(1)

            # Safety: verify it has basic frontmatter
            if "---" in new_content and "name:" in new_content:
                # Backup old version
                backup_path = skill_path.with_suffix(".md.backup")
                backup_path.write_text(old_content, encoding="utf-8")
                skill_path.write_text(new_content, encoding="utf-8")
                logger.info(
                    f"Analysis skill optimized ({signal.signal_type}). "
                    f"Backup saved to {backup_path}"
                )
            else:
                logger.warning(
                    "Meta optimizer output lacks frontmatter — not applying"
                )

        except Exception as e:
            logger.error(f"Meta optimization failed: {e}")

    @staticmethod
    def _build_optimization_prompt(
        signal: MetaSignal,
        current_skill: str,
        skill_path: str,
    ) -> str:
        """Build the optimization prompt for the analysis skill."""
        signal_descriptions = {
            "format_error_rate": (
                "The analysis LLM is producing malformed JSON that fails to parse. "
                "The output format instructions need to be clearer and more "
                "explicit about the required JSON structure."
            ),
            "no_suggestions": (
                "The analysis LLM is not finding improvement opportunities even "
                "when they likely exist. The analysis prompt should encourage "
                "more thorough evaluation and specific, actionable suggestions."
            ),
            "low_confidence_surge": (
                "Many analyses are reporting low confidence. The analysis "
                "prompt should provide better guidance on how to handle "
                "ambiguous situations."
            ),
            "regression": (
                "Skills evolved based on analysis suggestions are performing "
                "worse than before. The analysis may be misidentifying problems "
                "or suggesting harmful changes."
            ),
        }

        return f"""## Meta-Optimization Task

Optimize the following analysis skill's SKILL.md to address a detected issue.

**Signal:** {signal.signal_type}
**Description:** {signal_descriptions.get(signal.signal_type, signal.description)}
**Evidence:** {json.dumps(signal.evidence, indent=2)}

**Current Analysis Skill ({skill_path}):**

{current_skill[:10000]}

## Instructions

Update the skill to fix the identified issue. Be MINIMAL — only change
what's necessary to address the signal. Preserve the original structure.

Output the COMPLETE updated SKILL.md content.
"""
