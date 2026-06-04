from __future__ import annotations

import os
import shutil
import yaml
from skill_engine.models.skill import (
    SkillDefinition,
    StepDefinition,
    Criteria,
    RetryPolicy,
)


def _step_from_dict(d: dict) -> StepDefinition:
    success = d.get("success_criteria", {"type": "always"})
    failure = d.get("failure_criteria")
    retry = d.get("retry", {})
    return StepDefinition(
        id=d["id"],
        name=d.get("name", d["id"]),
        description=d.get("description", ""),
        tool=d.get("tool", ""),
        depends_on=d.get("depends_on", []),
        input_mapping=d.get("input_mapping", {}),
        success_criteria=Criteria(
            type=success.get("type", "always"),
            path=success.get("path"),
            expected=success.get("expected"),
        ),
        failure_criteria=Criteria(
            type=failure.get("type", "exception"),
            path=failure.get("path"),
            expected=failure.get("expected"),
        ) if failure else None,
        retry=RetryPolicy(
            max_attempts=retry.get("max_attempts", 1),
            backoff=retry.get("backoff", "none"),
            backoff_base_seconds=retry.get("backoff_base_seconds", 1.0),
        ),
        timeout_seconds=d.get("timeout_seconds", 60),
    )


def skill_to_dict(skill: SkillDefinition) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "tags": skill.tags,
        "timeout_seconds": skill.timeout_seconds,
        "max_concurrency": skill.max_concurrency,
        "input_schema": skill.input_schema,
        "output_schema": skill.output_schema,
        "steps": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tool": s.tool,
                "depends_on": s.depends_on,
                "input_mapping": s.input_mapping,
                "success_criteria": {
                    "type": s.success_criteria.type,
                    **({"path": s.success_criteria.path} if s.success_criteria.path else {}),
                    **({"expected": s.success_criteria.expected} if s.success_criteria.expected is not None else {}),
                },
                **({
                    "failure_criteria": {
                        "type": s.failure_criteria.type,
                        **({"path": s.failure_criteria.path} if s.failure_criteria.path else {}),
                        **({"expected": s.failure_criteria.expected} if s.failure_criteria.expected is not None else {}),
                    }
                } if s.failure_criteria else {}),
                "retry": {
                    "max_attempts": s.retry.max_attempts,
                    "backoff": s.retry.backoff,
                    "backoff_base_seconds": s.retry.backoff_base_seconds,
                },
                "timeout_seconds": s.timeout_seconds,
            }
            for s in skill.steps
        ],
    }


class SkillStore:
    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        os.makedirs(skills_dir, exist_ok=True)

    def _path(self, skill_id: str) -> str:
        return os.path.join(self.skills_dir, f"{skill_id}.yaml")

    def get(self, skill_id: str) -> SkillDefinition | None:
        path = self._path(skill_id)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            data = yaml.safe_load(f)
        return self._from_dict(data)

    def get_by_name(self, name: str) -> SkillDefinition | None:
        for skill in self.list_all():
            if skill.name.lower() == name.lower():
                return skill
        return None

    def save(self, skill: SkillDefinition) -> None:
        path = self._path(skill.id)
        if os.path.exists(path):
            backup = path + ".backup"
            shutil.copy2(path, backup)
        data = skill_to_dict(skill)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def delete(self, skill_id: str) -> bool:
        path = self._path(skill_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def list_all(self) -> list[SkillDefinition]:
        skills = []
        if not os.path.isdir(self.skills_dir):
            return skills
        for filename in sorted(os.listdir(self.skills_dir)):
            if filename.endswith((".yaml", ".yml")):
                filepath = os.path.join(self.skills_dir, filename)
                try:
                    with open(filepath) as f:
                        data = yaml.safe_load(f)
                    if data:
                        skills.append(self._from_dict(data))
                except (yaml.YAMLError, Exception):
                    continue
        return skills

    def _from_dict(self, data: dict) -> SkillDefinition:
        return SkillDefinition(
            id=data["id"],
            name=data.get("name", data["id"]),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            timeout_seconds=data.get("timeout_seconds", 300),
            max_concurrency=data.get("max_concurrency", 10),
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            steps=[_step_from_dict(s) for s in data.get("steps", [])],
        )
