from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod


class BaseDedup(ABC):
    """Decides whether a history event is a duplicate."""

    @abstractmethod
    def compute_hash(self, event: dict) -> str:
        """Compute a deduplication hash for an event."""
        ...

    def is_duplicate(self, event: dict, existing_hashes: set[str]) -> bool:
        """Check if event hash already exists in the set of known hashes."""
        return self.compute_hash(event) in existing_hashes


class SHA256Dedup(BaseDedup):
    """Exact deduplication via SHA256 hash of key fields."""

    def compute_hash(self, event: dict) -> str:
        key = json.dumps(
            {
                "session_id": event.get("session_id", ""),
                "tool_name": event.get("tool_name", ""),
                "tool_input": event.get("tool_input_json", ""),
            },
            sort_keys=True,
        )
        return hashlib.sha256(key.encode()).hexdigest()
