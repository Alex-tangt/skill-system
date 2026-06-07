from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from skill_engine.kernel.models.trace import ExecutionTrace
from skill_engine.plugins.data_pipeline.models import HistoryEvent, PipelineStatus
from skill_engine.plugins.data_pipeline.extractors import build_extractor_chain, BaseExtractor
from skill_engine.plugins.data_pipeline.dedup import SHA256Dedup, BaseDedup


class DataPipelinePlugin:
    """Pipeline that extracts structured traces from history events.

    Runs as a direct Python module inside the kernel process.
    Methods are called directly — no MCP JSON round-trip.
    """

    def __init__(self, config: dict | None = None):
        self._last_status = PipelineStatus()
        self._extractors: list[BaseExtractor] = []
        self._dedup: BaseDedup = SHA256Dedup()
        self._history_db_path: str = config.get("history_db_path", "./traces/history.db") if config else "./traces/history.db"
        self._trace_db_path: str = config.get("trace_db_path", "./traces/traces.db") if config else "./traces/traces.db"

    async def initialize(self) -> None:
        self._extractors = build_extractor_chain()

    async def health_check(self) -> bool:
        try:
            conn = sqlite3.connect(self._history_db_path)
            conn.execute("SELECT 1 FROM history_events LIMIT 1")
            conn.close()
            return True
        except Exception:
            return False

    @property
    def last_status(self) -> PipelineStatus:
        return self._last_status

    async def run(self, limit: int = 100) -> dict:
        """Process pending history events into traces. Returns status dict."""
        status = await self._run_pipeline(limit)
        return {
            "events_processed": status.events_processed,
            "traces_created": status.traces_created,
            "errors": status.errors,
            "last_run": status.last_run,
        }

    def status(self) -> dict:
        """Return last pipeline run status."""
        return {
            "events_processed": self._last_status.events_processed,
            "traces_created": self._last_status.traces_created,
            "errors": self._last_status.errors,
            "last_run": self._last_status.last_run,
        }

    async def _run_pipeline(self, limit: int = 100) -> PipelineStatus:
        status = PipelineStatus()
        status.last_run = datetime.now(timezone.utc).isoformat()

        try:
            conn = sqlite3.connect(self._history_db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM history_events WHERE processed = 0 ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
        except sqlite3.Error as e:
            status.errors.append(f"History DB error: {e}")
            self._last_status = status
            return status

        if not rows:
            self._last_status = status
            return status

        sessions: dict[str, list[dict]] = {}
        for row in rows:
            event = dict(row)
            sid = event.get("session_id", "unknown")
            sessions.setdefault(sid, []).append(event)

        from skill_engine.kernel.trace_store import TraceStore
        ts = TraceStore(self._trace_db_path)
        await ts.initialize()

        conn = sqlite3.connect(self._history_db_path)
        for sid, events in sessions.items():
            try:
                existing_trace = await self._find_trace_by_session(ts, sid)
                if existing_trace:
                    trace_id = existing_trace["id"]
                    trace = ExecutionTrace(
                        id=trace_id,
                        skill_id=existing_trace.get("skill_id", ""),
                        skill_version=existing_trace.get("skill_version", "unknown"),
                        run_id=existing_trace["run_id"],
                        status=existing_trace.get("status", "running"),
                        input=json.loads(existing_trace.get("input_json", "{}")),
                        context_type="hook",
                    )
                    existing_hashes = {
                        s.get("context_ref", "") for s in existing_trace.get("steps", [])
                    }
                else:
                    trace = ExecutionTrace(
                        id=str(uuid.uuid4()),
                        skill_id="",
                        skill_version="unknown",
                        run_id=str(uuid.uuid4()),
                        status="running",
                        input={"session_id": sid},
                        context_type="hook",
                    )
                    existing_hashes = set()

                step_traces = []
                new_events = []
                for event in events:
                    if event.get("dedup_hash", "") in existing_hashes:
                        continue
                    for extractor in self._extractors:
                        if extractor.can_extract(event):
                            step = extractor.extract(event, trace.id)
                            if step:
                                step.context_ref = event.get("dedup_hash", "")
                                step_traces.append(step)
                            break
                    new_events.append(event)

                if step_traces:
                    trace.step_traces = step_traces
                    trace.status = "succeeded"
                    if existing_trace:
                        await self._append_steps(ts, trace)
                    else:
                        await self._write_trace(ts, trace)
                    status.traces_created += 1

                event_ids = [e["id"] for e in new_events]
                if event_ids:
                    conn.executemany(
                        "UPDATE history_events SET processed = 2 WHERE id = ?",
                        [(eid,) for eid in event_ids],
                    )
                    conn.commit()

                status.events_processed += len(new_events)

            except Exception as e:
                status.errors.append(f"Session {sid}: {e}")

        conn.close()
        self._last_status = status
        return status

    async def _find_trace_by_session(self, ts, session_id: str) -> dict | None:
        traces = await ts.list_traces(limit=50)
        for t in traces:
            try:
                inp = json.loads(t.get("input_json", "{}")) if isinstance(t.get("input_json"), str) else t.get("input_json", {})
                if inp.get("session_id") == session_id:
                    return await ts.get_trace(t["run_id"])
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    async def _write_trace(self, ts, trace: ExecutionTrace) -> None:
        trace.started_at = time.time()
        await ts.insert_trace(trace)
        for step in trace.step_traces:
            await ts.upsert_step_trace(step)
        trace.finished_at = time.time()
        await ts.update_trace(trace)

    async def _append_steps(self, ts, trace: ExecutionTrace) -> None:
        for step in trace.step_traces:
            await ts.upsert_step_trace(step)
        trace.finished_at = time.time()
        await ts.update_trace(trace)
