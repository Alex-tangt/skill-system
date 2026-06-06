from __future__ import annotations

import tempfile
import pytest
from skill_engine.kernel.retriever import _tokenize, SkillRetriever
from skill_engine.kernel.skill_store import SkillStore
from skill_engine.kernel.models.skill_metadata import SkillMetadata


@pytest.fixture
def _r_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def rs(_r_dir):
    return SkillStore(_r_dir)


def _make(name, description="", metadata=None):
    return SkillMetadata(name=name, description=description, metadata=metadata or {})


class TestTokenize:
    def test_splits_to_lowercase_words(self):
        result = _tokenize("Hello World! Python3, testing.")
        assert "hello" in result
        assert "world" in result
        assert "python3" in result
        assert "testing" in result

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_special_chars_only(self):
        assert _tokenize("!@#$%") == []


class TestSkillRetriever:
    def test_search_empty_store(self, rs):
        retriever = SkillRetriever(rs)
        assert retriever.search("anything") == []

    def test_search_returns_relevant_skills(self, rs):
        rs.save(_make("pdf-extract", "Extract text from PDF files", {"tags": "pdf text"}))
        rs.save(_make("image-resize", "Resize and optimize images", {"tags": "image resize"}))
        retriever = SkillRetriever(rs)
        results = retriever.search("pdf text extraction")
        assert len(results) >= 1
        assert results[0][0].name == "pdf-extract"

    def test_search_respects_top_k(self, rs):
        for i in range(5):
            rs.save(_make(f"skill-{i}", f"A skill for task number {i}", {"tags": "test"}))
        retriever = SkillRetriever(rs)
        results = retriever.search("skill task", top_k=3)
        assert len(results) <= 3

    def test_search_no_matches(self, rs):
        rs.save(_make("pdf", "PDF processing"))
        retriever = SkillRetriever(rs)
        assert retriever.search("zzz_nonexistent_term_zzz") == []
