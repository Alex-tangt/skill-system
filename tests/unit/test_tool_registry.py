from __future__ import annotations

import pytest
from skill_engine.kernel.plugin_registry import PluginRegistry, PluginHandle


class TestPluginRegistry:
    def test_register_and_get(self):
        """Register a plugin handle and retrieve it by name."""
        r = PluginRegistry()
        h = PluginHandle(name="test-plugin", description="A test plugin")
        r.register("test-plugin", h)
        assert r.get("test-plugin") is h

    def test_get_nonexistent_returns_none(self):
        """Getting an unregistered plugin returns None."""
        r = PluginRegistry()
        assert r.get("nonexistent") is None

    def test_list_plugins_returns_registered_names(self):
        """list_plugins returns all registered plugin names."""
        r = PluginRegistry()
        r.register("echo", PluginHandle(name="echo"))
        assert r.list_plugins() == ["echo"]

    def test_list_plugins_empty(self):
        """list_plugins returns an empty list when nothing is registered."""
        r = PluginRegistry()
        assert r.list_plugins() == []

    def test_register_duplicate_raises(self):
        """Registering the same name twice raises ValueError."""
        r = PluginRegistry()
        r.register("echo", PluginHandle(name="echo"))
        with pytest.raises(ValueError, match="already registered"):
            r.register("echo", PluginHandle(name="echo"))

    def test_register_multiple_plugins(self):
        """Multiple plugins can coexist in the registry."""
        r = PluginRegistry()
        r.register("echo", PluginHandle(name="echo"))
        r.register("upper", PluginHandle(name="upper"))
        assert r.get("echo") is not None
        assert r.get("upper") is not None
        assert sorted(r.list_plugins()) == ["echo", "upper"]

    def test_unregister(self):
        """Unregister removes a plugin."""
        r = PluginRegistry()
        r.register("echo", PluginHandle(name="echo"))
        assert r.unregister("echo") is True
        assert r.get("echo") is None
        assert r.unregister("nonexistent") is False

    def test_list_healthy(self):
        """list_healthy returns only healthy plugins."""
        r = PluginRegistry()
        h1 = PluginHandle(name="healthy", health_status="healthy")
        h2 = PluginHandle(name="unhealthy", health_status="unhealthy")
        r.register("healthy", h1)
        r.register("unhealthy", h2)
        healthy = r.list_healthy()
        assert len(healthy) == 1
        assert healthy[0].name == "healthy"
