from __future__ import annotations

import importlib
import os
import yaml
from pathlib import Path

from skill_engine.kernel.plugin_registry import PluginRegistry, PluginHandle
from skill_engine.kernel.plugin_interface import BasePlugin

# Kernel API version. Only plugins with matching api_version are loaded.
KERNEL_API_VERSION = "0.2"


class PluginManager:
    """Loads plugin configuration from plugins.yaml and manages lifecycle.

    Internal plugins are imported and initialized in-process.
    External plugins are spawned as subprocess MCP servers.
    """

    def __init__(self, registry: PluginRegistry, config_path: str = "plugins.yaml"):
        self.registry = registry
        self.config_path = config_path
        self._internal_plugins: dict[str, BasePlugin] = {}
        self._api_errors: dict[str, str] = {}

    def load_config(self) -> dict:
        """Load and parse plugins.yaml."""
        path = Path(self.config_path)
        if not path.exists():
            return {"plugins": {}}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {"plugins": {}}

    async def load_all(self) -> dict[str, str]:
        """Load all plugins from config. Returns {name: status} mapping."""
        config = self.load_config()
        results: dict[str, str] = {}

        for name, plugin_cfg in config.get("plugins", {}).items():
            ptype = plugin_cfg.get("type", "internal")

            if ptype == "internal":
                status = await self._load_internal(name, plugin_cfg)
            elif ptype == "external":
                status = self._load_external(name, plugin_cfg)
            else:
                status = f"Unknown type: {ptype}"

            results[name] = status

        return results

    async def _load_internal(self, name: str, cfg: dict) -> str:
        module_path = cfg.get("module", "")
        if not module_path:
            return "Missing module path"

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            return f"Import failed: {e}"

        # Find the plugin class (first BasePlugin subclass in module)
        plugin_instance = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BasePlugin)
                and attr is not BasePlugin
            ):
                plugin_instance = attr(name, cfg.get("config", {}))
                break

        if plugin_instance is None:
            return "No BasePlugin subclass found in module"

        # Version check
        if plugin_instance.api_version != KERNEL_API_VERSION:
            msg = f"API version mismatch: plugin={plugin_instance.api_version}, kernel={KERNEL_API_VERSION}"
            self._api_errors[name] = msg
            return msg

        # Initialize
        try:
            await plugin_instance.initialize()
        except Exception as e:
            return f"Initialize failed: {e}"

        # Health check
        try:
            healthy = await plugin_instance.health_check()
        except Exception as e:
            return f"Health check failed: {e}"
        if not healthy:
            return "Health check returned unhealthy"

        # Register
        self._internal_plugins[name] = plugin_instance

        # Get MCP tools
        tools = plugin_instance.list_mcp_tools()
        handle = PluginHandle(
            name=name,
            version=plugin_instance.api_version,
            description=cfg.get("description", ""),
            health_status="healthy",
            registered_tools=[t["name"] for t in tools],
        )
        self.registry.register(name, handle)
        return "loaded"

    def _load_external(self, name: str, cfg: dict) -> str:
        command = cfg.get("command", "")
        if not command:
            return "Missing command"

        handle = PluginHandle(
            name=name,
            description=cfg.get("description", ""),
            health_status="unknown",
            mcp_server_command=command,
        )
        self.registry.register(name, handle)
        return "registered (external)"

    async def shutdown(self) -> None:
        """Shut down all internal plugins."""
        for name, plugin in self._internal_plugins.items():
            try:
                await plugin.shutdown()
            except Exception:
                pass
        self._internal_plugins.clear()

    def get_plugin(self, name: str) -> BasePlugin | None:
        """Get an internal plugin instance by name."""
        return self._internal_plugins.get(name)

    async def call_plugin_tool(self, plugin_name: str, tool_name: str, arguments: dict) -> str:
        """Call a tool on an internal plugin."""
        plugin = self._internal_plugins.get(plugin_name)
        if plugin is None:
            return f'{{"error": "Plugin not found: {plugin_name}"}}'
        return await plugin.call_tool(tool_name, arguments)
