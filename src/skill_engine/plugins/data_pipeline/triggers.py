from __future__ import annotations

from abc import ABC, abstractmethod


class BaseTrigger(ABC):
    """Decides when the data pipeline should run."""

    @abstractmethod
    async def should_run(self, history_store) -> bool:
        """Returns True if the pipeline should process events now."""
        ...

    @abstractmethod
    async def mark_run(self, history_store) -> None:
        """Called after a successful pipeline run."""
        ...


class ManualTrigger(BaseTrigger):
    """MVP: Pipeline only runs when explicitly invoked via pipeline_run MCP tool."""

    async def should_run(self, history_store) -> bool:
        # Always return True — the MCP tool call itself is the trigger.
        return True

    async def mark_run(self, history_store) -> None:
        # No automatic state to maintain.
        pass
