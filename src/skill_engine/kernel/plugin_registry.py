from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PluginHandle:
    """Handle to a registered plugin (typically an independent MCP server)."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    health_status: str = "unknown"  # unknown | healthy | unhealthy
    registered_tools: list[str] = field(default_factory=list)
    mcp_server_command: str | None = None  # e.g. "python3 -m plugins.optimizer"


class PluginRegistry:
    """Maps plugin names to PluginHandle objects.

    Replaces the v0.1.0 ToolRegistry. In v0.2, plugins are independent MCP
    servers that register with the kernel, not Python callables.
    """

    def __init__(self):
        self._plugins: dict[str, PluginHandle] = {}

    def register(self, name: str, handle: PluginHandle) -> None:
        if name in self._plugins:
            raise ValueError(f"Plugin '{name}' is already registered")
        self._plugins[name] = handle

    def unregister(self, name: str) -> bool:
        if name in self._plugins:
            del self._plugins[name]
            return True
        return False

    def get(self, name: str) -> PluginHandle | None:
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        return list(self._plugins.keys())

    def list_healthy(self) -> list[PluginHandle]:
        return [h for h in self._plugins.values() if h.health_status == "healthy"]
