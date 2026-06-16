from __future__ import annotations

from skill_engine.pipeline.transcript_reader import TranscriptReader, TranscriptEntry
from skill_engine.pipeline.models import (
    Segment,
    SegmentStats,
    ExecutionAnalysis,
    SkillPatch,
    ValidateResult,
)
from skill_engine.pipeline.segment_store import SegmentStore
from skill_engine.pipeline.segmenter import Segmenter
from skill_engine.pipeline.segment_watcher import SegmentWatcher
from skill_engine.pipeline.analysis_prompt import AnalysisPromptBuilder
from skill_engine.pipeline.analysis_runner import AnalysisRunner
from skill_engine.pipeline.evolution_runner import EvolutionRunner
from skill_engine.pipeline.analyzer_evolver import AnalyzerEvolverRunner
from skill_engine.pipeline.validator import Validator
from skill_engine.pipeline.metric_monitor import MetricMonitor, MetricAlert
from skill_engine.pipeline.meta_signal_detector import MetaSignalDetector, MetaSignal
from skill_engine.pipeline.llm_client import (
    LLMClient,
    ToolDefinition,
    BUILTIN_ANALYSIS_TOOLS,
)

__all__ = [
    # Transcript
    "TranscriptReader", "TranscriptEntry",
    # Models
    "Segment", "SegmentStats", "ExecutionAnalysis", "SkillPatch", "ValidateResult",
    # Storage
    "SegmentStore",
    # Segmentation
    "Segmenter", "SegmentWatcher",
    # Analysis + Evolution
    "AnalysisPromptBuilder", "AnalysisRunner", "EvolutionRunner", "AnalyzerEvolverRunner",
    # Validator
    "Validator",
    # Monitoring
    "MetricMonitor", "MetricAlert", "MetaSignalDetector", "MetaSignal",
    # LLM
    "LLMClient", "ToolDefinition", "BUILTIN_ANALYSIS_TOOLS",
]
