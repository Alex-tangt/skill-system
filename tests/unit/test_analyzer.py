from __future__ import annotations

import asyncio
import os
import tempfile
import pytest
from skill_engine.optimizer.analyzer import TraceAnalyzer, OptimizationRecommendation
from skill_engine.storage.trace_store import TraceStore
from skill_engine.models.trace import ExecutionTrace, StepTrace


@pytest.fixture
def analyzer_store():
    """A TraceStore pre-seeded with trace data for analyzer testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = TraceStore(path)
    asyncio.run(store.initialize())
    yield store
    if os.path.exists(path):
        os.unlink(path)


def _make_trace(skill_id="test-skill", run_id="run-001", status="running",
                input_data=None, output=None, error=None):
    return ExecutionTrace(
        id=f"trace-{run_id}",
        skill_id=skill_id,
        skill_version="1.0.0",
        run_id=run_id,
        status=status,
        input=input_data or {"x": 1},
        output=output,
        error=error,
    )


def _make_step_trace(trace_id, step_id="s1", step_name="Step 1",
                     status="succeeded", output=None, error=None, retry_count=0,
                     step_trace_id="st-001", started_at=1000.0, finished_at=1001.0):
    return StepTrace(
        id=step_trace_id,
        trace_id=trace_id,
        step_id=step_id,
        step_name=step_name,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        input={"msg": "hello"},
        output=output or {"result": "ok"},
        error=error,
        retry_count=retry_count,
    )


async def _seed_failure_hotspot_data(store):
    """10 traces for same skill/step, 6 failed (60% failure rate)."""
    for i in range(10):
        trace = _make_trace(skill_id="hotspot-skill", run_id=f"hs-run-{i}",
                            status="failed" if i < 6 else "succeeded")
        await store.insert_trace(trace)
        st = _make_step_trace(
            trace_id=trace.id, step_trace_id=f"st-hs-{i}",
            status="failed" if i < 6 else "succeeded",
            error="something broke" if i < 6 else None,
        )
        await store.upsert_step_trace(st)


async def _seed_timeout_data(store):
    """6 traces where the step timed out."""
    for i in range(6):
        trace = _make_trace(skill_id="timeout-skill", run_id=f"to-run-{i}", status="failed")
        await store.insert_trace(trace)
        st = _make_step_trace(
            trace_id=trace.id, step_trace_id=f"st-to-{i}",
            status="failed", error="Step 's1' timed out after 60s",
        )
        await store.upsert_step_trace(st)


async def _seed_validation_failure_data(store):
    """5 traces with input validation failures."""
    for i in range(5):
        trace = _make_trace(skill_id="val-skill", run_id=f"val-run-{i}", status="failed",
                            error="Input validation failed: missing required field 'file'")
        await store.insert_trace(trace)


@pytest.mark.asyncio
async def test_detect_failure_hotspots(analyzer_store):
    """Steps with >30% failure rate and enough samples generate recommendations."""
    await _seed_failure_hotspot_data(analyzer_store)
    analyzer = TraceAnalyzer(analyzer_store)
    recs = await analyzer._detect_failure_hotspots(None, min_samples=5)
    assert len(recs) >= 1
    rec = recs[0]
    assert rec.skill_id == "hotspot-skill"
    assert rec.type == "retry_policy"
    assert rec.confidence > 0.3


@pytest.mark.asyncio
async def test_detect_failure_hotspots_below_threshold(analyzer_store):
    """Single failure with many successes should not trigger recommendation."""
    store = analyzer_store
    for i in range(10):
        trace = _make_trace(skill_id="low-fail", run_id=f"lf-run-{i}",
                            status="failed" if i == 0 else "succeeded")
        await store.insert_trace(trace)
        st = _make_step_trace(
            trace_id=trace.id, step_trace_id=f"st-lf-{i}",
            status="failed" if i == 0 else "succeeded",
            error="boom" if i == 0 else None,
        )
        await store.upsert_step_trace(st)
    analyzer = TraceAnalyzer(store)
    recs = await analyzer._detect_failure_hotspots(None, min_samples=5)
    # 1/10 = 10% < 30% threshold
    assert len(recs) == 0


@pytest.mark.asyncio
async def test_detect_timeout_patterns(analyzer_store):
    """Timeout traces generate timeout recommendations."""
    await _seed_timeout_data(analyzer_store)
    analyzer = TraceAnalyzer(analyzer_store)
    recs = await analyzer._detect_timeout_patterns(None, min_samples=1)
    assert len(recs) >= 1
    rec = recs[0]
    assert rec.type == "timeout"
    assert rec.severity == "high"


@pytest.mark.asyncio
async def test_detect_retry_opportunities(analyzer_store):
    """Frequent errors generate retry recommendations."""
    store = analyzer_store
    for i in range(6):
        trace = _make_trace(skill_id="retry-skill", run_id=f"rr-run-{i}", status="failed")
        await store.insert_trace(trace)
        st = _make_step_trace(
            trace_id=trace.id, step_trace_id=f"st-rr-{i}",
            status="failed", error="connection refused",
        )
        await store.upsert_step_trace(st)
    analyzer = TraceAnalyzer(store)
    recs = await analyzer._detect_retry_opportunities(None, min_samples=5)
    assert len(recs) >= 1
    assert recs[0].type == "retry_policy"


@pytest.mark.asyncio
async def test_detect_validation_gaps(analyzer_store):
    """Input validation failures generate add_validation recommendations."""
    await _seed_validation_failure_data(analyzer_store)
    analyzer = TraceAnalyzer(analyzer_store)
    recs = await analyzer._detect_validation_gaps(None, min_samples=1)
    assert len(recs) >= 1
    rec = recs[0]
    assert rec.type == "add_validation"
    assert rec.skill_id == "val-skill"


@pytest.mark.asyncio
async def test_analyze_sorts_by_confidence(analyzer_store):
    """analyze() returns recommendations sorted descending by confidence."""
    store = analyzer_store
    # Create data for multiple pattern types with different confidences
    await _seed_timeout_data(store)
    await _seed_validation_failure_data(store)
    analyzer = TraceAnalyzer(store)
    recs = await analyzer.analyze(min_samples=1)
    # Verify sorted by confidence descending
    for i in range(len(recs) - 1):
        assert recs[i].confidence >= recs[i + 1].confidence
