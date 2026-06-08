"""PipelineStore — unified SQLite persistence for the entire pipeline.

Composes SegmentStore (segments table) and adds:
  - execution_analyses — Phase A output
  - analysis_traces — Analysis LLM self-recording
  - skill_records — Skill quality counters (for Metric Monitor)
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Optional

from skill_engine.pipeline.segment_store import SegmentStore
from skill_engine.pipeline.models import ExecutionAnalysis

import logging

logger = logging.getLogger(__name__)


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


class PipelineStore:
    """Unified persistence for pipeline data.

    Args:
        db_path: Path to SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.segments = SegmentStore(db_path)

    # --- Lifecycle ------------------------------------------------------------

    async def initialize(self) -> None:
        """Create all tables and indexes."""
        await self.segments.initialize()
        await self._create_tables()

    async def _create_tables(self) -> None:
        import asyncio as _asyncio
        await _asyncio.to_thread(self._create_tables_sync)

    @_db_retry()
    def _create_tables_sync(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            # Execution analyses (Phase A output)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_analyses (
                    id TEXT PRIMARY KEY,
                    segment_id TEXT NOT NULL,
                    task_completed INTEGER DEFAULT 0,
                    execution_note TEXT DEFAULT '',
                    skill_judgments_json TEXT DEFAULT '[]',
                    evolution_suggestions_json TEXT DEFAULT '[]',
                    tool_issues_json TEXT DEFAULT '[]',
                    analyzed_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (segment_id) REFERENCES segments(id)
                )
            """)

            # Analysis traces (analysis LLM self-recording)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analysis_traces (
                    id TEXT PRIMARY KEY,
                    analysis_id TEXT,
                    segment_id TEXT,
                    llm_model TEXT DEFAULT '',
                    prompt_json TEXT DEFAULT '',
                    response_json TEXT DEFAULT '',
                    tool_calls_json TEXT DEFAULT '',
                    tokens_used INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'success',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            # Skill records (quality counters for Metric Monitor)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_records (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    total_selections INTEGER DEFAULT 0,
                    total_applied INTEGER DEFAULT 0,
                    total_completions INTEGER DEFAULT 0,
                    total_fallbacks INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    last_updated TEXT DEFAULT (datetime('now'))
                )
            """)

            # Indexes
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_analyses_segment
                ON execution_analyses(segment_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_segment
                ON analysis_traces(segment_id, created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_status
                ON analysis_traces(status, created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_skill_records_name
                ON skill_records(name)
            """)

            conn.commit()
        finally:
            conn.close()

    # --- Execution Analyses --------------------------------------------------

    @_db_retry()
    def _save_analysis_sync(
        self, analysis_id: str, segment_id: str, analysis: ExecutionAnalysis
    ) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO execution_analyses
                   (id, segment_id, task_completed, execution_note,
                    skill_judgments_json, evolution_suggestions_json,
                    tool_issues_json, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    analysis_id,
                    segment_id,
                    1 if analysis.task_completed else 0,
                    analysis.execution_note,
                    json.dumps([j.to_dict() for j in analysis.skill_judgments]),
                    json.dumps([s.to_dict() for s in analysis.evolution_suggestions]),
                    json.dumps(analysis.tool_issues),
                    analysis.analyzed_at or datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def save_analysis(
        self, segment_id: str, analysis: ExecutionAnalysis
    ) -> str:
        """Persist an ExecutionAnalysis. Returns the analysis ID."""
        import asyncio as _asyncio

        analysis_id = str(uuid.uuid4())
        await _asyncio.to_thread(
            self._save_analysis_sync, analysis_id, segment_id, analysis
        )
        return analysis_id

    @_db_retry()
    def _get_analysis_sync(self, segment_id: str) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM execution_analyses WHERE segment_id = ? ORDER BY analyzed_at DESC LIMIT 1",
                (segment_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    async def get_analysis(self, segment_id: str) -> Optional[dict]:
        import asyncio as _asyncio
        return await _asyncio.to_thread(self._get_analysis_sync, segment_id)

    # --- Analysis Traces -----------------------------------------------------

    @_db_retry()
    def _save_trace_sync(
        self,
        analysis_id: str,
        segment_id: str,
        model: str,
        prompt: str,
        response: str,
        tool_calls: str,
        duration_ms: int,
        status: str,
    ) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            trace_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO analysis_traces
                   (id, analysis_id, segment_id, llm_model, prompt_json,
                    response_json, tool_calls_json, duration_ms, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace_id,
                    analysis_id,
                    segment_id,
                    model,
                    prompt,
                    response,
                    tool_calls,
                    duration_ms,
                    status,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def save_trace(
        self,
        analysis_id: str,
        segment_id: str,
        model: str = "",
        prompt: str = "",
        response: str = "",
        tool_calls: str = "",
        duration_ms: int = 0,
        status: str = "success",
    ) -> None:
        """Record an analysis LLM execution trace."""
        import asyncio as _asyncio

        await _asyncio.to_thread(
            self._save_trace_sync,
            analysis_id,
            segment_id,
            model,
            prompt,
            response,
            tool_calls,
            duration_ms,
            status,
        )

    @_db_retry()
    def _get_recent_traces_sync(self, limit: int = 20) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM analysis_traces ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    async def get_recent_traces(self, limit: int = 20) -> list[dict]:
        import asyncio as _asyncio
        return await _asyncio.to_thread(self._get_recent_traces_sync, limit)

    # --- Skill Records -------------------------------------------------------

    @_db_retry()
    def _upsert_skill_record_sync(
        self, skill_id: str, name: str, selections: int = 0,
        applied: int = 0, completions: int = 0, fallbacks: int = 0,
    ) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            existing = conn.execute(
                "SELECT skill_id FROM skill_records WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE skill_records
                       SET total_selections = total_selections + ?,
                           total_applied = total_applied + ?,
                           total_completions = total_completions + ?,
                           total_fallbacks = total_fallbacks + ?,
                           last_updated = ?
                       WHERE skill_id = ?""",
                    (selections, applied, completions, fallbacks,
                     datetime.now(timezone.utc).isoformat(), skill_id),
                )
            else:
                conn.execute(
                    """INSERT INTO skill_records
                       (skill_id, name, total_selections, total_applied,
                        total_completions, total_fallbacks, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (skill_id, name, selections, applied, completions, fallbacks,
                     datetime.now(timezone.utc).isoformat()),
                )
            conn.commit()
        finally:
            conn.close()

    async def update_skill_record(
        self,
        skill_id: str,
        name: str = "",
        selections: int = 0,
        applied: int = 0,
        completions: int = 0,
        fallbacks: int = 0,
    ) -> None:
        """Update skill quality counters (incremental)."""
        import asyncio as _asyncio

        await _asyncio.to_thread(
            self._upsert_skill_record_sync,
            skill_id, name or skill_id,
            selections, applied, completions, fallbacks,
        )

    @_db_retry()
    def _get_skill_records_sync(self) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM skill_records WHERE is_active = 1"
            )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

    async def get_skill_records(self) -> list[dict]:
        import asyncio as _asyncio
        return await _asyncio.to_thread(self._get_skill_records_sync)
