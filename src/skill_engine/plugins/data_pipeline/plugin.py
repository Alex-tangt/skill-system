from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from skill_engine.kernel.plugin_interface import BasePlugin
from skill_engine.kernel.models.trace import ExecutionTrace
from skill_engine.plugins.data_pipeline.models import HistoryEvent, PipelineStatus
from skill_engine.plugins.data_pipeline.extractors import build_extractor_chain, BaseExtractor
from skill_engine.plugins.data_pipeline.dedup import SHA256Dedup, BaseDedup
from skill_engine.plugins.data_pipeline.triggers import ManualTrigger, BaseTrigger


class DataPipelinePlugin(BasePlugin):
    """Internal plugin that extracts structured traces from history events.

    MCP tools exposed:
      - pipeline_run: Process pending history events into traces
      - pipeline_status: Query last pipeline run status
    """

    api_version = "0.2"

    def __init__(self, name: str = "data-pipeline", config: dict | None = None):
        super().__init__(name, config)
        self._last_status = PipelineStatus()
        self._extractors: list[BaseExtractor] = []
        self._dedup: BaseDedup = SHA256Dedup()
        self._trigger: BaseTrigger = ManualTrigger()

        # Paths from config
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

    async def shutdown(self) -> None:
        pass

    def list_mcp_tools(self) -> list[dict]:
        return [
            {
                "name": "pipeline_run",
                "description": "Process pending history events into structured execution traces.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max events to process (default 100)",
                            "default": 100,
                        },
                    },
                },
            },
            {
                "name": "pipeline_status",
                "description": "Get the last data pipeline run status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        if tool_name == "pipeline_run":
            limit = arguments.get("limit", 100)
            status = await self._run_pipeline(limit)
            return json.dumps({
                "events_processed": status.events_processed,
                "traces_created": status.traces_created,
                "errors": status.errors,
                "last_run": status.last_run,
            })
        elif tool_name == "pipeline_status":
            return json.dumps({
                "events_processed": self._last_status.events_processed,
                "traces_created": self._last_status.traces_created,
                "errors": self._last_status.errors,
                "last_run": self._last_status.last_run,
            })
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

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

        # Group events by session_id
        sessions: dict[str, list[dict]] = {}
        for row in rows:
            event = dict(row)
            sid = event.get("session_id", "unknown")
            sessions.setdefault(sid, []).append(event)

        # Build one ExecutionTrace per session
        conn = sqlite3.connect(self._history_db_path)
        for sid, events in sessions.items():
            try:
                trace = ExecutionTrace(
                    id=str(uuid.uuid4()),
                    skill_id="",  # Will be filled by extractors
                    skill_version="unknown",
                    run_id=str(uuid.uuid4()),
                    status="running",
                    input={"session_id": sid},
                    context_type="hook",
                )

                step_traces = []
                for event in events:
                    for extractor in self._extractors:
                        if extractor.can_extract(event):
                            step = extractor.extract(event, trace.id)
                            if step:
                                step_traces.append(step)
                            break

                if step_traces:
                    trace.step_traces = step_traces
                    trace.status = "succeeded"

                    # Write to TraceStore
                    await self._write_trace(trace)

                    status.traces_created += 1

                # Mark as processed
                event_ids = [e["id"] for e in events]
                conn.executemany(
                    "UPDATE history_events SET processed = 2 WHERE id = ?",
                    [(eid,) for eid in event_ids],
                )
                conn.commit()

                status.events_processed += len(events)

            except Exception as e:
                status.errors.append(f"Session {sid}: {e}")

        conn.close()
        self._last_status = status
        return status

    async def _write_trace(self, trace: ExecutionTrace) -> None:
        """Write a trace to the TraceStore."""
        from skill_engine.kernel.trace_store import TraceStore

        ts = TraceStore(self._trace_db_path)
        await ts.initialize()
        await ts.insert_trace(trace)
        for step in trace.step_traces:
            await ts.upsert_step_trace(step)
        trace.finished_at = time.time()
        await ts.update_trace(trace)
