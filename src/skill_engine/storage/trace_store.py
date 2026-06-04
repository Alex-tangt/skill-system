from __future__ import annotations

import json
import aiosqlite
from skill_engine.models.trace import ExecutionTrace, StepTrace


class TraceStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS execution_traces (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    status TEXT NOT NULL DEFAULT 'running',
                    input_json TEXT NOT NULL,
                    output_json TEXT,
                    error TEXT,
                    parent_run_id TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_skill
                ON execution_traces(skill_id, status)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_run_id
                ON execution_traces(run_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_status
                ON execution_traces(status, started_at)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS step_traces (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL REFERENCES execution_traces(id),
                    step_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    input_json TEXT,
                    output_json TEXT,
                    error TEXT,
                    retry_count INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_step_traces_trace
                ON step_traces(trace_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_step_traces_step
                ON step_traces(step_id, status)
            """)
            await db.commit()

    async def insert_trace(self, trace: ExecutionTrace) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO execution_traces
                   (id, run_id, skill_id, skill_version, started_at, status, input_json, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace.id,
                    trace.run_id,
                    trace.skill_id,
                    trace.skill_version,
                    trace.started_at,
                    trace.status,
                    json.dumps(trace.input),
                    trace.error,
                ),
            )
            await db.commit()

    async def update_trace(self, trace: ExecutionTrace) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE execution_traces
                   SET finished_at = ?, status = ?, output_json = ?, error = ?
                   WHERE id = ?""",
                (
                    trace.finished_at,
                    trace.status,
                    json.dumps(trace.output) if trace.output else None,
                    trace.error,
                    trace.id,
                ),
            )
            await db.commit()

    async def upsert_step_trace(self, step: StepTrace) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            existing = await db.execute(
                "SELECT id FROM step_traces WHERE id = ?", (step.id,)
            )
            row = await existing.fetchone()
            if row:
                await db.execute(
                    """UPDATE step_traces
                       SET finished_at = ?, status = ?, output_json = ?, error = ?,
                           retry_count = ?
                       WHERE id = ?""",
                    (
                        step.finished_at,
                        step.status,
                        json.dumps(step.output) if step.output is not None else None,
                        step.error,
                        step.retry_count,
                        step.id,
                    ),
                )
            else:
                await db.execute(
                    """INSERT INTO step_traces
                       (id, trace_id, step_id, step_name, started_at, status, input_json,
                        finished_at, output_json, error, retry_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        step.id,
                        step.trace_id,
                        step.step_id,
                        step.step_name,
                        step.started_at,
                        step.status,
                        json.dumps(step.input) if step.input else None,
                        step.finished_at,
                        json.dumps(step.output) if step.output is not None else None,
                        step.error,
                        step.retry_count,
                    ),
                )
            await db.commit()

    async def get_trace(self, run_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM execution_traces WHERE run_id = ?", (run_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            trace = dict(row)
            cursor = await db.execute(
                "SELECT * FROM step_traces WHERE trace_id = ? ORDER BY started_at",
                (trace["id"],),
            )
            trace["steps"] = [dict(r) for r in await cursor.fetchall()]
            return trace

    async def list_traces(
        self, skill_id: str | None = None, status: str | None = None, limit: int = 20
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM execution_traces WHERE 1=1"
            params: list = []
            if skill_id:
                query += " AND skill_id = ?"
                params.append(skill_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY started_at DESC LIMIT ?"
            params.append(limit)
            cursor = await db.execute(query, params)
            return [dict(r) for r in await cursor.fetchall()]

    async def get_error_traces(
        self, skill_id: str | None = None, limit: int = 10
    ) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = """
                SELECT et.run_id, et.skill_id, et.status, et.error as overall_error,
                       st.step_id, st.step_name, st.status as step_status,
                       st.error as step_error, st.input_json, st.retry_count,
                       CAST((st.finished_at - st.started_at) * 1000 AS INTEGER) as duration_ms
                FROM execution_traces et
                JOIN step_traces st ON et.id = st.trace_id
                WHERE et.status = 'failed' AND st.status = 'failed'
            """
            params: list = []
            if skill_id:
                query += " AND et.skill_id = ?"
                params.append(skill_id)
            query += " ORDER BY et.started_at DESC LIMIT ?"
            params.append(limit)
            cursor = await db.execute(query, params)
            return [dict(r) for r in await cursor.fetchall()]
