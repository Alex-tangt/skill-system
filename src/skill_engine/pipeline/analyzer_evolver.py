"""Analyzer-Evolver orchestrator (Task 2 coroutine).

Phase A (diagnosis) → Phase B (evolution with validate→fix loop).
Phases share the same segment context.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from skill_engine.pipeline.models import ExecutionAnalysis, Segment
from skill_engine.pipeline.segment_store import SegmentStore
from skill_engine.pipeline.analysis_runner import AnalysisRunner
from skill_engine.pipeline.evolution_runner import EvolutionRunner
from skill_engine.pipeline.llm_client import LLMClient
from skill_engine.pipeline.validator import Validator

import logging

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 2


class AnalyzerEvolverRunner:
    """Orchestrate Phase A → Phase B.

    Args:
        llm_client: Shared LLM client.
        segment_store: For loading segments and chain context.
        skill_store: For loading skill content.
        pipeline_store: PipelineStore for persistence (analysis traces, etc.).
        validator: Validator instance for Phase B validate→fix loop.
        analysis_queue: asyncio.Queue of segment IDs.
        model: Override model name.
        max_concurrency: Maximum concurrent analysis tasks.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        segment_store: SegmentStore,
        skill_store: object = None,
        pipeline_store: object = None,
        validator: Validator | None = None,
        analysis_queue: asyncio.Queue | None = None,
        model: Optional[str] = None,
        max_concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._llm = llm_client
        self._segment_store = segment_store
        self._skill_store = skill_store
        self._pipeline_store = pipeline_store
        self._validator = validator
        self._analysis_queue = analysis_queue or asyncio.Queue()
        self._model = model
        self._max_concurrency = max_concurrency

        self._analysis_runner = AnalysisRunner(
            llm_client=llm_client,
            segment_store=segment_store,
            skill_store=skill_store,
            model=model,
            analysis_store=pipeline_store,
        )
        self._evolution_runner = EvolutionRunner(
            llm_client=llm_client,
            skill_store=skill_store,
            validator=validator,
            model=model,
        )

        self._running: bool = False
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # --- Main loop -----------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        logger.info(f"AnalyzerEvolver started (concurrency={self._max_concurrency})")
        while self._running:
            try:
                segment_id = await asyncio.wait_for(
                    self._analysis_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            asyncio.create_task(self._process(segment_id))

    def stop(self) -> None:
        self._running = False

    # --- Processing ----------------------------------------------------------

    async def _process(self, segment_id: str) -> None:
        async with self._semaphore:
            try:
                row = await self._segment_store.get(segment_id)
                if not row:
                    return
                segment = Segment(
                    id=row["id"], session_id=row.get("session_id", ""),
                    user_msg=row.get("user_msg", ""),
                    user_msg_index=row.get("user_msg_index", 0),
                    execution_json=row.get("execution_json", "[]"),
                    prev_id=row.get("prev_id"), next_id=row.get("next_id"),
                    stats_json=row.get("stats_json", "{}"),
                    skills_available=row.get("skills_available", "[]"),
                    files_modified=row.get("files_modified", "[]"),
                )

                # Phase A
                analysis = await self._analysis_runner.analyze(segment)

                # Persist analysis
                if self._pipeline_store:
                    await self._pipeline_store.save_analysis(
                        segment_id, analysis,
                    )

                # Phase B
                if analysis.diagnosis and "Analysis failed" not in analysis.diagnosis:
                    patch = await self._evolution_runner.evolve(analysis, segment)
                    if patch:
                        logger.info(
                            f"SkillPatch produced: {patch.skill_id} "
                            f"({patch.patch_type}) — ready to apply"
                        )
                        # In production: apply_patch(patch) + update SkillRecord
                    else:
                        logger.debug(
                            f"No valid patch for segment {segment_id[:8]}"
                        )

            except Exception as e:
                logger.error(
                    f"Analyzer-Evolver failed for {segment_id[:8]}: {e}",
                    exc_info=True,
                )

    # --- Direct API -----------------------------------------------------------

    async def analyze_and_evolve(
        self, segment_id: str,
    ) -> tuple[Optional[ExecutionAnalysis], Optional[object]]:
        """Analyze + evolve a single segment. Returns (analysis, patch)."""
        row = await self._segment_store.get(segment_id)
        if not row:
            return None, None

        segment = Segment(
            id=row["id"], session_id=row.get("session_id", ""),
            user_msg=row.get("user_msg", ""),
            user_msg_index=row.get("user_msg_index", 0),
            execution_json=row.get("execution_json", "[]"),
            prev_id=row.get("prev_id"), next_id=row.get("next_id"),
            stats_json=row.get("stats_json", "{}"),
            skills_available=row.get("skills_available", "[]"),
            files_modified=row.get("files_modified", "[]"),
        )

        analysis = await self._analysis_runner.analyze(segment)

        if self._pipeline_store:
            await self._pipeline_store.save_analysis(segment_id, analysis)

        patch = None
        if analysis.diagnosis and "Analysis failed" not in analysis.diagnosis:
            patch = await self._evolution_runner.evolve(analysis, segment)

        return analysis, patch
