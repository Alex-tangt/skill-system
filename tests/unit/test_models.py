from __future__ import annotations

import json

from skill_engine.pipeline.models import (
    Segment, SegmentStats, ExecutionAnalysis, SkillPatch, ValidateResult,
)


def test_segment_stats_roundtrip():
    stats = SegmentStats(
        tool_count=5, tool_types={"shell": 3, "mcp": 2},
        iteration_count=3, status="success",
        skills_referenced=["test-skill"], total_chars=1234,
    )
    j = stats.to_json()
    parsed = SegmentStats.from_json(j)
    assert parsed.tool_count == 5
    assert parsed.tool_types == {"shell": 3, "mcp": 2}
    assert parsed.skills_referenced == ["test-skill"]


def test_segment_chain_properties():
    s = Segment(id="a", session_id="s1", user_msg="hello", user_msg_index=0)
    assert not s.has_next
    assert not s.has_prev

    s2 = Segment(id="b", session_id="s1", user_msg="world", user_msg_index=1, prev_id="a")
    s.next_id = s2.id
    assert s.has_next
    assert s2.has_prev

    d = s.to_dict()
    assert d["user_msg"] == "hello"


def test_execution_analysis():
    a = ExecutionAnalysis(
        segment_id="seg-1", diagnosis="Task completed successfully.",
        error_summary=["ModuleNotFoundError: requests"],
    )
    d = a.to_dict()
    assert d["diagnosis"] == "Task completed successfully."
    assert "ModuleNotFoundError" in d["error_summary"][0]

    parsed = ExecutionAnalysis.from_dict(d)
    assert parsed.diagnosis == a.diagnosis


def test_skill_patch():
    p = SkillPatch(
        skill_id="test-skill", patch_type="diff",
        content="<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE",
        change_summary="Fixed parameter",
    )
    d = p.to_dict()
    assert d["patch_type"] == "diff"
    assert "SEARCH" in d["content"]


def test_validate_result():
    r = ValidateResult(
        verdict="fail", reason="Missing frontmatter",
        failed_cases=["test_case:no-requests-handling"],
        suggestion="Add frontmatter name and description fields.",
    )
    assert r.verdict == "fail"
    assert len(r.failed_cases) == 1
