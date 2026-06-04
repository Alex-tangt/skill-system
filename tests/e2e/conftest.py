from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import time
import pytest
import pytest_asyncio


class MCPTestClient:
    """A lightweight MCP JSON-RPC client that communicates with the server via stdio.

    Usage:
        async with MCPTestClient(skills_dir="...") as client:
            result = await client.call_tool("skill_list", {})
    """

    def __init__(self, skills_dir: str, traces_db: str | None = None):
        self.skills_dir = skills_dir
        self.traces_db = traces_db or os.path.join(skills_dir, "..", "traces.db")
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._reader_task: asyncio.Task | None = None
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._initialized = False
        self._available_tools: dict[str, dict] = {}

    async def __aenter__(self):
        env = {
            **os.environ,
            "SKILL_ENGINE_SKILLS_DIR": self.skills_dir,
            "SKILL_ENGINE_TRACES_DB": self.traces_db,
        }
        self._process = subprocess.Popen(
            ["python3", "-m", "skill_engine.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        # Start reader
        self._reader_task = asyncio.create_task(self._read_responses())
        # Initialize MCP session
        await self._initialize()
        return self

    async def __aexit__(self, *args):
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._process:
            self._process.stdin.close()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

    async def _read_responses(self):
        """Read JSON-RPC responses from the server's stdout."""
        while self._process and self._process.stdout:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self._process.stdout.readline
                )
                if not line:
                    break
                line = line.strip()
                if line:
                    try:
                        msg = json.loads(line)
                        await self._response_queue.put(msg)
                    except json.JSONDecodeError:
                        pass
            except Exception:
                break

    async def _send(self, msg: dict):
        """Send a JSON-RPC message to the server."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Server process not running")
        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()

    async def _request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        self._request_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        await self._send(req)
        # Wait for matching response
        timeout = 10.0
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = await asyncio.wait_for(
                    self._response_queue.get(), timeout=timeout - (time.time() - start)
                )
                if msg.get("id") == self._request_id:
                    if "error" in msg:
                        return {"error": msg["error"]}
                    return msg.get("result", msg)
            except asyncio.TimeoutError:
                break
        raise TimeoutError(f"No response for {method} within {timeout}s")

    async def _initialize(self):
        """Perform MCP initialization handshake."""
        result = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        })
        # Send initialized notification (no id)
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        # Discover tools
        tools_result = await self._request("tools/list", {})
        tools = tools_result.get("tools", [])
        for tool in tools:
            self._available_tools[tool["name"]] = tool
        self._initialized = True

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call an MCP tool and return the parsed JSON result."""
        result = await self._request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        # MCP tool result is in content[0].text
        content = result.get("content", [])
        if content and isinstance(content, list):
            text = content[0].get("text", "{}") if content else "{}"
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw_text": text}
        return result

    @property
    def tool_names(self) -> list[str]:
        return list(self._available_tools.keys())


@pytest.fixture
def test_skills_dir():
    """Create a temp directory with a simple test skill YAML."""
    with tempfile.TemporaryDirectory() as d:
        import yaml
        # Write a test skill for E2E tests
        skill_yaml = {
            "id": "e2e-test",
            "name": "E2E Test Skill",
            "version": "1.0.0",
            "description": "A test skill for E2E tests",
            "tags": ["test", "e2e"],
            "steps": [
                {
                    "id": "echo1",
                    "name": "Echo Step",
                    "tool": "echo",
                    "input_mapping": {"message": "$input.text"},
                    "success_criteria": {"type": "always"},
                    "failure_criteria": {"type": "exception"},
                    "retry": {"max_attempts": 1, "backoff": "none", "backoff_base_seconds": 1.0},
                    "timeout_seconds": 10,
                },
            ],
        }
        with open(os.path.join(d, "e2e-test.yaml"), "w") as f:
            yaml.safe_dump(skill_yaml, f, default_flow_style=False)
        yield d


@pytest.fixture
def test_traces_db():
    """Create a temp file for traces.db."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest_asyncio.fixture
async def mcp_client(test_skills_dir, test_traces_db):
    """An initialized MCPTestClient connected to a fresh server instance."""
    async with MCPTestClient(skills_dir=test_skills_dir, traces_db=test_traces_db) as client:
        yield client
