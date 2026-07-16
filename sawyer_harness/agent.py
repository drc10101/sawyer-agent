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

# Context pressure threshold — fraction of context window that triggers wrap-up
CONTEXT_PRESSURE_THRESHOLD = 0.80

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

# Injected on the final turn when approaching the tool round limit or context pressure
WRAP_UP_INSTRUCTION = (
    "\n## Important: Final Turn\n"
    "This is your LAST turn. Do NOT make any more tool calls.\n"
    "Instead, summarize what you have accomplished so far and what remains "
    "to be done. Be specific about any incomplete work so the user can "
    "pick up where you left off. If the task is complete, give the final "
    "answer directly.\n"
)

# Injected when context is running low — tells the LLM to make handoff notes
CONTEXT_PRESSURE_INSTRUCTION = (
    "\n## Context Pressure Warning\n"
    "The conversation is approaching the context window limit. This is your "
    "last chance to make progress. Prioritize completing the current task. "
    "If you cannot finish, provide a detailed summary of:\n"
    "1. What you've accomplished\n"
    "2. What's still left to do\n"
    "3. The exact next steps for whoever continues\n"
    "Do NOT start new subtasks. Focus on wrapping up cleanly.\n"
)


def _estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    total = 0
    for m in messages:
        total += len(m.content) // 4 if m.content else 0
        # Tool call arguments also count
        if hasattr(m, 'tool_calls') and m.tool_calls:
            for tc in m.tool_calls:
                total += len(json.dumps(tc.arguments, default=str)) // 4
    return total


