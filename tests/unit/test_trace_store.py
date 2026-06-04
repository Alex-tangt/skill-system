from __future__ import annotations

import asyncio
import os
import tempfile
import pytest
from skill_engine.storage.trace_store import TraceStore
from skill_engine.models.trace import ExecutionTrace, StepTrace


@pytest.fixture
def initialized_store():
    """Create a TraceStore with a temp database, initialize it, and return."""
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


def _make_step_trace(trace_id="trace-run-001", step_id="s1", step_name="Step 1",
                     status="succeeded", output=None, error=None, retry_count=0,
                     step_trace_id="st-001"):
    return StepTrace(
        id=step_trace_id,
        trace_id=trace_id,
        step_id=step_id,
        step_name=step_name,
        started_at=1000.0,
        finished_at=1001.0,
        status=status,
        input={"msg": "hello"},
        output=output or {"result": "ok"},
        error=error,
        retry_count=retry_count,
    )


@pytest.mark.asyncio
async def test_initialize_creates_tables(initialized_store):
    """After initialize(), both tables exist and are queryable."""
    import aiosqlite
    async with aiosqlite.connect(initialized_store.db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] async for row in cursor]
    assert "execution_traces" in tables
    assert "step_traces" in tables


@pytest.mark.asyncio
async def test_insert_and_get_trace(initialized_store):
    """Insert a trace then retrieve it by run_id."""
    trace = _make_trace(run_id="run-abc")
    await initialized_store.insert_trace(trace)
    result = await initialized_store.get_trace("run-abc")
    assert result is not None
    assert result["run_id"] == "run-abc"
    assert result["skill_id"] == "test-skill"
    assert result["status"] == "running"


@pytest.mark.asyncio
async def test_get_trace_nonexistent(initialized_store):
    """get_trace returns None for an unknown run_id."""
    result = await initialized_store.get_trace("nonexistent-run")
    assert result is None


@pytest.mark.asyncio
async def test_update_trace(initialized_store):
    """update_trace persists status, output, and finished_at."""
    trace = _make_trace(run_id="run-update")
    await initialized_store.insert_trace(trace)
    trace.status = "succeeded"
    trace.output = {"result": "done"}
    trace.finished_at = 2000.0
    await initialized_store.update_trace(trace)
    result = await initialized_store.get_trace("run-update")
    assert result["status"] == "succeeded"
    assert result["finished_at"] is not None


@pytest.mark.asyncio
async def test_upsert_step_trace_insert(initialized_store):
    """First upsert_step_trace call inserts a new row."""
    trace = _make_trace(run_id="run-upsert")
    await initialized_store.insert_trace(trace)
    st = _make_step_trace(trace_id=trace.id, step_trace_id="st-new")
    await initialized_store.upsert_step_trace(st)
    result = await initialized_store.get_trace("run-upsert")
    assert len(result["steps"]) == 1
    assert result["steps"][0]["step_id"] == "s1"


@pytest.mark.asyncio
async def test_upsert_step_trace_update(initialized_store):
    """Second upsert_step_trace call updates the existing row."""
    trace = _make_trace(run_id="run-upsert2")
    await initialized_store.insert_trace(trace)
    st = _make_step_trace(trace_id=trace.id, step_trace_id="st-update",
                          status="running", output=None)
    await initialized_store.upsert_step_trace(st)
    st.status = "succeeded"
    st.output = {"result": "ok"}
    await initialized_store.upsert_step_trace(st)
    result = await initialized_store.get_trace("run-upsert2")
    assert result["steps"][0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_list_traces_filtered_by_skill_id(initialized_store):
    """list_traces filters by skill_id."""
    await initialized_store.insert_trace(_make_trace(skill_id="skill-a", run_id="ra1"))
    await initialized_store.insert_trace(_make_trace(skill_id="skill-b", run_id="rb1"))
    results = await initialized_store.list_traces(skill_id="skill-a")
    assert len(results) == 1
    assert results[0]["skill_id"] == "skill-a"


@pytest.mark.asyncio
async def test_list_traces_filtered_by_status(initialized_store):
    """list_traces filters by status."""
    await initialized_store.insert_trace(_make_trace(run_id="r1", status="succeeded"))
    await initialized_store.insert_trace(_make_trace(run_id="r2", status="failed"))
    results = await initialized_store.list_traces(status="failed")
    assert len(results) == 1
    assert results[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_list_traces_respects_limit(initialized_store):
    """list_traces respects the limit parameter."""
    for i in range(5):
        await initialized_store.insert_trace(_make_trace(run_id=f"run-{i}"))
    results = await initialized_store.list_traces(limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_get_error_traces_only_failed(initialized_store):
    """get_error_traces only returns failed traces with failed steps."""
    # Insert a failed trace with a failed step
    trace = _make_trace(run_id="run-err", status="failed", error="boom")
    await initialized_store.insert_trace(trace)
    st = _make_step_trace(trace_id=trace.id, step_trace_id="st-err",
                          status="failed", error="step boom", retry_count=2)
    await initialized_store.upsert_step_trace(st)
    # Insert a succeeded trace with succeeded step (should not appear)
    trace2 = _make_trace(run_id="run-ok", status="succeeded")
    await initialized_store.insert_trace(trace2)
    st2 = _make_step_trace(trace_id=trace2.id, step_trace_id="st-ok",
                           status="succeeded")
    await initialized_store.upsert_step_trace(st2)
    errors = await initialized_store.get_error_traces()
    assert len(errors) == 1
    assert errors[0]["run_id"] == "run-err"
    assert errors[0]["step_error"] == "step boom"
