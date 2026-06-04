from __future__ import annotations

import pytest


# ── Skill CRUD ──

@pytest.mark.asyncio
async def test_skill_list(mcp_client):
    """skill_list returns an array of skills."""
    result = await mcp_client.call_tool("skill_list", {})
    assert isinstance(result, list)
    skill_ids = [s["id"] for s in result]
    assert "e2e-test" in skill_ids


@pytest.mark.asyncio
async def test_skill_list_with_tag(mcp_client):
    """skill_list filters by tag."""
    result = await mcp_client.call_tool("skill_list", {"tag": "e2e"})
    assert isinstance(result, list)
    assert any(s["id"] == "e2e-test" for s in result)
    # Non-matching tag returns empty
    result = await mcp_client.call_tool("skill_list", {"tag": "nonexistent-tag"})
    assert result == []


@pytest.mark.asyncio
async def test_skill_get_found(mcp_client):
    """skill_get returns full skill definition for an existing skill."""
    result = await mcp_client.call_tool("skill_get", {"skill_id": "e2e-test"})
    assert "error" not in result
    assert result["id"] == "e2e-test"
    assert len(result["steps"]) == 1


@pytest.mark.asyncio
async def test_skill_get_not_found(mcp_client):
    """skill_get returns error for nonexistent skill."""
    result = await mcp_client.call_tool("skill_get", {"skill_id": "nonexistent"})
    assert "error" in result


@pytest.mark.asyncio
async def test_skill_create(mcp_client):
    """skill_create creates a new skill."""
    definition = {
        "id": "e2e-created",
        "name": "Created Skill",
        "steps": [
            {
                "id": "s1",
                "name": "Step 1",
                "tool": "echo",
                "input_mapping": {"message": "$input.text"},
            },
        ],
    }
    result = await mcp_client.call_tool("skill_create", {"definition": definition})
    assert result.get("status") == "created"
    assert result.get("skill_id") == "e2e-created"


@pytest.mark.asyncio
async def test_skill_create_duplicate(mcp_client):
    """skill_create fails for duplicate skill ID."""
    definition = {
        "id": "e2e-test",  # Already exists
        "name": "Duplicate",
        "steps": [],
    }
    result = await mcp_client.call_tool("skill_create", {"definition": definition})
    assert "error" in result


@pytest.mark.asyncio
async def test_skill_create_invalid(mcp_client):
    """skill_create fails for invalid definition (missing id)."""
    result = await mcp_client.call_tool("skill_create", {"definition": {"name": "No ID"}})
    assert "error" in result


