from __future__ import annotations

from abc import ABC, abstractmethod


class BasePlugin(ABC):
    """Abstract base for all Skill Engine plugins.

    Plugins implement this interface to register with the kernel.
    Internal plugins share the kernel process; external plugins run
    as independent MCP servers connected via ClientSession.

    Version negotiation: kernel checks api_version on registration.
    Incompatible versions are rejected.
    """

    api_version: str = "0.2"

    def __init__(self, name: str, config: dict | None = None):
        self.name = name
        self.config = config or {}

    @abstractmethod
    async def initialize(self) -> None:
        """Called once after registration. Set up resources, connections, etc."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the plugin is healthy and ready to serve requests."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Called on kernel shutdown or plugin unload. Clean up resources."""
        ...

    @abstractmethod
    def list_mcp_tools(self) -> list[dict]:
        """Return declarations of MCP tools this plugin exposes.

        Each dict: {"name": "...", "description": "...", "inputSchema": {...}}
        """
        ...

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Invoke a plugin MCP tool by name. Returns JSON string result."""
        ...
