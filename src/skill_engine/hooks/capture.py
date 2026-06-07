#!/usr/bin/env python3
"""Claude Code hook capture script.

Zero external dependencies — uses only Python stdlib.
Captures hook events (PostToolUse, UserPromptSubmit, etc.) from stdin JSON
and writes them to a SQLite history database for later processing.

Configure in .claude/settings.json:
  "hooks": {
    "PostToolUse": [{"matcher": "*", "command": "python3 path/to/capture.py"}]
  }
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import logging
from datetime import datetime, timezone

# Persistent log file — hook errors are silent (don't crash Claude Code)
# but we need to know when data is lost.
LOG_PATH = os.environ.get(
    "SKILL_ENGINE_HOOK_LOG",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "traces", "hook.log"),
)
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
_log = logging.getLogger("capture")


def get_db_path() -> str:
    """Resolve history.db path. Configurable via SKILL_ENGINE_HISTORY_DB env var."""
    return os.environ.get(
        "SKILL_ENGINE_HISTORY_DB",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "traces", "history.db"),
    )


def init_db(db_path: str) -> sqlite3.Connection:
    """Create history_events table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            hook_event_name TEXT NOT NULL,
            tool_name TEXT,
            tool_input_json TEXT,
            tool_output_json TEXT,
            transcript_path TEXT,
            dedup_hash TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            processed INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_history_session
        ON history_events(session_id, created_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_history_processed
        ON history_events(processed, created_at)
    """)
    conn.commit()
    return conn


def compute_dedup_hash(event: dict) -> str:
    """SHA256 of session_id + tool_name + tool_input + tool_output for exact dedup."""
    key = json.dumps(
        {
            "session_id": event.get("session_id", ""),
            "tool_name": event.get("tool_name", ""),
            "tool_input": event.get("tool_input", ""),
            "tool_output": event.get("tool_output", ""),
        },
        sort_keys=True,
    )
    return hashlib.sha256(key.encode()).hexdigest()


def capture() -> None:
    """Read hook event from stdin, write to history.db."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        hook_input = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("Invalid JSON from stdin: %s", raw[:200] if raw else "(empty)")
        sys.exit(0)
    except Exception:
        _log.exception("Unexpected error reading hook input")
        sys.exit(0)

    session_id = hook_input.get("session_id", "unknown")
    hook_event_name = hook_input.get("hook_event_name", "unknown")
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_result = hook_input.get("tool_result", {})
    transcript_path = hook_input.get("transcript_path", "")

    tool_input_json = json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input)
    tool_output_json = json.dumps(tool_result) if isinstance(tool_result, (dict, list)) else str(tool_result)

    event = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input_json,
        "tool_output": tool_output_json,
    }
    dedup_hash = compute_dedup_hash(event)

    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    try:
        conn = init_db(db_path)
        conn.execute(
            """INSERT OR IGNORE INTO history_events
               (session_id, hook_event_name, tool_name, tool_input_json, tool_output_json,
                transcript_path, dedup_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, hook_event_name, tool_name, tool_input_json, tool_output_json,
             transcript_path, dedup_hash),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        _log.error("DB error (data lost): %s", e)
        sys.exit(0)

    # Signal success to Claude Code
    print(json.dumps({"continue": True, "suppressOutput": True}))


if __name__ == "__main__":
    capture()
