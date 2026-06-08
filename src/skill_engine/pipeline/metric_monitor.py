"""Metric Monitor — scans SkillRecord health indicators and pushes alerts
into the analysis queue.

Pure SQL queries, zero LLM calls.  This is a signal source, not an
analysis engine.  When a skill's metrics degrade, it constructs a
virtual analysis request and pushes it into the same queue that the
Segment Watcher uses.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Optional

from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_SCAN_INTERVAL = 300  # seconds (5 minutes)
COMPLETION_RATE_MIN = 0.3  # Below this → alert
FALLBACK_RATE_MAX = 0.5  # Above this → alert
MIN_SELECTIONS = 5  # Minimum selections before alerting (avoid noise)


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
class MetricAlert:
    """An alert produced by the Metric Monitor."""
    alert_id: str
    skill_id: str
    skill_name: str
    reason: str
    metrics: dict
    timestamp: str


class MetricMonitor:
    """Scans skill quality metrics and generates alerts.

    Alerts are pushed into the analysis queue as virtual segments,
    allowing the Analyzer-Evolver to process them using the same
    pipeline as real user-driven segments.

    Args:
        db_path: Path to the SQLite database with skill_records table.
        analysis_queue: asyncio.Queue to push virtual segment IDs into.
        segment_store: SegmentStore for persisting virtual segments.
        scan_interval: Seconds between scans.
    """

    def __init__(
        self,
        db_path: str,
        analysis_queue: asyncio.Queue,
        segment_store: Any = None,
        scan_interval: float = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        self._db_path = db_path
        self._queue = analysis_queue
        self._segment_store = segment_store
        self._scan_interval = scan_interval
        self._running: bool = False

    async def start(self) -> None:
        """Start periodic scanning."""
        self._running = True
        logger.info(
            f"MetricMonitor started (interval={self._scan_interval}s)"
        )
        while self._running:
            try:
                await self.scan()
            except Exception as e:
                logger.error(f"MetricMonitor scan error: {e}")
            await asyncio.sleep(self._scan_interval)

    def stop(self) -> None:
        self._running = False

    async def scan(self) -> list[MetricAlert]:
        """Run one scan cycle. Returns any alerts generated."""
        alerts = await self._scan_sync()
        for alert in alerts:
            await self._push_alert(alert)
        return alerts

    async def _scan_sync(self) -> list[MetricAlert]:
        """Synchronous scan logic, run in executor."""
        import asyncio as _asyncio
        return await _asyncio.to_thread(self._scan_impl)

    @_db_retry()
    def _scan_impl(self) -> list[MetricAlert]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Check if skill_records table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_records'"
            )
            if not cursor.fetchone():
                logger.debug("skill_records table not found — skipping MetricMonitor scan")
                return []

            rows = conn.execute("""
                SELECT skill_id, name,
                       total_selections, total_applied, total_completions, total_fallbacks
                FROM skill_records
                WHERE is_active = 1 AND total_selections >= ?
            """, (MIN_SELECTIONS,)).fetchall()

            alerts: list[MetricAlert] = []
            now = datetime.now(timezone.utc).isoformat()

            for row in rows:
                skill_id = row["skill_id"]
                name = row["name"]
                selections = row["total_selections"] or 0
                applied = row["total_applied"] or 0
                completions = row["total_completions"] or 0
                fallbacks = row["total_fallbacks"] or 0

                applied_rate = applied / selections if selections > 0 else 1.0
                completion_rate = completions / applied if applied > 0 else 0.0
                fallback_rate = fallbacks / selections if selections > 0 else 0.0

                reason = None
                if completion_rate < COMPLETION_RATE_MIN:
                    reason = (
                        f"Skill '{name}' completion_rate={completion_rate:.2f} "
                        f"(below {COMPLETION_RATE_MIN}), "
                        f"selections={selections}, applied={applied}"
                    )
                elif fallback_rate > FALLBACK_RATE_MAX:
                    reason = (
                        f"Skill '{name}' fallback_rate={fallback_rate:.2f} "
                        f"(above {FALLBACK_RATE_MAX}), "
                        f"selections={selections}, fallbacks={fallbacks}"
                    )
                elif applied_rate < 0.3 and selections >= 10:
                    reason = (
                        f"Skill '{name}' applied_rate={applied_rate:.2f} "
                        f"(very low), selections={selections}, applied={applied}"
                    )

                if reason:
                    alert = MetricAlert(
                        alert_id=f"metric-{skill_id}-{int(time.time())}",
                        skill_id=skill_id,
                        skill_name=name,
                        reason=reason,
                        metrics={
                            "applied_rate": applied_rate,
                            "completion_rate": completion_rate,
                            "fallback_rate": fallback_rate,
                            "total_selections": selections,
                            "total_applied": applied,
                            "total_completions": completions,
                            "total_fallbacks": fallbacks,
                        },
                        timestamp=now,
                    )
                    alerts.append(alert)

            if alerts:
                logger.info(f"MetricMonitor: {len(alerts)} alert(s) generated")
            return alerts

        finally:
            conn.close()

    async def _push_alert(self, alert: MetricAlert) -> None:
        """Push an alert into the analysis queue as a virtual segment."""
        import uuid
        import json

        if self._segment_store:
            from skill_engine.pipeline.models import Segment, SegmentStats

            virtual_id = str(uuid.uuid4())
            stats = SegmentStats(
                status=f"metric_alert:{alert.reason[:200]}",
                skills_referenced=[alert.skill_name],
            )
            segment = Segment(
                id=virtual_id,
                session_id=f"metric-monitor-{alert.timestamp[:10]}",
                user_msg=f"Metric alert: {alert.reason}",
                user_msg_index=0,
                execution_json=json.dumps({"alert": alert.__dict__}),
                stats_json=stats.to_json(),
                skills_available=json.dumps([alert.skill_name]),
            )
            await self._segment_store.save(segment)

        await self._queue.put(virtual_id if self._segment_store else alert.alert_id)
        logger.info(f"Metric alert pushed to analysis queue: {alert.skill_name}")
