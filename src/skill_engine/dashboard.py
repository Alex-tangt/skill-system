"""Pipeline v0.3 Debug Dashboard.

Flask-based web UI for inspecting pipeline data.  Zero new Python
dependencies beyond Flask.  Connects to the same SQLite DB as the MCP server.

Usage:
    python3 -m skill_engine.dashboard --port 7788
    # or: python3 src/skill_engine/dashboard.py --port 7788
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# --- DB connection ----------------------------------------------------------

DB_PATH = os.environ.get("SKILL_ENGINE_PIPELINE_DB", "./traces/pipeline.db")


def _query(sql: str, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _query_one(sql: str, params=()):
    rows = _query(sql, params)
    return rows[0] if rows else None


# --- Base layout ------------------------------------------------------------

_BASE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pipeline Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}
nav{background:#161b22;padding:12px 20px;margin:-20px -20px 20px;display:flex;gap:20px}
nav a{color:#58a6ff;text-decoration:none;font-weight:500}nav a:hover{color:#79c0ff}
h1{font-size:20px;margin-bottom:16px}
h2{font-size:16px;margin:16px 0 8px;color:#f0f6fc}
table{width:100%;border-collapse:collapse;margin:8px 0 20px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #21262d}
th{background:#161b22;color:#8b949e;font-size:12px;text-transform:uppercase}
tr:hover{background:#1a1f2e}
pre{background:#161b22;padding:12px;border-radius:6px;overflow-x:auto;font-size:13px;max-height:400px;overflow-y:auto}
.mono{font-family:monospace;font-size:12px}
.badge{padding:2px 8px;border-radius:12px;font-size:11px}
.badge-ok{background:#23863633;color:#3fb950}
.badge-err{background:#da363333;color:#f85149}
.badge-info{background:#1f6feb33;color:#58a6ff}
.card{background:#161b22;border:1px solid #21262d;border-radius:6px;padding:16px;margin:12px 0}
.row{display:flex;gap:16px;flex-wrap:wrap}
.col{flex:1;min-width:300px}
summary{cursor:pointer;color:#58a6ff;padding:4px 0}
summary:hover{color:#79c0ff}
a{color:#58a6ff}</style>
</head><body>
<nav>
  <a href="/">Dashboard</a>
  <a href="/segments">Segments</a>
  <a href="/skills">Skills</a>
  <a href="/validator">Validator</a>
</nav>
__CONTENT__
<p style="color:#484f58;font-size:12px;margin-top:40px;text-align:center">
  Pipeline v0.3 Dashboard | DB: __DB_PATH__
</p>
</body></html>"""


def render(content: str) -> str:
    return _BASE.replace("__CONTENT__", content).replace("__DB_PATH__", DB_PATH)


# --- Routes -----------------------------------------------------------------


@app.route("/")
def index():
    segs = _query("SELECT COUNT(*) as cnt FROM segments")
    analyses = _query("SELECT COUNT(*) as cnt FROM execution_analyses")
    skills = _query("SELECT COUNT(*) as cnt FROM skill_records WHERE is_active=1")
    traces = _query("SELECT COUNT(*) as cnt FROM analysis_traces")
    cases = _query("SELECT COUNT(*) as cnt FROM validator_test_cases")

    recent = _query(
        "SELECT id, user_msg, stats_json, created_at FROM segments ORDER BY created_at DESC LIMIT 5"
    )

    cards = "".join(
        f'<div class="col"><div class="card"><h2>{label}</h2><h1>{count}</h1></div></div>'
        for label, count in [
            ("Segments", segs[0]["cnt"] if segs else 0),
            ("Analyses", analyses[0]["cnt"] if analyses else 0),
            ("Active Skills", skills[0]["cnt"] if skills else 0),
            ("Analysis Traces", traces[0]["cnt"] if traces else 0),
            ("Validator Cases", cases[0]["cnt"] if cases else 0),
        ]
    )

    seg_rows = "".join(
        f'<tr><td class="mono">{r["id"][:12]}...</td>'
        f'<td>{r["user_msg"][:80]}</td>'
        f'<td class="mono">{r["created_at"][:19] if r["created_at"] else ""}</td></tr>'
        for r in recent
    )

    content = f"""
    <h1>Pipeline v0.3 Dashboard</h1>
    <div class="row">{cards}</div>
    <h2>Recent Segments</h2>
    <table><tr><th>ID</th><th>Task</th><th>Created</th></tr>{seg_rows}</table>
    """
    return render(content)


@app.route("/segments")
def segments_view():
    rows = _query(
        "SELECT id, session_id, user_msg, user_msg_index, stats_json, prev_id, next_id, created_at "
        "FROM segments ORDER BY created_at DESC LIMIT 100"
    )
    seg_rows = "".join(
        f'<tr>'
        f'<td><a href="/segment/{r["id"]}">{r["id"][:12]}...</a></td>'
        f'<td>{r["user_msg"][:100]}</td>'
        f'<td class="mono">{r["session_id"][:12]}...</td>'
        f'<td>{r["user_msg_index"]}</td>'
        f'<td>{"✓" if r["next_id"] else "…"}</td>'
        f'</tr>'
        for r in rows
    )
    content = f"""
    <h1>Segments</h1>
    <table><tr><th>ID</th><th>Task</th><th>Session</th><th>#</th><th>Done</th></tr>{seg_rows}</table>
    """
    return render(content)


