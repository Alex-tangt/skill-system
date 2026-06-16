#!/usr/bin/env python3
"""Bootstrap the pipeline v0.3 for production use.

Initializes the database, seeds Validator with initial test cases,
and runs Phase A analysis on the current session's segments.

Usage:
    python3 scripts/bootstrap_pipeline.py              # analyze all tool-using segments
    python3 scripts/bootstrap_pipeline.py --dry-run    # segment only, no LLM calls
    python3 scripts/bootstrap_pipeline.py --seed-only  # only seed Validator
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def main() -> None:
    args = parse_args()

    from skill_engine.pipeline import (
        TranscriptReader, Segmenter, PipelineStore, Validator,
        AnalyzerEvolverRunner,
    )
    from skill_engine.pipeline.llm_client_impl import AnthropicLLMClient

    db_path = os.environ.get("SKILL_ENGINE_PIPELINE_DB", "./traces/pipeline.db")
    print(f"Database: {db_path}")

    # 1. Initialize infrastructure
    store = PipelineStore(db_path)
    await store.initialize()
    print("PipelineStore initialized")

    validator = Validator(db_path)
    await validator.initialize()
    print("Validator initialized")

    # 2. Seed Validator with baseline test cases
    if not args.skip_seed:
        await seed_validator(validator)
        suite = await validator.run_test_suite("dev-diary")
        print(f"Validator: {len(suite['test_cases'])} test cases")

    # 3. Segment current session
    try:
        reader = TranscriptReader(str(TranscriptReader.resolve_from_env()))
    except (RuntimeError, FileNotFoundError) as e:
        print(f"Cannot read transcript: {e}")
        print("Set CLAUDE_CODE_SESSION_ID or run inside Claude Code")
        if args.dry_run:
            return
        sys.exit(1)

    segments = Segmenter(reader).segment()
    print(f"Segments: {len(segments)}")

    for seg in segments:
        await store.segments.save(seg)
    print("All segments saved")

    if args.dry_run:
        tool_segs = [s for s in segments if json.loads(s.stats_json)["tool_count"] > 0]
        print(f"Tool-using segments: {len(tool_segs)} (dry run, no LLM calls)")
        for i, s in enumerate(tool_segs[:5]):
            stats = json.loads(s.stats_json)
            print(f"  [{i}] {s.user_msg[:80]}... (tools={stats['tool_count']})")
        return

    # 4. Run Phase A on all tool-using segments
    if args.seed_only:
        return

    tool_segs = [s for s in segments if json.loads(s.stats_json)["tool_count"] > 0]
    print(f"\nAnalyzing {len(tool_segs)} tool-using segments...")

    llm = AnthropicLLMClient()
    runner = AnalyzerEvolverRunner(
        llm_client=llm, segment_store=store.segments,
        pipeline_store=store, validator=validator, model=llm.model,
    )

    for i, seg in enumerate(tool_segs):
        print(f"\n[{i+1}/{len(tool_segs)}] {seg.user_msg[:80]}...")
        analysis, patch = await runner.analyze_and_evolve(seg.id)
        if analysis:
            print(f"  Diagnosis: {len(analysis.diagnosis)} chars")
            print(f"  Patch: {'PRODUCED' if patch else 'none (no skills referenced)'}")
        else:
            print(f"  FAILED")

    # 5. Summary
    traces = await store.get_recent_traces(100)
    analyses = sum(1 for t in traces if t.get("status") == "success")
    print(f"\n=== Bootstrap Complete ===")
    print(f"Segments: {len(segments)}")
    print(f"Analyses: {analyses}")
    print(f"Analysis traces: {len(traces)}")


async def seed_validator(validator) -> None:
    """Seed the Validator with baseline test cases for existing skills."""
    cases = [
        ("dev-diary", "User wants to add a development diary entry",
         "Use diary.py script with --file, --operation, --title, --description flags"),
        ("hello-world", "User asks for a greeting",
         "Output a friendly greeting message"),
        ("run-tests", "User wants to run project tests",
         "Execute pytest with correct flags and report results"),
        ("markdown-stats", "User wants statistics about markdown files",
         "Count and analyze markdown files in the specified directory"),
        ("git-status", "User wants to check git repository status",
         "Show current branch, modified files, and commit status"),
    ]
    for skill_id, desc, expected in cases:
        await validator.add_test_case(skill_id, desc, expected, "bootstrap")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap pipeline v0.3")
    p.add_argument("--dry-run", action="store_true", help="Segment only, no LLM calls")
    p.add_argument("--seed-only", action="store_true", help="Only seed Validator")
    p.add_argument("--skip-seed", action="store_true", help="Skip Validator seeding")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
