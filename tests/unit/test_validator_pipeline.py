from __future__ import annotations

import os
import tempfile

import pytest

from skill_engine.pipeline.validator import Validator, _extract_key_terms


@pytest.fixture
def validator():
    db = tempfile.mktemp(suffix=".db")
    v = Validator(db)
    # Initialize synchronously (it's a CREATE TABLE, no async needed in test)
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS validator_test_cases (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            input_desc TEXT NOT NULL, expected_behavior TEXT NOT NULL,
            source TEXT DEFAULT 'execution_error',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS validator_runs (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            patch_hash TEXT, verdict TEXT NOT NULL,
            failed_cases TEXT DEFAULT '[]', reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit(); conn.close()
    yield v
    os.unlink(db)


def test_add_and_run_test_case(validator):
    import asyncio
    cid = asyncio.run(validator.add_test_case(
        "test-skill", "No requests module", "Fallback to urllib",
    ))
    assert cid

    suite = asyncio.run(validator.run_test_suite("test-skill"))
    assert len(suite["test_cases"]) == 1


def test_validate_pass(validator):
    import asyncio
    asyncio.run(validator.add_test_case("test-skill", "No requests", "Use urllib fallback"))

    patch = "<<<<<<< SEARCH\nuse requests\n=======\ncheck for requests, use urllib\n>>>>>>> REPLACE"
    result = asyncio.run(validator.validate_patch(
        patch, "test-skill", "old content",
        ["ModuleNotFoundError: No module named requests"],
    ))
    assert result.verdict == "pass"


def test_validate_fail_missing_frontmatter(validator):
    import asyncio
    asyncio.run(validator.add_test_case("test-skill", "No requests", "Use urllib"))
    patch = "*** File: SKILL.md\n\n# Just some content without frontmatter\n"
    result = asyncio.run(validator.validate_patch(patch, "test-skill", ""))
    assert result.verdict == "fail"


def test_validate_fail_no_error_coverage(validator):
    import asyncio
    asyncio.run(validator.add_test_case("test-skill", "No requests", "Use urllib"))
    patch = "<<<<<<< SEARCH\nprint hello\n=======\nprint world\n>>>>>>> REPLACE"
    result = asyncio.run(validator.validate_patch(
        patch, "test-skill", "old",
        ["ModuleNotFoundError: No module named requests"],
    ))
    assert result.verdict == "fail"
    assert "does not address" in result.reason.lower()


def test_validate_empty(validator):
    import asyncio
    result = asyncio.run(validator.validate_patch("", "test-skill"))
    assert result.verdict == "fail"


def test_validate_identical(validator):
    import asyncio
    content = "---\nname: test\ndescription: test desc\n---\n\n# Body\n"
    result = asyncio.run(validator.validate_patch(content, "test-skill", content))
    assert result.verdict == "fail"


def test_extract_key_terms():
    terms = _extract_key_terms("ModuleNotFoundError: No module named 'requests'")
    assert "modulenotfounderror" in terms or "requests" in terms

    terms2 = _extract_key_terms("the agent used the wrong parameter")
    assert len(terms2) > 0
    assert "the" not in terms2