@app.route("/segment/<seg_id>")
def segment_detail(seg_id):
    r = _query_one("SELECT * FROM segments WHERE id = ?", (seg_id,))
    if not r:
        return render("<h1>Not Found</h1>"), 404

    try:
        stats = json.loads(r["stats_json"])
        exec_entries = json.loads(r["execution_json"])
        skills = json.loads(r["skills_available"])
    except (json.JSONDecodeError, KeyError):
        stats = {}
        exec_entries = []
        skills = []

    execution_html = ""
    for e in exec_entries:
        role = e.get("role", "?")
        content = (e.get("content", "") or "")[:500]
        tool_calls = e.get("tool_calls", [])
        is_err = e.get("is_tool_result") and ("error" in content.lower()[:50])

        cls = "badge-err" if is_err else "badge-ok" if role == "assistant" else "badge-info"
        execution_html += f'<div style="margin:4px 0">'
        execution_html += f'<span class="badge {cls}">{role}</span> '
        if tool_calls:
            execution_html += " ".join(
                f'<code>{tc.get("name","?")}</code>' for tc in tool_calls
            )
        if content:
            content = content.replace("<", "&lt;")
            execution_html += f'<details><summary>{content[:100]}...</summary><pre>{content}</pre></details>'
        execution_html += '</div>'

    analysis = _query_one(
        "SELECT * FROM execution_analyses WHERE segment_id = ?", (seg_id,)
    )

    content = f"""<h1>Segment {r["id"][:12]}...</h1>
    <div class="card"><strong>Task:</strong> {r["user_msg"]}</div>
    <div class="row">
      <div class="col"><div class="card">
        <h2>Stats</h2>
        <p>Tools: {stats.get("tool_count",0)} | Iterations: {stats.get("iteration_count",0)} | Status: {stats.get("status","?")}</p>
        <p>Skills: {", ".join(skills) or "none"} | Chars: {stats.get("total_chars",0)}</p>
      </div></div>
      <div class="col"><div class="card">
        <h2>Chain</h2>
        <p>Prev: {r["prev_id"][:12] if r["prev_id"] else "none"}...</p>
        <p>Next: {r["next_id"][:12] if r["next_id"] else "none"}...</p>
      </div></div>
    </div>
    <h2>Execution Trace ({len(exec_entries)} entries)</h2>
    {execution_html}
    <h2>Analysis</h2>
    {"<pre>" + analysis["execution_note"][:2000] + "</pre>" if analysis and analysis.get("execution_note") else "<p>Not yet analyzed</p>"}
    """
    return render(content)


@app.route("/skills")
def skills_view():
    rows = _query("""
        SELECT skill_id, name, total_selections, total_applied, total_completions,
               total_fallbacks, is_active, last_updated
        FROM skill_records ORDER BY total_selections DESC
    """)
    skill_rows = "".join(
        f'<tr>'
        f'<td>{r["name"]}</td>'
        f'<td>{r["total_selections"]}</td>'
        f'<td>{r["total_applied"]}</td>'
        f'<td>{round(r["total_completions"]/max(1,r["total_applied"]),2):.0%}</td>'
        f'<td><span class="badge {"badge-ok" if r["is_active"] else "badge-err"}">{"active" if r["is_active"] else "inactive"}</span></td>'
        f'</tr>'
        for r in rows
    ) if rows else "<tr><td colspan=5>No skill records yet. Run bootstrap or analyze segments to populate.</td></tr>"

    content = f"""<h1>Skills</h1>
    <table><tr><th>Name</th><th>Selections</th><th>Applied</th><th>Completion%</th><th>Status</th></tr>{skill_rows}</table>
    """
    return render(content)


@app.route("/validator")
def validator_view():
    cases = _query(
        "SELECT * FROM validator_test_cases ORDER BY created_at DESC LIMIT 100"
    )
    runs = _query(
        "SELECT * FROM validator_runs ORDER BY created_at DESC LIMIT 50"
    )

    case_rows = "".join(
        f'<tr><td class="mono">{r["skill_id"]}</td><td>{r["input_desc"][:100]}</td>'
        f'<td>{r["expected_behavior"][:150]}</td><td>{r["source"]}</td></tr>'
        for r in cases
    ) if cases else "<tr><td colspan=4>No test cases yet</td></tr>"

    run_rows = "".join(
        f'<tr><td class="mono">{r["skill_id"]}</td>'
        f'<td><span class="badge {"badge-ok" if r["verdict"]=="pass" else "badge-err"}">{r["verdict"]}</span></td>'
        f'<td>{r["reason"][:100]}</td><td class="mono">{r["created_at"][:19]}</td></tr>'
        for r in runs
    ) if runs else "<tr><td colspan=4>No runs yet</td></tr>"

    content = f"""<h1>Validator</h1>
    <div class="card"><strong>Test Cases:</strong> {len(cases)} | <strong>Validation Runs:</strong> {len(runs)}</div>
    <h2>Test Cases</h2><table><tr><th>Skill</th><th>Input</th><th>Expected</th><th>Source</th></tr>{case_rows}</table>
    <h2>Recent Runs</h2><table><tr><th>Skill</th><th>Verdict</th><th>Reason</th><th>Time</th></tr>{run_rows}</table>
    """
    return render(content)


# --- Entry point ------------------------------------------------------------


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=7788)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()

    if not Path(DB_PATH).exists():
        print(f"Warning: Database not found at {DB_PATH}")
        print("Run bootstrap_pipeline.py first to initialize.")

    print(f"Pipeline Dashboard → http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
