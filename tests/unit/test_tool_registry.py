from __future__ import annotations

import pytest
from skill_engine.models.registry import ToolRegistry


def echo_fn(message: str = "") -> dict:
    return {"echoed": message}


class TestToolRegistry:
    def test_register_and_get(self):
        """Register a callable and retrieve it by name."""
        r = ToolRegistry()
        r.register("echo", echo_fn)
        assert r.get("echo") is echo_fn

    def test_get_nonexistent_returns_none(self):
        """Getting an unregistered tool returns None."""
        r = ToolRegistry()
        assert r.get("nonexistent") is None

    def test_list_tools_returns_registered_names(self):
        """list_tools returns all registered tool names."""
        r = ToolRegistry()
        r.register("echo", echo_fn)
        assert r.list_tools() == ["echo"]

    def test_list_tools_empty(self):
        """list_tools returns an empty list when nothing is registered."""
        r = ToolRegistry()
        assert r.list_tools() == []

    def test_register_duplicate_raises(self):
        """Registering the same name twice raises ValueError."""
        r = ToolRegistry()
        r.register("echo", echo_fn)
        with pytest.raises(ValueError, match="already registered"):
            r.register("echo", echo_fn)

    def test_register_multiple_tools(self):
        """Multiple tools can coexist in the registry."""
        r = ToolRegistry()
        r.register("echo", echo_fn)
        r.register("upper", lambda s: s.upper())
        assert r.get("echo") is not None
        assert r.get("upper") is not None
        assert sorted(r.list_tools()) == ["echo", "upper"]
