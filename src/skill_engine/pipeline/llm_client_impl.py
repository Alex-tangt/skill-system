"""Concrete LLM client implementation using Anthropic-compatible API.

Uses stdlib ``urllib`` for HTTP (zero additional dependencies).  Callers
are expected to run this in an async context; blocking I/O is offloaded
to a thread executor via ``asyncio.to_thread``.

Environment variables:
  ``ANTHROPIC_BASE_URL`` — API base URL (default: https://api.anthropic.com)
  ``ANTHROPIC_AUTH_TOKEN`` — API key / auth token
  ``ANTHROPIC_MODEL`` — Default model name
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicLLMClient:
    """LLM client for Anthropic-compatible APIs (Claude, DeepSeek, etc.).

    Implements the ``LLMClient`` protocol from ``skill_engine.pipeline.llm_client``.

    Usage::

        client = AnthropicLLMClient()
        result = await client.complete([
            {"role": "user", "content": "Hello"}
        ])
        print(result["message"]["content"])
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self._api_key = api_key or os.getenv("ANTHROPIC_AUTH_TOKEN", "")
        self._base_url = (base_url or os.getenv("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    # --- Main completion API -------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        execute_tools: bool = False,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Send a completion request and return the result.

        Returns a dict with keys:
          - message: {"role": "assistant", "content": "..."}
          - has_tool_calls: bool
          - tool_results: list (if execute_tools=True)
          - messages: updated message list (if execute_tools=True)
          - usage: {"input_tokens": N, "output_tokens": N}
        """
        result = await self._send(messages, tools, model)

        content_blocks = result.get("content", [])
        text_parts = []
        tool_calls = []
        for block in content_blocks:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append(block)

        response: dict[str, Any] = {
            "message": {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else "",
            },
            "has_tool_calls": len(tool_calls) > 0,
            "tool_results": [],
            "messages": list(messages),
            "usage": result.get("usage", {}),
        }

        # Add assistant message to the message list
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content_blocks}
        response["messages"].append(assistant_msg)

        if not tool_calls or not execute_tools:
            return response

        # Execute tool calls and add results
        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_input = tc.get("input", {})

            # Only execute pipeline built-in tools
            tool_result = await self._execute_tool(tool_name, tool_input)
            response["tool_results"].append({
                "tool_call": {"name": tool_name, "arguments": tool_input},
                "result": tool_result,
            })

            # Add tool result to messages
            response["messages"].append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.get("id", ""),
                        "content": json.dumps(tool_result) if isinstance(tool_result, dict) else str(tool_result),
                    }
                ],
            })

        return response

    # --- HTTP transport -------------------------------------------------------

    async def _send(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Send request to the API (blocking, run in thread executor)."""
        body = {
            "model": model or self._model,
            "max_tokens": self._max_tokens,
            "messages": self._normalize_messages(messages),
        }
        if tools:
            # Convert our tool schema to Anthropic format
            body["tools"] = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", t.get("parameters", {})),
                }
                for t in tools
            ]

        return await asyncio.to_thread(self._send_sync, body)

    def _send_sync(self, body: dict[str, Any]) -> dict[str, Any]:
        """Synchronous HTTP POST with urllib."""
        url = f"{self._base_url}/v1/messages"
        data = json.dumps(body).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else str(e)
            logger.error(f"LLM API error {e.code}: {error_body[:500]}")
            raise RuntimeError(f"LLM API returned {e.code}: {error_body[:300]}")
        except Exception as e:
            logger.error(f"LLM API request failed: {e}")
            raise

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize messages to Anthropic format.

        Converts simple string content to content-block format and
        drops unsupported fields.
        """
        normalized = []
        for msg in messages:
            role = msg.get("role", "user")
            if role not in ("user", "assistant"):
                continue

            content = msg.get("content", "")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            elif not isinstance(content, list):
                content = [{"type": "text", "text": str(content)}]

            normalized.append({"role": role, "content": content})

        return normalized

    async def _execute_tool(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a built-in pipeline analysis tool."""
        from pathlib import Path

        if tool_name == "read_file":
            path = tool_input.get("path", "")
            try:
                content = Path(path).read_text(encoding="utf-8")
                return {"status": "success", "content": content[:5000]}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        elif tool_name == "get_skill_content":
            skill_id = tool_input.get("skill_id", "")
            for skills_dir in [Path("skills"), Path.home() / ".claude" / "skills"]:
                skill_md = skills_dir / skill_id / "SKILL.md"
                if skill_md.exists():
                    content = skill_md.read_text(encoding="utf-8")
                    return {"status": "success", "content": content[:8000]}
            return {"status": "error", "error": f"Skill not found: {skill_id}"}

        elif tool_name == "traverse_chain":
            return {
                "status": "success",
                "note": "traverse_chain must be handled by the calling AnalysisRunner",
                "direction": tool_input.get("direction", ""),
                "steps": tool_input.get("steps", 1),
            }

        return {"status": "error", "error": f"Unknown tool: {tool_name}"}


# --- Quick self-test -------------------------------------------------------

if __name__ == "__main__":
    async def _test():
        client = AnthropicLLMClient()
        print(f"Model: {client.model}")
        print(f"Base URL: {client._base_url}")
        print(f"API Key: {'***' + client._api_key[-4:] if client._api_key else 'NOT SET'}")

        if not client._api_key:
            print("SKIP: No API key configured")
            return

        result = await client.complete([
            {"role": "user", "content": "Say 'pipeline OK' in exactly those words."}
        ], max_tokens=50)
        print(f"Response: {result['message']['content'][:200]}")

    asyncio.run(_test())
