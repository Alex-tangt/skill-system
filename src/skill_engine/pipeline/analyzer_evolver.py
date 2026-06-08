"""Analyzer-Evolver orchestrator (Task 2 coroutine).

Consumes the analysis queue and orchestrates Phase A (analysis)
followed by Phase B (evolution).  Phase A and Phase B share the
same segment context to avoid information loss between analysis
conclusions and patch generation.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from skill_engine.pipeline.models import ExecutionAnalysis, Segment
from skill_engine.pipeline.segment_store import SegmentStore
from skill_engine.pipeline.analysis_runner import AnalysisRunner
from skill_engine.pipeline.evolution_runner import EvolutionRunner
from skill_engine.pipeline.llm_client import LLMClient
import logging

logger = logging.getLogger(__name__)

# Default concurrency for analysis tasks.
DEFAULT_CONCURRENCY = 2


class AnalyzerEvolverRunner:
    """Orchestrate Phase A → Phase B for segments from the analysis queue.

    Args:
        llm_client: Shared LLM client for both phases.
        segment_store: For loading segments and chain context.
        skill_store: For loading skill content.
        analysis_queue: asyncio.Queue of segment IDs to analyze.
        validator_queue: asyncio.Queue for SkillPatch objects ready for validation.
        model: Override model name.
        max_concurrency: Maximum concurrent analysis tasks.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        segment_store: SegmentStore,
        skill_store: object = None,
        pipeline_store: object = None,  # PipelineStore for persistence
        analysis_queue: asyncio.Queue | None = None,
        validator_queue: asyncio.Queue | None = None,
        model: Optional[str] = None,
        max_concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._llm = llm_client
        self._segment_store = segment_store
        self._skill_store = skill_store
        self._pipeline_store = pipeline_store
        self._analysis_queue = analysis_queue or asyncio.Queue()
        self._validator_queue = validator_queue or asyncio.Queue()
        self._model = model
        self._max_concurrency = max_concurrency

        self._analysis_runner = AnalysisRunner(
            llm_client=llm_client,
            segment_store=segment_store,
            skill_store=skill_store,
            model=model,
            analysis_store=pipeline_store,  # PipelineStore implements save_trace
        )
        self._evolution_runner = EvolutionRunner(
            llm_client=llm_client,
            skill_store=skill_store,
            model=model,
        )

        self._running: bool = False
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # --- Main loop -----------------------------------------------------------

    async def run(self) -> None:
        """Start consuming the analysis queue. Runs until stop() is called."""
        self._running = True
        logger.info(
            f"AnalyzerEvolver started (concurrency={self._max_concurrency})"
        )

        while self._running:
            try:
                # Non-blocking poll with timeout
                segment_id = await asyncio.wait_for(
                    self._analysis_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            # Spawn analysis as concurrent task
            asyncio.create_task(self._process(segment_id))

    def stop(self) -> None:
        """Signal the runner to stop after current tasks complete."""
        self._running = False

    # --- Processing ----------------------------------------------------------

    async def _process(self, segment_id: str) -> None:
        """Process a single segment: Phase A → Phase B → push to validator."""
        async with self._semaphore:
            try:
                # Load segment
                row = await self._segment_store.get(segment_id)
                if not row:
                    logger.warning(f"Segment {segment_id[:8]} not found — skipping")
                    return

                segment = Segment(
                    id=row["id"],
                    session_id=row.get("session_id", ""),
                    user_msg=row.get("user_msg", ""),
                    user_msg_index=row.get("user_msg_index", 0),
                    execution_json=row.get("execution_json", "[]"),
                    prev_id=row.get("prev_id"),
                    next_id=row.get("next_id"),
                    stats_json=row.get("stats_json", "{}"),
                    skills_available=row.get("skills_available", "[]"),
                    files_modified=row.get("files_modified", "[]"),
                )

                # Phase A: Analyze
                analysis = await self._analysis_runner.analyze(segment)

                # Persist analysis
                if self._pipeline_store:
                    await self._pipeline_store.save_analysis(segment_id, analysis)

                    # Update skill records from judgments
                    for j in analysis.skill_judgments:
                        await self._pipeline_store.update_skill_record(
                            skill_id=j.skill_id,
                            name=j.skill_id,
                            selections=1,
                            applied=1 if j.skill_applied else 0,
                            completions=1 if analysis.task_completed and j.skill_helpful else 0,
                            fallbacks=1 if not j.skill_applied else 0,
                        )

                if not analysis.has_actionable_suggestions:
                    logger.debug(
                        f"No actionable suggestions for segment {segment_id[:8]}"
                    )
                    return

                # Phase B: Evolve each suggestion into concrete patches
                patches = await self._evolution_runner.evolve(analysis, segment)

                # Push patches to validator queue
                for patch in patches:
                    await self._validator_queue.put(patch)
                    logger.info(
                        f"SkillPatch queued for validation: "
                        f"{patch.skill_id} ({patch.patch_type})"
                    )

            except Exception as e:
                logger.error(
                    f"Analyzer-Evolver failed for segment {segment_id[:8]}: {e}",
                    exc_info=True,
                )

    # --- Direct API (for non-queue usage) ------------------------------------

    async def analyze_and_evolve(self, segment_id: str) -> tuple[
        Optional[ExecutionAnalysis],
        list,
    ]:
        """Synchronous convenience: analyze + evolve a single segment.

        Returns (analysis, patches) tuple.
        """
        row = await self._segment_store.get(segment_id)
        if not row:
            return None, []

        segment = Segment(
            id=row["id"],
            session_id=row.get("session_id", ""),
            user_msg=row.get("user_msg", ""),
            user_msg_index=row.get("user_msg_index", 0),
            execution_json=row.get("execution_json", "[]"),
            prev_id=row.get("prev_id"),
            next_id=row.get("next_id"),
            stats_json=row.get("stats_json", "{}"),
            skills_available=row.get("skills_available", "[]"),
            files_modified=row.get("files_modified", "[]"),
        )

        analysis = await self._analysis_runner.analyze(segment)

        # Persist analysis (same as _process does)
        if self._pipeline_store:
            await self._pipeline_store.save_analysis(segment_id, analysis)
            for j in analysis.skill_judgments:
                await self._pipeline_store.update_skill_record(
                    skill_id=j.skill_id,
                    name=j.skill_id,
                    selections=1,
                    applied=1 if j.skill_applied else 0,
                    completions=1 if analysis.task_completed and j.skill_helpful else 0,
                    fallbacks=1 if not j.skill_applied else 0,
                )

        patches = await self._evolution_runner.evolve(analysis, segment) if analysis.has_actionable_suggestions else []
        return analysis, patches
