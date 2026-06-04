from __future__ import annotations

import time

from skill_engine.optimizer.analyzer import TraceAnalyzer, OptimizationRecommendation
from skill_engine.storage.trace_store import TraceStore
from skill_engine.storage.skill_store import SkillStore


class OptimizerAgent:
    def __init__(
        self,
        trace_store: TraceStore,
        skill_store: SkillStore,
        analyzer: TraceAnalyzer,
    ):
        self.trace_store = trace_store
        self.skill_store = skill_store
        self.analyzer = analyzer
        self._recommendations: dict[str, OptimizationRecommendation] = {}
        self._last_scan: float | None = None

    @property
    def last_scan(self) -> float | None:
        return self._last_scan

    async def analyze(
        self, skill_id: str | None = None, min_samples: int = 5
    ) -> list[OptimizationRecommendation]:
        recommendations = await self.analyzer.analyze(
            skill_id=skill_id, min_samples=min_samples
        )
        for rec in recommendations:
            self._recommendations[rec.id] = rec
        self._last_scan = time.time()
        return recommendations

    def get_recommendations(self, skill_id: str | None = None) -> list[OptimizationRecommendation]:
        recs = list(self._recommendations.values())
        if skill_id:
            recs = [r for r in recs if r.skill_id == skill_id]
        return [r for r in recs if not r.applied]

    async def apply(self, recommendation_id: str) -> dict:
        rec = self._recommendations.get(recommendation_id)
        if not rec:
            return {"error": f"Unknown recommendation: {recommendation_id}"}
        if rec.applied:
            return {"error": "Recommendation already applied"}

        skill = self.skill_store.get(rec.skill_id)
        if not skill:
            return {"error": f"Skill not found: {rec.skill_id}"}

        if rec.type == "timeout":
            for step_id in rec.affected_step_ids:
                for step in skill.steps:
                    if step.id == step_id:
                        step.timeout_seconds = rec.suggested_change["timeout_seconds"]

        elif rec.type == "retry_policy":
            retry_cfg = rec.suggested_change["retry"]
            for step_id in rec.affected_step_ids:
                for step in skill.steps:
                    if step.id == step_id:
                        step.retry.max_attempts = retry_cfg.get("max_attempts", 1)
                        step.retry.backoff = retry_cfg.get("backoff", "none")
                        step.retry.backoff_base_seconds = retry_cfg.get("backoff_base_seconds", 1.0)

        elif rec.type == "add_validation":
            pass

        elif rec.type == "parallelize":
            pass

        else:
            return {"error": f"Unknown optimization type: {rec.type}"}

        version_parts = skill.version.split(".")
        version_parts[-1] = str(int(version_parts[-1]) + 1)
        skill.version = ".".join(version_parts)

        self.skill_store.save(skill)
        rec.applied = True
        from skill_engine.storage.skill_store import skill_to_dict

        return {"status": "applied", "recommendation_id": rec.id, "skill": skill_to_dict(skill)}
