"""
LLM client -- model-agnostic interface to language models.

Supports: Sawyer (OpenAI-compatible), OpenAI, Anthropic, local (Ollama).
The client normalizes all providers to a single interface:
- send messages, get responses
- handle tool calls uniformly
- stream responses when available

Sawyer is first-class: if provider is "sawyer", we route through
the Sawyer network with appropriate headers and fallback.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import LLMConfig
from .tools import ToolRegistry

logger = logging.getLogger("sawyer-harness.llm")


@dataclass
class Message:
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)  # For assistant messages with multiple tool calls


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    reasoning_content: str = ""  # Separate thinking/reasoning from models that support it


class LLMClient:
    """Unified LLM client for multiple providers."""

    def __init__(self, config: LLMConfig, tool_registry: ToolRegistry | None = None):
        self.config = config
        self.tool_registry = tool_registry
        self._client = httpx.AsyncClient(timeout=120.0)
        self._setup_provider()

    def _setup_provider(self):
        """Configure the provider-specific client."""
        if self.config.provider in ("sawyer", "openai", "ollama", "local"):
            self._base_url = self.config.base_url or {
                "sawyer": "https://api.sawyernetwork.ai/v1",
                "openai": "https://api.openai.com/v1",
                "ollama": "http://localhost:11434/v1",
                "local": "http://localhost:8000/v1",
            }.get(self.config.provider, "https://api.openai.com/v1")
            self._api_key = self.config.api_key
            self._provider_type = "openai_compatible"

        elif self.config.provider == "anthropic":
            self._base_url = "https://api.anthropic.com"
            self._api_key = self.config.api_key
            self._provider_type = "anthropic"

        else:
            raise ValueError(f"Unknown provider: {self.config.provider}")

    async def chat(
        self,
        messages: list[Message],
        system_prompt: str = "",
        tools: bool = True,
    ) -> LLMResponse:
        """Send a chat completion request and return the response."""
        if self._provider_type == "openai_compatible":
            return await self._chat_openai(messages, system_prompt, tools)
        elif self._provider_type == "anthropic":
            return await self._chat_anthropic(messages, system_prompt, tools)
        else:
            raise ValueError(f"Unsupported provider type: {self._provider_type}")

    async def _chat_openai(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: bool,
    ) -> LLMResponse:
        """OpenAI-compatible chat completion (Sawyer, OpenAI, Ollama, local)."""
        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        # Sawyer-specific headers
        if self.config.provider == "sawyer":
            headers["X-Sawyer-Network"] = "preferred"
            headers["X-Sawyer-Priority"] = "low-latency"

        # Build message list
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            if msg.role == "tool" and msg.tool_call_id:
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                # Assistant message with tool calls
                tc_list = []
                for tc in msg.tool_calls:
                    tc_list.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    })
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": tc_list,
                })
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": api_messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }

        # Add tools if available and requested
        if tools and self.tool_registry:
            body["tools"] = self.tool_registry.list_tools()
            body["tool_choice"] = "auto"

        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers=headers,
            json=body,
        )
        response.raise_for_status()
        data = response.json()

        # Parse response
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content", "") or ""
        # Extract separate reasoning/thinking content (DeepSeek, GLM, etc.)
        reasoning_content = message.get("reasoning_content", "") or ""
        tool_calls = []

        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                func = tc["function"]
                try:
                    args = json.loads(func["arguments"])
                except json.JSONDecodeError:
                    args = {"raw": func["arguments"]}
                tool_calls.append(ToolCall(
                    id=tc["id"],
                    name=func["name"],
                    arguments=args,
                ))

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", ""),
            usage=data.get("usage", {}),
            reasoning_content=reasoning_content,
        )

    async def _chat_anthropic(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: bool,
    ) -> LLMResponse:
        """Anthropic Claude chat completion."""
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }

        api_messages = []
        for msg in messages:
            api_messages.append({"role": msg.role, "content": msg.content})

        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": api_messages,
            "max_tokens": self.config.max_tokens,
        }
        if system_prompt:
            body["system"] = system_prompt

        # Tool use with Anthropic format
        if tools and self.tool_registry:
            body["tools"] = self.tool_registry.list_tools()

        response = await self._client.post(
            f"{self._base_url}/v1/messages",
            headers=headers,
            json=body,
        )
        response.raise_for_status()
        data = response.json()

        content_parts = []
        tool_calls = []
        for block in data.get("content", []):
            if block["type"] == "text":
                content_parts.append(block["text"])
            elif block["type"] == "tool_use":
                tool_calls.append(ToolCall(
                    id=block["id"],
                    name=block["name"],
                    arguments=block["input"],
                ))

        return LLMResponse(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=data.get("stop_reason", ""),
            usage=data.get("usage", {}),
        )

    async def close(self):
        await self._client.aclose()