def _generate_handoff_notes(
    conversation: list[Message],
    tool_calls_made: list[str],
    round_num: int,
    reason: str,
) -> str:
    """Generate a handoff note summarizing session progress for the next session.

    This is saved to memory so the next session can pick up where this one left off.
    """
    # Extract user messages for context
    user_msgs = [m.content for m in conversation if m.role == "user"]

    # Extract assistant text responses
    assistant_msgs = [m.content for m in conversation if m.role == "assistant" and m.content]

    notes_lines = [
        f"## Session Handoff Note",
        f"**Reason**: {reason}",
        f"**Rounds used**: {round_num}",
        f"**Tools called**: {', '.join(tool_calls_made) if tool_calls_made else 'none'}",
        f"",
        f"### What was requested",
        user_msgs[0][:200] if user_msgs else "No user message",
        f"",
        f"### Progress",
    ]

    # Add last few assistant messages as progress summary
    for msg in assistant_msgs[-3:]:
        if msg and len(msg.strip()) > 0:
            notes_lines.append(f"- {msg[:150]}")

    notes_lines.extend([
        f"",
        f"### Conversation history (condensed)",
    ])

    # Add a condensed version of the conversation
    for m in conversation[-10:]:
        role_label = {"user": "User", "assistant": "Assistant", "tool": "Tool"}.get(m.role, m.role)
        content_preview = m.content[:100] if m.content else "(no content)"
        notes_lines.append(f"- {role_label}: {content_preview}")

    return "\n".join(notes_lines)


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
        context_window: int | None = None,
    ):
        self.config = config
        self.llm = llm
        self.memory = memory
        self.tools = tools
        self.system_prompt = system_prompt
        self.skills = skills
        self.rules_store = rules_store
        self.conversation: list[Message] = []
        # Context window size for pressure detection
        self.context_window = context_window or 128000  # Default 128K

    def _build_system_prompt(self, user_message: str = "", wrap_up: bool = False) -> str:
        """Assemble system prompt with injected memory, skills, capabilities, and tool descriptions.

        When wrap_up is True, adds the final-turn instruction telling the LLM
        to summarize progress instead of making more tool calls.
        """
        parts = [self.system_prompt]

        # Inject verbosity style from config
        verbosity = getattr(self.config, "agent", None)
        if verbosity:
            vlevel = getattr(verbosity, "verbosity", "normal")
        else:
            vlevel = "normal"
        parts.append(VERBOSITY_PROMPTS.get(vlevel, VERBOSITY_PROMPTS["normal"]))

        # Inject wrap-up instruction on final turn
        if wrap_up:
            parts.append(WRAP_UP_INSTRUCTION)

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

        # Inject memory (with session handoff notes promoted to their own section)
        handoff_entries = []
        other_entries = []
        if self.memory.total_chars() > 0:
            for entry in self.memory.all_entries():
                if entry.get("category") == "session-handoff":
                    handoff_entries.append(entry)
                else:
                    other_entries.append(entry)

        if handoff_entries:
            parts.append("\n## Previous Session Handoff\n")
            parts.append("The previous session ran out of context. Here is what was being worked on:\n")
            for entry in handoff_entries:
                parts.append(entry["content"])
            parts.append("")

        if other_entries:
            parts.append("\n## Memory\n")
            for entry in other_entries:
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

    def _check_context_pressure(self) -> float:
        """Check how full the context window is as a fraction (0.0 to 1.0).

        Returns the ratio of estimated tokens used to the context window size.
        A value above CONTEXT_PRESSURE_THRESHOLD means we should wrap up.
        """
        system_prompt = self._build_system_prompt()
        total_tokens = _estimate_tokens(self.conversation) + len(system_prompt) // 4
        return total_tokens / self.context_window if self.context_window > 0 else 0.0

    async def run(self, user_message: str) -> AsyncIterator[str]:
        """
        Run the agent loop for a single user message.

        Yields text chunks as they arrive. Handles tool calls internally.
        Stops when:
        1. The LLM produces a final text response with no tool calls (task done)
        2. max_tool_rounds is reached (wrap-up with summary)
        3. Context pressure exceeds threshold (save notes, signal continuation)

        On early termination (cases 2 and 3), the agent:
        - Saves a handoff note to memory for the next session
        - Yields a continuation signal that the UI can detect to offer
          "Continue in new session"
        - The LLM gets a final turn with wrap-up instructions so it produces
          a useful summary instead of dying mid-task
        """
        self.conversation.append(Message(role="user", content=user_message))

        # Get configurable limits from agent config
        agent_cfg = getattr(self.config, "agent", None)
        max_rounds = getattr(agent_cfg, "max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS) if agent_cfg else DEFAULT_MAX_TOOL_ROUNDS
        verbosity = getattr(agent_cfg, "verbosity", "normal") if agent_cfg else "normal"
        stream_tools = getattr(agent_cfg, "stream_tool_output", True) if agent_cfg else True
        is_concise = verbosity == "concise"

        # Track tool calls for handoff notes
        tool_calls_made: list[str] = []

        for round_num in range(max_rounds):
            # Check context pressure BEFORE calling the LLM
            # If we're past the threshold, this is our final round regardless
            pressure = self._check_context_pressure()
            context_pressure = pressure >= CONTEXT_PRESSURE_THRESHOLD

            # Final round: either max_tool_rounds limit or context pressure
            is_final_round = (round_num == max_rounds - 1) or context_pressure

            if context_pressure:
                logger.warning(
                    f"Context pressure at {pressure:.0%} (threshold {CONTEXT_PRESSURE_THRESHOLD:.0%}). "
                    f"Wrapping up at round {round_num + 1}/{max_rounds}."
                )

            system_prompt = self._build_system_prompt(
                user_message,
                wrap_up=is_final_round,
            )

            # Inject context pressure warning if close but not yet final
            if context_pressure:
                # Already on final round with wrap_up=True, add extra urgency
                pass

            # On final round, call LLM with tools=False so it MUST give a text
            # response (no new tool calls that would be cut off)
            response: LLMResponse = await self.llm.chat(
                messages=self.conversation,
                system_prompt=system_prompt,
                tools=not is_final_round,
            )

            # If no tool calls, we're done -- yield the text response
            if not response.tool_calls:
                self.conversation.append(
                    Message(role="assistant", content=response.content)
                )
                if response.content:
                    yield response.content

                # If we stopped due to context pressure, save handoff notes
                # and signal the UI that a new session is needed
                if context_pressure:
                    handoff = _generate_handoff_notes(
                        self.conversation,
                        tool_calls_made,
                        round_num + 1,
                        f"Context window {pressure:.0%} full (threshold {CONTEXT_PRESSURE_THRESHOLD:.0%})",
                    )
                    self.memory.add(
                        key=f"session-handoff-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                        content=handoff,
                        category="session-handoff",
                    )
                    # Signal to UI: this session needs to continue in a new one
                    yield "\n\n---\n*Session context is full. Handoff notes saved. Start a new session to continue.*"

                return

            # If we somehow still got tool calls on the final round, ignore them
            # and just yield whatever text content came with the response
            if is_final_round:
                self.conversation.append(
                    Message(role="assistant", content=response.content or "")
                )
                if response.content:
                    yield response.content
                else:
                    # LLM ignored the wrap-up instruction and tried tools anyway.
                    # Give the user a useful summary of what happened.
                    tool_names = [tc.name for tc in response.tool_calls]
                    summary = (
                        f"I used {round_num + 1} tool rounds and reached the limit. "
                        f"Tools called: {', '.join(tool_names)}. "
                    )
                    if context_pressure:
                        summary += "The context window is nearly full. "
                    summary += "Increase max tool rounds or start a new session to continue."
                    yield summary

                # Save handoff notes for continuation
                reason = (
                    f"Context window {pressure:.0%} full"
                    if context_pressure
                    else f"Max tool rounds ({max_rounds}) reached"
                )
                handoff = _generate_handoff_notes(
                    self.conversation,
                    tool_calls_made,
                    round_num + 1,
                    reason,
                )
                self.memory.add(
                    key=f"session-handoff-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                    content=handoff,
                    category="session-handoff",
                )

                if context_pressure:
                    yield "\n\n---\n*Session context is full. Handoff notes saved. Start a new session to continue.*"
                else:
                    yield f"\n\n---\n*Tool round limit reached. Handoff notes saved. Start a new session to continue.*"

                return

            # Process tool calls (normal round)
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
                tool_calls_made.append(tc.name)

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

    def save_memory(self, key: str, content: str, category: str = "general"):
        """Save a fact to persistent memory."""
        self.memory.add(key, content, category)

    def reset_conversation(self):
        """Clear conversation history (keep memory)."""
        self.conversation.clear()