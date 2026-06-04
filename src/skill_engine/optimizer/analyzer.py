from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from skill_engine.storage.trace_store import TraceStore


@dataclass
class OptimizationRecommendation:
    id: str
    skill_id: str
    type: str  # timeout | parallelize | add_validation | retry_policy | compose
    severity: str  # low | medium | high
    description: str
    affected_step_ids: list[str]
    suggested_change: dict
    confidence: float
    evidence: dict
    applied: bool = False


class TraceAnalyzer:
    def __init__(self, trace_store: TraceStore):
        self.trace_store = trace_store

    async def analyze(
        self, skill_id: str | None = None, min_samples: int = 5
    ) -> list[OptimizationRecommendation]:
        recommendations: list[OptimizationRecommendation] = []

        recs = await self._detect_failure_hotspots(skill_id, min_samples)
        recommendations.extend(recs)

        recs = await self._detect_timeout_patterns(skill_id, min_samples)
        recommendations.extend(recs)

        recs = await self._detect_retry_opportunities(skill_id, min_samples)
        recommendations.extend(recs)

        recs = await self._detect_composition_opportunities(skill_id, min_samples)
        recommendations.extend(recs)

        recs = await self._detect_validation_gaps(skill_id, min_samples)
        recommendations.extend(recs)

        return sorted(recommendations, key=lambda r: r.confidence, reverse=True)

    async def _detect_failure_hotspots(
        self, skill_id: str | None, min_samples: int
    ) -> list[OptimizationRecommendation]:
        traces = await self.trace_store.list_traces(skill_id=skill_id, limit=500)
        step_failures: dict[str, list[dict]] = {}
        step_successes: dict[str, int] = {}
        for t in traces:
            trace = await self.trace_store.get_trace(t["run_id"])
            if not trace:
                continue
            for step in trace.get("steps", []):
                sid = f"{trace['skill_id']}/{step['step_id']}"
                if step["status"] == "failed":
                    step_failures.setdefault(sid, []).append(step)
                elif step["status"] == "succeeded":
                    step_successes[sid] = step_successes.get(sid, 0) + 1

        recommendations = []
        for sid, failures in step_failures.items():
            total = len(failures) + step_successes.get(sid, 0)
            if total < min_samples:
                continue
            failure_rate = len(failures) / total
            if failure_rate > 0.3:
                skill_id_only, step_id = sid.split("/", 1)
                rec = OptimizationRecommendation(
                    id=str(uuid.uuid4()),
                    skill_id=skill_id_only,
                    type="retry_policy",
                    severity="medium" if failure_rate > 0.5 else "low",
                    description=f"Step '{step_id}' fails at {failure_rate:.0%} rate ({len(failures)}/{total} executions)",
                    affected_step_ids=[step_id],
                    suggested_change={"retry": {"max_attempts": 3, "backoff": "exponential", "backoff_base_seconds": 2}},
                    confidence=failure_rate,
                    evidence={"failure_count": len(failures), "total": total, "failure_rate": failure_rate},
                )
                recommendations.append(rec)

        return recommendations

    async def _detect_timeout_patterns(
        self, skill_id: str | None, min_samples: int
    ) -> list[OptimizationRecommendation]:
        traces = await self.trace_store.list_traces(skill_id=skill_id, status="failed", limit=200)
        timeout_steps: dict[str, list[dict]] = {}

        for t in traces:
            trace = await self.trace_store.get_trace(t["run_id"])
            if not trace:
                continue
            for step in trace.get("steps", []):
                if step["status"] == "failed" and (step.get("error") or "").lower().find("timed out") != -1:
                    sid = f"{trace['skill_id']}/{step['step_id']}"
                    timeout_steps.setdefault(sid, []).append(step)

        recommendations = []
        for sid, timeouts in timeout_steps.items():
            if len(timeouts) < min_samples:
                continue
            skill_id_only, step_id = sid.split("/", 1)
            avg_duration = sum(
                s.get("finished_at", s.get("started_at", 0)) - s.get("started_at", 0)
                for s in timeouts
            ) / len(timeouts)
            rec = OptimizationRecommendation(
                id=str(uuid.uuid4()),
                skill_id=skill_id_only,
                type="timeout",
                severity="high",
                description=f"Step '{step_id}' timed out {len(timeouts)} times (avg duration: {avg_duration:.1f}s)",
                affected_step_ids=[step_id],
                suggested_change={"timeout_seconds": int(avg_duration * 2)},
                confidence=min(1.0, len(timeouts) / min_samples),
                evidence={"timeout_count": len(timeouts), "avg_duration_s": avg_duration},
            )
            recommendations.append(rec)

        return recommendations

    async def _detect_retry_opportunities(
        self, skill_id: str | None, min_samples: int
    ) -> list[OptimizationRecommendation]:
        error_traces = await self.trace_store.get_error_traces(skill_id=skill_id, limit=100)
        step_errors: dict[str, int] = {}
        for e in error_traces:
            key = f"{e['skill_id']}/{e['step_id']}"
            step_errors[key] = step_errors.get(key, 0) + 1

        recommendations = []
        for key, count in step_errors.items():
            if count < min_samples:
                continue
            skill_id_only, step_id = key.split("/", 1)
            rec = OptimizationRecommendation(
                id=str(uuid.uuid4()),
                skill_id=skill_id_only,
                type="retry_policy",
                severity="low",
                description=f"Step '{step_id}' fails frequently ({count} errors). Consider adding retry logic.",
                affected_step_ids=[step_id],
                suggested_change={"retry": {"max_attempts": 3, "backoff": "exponential", "backoff_base_seconds": 1}},
                confidence=min(0.8, count / (min_samples * 2)),
                evidence={"error_count": count},
            )
            recommendations.append(rec)

        return recommendations

    async def _detect_composition_opportunities(
        self, skill_id: str | None, min_samples: int
    ) -> list[OptimizationRecommendation]:
        return []

    async def _detect_validation_gaps(
        self, skill_id: str | None, min_samples: int
    ) -> list[OptimizationRecommendation]:
        traces = await self.trace_store.list_traces(status="failed", limit=200)
        validation_failures: dict[str, int] = {}
        for t in traces:
            if (t.get("error") or "").find("Input validation failed") != -1:
                validation_failures[t["skill_id"]] = validation_failures.get(t["skill_id"], 0) + 1

        recommendations = []
        for sid, count in validation_failures.items():
            if skill_id and sid != skill_id:
                continue
            if count >= min_samples:
                rec = OptimizationRecommendation(
                    id=str(uuid.uuid4()),
                    skill_id=sid,
                    type="add_validation",
                    severity="medium",
                    description=f"Skill '{sid}' has {count} input validation failures. Consider documenting required fields more clearly.",
                    affected_step_ids=[],
                    suggested_change={"add_input_examples": True},
                    confidence=min(0.7, count / (min_samples * 2)),
                    evidence={"validation_failure_count": count},
                )
                recommendations.append(rec)

        return recommendations
