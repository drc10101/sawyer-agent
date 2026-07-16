"""
Agent core -- the main loop.

Message comes in -> context assembled (memory + skills + history) ->
LLM decides -> tools execute -> response goes out. Repeat until done.

This is the heart of the harness. Everything else supports this loop.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Any

from .config import HarnessConfig
from .llm import LLMClient, LLMResponse, Message, ToolCall
from .memory import MemoryStore
from .skills import SkillStore
from .tools import ToolRegistry, ToolResult

logger = logging.getLogger("sawyer-harness.agent")

# Default safety limit — overridden by config.agent.max_tool_rounds
DEFAULT_MAX_TOOL_ROUNDS = 20

# Verbosity-specific system prompt additions
VERBOSITY_PROMPTS = {
    "concise": (
        "\n## Response Style: Concise\n"
        "Be brief and to the point. Answer in as few words as possible. "
        "No explanations unless asked. No step-by-step commentary. "
        "If the answer is a single word or number, give just that. "
        "Never narrate your reasoning process. Never say 'Let me think' or 'I'll check'. "
        "Just give the answer directly.\n"
    ),
    "normal": (
        "\n## Response Style: Balanced\n"
        "Be helpful, direct, and thorough.\n"
    ),
    "thorough": (
        "\n## Response Style: Thorough\n"
        "Be detailed and comprehensive. Explain your reasoning. "
        "Show your work. Include context and alternatives. "
        "When in doubt, provide more detail rather than less.\n"
    ),
}


class Agent:
    """The core agent loop: receive, think, act, respond."""

    def __init__(
        self,
        config: HarnessConfig,
        llm: LLMClient,
        memory: MemoryStore,
        tools: ToolRegistry,
        system_prompt: str = "",
        skills: SkillStore | None = None,
        rules_store: Any | None = None,
    ):
        self.config = config
        self.llm = llm
        self.memory = memory
        self.tools = tools
        self.system_prompt = system_prompt
        self.skills = skills
        self.rules_store = rules_store
        self.conversation: list[Message] = []

    def _build_system_prompt(self, user_message: str = "") -> str:
        """Assemble system prompt with injected memory, skills, capabilities, and tool descriptions."""
        parts = [self.system_prompt]

        # Inject verbosity style from config
        verbosity = getattr(self.config, "agent", None)
        if verbosity:
            vlevel = getattr(verbosity, "verbosity", "normal")
        else:
            vlevel = "normal"
        parts.append(VERBOSITY_PROMPTS.get(vlevel, VERBOSITY_PROMPTS["normal"]))

        # Inject capabilities rules (always present)
        try:
            from pathlib import Path
            cap_path = Path(__file__).parent / "capabilities.yaml"
            if cap_path.exists():
                import yaml
                caps = yaml.safe_load(cap_path.read_text())
                if caps and "behavior_rules" in caps:
                    parts.append("\n## Agent Behavior Rules\n")
                    for rule in caps["behavior_rules"]:
                        parts.append(f"- [{rule['priority']}] {rule['rule']}")
                        if rule.get("detail"):
                            parts.append(f"  {rule['detail']}")
                    # Summarize what the agent handles vs what needs user
                    if caps.get("agent_handles"):
                        parts.append("\n## What I Handle Myself\n")
                        for item in caps["agent_handles"]:
                            parts.append(f"- {item['what']} ({item['how']})")
                    if caps.get("requires_user"):
                        parts.append("\n## What I Need Your Help With\n")
                        for item in caps["requires_user"]:
                            parts.append(f"- {item['what']}: {item['why']} — {item['action']}")
        except Exception:
            pass  # Capabilities file is optional

        # Inject relevant skills
        if self.skills and user_message:
            relevant = self.skills.find_relevant(user_message, max_chars=4000)
            if relevant:
                parts.append(self.skills.format_for_prompt(relevant))

        # Inject memory
        if self.memory.total_chars() > 0:
            parts.append("\n## Memory\n")
            for entry in self.memory.all_entries():
                parts.append(f"- {entry['key']}: {entry['content']}")
            parts.append("")

        # Inject available tools
        tool_schemas = self.tools.list_tools()
        if tool_schemas:
            parts.append("\n## Available Tools\n")
            for schema in tool_schemas:
                func = schema["function"]
                parts.append(f"- **{func['name']}**: {func['description']}")

        # Inject custom agent rules (supersede capabilities.yaml defaults)
        if self.rules_store:
            rules_prompt = self.rules_store.get_rules_prompt()
            if rules_prompt:
                parts.append(rules_prompt)

        return "\n".join(parts)

    async def run(self, user_message: str) -> AsyncIterator[str]:
        """
        Run the agent loop for a single user message.

        Yields text chunks as they arrive. Handles tool calls internally.
        Stops when the LLM produces a final text response with no tool calls,
        or after max_tool_rounds rounds.
        """
        self.conversation.append(Message(role="user", content=user_message))

        # Get configurable limits from agent config
        agent_cfg = getattr(self.config, "agent", None)
        max_rounds = getattr(agent_cfg, "max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS) if agent_cfg else DEFAULT_MAX_TOOL_ROUNDS
        verbosity = getattr(agent_cfg, "verbosity", "normal") if agent_cfg else "normal"
        stream_tools = getattr(agent_cfg, "stream_tool_output", True) if agent_cfg else True
        is_concise = verbosity == "concise"

        for round_num in range(max_rounds):
            # Call LLM
            system_prompt = self._build_system_prompt(user_message)
            response: LLMResponse = await self.llm.chat(
                messages=self.conversation,
                system_prompt=system_prompt,
                tools=True,
            )

            # If no tool calls, we're done -- yield the text response
            if not response.tool_calls:
                self.conversation.append(
                    Message(role="assistant", content=response.content)
                )
                if response.content:
                    yield response.content
                return

            # Process tool calls
            assistant_msg = Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            self.conversation.append(assistant_msg)

            # Yield any text content before tool calls (suppressed in concise mode)
            if response.content and not is_concise:
                yield response.content + "\n"

            # Execute each tool call
            for tc in response.tool_calls:
                logger.info(f"Tool call: {tc.name}({json.dumps(tc.arguments, default=str)[:200]})")
                result: ToolResult = self.tools.execute(tc.name, tc.arguments)

                # Add tool result to conversation
                tool_msg = Message(
                    role="tool",
                    content=result.output if result.success else f"Error: {result.error}",
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                )
                self.conversation.append(tool_msg)

                # Yield tool result for streaming (suppressed in concise mode unless error)
                if stream_tools and not is_concise:
                    if result.success:
                        yield f"[{tc.name}] {result.output[:2000]}\n"
                    else:
                        yield f"[{tc.name}] ERROR: {result.error}\n"
                elif not result.success:
                    # Always show errors even in concise mode
                    yield f"[{tc.name}] ERROR: {result.error}\n"

        # Safety: if we hit max rounds, yield a warning
        yield f"\n[Agent reached maximum tool rounds ({max_rounds}). Stopping.]"

    def save_memory(self, key: str, content: str, category: str = "general"):
        """Save a fact to persistent memory."""
        self.memory.add(key, content, category)

    def reset_conversation(self):
        """Clear conversation history (keep memory)."""
        self.conversation.clear()