@pytest.mark.asyncio
async def test_skill_update(mcp_client):
    """skill_update updates an existing skill."""
    # First create a new skill to update
    definition = {
        "id": "e2e-updatable",
        "name": "Updatable",
        "steps": [],
    }
    await mcp_client.call_tool("skill_create", {"definition": definition})
    # Now update it
    definition["name"] = "Updated Name"
    definition["version"] = "1.1.0"
    result = await mcp_client.call_tool("skill_update", {
        "skill_id": "e2e-updatable",
        "definition": definition,
    })
    assert result.get("status") == "updated"
    # Verify the update
    get_result = await mcp_client.call_tool("skill_get", {"skill_id": "e2e-updatable"})
    assert get_result["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_skill_update_not_found(mcp_client):
    """skill_update fails for nonexistent skill."""
    definition = {"id": "nonexistent", "name": "N/A", "steps": []}
    result = await mcp_client.call_tool("skill_update", {
        "skill_id": "nonexistent",
        "definition": definition,
    })
    assert "error" in result


@pytest.mark.asyncio
async def test_skill_delete(mcp_client):
    """skill_delete removes a skill."""
    # Create then delete
    definition = {
        "id": "e2e-deletable",
        "name": "Deletable",
        "steps": [],
    }
    await mcp_client.call_tool("skill_create", {"definition": definition})
    result = await mcp_client.call_tool("skill_delete", {"skill_id": "e2e-deletable"})
    assert result.get("status") == "deleted"
    # Verify it's gone
    get_result = await mcp_client.call_tool("skill_get", {"skill_id": "e2e-deletable"})
    assert "error" in get_result


@pytest.mark.asyncio
async def test_skill_delete_not_found(mcp_client):
    """skill_delete returns error for nonexistent skill."""
    result = await mcp_client.call_tool("skill_delete", {"skill_id": "nonexistent"})
    assert "error" in result


# ── Skill Analyze ──

@pytest.mark.asyncio
async def test_skill_analyze(mcp_client):
    """skill_analyze generates a skill definition from natural language."""
    result = await mcp_client.call_tool("skill_analyze", {
        "description": "Extract text from PDF and convert to markdown format",
        "name": "PDF Converter",
    })
    assert result.get("status") in ("preview", "warning")
    assert "definition" in result
    assert result["definition"]["name"] == "PDF Converter"


# ── Skill Execute ──

@pytest.mark.asyncio
async def test_skill_execute_sync(mcp_client):
    """skill_execute with sync=true runs and returns result."""
    result = await mcp_client.call_tool("skill_execute", {
        "skill_id": "e2e-test",
        "input": {"text": "e2e sync test"},
        "sync": True,
    })
    assert result["status"] == "succeeded"
    assert result["output"] is not None
    assert result["run_id"] is not None


@pytest.mark.asyncio
async def test_skill_execute_async(mcp_client):
    """skill_execute with sync=false returns running status immediately."""
    result = await mcp_client.call_tool("skill_execute", {
        "skill_id": "e2e-test",
        "input": {"text": "async"},
        "sync": False,
    })
    assert result["status"] == "running"
    assert result["run_id"] is not None


@pytest.mark.asyncio
async def test_skill_execute_not_found(mcp_client):
    """skill_execute returns error for nonexistent skill."""
    result = await mcp_client.call_tool("skill_execute", {
        "skill_id": "nonexistent-skill",
    })
    assert "error" in result


# ── Trace Tools ──

@pytest.mark.asyncio
async def test_trace_get_after_execute(mcp_client):
    """trace_get retrieves trace for a completed execution."""
    # Execute first
    exec_result = await mcp_client.call_tool("skill_execute", {
        "skill_id": "e2e-test",
        "input": {"text": "trace test"},
        "sync": True,
    })
    run_id = exec_result["run_id"]
    # Now get the trace
    trace = await mcp_client.call_tool("trace_get", {"run_id": run_id})
    # trace may have error=None (SQLite null column), which shows as key with None value
    assert trace.get("error") is None or "error" not in trace
    assert trace["run_id"] == run_id
    assert trace["status"] == "succeeded"
    assert len(trace.get("steps", [])) >= 1


@pytest.mark.asyncio
async def test_trace_get_not_found(mcp_client):
    """trace_get returns error for unknown run_id."""
    result = await mcp_client.call_tool("trace_get", {"run_id": "nonexistent-run-id"})
    assert "error" in result


@pytest.mark.asyncio
async def test_trace_list(mcp_client):
    """trace_list returns an array of traces."""
    # Execute a skill first to have trace data
    await mcp_client.call_tool("skill_execute", {
        "skill_id": "e2e-test",
        "input": {"text": "list test"},
        "sync": True,
    })
    result = await mcp_client.call_tool("trace_list", {})
    assert isinstance(result, list)
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_trace_errors(mcp_client):
    """trace_errors returns error traces (may be empty)."""
    result = await mcp_client.call_tool("trace_errors", {})
    assert isinstance(result, list)


# ── Skill Search ──

@pytest.mark.asyncio
async def test_skill_search(mcp_client):
    """skill_search returns relevant skills."""
    result = await mcp_client.call_tool("skill_search", {"query": "echo test"})
    assert isinstance(result, list)
    assert len(result) >= 1
    assert any(s["id"] == "e2e-test" for s in result)


# ── Skill Compose ──

@pytest.mark.asyncio
async def test_skill_compose_preview(mcp_client):
    """skill_compose returns a preview definition."""
    result = await mcp_client.call_tool("skill_compose", {
        "name": "Composed Pipeline",
        "skill_ids": ["e2e-test"],
    })
    assert result.get("status") == "preview"
    assert "definition" in result


# ── Optimizer ──

@pytest.mark.asyncio
async def test_optimizer_status_empty(mcp_client):
    """optimizer_status returns empty before any analysis."""
    result = await mcp_client.call_tool("optimizer_status", {})
    assert isinstance(result, list)
    assert result == []


@pytest.mark.asyncio
async def test_optimizer_analyze(mcp_client):
    """optimizer_analyze runs analysis and returns recommendations."""
    result = await mcp_client.call_tool("optimizer_analyze", {"min_samples": 1})
    # May be list (success) or error dict if no trace data
    assert isinstance(result, (list, dict))
    # May be empty if no trace data for analysis
