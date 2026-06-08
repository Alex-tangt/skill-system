"""Phase A: Analysis Runner.

Executes the LLM analysis of a segment — builds the prompt, calls the
LLM with analysis tools (traverse_chain, read_file, get_skill_content),
parses the JSON output into ExecutionAnalysis, and records the analysis
process into analysis_traces.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from skill_engine.pipeline.models import (
    ExecutionAnalysis,
    EvolutionSuggestion,
    Segment,
    SegmentStats,
    SkillJudgment,
)
from skill_engine.pipeline.analysis_prompt import AnalysisPromptBuilder
from skill_engine.pipeline.llm_client import (
    LLMClient,
    BUILTIN_ANALYSIS_TOOLS,
    ToolDefinition,
)
from skill_engine.pipeline.segment_store import SegmentStore
import logging

logger = logging.getLogger(__name__)

# Maximum LLM agent loop iterations for analysis.
_MAX_ANALYSIS_ITERATIONS = 5


class AnalysisRunner:
    """Run Phase A analysis of a segment.

    Args:
        llm_client: LLM client satisfying the LLMClient protocol.
        segment_store: For traversing the segment chain.
        skill_store: For loading skill content (passed to prompt builder).
        model: Override model name. If None, uses the LLM client's default.
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
        self._skill_store = skill_store
        self._model = model
        self._analysis_store = analysis_store
        self._prompt_builder = AnalysisPromptBuilder(
            skill_store=skill_store,
        )

    async def analyze(self, segment: Segment) -> ExecutionAnalysis:
        """Run the full Phase A analysis on a segment.

        Returns an ExecutionAnalysis even on partial failure —
        check ``has_actionable_suggestions`` before proceeding to Phase B.
        """
        start_time = time.time()
        segment_id = segment.id

        # Load chain context
        prev_msg, next_msg = await self._load_chain_context(segment)

        # Build prompt
        model_name = self._model or "pipeline-analyzer"
        prompt = await self._prompt_builder.build(
            segment,
            prev_user_msg=prev_msg,
            next_user_msg=next_msg,
            model_name=model_name,
        )

        # Run LLM agent loop
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": prompt},
        ]
        analysis_tools = self._build_tool_schemas()

        raw_json: Optional[dict] = None
        tool_calls_log: list[dict] = []
        status = "success"

        try:
            raw_json = await self._run_loop(
                messages, analysis_tools, tool_calls_log
            )
        except Exception as e:
            logger.error(f"Analysis LLM call failed for {segment_id}: {e}")
            status = "error"

        # Parse into ExecutionAnalysis
        analysis = self._parse_result(segment_id, raw_json)

        # Record analysis trace
        if self._analysis_store:
            await self._record_trace(
                segment_id=segment_id,
                analysis_id=analysis.task_id,
                prompt=prompt,
                raw_json=raw_json,
                tool_calls_log=tool_calls_log,
                duration_ms=int((time.time() - start_time) * 1000),
                status=status,
                model=model_name,
            )

        suggestions_info = [
            f"{s.type}({','.join(s.target_skill_ids)})"
            for s in analysis.evolution_suggestions
        ]
        logger.info(
            f"Analysis complete for {segment_id[:8]}: "
            f"completed={analysis.task_completed}, "
            f"judgments={len(analysis.skill_judgments)}, "
            f"suggestions={suggestions_info or 'none'}"
        )

        return analysis

    # --- LLM agent loop ------------------------------------------------------

    async def _run_loop(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
        tool_calls_log: list[dict],
    ) -> Optional[dict]:
        """Run the LLM agent loop, allowing up to _MAX_ANALYSIS_ITERATIONS."""
        for iteration in range(_MAX_ANALYSIS_ITERATIONS):
            is_last = iteration == _MAX_ANALYSIS_ITERATIONS - 1

            if is_last:
                messages.append({
                    "role": "system",
                    "content": (
                        "This is your FINAL round — no more tool calls. "
                        "Output the JSON analysis object NOW based on all "
                        "information gathered so far."
                    ),
                })

            result = await self._llm.complete(
                messages=messages,
                tools=tool_schemas if not is_last else None,
                execute_tools=not is_last,
                model=self._model,
            )

            has_tool_calls = result.get("has_tool_calls", False)
            content = result.get("message", {}).get("content", "")

            if not has_tool_calls:
                return self._extract_json(content)

            # Tools were called — continue with updated messages
            messages = result.get("messages", messages)
            tool_results = result.get("tool_results", [])
            for tr in tool_results:
                tool_calls_log.append({
                    "tool_call": tr.get("tool_call", {}),
                    "result_summary": str(tr.get("result", ""))[:500],
                })

            logger.debug(
                f"Analysis agent used tools (iter {iteration + 1}/{_MAX_ANALYSIS_ITERATIONS})"
            )

        # Final fallback: try to extract from last message
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                return self._extract_json(m["content"])

        return None

    # --- JSON extraction -----------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Extract a JSON object from LLM response text.

        Handles markdown code fences and bare JSON.  Uses brace-matching
        to handle nested structures robustly.
        """
        if not text:
            return None

        # Try code block first
        code_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL
        )
        if code_match:
            text = code_match.group(1).strip()

        # Locate first complete JSON object by counting braces
        start = text.find("{")
        if start == -1:
            return None

        count = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if not in_string:
                if ch == "{":
                    count += 1
                elif ch == "}":
                    count -= 1
                    if count == 0:
                        json_str = text[start:i + 1]
                        try:
                            data = json.loads(json_str)
                            if isinstance(data, dict):
                                return data
                        except json.JSONDecodeError:
                            return None
        return None

    # --- Result parsing ------------------------------------------------------

    def _parse_result(
        self, segment_id: str, raw_json: Optional[dict]
    ) -> ExecutionAnalysis:
        """Convert raw LLM JSON output into an ExecutionAnalysis."""
        now = datetime.now(timezone.utc).isoformat()

        analysis = ExecutionAnalysis(
            task_id=segment_id,
            analyzed_at=now,
        )

        if raw_json is None:
            analysis.execution_note = "Analysis failed: no valid JSON output from LLM"
            return analysis

        try:
            analysis.task_completed = bool(raw_json.get("task_completed", False))
            analysis.execution_note = raw_json.get("execution_note", "")
            analysis.tool_issues = raw_json.get("tool_issues", [])

            # Parse skill judgments
            for jd in raw_json.get("skill_judgments", []):
                analysis.skill_judgments.append(SkillJudgment.from_dict(jd))

            # Parse evolution suggestions
            for sd in raw_json.get("evolution_suggestions", []):
                analysis.evolution_suggestions.append(
                    EvolutionSuggestion.from_dict(sd)
                )

        except Exception as e:
            logger.error(f"Failed to parse analysis response: {e}")
            analysis.execution_note = f"Parse error: {e}"

        return analysis

    # --- Chain context -------------------------------------------------------

    async def _load_chain_context(self, segment: Segment) -> tuple[str, str]:
        """Load prev/next user messages from the segment chain."""
        prev_msg = "(none)"
        next_msg = "(none)"

        if segment.prev_id:
            prev_row = await self._segment_store.get(segment.prev_id)
            if prev_row:
                prev_msg = prev_row.get("user_msg", "(none)")

        if segment.next_id:
            next_row = await self._segment_store.get(segment.next_id)
            if next_row:
                next_msg = next_row.get("user_msg", "(none)")

        return prev_msg, next_msg

    # --- Tools ---------------------------------------------------------------

    @staticmethod
    def _build_tool_schemas() -> list[dict[str, Any]]:
        """Convert built-in analysis tools to LLM-compatible schemas."""
        return [t.to_schema() for t in BUILTIN_ANALYSIS_TOOLS]

    # --- Trace recording -----------------------------------------------------

    async def _record_trace(
        self,
        segment_id: str,
        analysis_id: str,
        prompt: str,
        raw_json: Optional[dict],
        tool_calls_log: list[dict],
        duration_ms: int,
        status: str,
        model: str,
    ) -> None:
        """Record the analysis execution trace (if analysis_store is available)."""
        if not self._analysis_store:
            return

        try:
            await self._analysis_store.save_trace(
                analysis_id=analysis_id,
                segment_id=segment_id,
                model=model,
                prompt=prompt,
                response=json.dumps(raw_json, ensure_ascii=False) if raw_json else "",
                tool_calls=json.dumps(tool_calls_log, ensure_ascii=False),
                duration_ms=duration_ms,
                status=status,
            )
        except Exception as e:
            logger.debug(f"Failed to record analysis trace: {e}")
