"""Minimal LLM client protocol for the pipeline.

The pipeline does not depend on a specific LLM provider.  Any object
that satisfies this interface can be injected into AnalysisRunner and
EvolutionRunner.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM clients used by the pipeline.

    Implementations can wrap LiteLLM, Claude API, OpenAI, or any provider.
    """

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[Any]] = None,
        execute_tools: bool = False,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Send messages to the LLM and return the response.

        Returns a dict with:
          - message: dict with "role" and "content" keys
          - has_tool_calls: bool
          - tool_results: list of tool execution results (if execute_tools=True)
          - messages: the updated message list (including tool results)
          - usage: dict with token counts (optional)
        """
        ...


class ToolDefinition:
    """Simplified tool definition for the pipeline's built-in tools.

    These are the tools exposed to the analysis LLM during Phase A.
    """

    def __init__(self, name: str, description: str, parameters: dict[str, Any]) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.parameters,
                "required": list(self.parameters.keys()),
            },
        }


# --- Built-in analysis tools -------------------------------------------------

TRAVERSE_CHAIN_TOOL = ToolDefinition(
    name="traverse_chain",
    description=(
        "Expand context along the conversation chain. "
        "Use this when prev/next user messages suggest the task spans "
        "multiple segments."
    ),
    parameters={
        "direction": {
            "type": "string",
            "enum": ["prev", "next"],
            "description": "Direction to traverse: 'prev' for earlier messages, 'next' for later ones",
        },
        "steps": {
            "type": "integer",
            "description": "Number of segments to traverse (1-5)",
            "minimum": 1,
            "maximum": 5,
        },
    },
)

READ_FILE_TOOL = ToolDefinition(
    name="read_file",
    description=(
        "Read a file from disk. Use this to inspect the actual output "
        "produced by the agent during execution."
    ),
    parameters={
        "path": {
            "type": "string",
            "description": "Absolute path to the file to read",
        },
    },
)

GET_SKILL_CONTENT_TOOL = ToolDefinition(
    name="get_skill_content",
    description=(
        "Load the full SKILL.md content for a skill. "
        "Use this when the truncated content in the prompt is insufficient."
    ),
    parameters={
        "skill_id": {
            "type": "string",
            "description": "The skill ID or skill name to load",
        },
    },
)

BUILTIN_ANALYSIS_TOOLS: list[ToolDefinition] = [
    TRAVERSE_CHAIN_TOOL,
    READ_FILE_TOOL,
    GET_SKILL_CONTENT_TOOL,
]
