from __future__ import annotations

import pytest


class TestCRUDPipeline:
    @pytest.mark.asyncio
    async def test_full_crud_lifecycle(self, mcp_client):
        """Create → Get → Update → Get → Delete → Get(not found)."""
        # Create
        definition = {
            "id": "pipeline-crud",
            "name": "Pipeline CRUD",
            "steps": [{"id": "s1", "name": "S1", "tool": "echo",
                        "input_mapping": {"message": "$input.text"}}],
        }
        create_result = await mcp_client.call_tool("skill_create", {"definition": definition})
        assert create_result["status"] == "created"

        # Get (exists)
        get_result = await mcp_client.call_tool("skill_get", {"skill_id": "pipeline-crud"})
        assert get_result["id"] == "pipeline-crud"

        # Update
        definition["name"] = "Updated Pipeline"
        definition["version"] = "1.1.0"
        update_result = await mcp_client.call_tool("skill_update", {
            "skill_id": "pipeline-crud",
            "definition": definition,
        })
        assert update_result["status"] == "updated"

        # Get (updated)
        get_updated = await mcp_client.call_tool("skill_get", {"skill_id": "pipeline-crud"})
        assert get_updated["name"] == "Updated Pipeline"

        # Delete
        delete_result = await mcp_client.call_tool("skill_delete", {"skill_id": "pipeline-crud"})
        assert delete_result["status"] == "deleted"

        # Get (not found)
        get_deleted = await mcp_client.call_tool("skill_get", {"skill_id": "pipeline-crud"})
        assert "error" in get_deleted


class TestExecutePipeline:
    @pytest.mark.asyncio
    async def test_create_execute_trace(self, mcp_client):
        """Create skill → Execute → Trace Get → Trace List."""
        # Create
        definition = {
            "id": "pipeline-exec",
            "name": "Pipeline Exec",
            "steps": [
                {"id": "s1", "name": "S1", "tool": "echo",
                 "input_mapping": {"message": "$input.text"}},
            ],
        }
        await mcp_client.call_tool("skill_create", {"definition": definition})

        # Execute
        exec_result = await mcp_client.call_tool("skill_execute", {
            "skill_id": "pipeline-exec",
            "input": {"text": "pipeline test"},
            "sync": True,
        })
        assert exec_result["status"] == "succeeded"
        run_id = exec_result["run_id"]

        # Trace Get
        trace = await mcp_client.call_tool("trace_get", {"run_id": run_id})
        assert trace["run_id"] == run_id
        assert trace["status"] == "succeeded"

        # Trace List
        traces = await mcp_client.call_tool("trace_list", {"skill_id": "pipeline-exec"})
        assert len(traces) >= 1
        assert traces[0]["skill_id"] == "pipeline-exec"

    @pytest.mark.asyncio
    async def test_execute_failure_tracing(self, mcp_client):
        """Execute a failing skill and verify trace captures the error."""
        definition = {
            "id": "pipeline-fail",
            "name": "Pipeline Fail",
            "steps": [
                {"id": "s1", "name": "S1", "tool": "nonexistent_command_xyz",
                 "input_mapping": {}},
            ],
        }
        await mcp_client.call_tool("skill_create", {"definition": definition})

        exec_result = await mcp_client.call_tool("skill_execute", {
            "skill_id": "pipeline-fail",
            "sync": True,
        })
        assert exec_result["status"] == "failed"

        trace = await mcp_client.call_tool("trace_get", {"run_id": exec_result["run_id"]})
        assert trace["status"] == "failed"


class TestComposePipeline:
    @pytest.mark.asyncio
    async def test_compose_preview_then_create(self, mcp_client):
        """Preview composed skill, then create and execute it."""
        # First create two skills to compose
        for i, sid in enumerate(["pipe-a", "pipe-b"]):
            definition = {
                "id": sid,
                "name": f"Pipe {sid}",
                "steps": [
                    {"id": f"step{i+1}", "name": f"Step {i+1}", "tool": "echo",
                     "input_mapping": {"message": "$input.text"}},
                ],
            }
            await mcp_client.call_tool("skill_create", {"definition": definition})

        # Compose
        compose_result = await mcp_client.call_tool("skill_compose", {
            "name": "My Pipe",
            "skill_ids": ["pipe-a", "pipe-b"],
        })
        assert compose_result["status"] == "preview"
        assert "definition" in compose_result

        # Create the composed skill
        comp_def = compose_result["definition"]
        comp_def["id"] = "my-pipe"
        create_result = await mcp_client.call_tool("skill_create", {"definition": comp_def})
        assert create_result["status"] == "created"


class TestErrorHandlingPipeline:
    @pytest.mark.asyncio
    async def test_skill_create_invalid_json(self, mcp_client):
        """Creating skill with bogus definition fails gracefully."""
        result = await mcp_client.call_tool("skill_create", {"definition": {"not": "valid"}})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_skill_execute_invalid_input(self, mcp_client):
        """Execute with wrong input type returns validation error."""
        # Create a skill with required field
        definition = {
            "id": "pipeline-validate",
            "name": "Validation Test",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "steps": [
                {"id": "s1", "name": "S1", "tool": "echo",
                 "input_mapping": {"message": "$input.text"}},
            ],
        }
        await mcp_client.call_tool("skill_create", {"definition": definition})

        result = await mcp_client.call_tool("skill_execute", {
            "skill_id": "pipeline-validate",
            "input": {},  # missing required "text"
        })
        assert result["status"] == "failed"
        assert "validation" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_trace_get_invalid_run_id(self, mcp_client):
        """trace_get with invalid run_id returns error."""
        result = await mcp_client.call_tool("trace_get", {"run_id": "definitely-not-a-valid-uuid"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_then_execute(self, mcp_client):
        """Executing a deleted skill returns error."""
        definition = {
            "id": "pipeline-gone",
            "name": "Gone",
            "steps": [{"id": "s1", "name": "S1", "tool": "echo",
                        "input_mapping": {"message": "$input.text"}}],
        }
        await mcp_client.call_tool("skill_create", {"definition": definition})
        await mcp_client.call_tool("skill_delete", {"skill_id": "pipeline-gone"})

        result = await mcp_client.call_tool("skill_execute", {
            "skill_id": "pipeline-gone",
        })
        assert "error" in result
