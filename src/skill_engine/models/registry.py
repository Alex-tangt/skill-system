from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ToolRegistry:
    """Maps tool names to Python callables that execute them."""

    def __init__(self):
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        self._tools[name] = fn

    def get(self, name: str) -> Callable[..., Any] | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())
