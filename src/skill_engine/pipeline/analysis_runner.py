"""Phase A: Analysis Runner.

Executes the LLM analysis of a segment — builds the prompt, calls the
LLM, and returns a natural language diagnosis (not JSON).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

from skill_engine.pipeline.models import ExecutionAnalysis, Segment, SegmentStats
from skill_engine.pipeline.analysis_prompt import AnalysisPromptBuilder
from skill_engine.pipeline.llm_client import LLMClient
from skill_engine.pipeline.segment_store import SegmentStore

import logging

logger = logging.getLogger(__name__)


class AnalysisRunner:
    """Run Phase A analysis of a segment.

    Args:
        llm_client: LLM client.
        segment_store: For traversing the segment chain.
        skill_store: For loading skill content.
        model: Override model name.
        analysis_store: For persisting analysis traces (optional).
    """

    def __init__(
        self,
        llm_client: LLMClient,
        segment_store: SegmentStore,
        skill_store: Any = None,
        model: Optional[str] = None,
        analysis_store: Any = None,
    ) -> None:
        self._llm = llm_client
        self._segment_store = segment_store
        self._model = model
        self._analysis_store = analysis_store
        self._prompt_builder = AnalysisPromptBuilder(skill_store=skill_store)

    async def analyze(self, segment: Segment) -> ExecutionAnalysis:
        """Run Phase A and return a natural language diagnosis."""
        start_time = time.time()

        # Load chain context
        prev_msg, next_msg = await self._load_chain_context(segment)

        # Build prompt
        prompt = await self._prompt_builder.build(segment, prev_msg, next_msg)

        # Call LLM with retry
        diagnosis = ""
        last_error = ""
        for attempt in range(3):
            try:
                result = await self._llm.complete(
                    messages=[{"role": "user", "content": prompt}],
                    model=self._model,
                )
                diagnosis = result.get("message", {}).get("content", "")
                if diagnosis:
                    break
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Phase A LLM attempt {attempt+1}/3 failed: {e}"
                )
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))

        if not diagnosis:
            diagnosis = f"Analysis failed after 3 attempts: {last_error}"

        # Extract error summary mechanically from execution
        error_summary = self._extract_error_summary(segment)

        analysis = ExecutionAnalysis(
            segment_id=segment.id,
            diagnosis=diagnosis,
            error_summary=error_summary,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
        )

        # Record trace
        if self._analysis_store:
            await self._record_trace(
                segment_id=segment.id,
                prompt=prompt,
                diagnosis=diagnosis,
                duration_ms=int((time.time() - start_time) * 1000),
                model=self._model or "unknown",
            )

        logger.info(
            f"Phase A complete for {segment.id[:8]}: "
            f"diagnosis={len(diagnosis)} chars, errors={len(error_summary)}"
        )
        return analysis

    async def _load_chain_context(self, segment: Segment) -> tuple[str, str]:
        prev_msg = "(none)"
        next_msg = "(none)"
        if segment.prev_id:
            row = await self._segment_store.get(segment.prev_id)
            if row:
                prev_msg = row.get("user_msg", "(none)")
        if segment.next_id:
            row = await self._segment_store.get(segment.next_id)
            if row:
                next_msg = row.get("user_msg", "(none)")
        return prev_msg, next_msg

    @staticmethod
    def _extract_error_summary(segment: Segment) -> list[str]:
        """Mechanically extract error snippets from execution."""
        import json as _json
        errors: list[str] = []
        try:
            entries = _json.loads(segment.execution_json)
        except _json.JSONDecodeError:
            return errors
        for entry in entries:
            if entry.get("is_tool_result"):
                content = entry.get("content", "")
                if _is_error(content):
                    errors.append(content[:200])
        return errors[:5]  # Max 5 error snippets

    async def _record_trace(
        self, segment_id: str, prompt: str, diagnosis: str,
        duration_ms: int, model: str,
    ) -> None:
        if not self._analysis_store:
            return
        try:
            await self._analysis_store.save_trace(
                analysis_id=segment_id,
                segment_id=segment_id,
                model=model,
                prompt=prompt,
                response=diagnosis,
                tool_calls="[]",
                duration_ms=duration_ms,
                status="success",
            )
        except Exception as e:
            logger.debug(f"Failed to record analysis trace: {e}")


def _is_error(text: str) -> bool:
    if not text:
        return False
    head = text[:200].lower()
    return any(kw in head for kw in ("error", "failed", "traceback", "exception"))
