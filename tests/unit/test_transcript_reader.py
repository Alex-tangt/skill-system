from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from skill_engine.pipeline.transcript_reader import TranscriptReader, TranscriptEntry


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")


def test_read_empty_transcript():
    path = Path(tempfile.mktemp(suffix=".jsonl"))
    path.write_text("", encoding="utf-8")
    reader = TranscriptReader(str(path))
    entries = list(reader.entries())
    assert len(entries) == 0
    os.unlink(path)


def test_message_classification():
    path = Path(tempfile.mktemp(suffix=".jsonl"))
    _write_transcript(path, [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "hi there"}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x"}]}, "toolUseResult": {}},
        {"type": "user", "message": {"role": "user", "content": "next task"}},
    ])
    reader = TranscriptReader(str(path))
    entries = list(reader.entries())

    assert entries[0].is_user_message
    assert not entries[0].is_tool_result
    assert entries[1].is_assistant
    assert not entries[2].is_user_message
    assert entries[2].is_tool_result
    assert entries[3].is_user_message
    assert len(entries) == 4
    os.unlink(path)


def test_user_messages():
    path = Path(tempfile.mktemp(suffix=".jsonl"))
    _write_transcript(path, [
        {"type": "user", "message": {"role": "user", "content": "msg1"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "reply"}},
        {"type": "user", "message": {"role": "user", "content": "msg2"}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result"}]}, "toolUseResult": {}},
    ])
    reader = TranscriptReader(str(path))
    msgs = list(reader.user_messages())
    assert len(msgs) == 2
    assert msgs[0].content_text == "msg1"
    assert msgs[1].content_text == "msg2"
    assert reader.count_user_messages() == 2
    os.unlink(path)


def test_range():
    path = Path(tempfile.mktemp(suffix=".jsonl"))
    _write_transcript(path, [
        {"type": "user", "message": {"role": "user", "content": "a"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "b"}},
        {"type": "user", "message": {"role": "user", "content": "c"}},
    ])
    reader = TranscriptReader(str(path))
    r = reader.range(0, 2)
    assert len(r) == 2
    assert r[0].content_text == "a"
    assert r[1].content_text == "b"
    os.unlink(path)


def test_resolve_path():
    p = TranscriptReader.resolve_path("abc-123", "/home/user/project")
    expected = Path.home() / ".claude" / "projects" / "-home-user-project" / "abc-123.jsonl"
    assert str(p) == str(expected)


def test_stats():
    path = Path(tempfile.mktemp(suffix=".jsonl"))
    _write_transcript(path, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "hello"}},
    ])
    reader = TranscriptReader(str(path))
    stats = reader.stats()
    assert stats["user"] == 1
    assert stats["assistant"] == 1
    os.unlink(path)


def test_content_text_multipart():
    path = Path(tempfile.mktemp(suffix=".jsonl"))
    _write_transcript(path, [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ]}},
    ])
    reader = TranscriptReader(str(path))
    entry = next(reader.entries())
    assert "part one" in entry.content_text
    assert "part two" in entry.content_text
    os.unlink(path)
