"""SegmentStore — SQLite persistence for Segment records.

Shares the same database file as other stores but uses a separate
``segments`` table.  Uses WAL mode for concurrent reads.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

from skill_engine.pipeline.models import Segment, SegmentStats


def _db_retry(max_retries: int = 5, initial_delay: float = 0.1, backoff: float = 2.0):
    """Retry on transient SQLite errors with exponential backoff."""

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


class SegmentStore:
    """Persists Segment records to SQLite.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    # --- Lifecycle ------------------------------------------------------------

    async def initialize(self) -> None:
        """Create tables and indexes if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    transcript_path TEXT NOT NULL,
                    user_msg_index INTEGER NOT NULL,
                    user_msg TEXT NOT NULL,
                    execution_json TEXT NOT NULL DEFAULT '[]',
                    prev_id TEXT,
                    next_id TEXT,
                    stats_json TEXT NOT NULL DEFAULT '{}',
                    skills_available TEXT NOT NULL DEFAULT '[]',
                    files_modified TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(session_id, user_msg_index)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_segments_session
                ON segments(session_id, user_msg_index)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_segments_prev
                ON segments(prev_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_segments_next
                ON segments(next_id)
            """)

            conn.commit()
        finally:
            conn.close()

    # --- CRUD ----------------------------------------------------------------

    @_db_retry()
    def _save_sync(self, segment: Segment) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO segments
                   (id, session_id, transcript_path, user_msg_index, user_msg,
                    execution_json, prev_id, next_id, stats_json,
                    skills_available, files_modified, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    segment.id,
                    segment.session_id,
                    "",  # transcript_path — filled by Segmenter if available
                    segment.user_msg_index,
                    segment.user_msg,
                    segment.execution_json,
                    segment.prev_id,
                    segment.next_id,
                    segment.stats_json,
                    segment.skills_available,
                    segment.files_modified,
                    segment.created_at or datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def save(self, segment: Segment) -> None:
        """Insert or replace a segment."""
        import asyncio

        await asyncio.to_thread(self._save_sync, segment)

    @_db_retry()
    def _get_sync(self, segment_id: str) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    async def get(self, segment_id: str) -> Optional[dict]:
        import asyncio

        return await asyncio.to_thread(self._get_sync, segment_id)

    @_db_retry()
    def _get_by_session_sync(self, session_id: str) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM segments WHERE session_id = ? ORDER BY user_msg_index",
                (session_id,),
            )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    async def get_by_session(self, session_id: str) -> list[dict]:
        import asyncio

        return await asyncio.to_thread(self._get_by_session_sync, session_id)

    @_db_retry()
    def _update_next_sync(self, segment_id: str, next_id: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE segments SET next_id = ? WHERE id = ?",
                (next_id, segment_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def update_next(self, segment_id: str, next_id: str) -> None:
        """Set the next_id of a segment (completes the chain link)."""
        import asyncio

        await asyncio.to_thread(self._update_next_sync, segment_id, next_id)

    @_db_retry()
    def _update_prev_sync(self, segment_id: str, prev_id: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE segments SET prev_id = ? WHERE id = ?",
                (prev_id, segment_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def update_prev(self, segment_id: str, prev_id: str) -> None:
        """Set the prev_id of a segment."""
        import asyncio

        await asyncio.to_thread(self._update_prev_sync, segment_id, prev_id)

    async def get_chain(self, session_id: str) -> list[dict]:
        """Get all segments for a session, ordered by the linked list.

        Starts from the segment with ``prev_id IS NULL`` and follows
        ``next_id`` pointers.
        """
        rows = await self.get_by_session(session_id)
        if not rows:
            return []

        # Build lookup
        by_id: dict[str, dict] = {r["id"]: r for r in rows if r.get("id")}

        # Find head
        head = None
        for r in rows:
            if not r.get("prev_id"):
                head = r
                break
        if head is None:
            # Fallback: order by user_msg_index
            return sorted(rows, key=lambda r: r.get("user_msg_index", 0))

        # Traverse
        result = []
        visited: set[str] = set()
        current = head
        while current and current["id"] not in visited:
            result.append(current)
            visited.add(current["id"])
            nid = current.get("next_id")
            current = by_id.get(nid) if nid else None

        return result
