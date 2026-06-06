from __future__ import annotations

import re
import math
from collections import Counter

from skill_engine.kernel.models.skill_metadata import SkillMetadata


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _get_search_text(skill: SkillMetadata) -> str:
    parts = [
        skill.name,
        skill.description,
        " ".join(skill.metadata.values()),
    ]
    if skill.allowed_tools:
        parts.append(" ".join(skill.allowed_tools))
    return " ".join(p for p in parts if p)


class SkillRetriever:
    """TF-IDF search over skill metadata. Architecture-agnostic.

    v0.2: Migrated to use SkillMetadata (SKILL.md frontmatter) instead of
    the old SkillDefinition DAG model. compose_skills removed (replaced by
    SKILL.md body references + export expand).
    """

    def __init__(self, skill_store):
        self.store = skill_store

    def search(self, query: str, top_k: int = 5) -> list[tuple[SkillMetadata, float]]:
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
