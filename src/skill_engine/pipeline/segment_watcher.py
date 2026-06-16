"""Segment Watcher — monitors transcript for new user messages and triggers
real-time segmentation.

This is Task 1 of the pipeline async design.  It polls the transcript
file for new entries and creates Segments as new user messages arrive.

When a new user message creates a Segment₃, Segment₂'s next_id is
automatically completed → Segment₂ is pushed to the analysis queue.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

from skill_engine.pipeline.transcript_reader import TranscriptReader
from skill_engine.pipeline.segmenter import Segmenter
from skill_engine.pipeline.segment_store import SegmentStore
import logging

logger = logging.getLogger(__name__)

# How often to poll for new transcript entries (seconds)
POLL_INTERVAL = 2.0


class SegmentWatcher:
    """Watch a transcript file and create Segments as user messages arrive.

    Args:
        store: SegmentStore for persisting segments.
        analysis_queue: asyncio.Queue to push completed segments into.
        cwd: Working directory (for transcript path resolution).
        poll_interval: Seconds between transcript polls.
    """

    def __init__(
        self,
        store: SegmentStore,
        analysis_queue: asyncio.Queue,
        cwd: str | None = None,
        poll_interval: float = POLL_INTERVAL,
    ) -> None:
        self._store = store
        self._queue = analysis_queue
        self._cwd = cwd or os.getenv("PWD", os.getcwd())
        self._poll_interval = poll_interval
        self._last_user_msg_count: int = 0
        self._session_id: str = ""
        self._running: bool = False

    # --- Main watch loop -----------------------------------------------------

    async def watch(self, session_id: str) -> None:
        """Start watching a session's transcript.

        Runs until :meth:`stop` is called or the session ends
        (transcript stops growing for an extended period).
        """
        self._session_id = session_id
        self._running = True

        transcript_path = TranscriptReader.resolve_path(session_id, self._cwd)

        logger.info(
            f"SegmentWatcher started: session={session_id}, "
            f"transcript={transcript_path}"
        )

        while self._running:
            try:
                await self._poll(transcript_path)
            except FileNotFoundError:
                # Transcript not yet created — wait and retry
                logger.debug(f"Waiting for transcript: {transcript_path}")
            except Exception as e:
                logger.error(f"SegmentWatcher error: {e}")

            await asyncio.sleep(self._poll_interval)

        # Session ended — push the last segment if it exists
        await self._finalize_last_segment()

    def stop(self) -> None:
        """Signal the watcher to stop after the next poll cycle."""
        self._running = False

    # --- Poll logic ----------------------------------------------------------

    async def _poll(self, transcript_path: Path) -> None:
        """Check for new user messages in the transcript (incremental)."""
        if not transcript_path.exists():
            return

        reader = TranscriptReader(str(transcript_path))
        current_count = reader.count_user_messages()

        if current_count == self._last_user_msg_count:
            return

        if current_count < self._last_user_msg_count:
            logger.warning("User message count decreased — transcript may have rotated")
            self._last_user_msg_count = 0

        # Incremental: only segment new user messages
        segmenter = Segmenter(reader)
        new_segments = segmenter.segment_from(self._last_user_msg_count)

        if not new_segments:
            return

        # Persist new segments and link to existing chain
        new_count = 0
        for seg in new_segments:
            existing = await self._store.get(seg.id)
            if existing:
                if seg.next_id and seg.next_id != existing.get("next_id"):
                    await self._store.update_next(seg.id, seg.next_id)
                continue

            await self._store.save(seg)
            new_count += 1

            # Link to previous segment if it exists
            if seg.prev_id is None and self._last_user_msg_count > 0:
                # Find the last segment from previous batch
                prev_rows = await self._store.get_by_session(self._session_id)
                if prev_rows:
                    prev_sorted = sorted(prev_rows, key=lambda r: r.get("user_msg_index", 0))
                    last_prev = prev_sorted[-1] if prev_sorted else None
                    if last_prev and last_prev["id"] != seg.id:
                        seg.prev_id = last_prev["id"]
                        await self._store.update_prev(seg.id, last_prev["id"])
                        await self._store.update_next(last_prev["id"], seg.id)
                        # Push the now-completed previous segment
                        await self._queue.put(last_prev["id"])
                        logger.info(f"Segment {last_prev['id'][:8]} ready (chain linked)")

            # If this segment has a next segment already, push it
            if seg.has_next:
                if seg.prev_id:
                    prev_row = await self._store.get(seg.prev_id)
                    if prev_row and not prev_row.get("next_id"):
                        await self._store.update_next(seg.prev_id, seg.id)
                    await self._queue.put(seg.prev_id)
                    logger.info(f"Segment {seg.prev_id[:8]} ready for analysis")

        self._last_user_msg_count = current_count

        if new_count > 0:
            logger.info(f"SegmentWatcher: {new_count} new segment(s)")

    async def _finalize_last_segment(self) -> None:
        """Push the last segment (no next_id) for analysis as session ends."""
        try:
            rows = await self._store.get_by_session(self._session_id)
        except Exception:
            return

        if not rows:
            return

        # Find the tail (no next_id)
        by_id = {r["id"]: r for r in rows if r.get("id")}
        for row in rows:
            if not row.get("next_id"):
                # Check if already analyzed
                last_id = row["id"]
                await self._queue.put(last_id)
                logger.info(
                    f"Final segment {last_id[:8]} pushed for analysis "
                    f"(session end)"
                )
                break
