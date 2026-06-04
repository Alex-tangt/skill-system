from __future__ import annotations

import re
import math
from collections import Counter
from copy import deepcopy

from skill_engine.models.skill import SkillDefinition, StepDefinition


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _get_search_text(skill: SkillDefinition) -> str:
    parts = [
        skill.name,
        skill.description,
        " ".join(skill.tags),
        " ".join(skill.input_schema.get("properties", {}).keys()),
        " ".join(skill.output_schema.get("properties", {}).keys()),
    ]
    return " ".join(p for p in parts if p)


class SkillRetriever:
    def __init__(self, skill_store):
        self.store = skill_store

    def search(self, query: str, top_k: int = 5) -> list[tuple[SkillDefinition, float]]:
        all_skills = self.store.list_all()
        if not all_skills:
            return []

        query_tokens = _tokenize(query)
        corpus = [_get_search_text(s) for s in all_skills]

        scores = []
        for skill, doc in zip(all_skills, corpus):
            score = self._tfidf_score(query_tokens, doc, corpus)
            if score > 0:
                scores.append((skill, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _tfidf_score(
        self, query_tokens: list[str], doc: str, corpus: list[str]
    ) -> float:
        doc_tokens = _tokenize(doc)
        N = len(corpus)
        score = 0.0
        for token in set(query_tokens):
            tf = doc_tokens.count(token) / max(len(doc_tokens), 1)
            df = sum(1 for d in corpus if token in _tokenize(d))
            idf = math.log((N + 1) / (df + 1)) + 1
            score += tf * idf
        return score


def compose_skills(
    name: str,
    skill_ids: list[str],
    store,
    output_mappings: dict | None = None,
    tags: list[str] | None = None,
) -> SkillDefinition:
    skills = [store.get(sid) for sid in skill_ids]
    for i, (sid, s) in enumerate(zip(skill_ids, skills)):
        if s is None:
            raise ValueError(f"Skill not found: {sid}")

    composed_steps: list[StepDefinition] = []
    prev_terminal_steps: list[str] = []

    for i, skill in enumerate(skills):
        prefix = f"_s{i}_"
        for step in skill.steps:
            prefixed = deepcopy(step)
            prefixed.id = f"{prefix}{step.id}"
            prefixed.depends_on = [f"{prefix}{d}" for d in step.depends_on]
            # Add dependency on previous skill's terminal steps
            if i > 0 and not step.depends_on:
                prefixed.depends_on.extend(prev_terminal_steps)
            composed_steps.append(prefixed)

        prev_terminal_steps = [
            f"{prefix}{t}" for t in skill.terminal_steps()
        ]

    # Build input_schema from first skill
    input_schema = deepcopy(skills[0].input_schema) if skills else {}
    output_schema = deepcopy(skills[-1].output_schema) if skills else {}

    # If output_mappings provided, add them as explicit input mappings on
    # the first step of downstream skills, ensuring data flows correctly
    if output_mappings:
        input_schema = {
            **input_schema,
            "properties": {
                **(input_schema.get("properties", {})),
                **{k: {"type": "string"} for k in output_mappings},
            },
        }

    composed_id = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")

    return SkillDefinition(
        id=composed_id,
        name=name,
        description=f"Composed skill: {' -> '.join(s.name for s in skills)}",
        tags=tags or [],
        input_schema=input_schema,
        output_schema=output_schema,
        steps=composed_steps,
    )
