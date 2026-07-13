"""
Context window manager -- token budgets, model configs, smart allocation.

The context window isn't just "how big is it?" It's about how you USE it.

A 128K window with smart compression holds more useful context than a
400K window with naive truncation. This module manages the allocation:

- System prompt: reserved, never compressed
- Memory: reserved, never compressed
- Skills: reserved, never compressed
- Recent messages: kept verbatim (recency window)
- Older messages: compressed based on priority
- Tool output: compressed first (low info density)
- Reserve: 20% kept free for the next exchange

Different models have different window sizes. This module knows them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("sawyer-harness.context_manager")


# Known model context windows (in tokens)
# These are the ACTUAL maximums. We reserve 20% for safety.
MODEL_WINDOWS = {
    # OpenAI
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o1-pro": 200000,
    "o3": 200000,
    "o3-mini": 200000,
    "o4-mini": 200000,

    # Anthropic
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-3-opus": 200000,
    "claude-3-haiku": 200000,

    # Google
    "gemini-2.5-pro": 1048576,
    "gemini-2.5-flash": 1048576,
    "gemini-1.5-pro": 2097152,
    "gemini-1.5-flash": 1048576,

    # DeepSeek
    "deepseek-chat": 131072,
    "deepseek-reasoner": 131072,

    # GLM / Z.AI
    "glm-5.2": 128000,
    "glm-5.2:cloud": 202752,
    "glm-5.1:cloud": 202752,
    "glm-5.1": 128000,
    "glm-4-plus": 128000,
    "glm-4": 128000,

    # OpenRouter (varies by model, this is a safe default)
    "openrouter-default": 128000,

    # Sawyer Network
    "sawyer-default": 131072,

    # Local models (common defaults)
    "llama-3-70b": 8192,
    "llama-3-8b": 8192,
    "mistral-7b": 32768,
    "mixtral-8x7b": 32768,
    "qwen2-72b": 32768,
}

# Fallback for unknown models
DEFAULT_WINDOW = 128000


@dataclass
class ContextBudget:
    """Token budget allocation for a conversation."""
    total_window: int        # Full context window size
    system_reserve: int      # Tokens reserved for system prompt
    memory_reserve: int      # Tokens reserved for memory
    skills_reserve: int      # Tokens reserved for skills
    session_context: int     # Tokens reserved for previous session notes
    recency_window: int      # Recent messages kept verbatim
    compression_target: int   # Target token count after compression
    free_space: int          # Tokens available for new messages

    @property
    def used_percentage(self) -> float:
        """How much of the window is allocated (excluding free space)."""
        used = self.total_window - self.free_space
        return (used / self.total_window) * 100 if self.total_window else 0


@dataclass
class ModelContextConfig:
    """Configuration for a specific model's context window."""
    model_name: str
    window_size: int
    system_reserve_pct: float = 0.05   # 5% for system prompt
    memory_reserve_pct: float = 0.05   # 5% for memory
    skills_reserve_pct: float = 0.03   # 3% for skills
    session_reserve_pct: float = 0.03  # 3% for session notes
    recency_pct: float = 0.40          # 40% for recent messages
    free_pct: float = 0.20             # 20% free for new messages
    # Remaining: ~24% for compressed older messages


class ContextManager:
    """
    Manages context window allocation and triggers compression.

    Knows the context window size for each model, allocates budgets,
    and decides when compression is needed.
    """

    def __init__(
        self,
        model_name: str = "",
        window_size: int | None = None,
        reserve_ratio: float = 0.20,
        context_length_override: int | None = None,
    ):
        """
        Args:
            model_name: Name of the model (used to look up window size)
            window_size: Override the model's default window size
            reserve_ratio: Fraction of window to keep free (0.20 = 20%)
            context_length_override: Explicit context window size from config
                (takes precedence over window_size and model lookup)
        """
        self.model_name = model_name
        self.reserve_ratio = reserve_ratio

        if context_length_override and context_length_override > 0:
            self.window_size = context_length_override
        elif window_size:
            self.window_size = window_size
        else:
            self.window_size = self._lookup_window(model_name)

    def _lookup_window(self, model_name: str) -> int:
        """Look up the context window size for a model."""
        if not model_name:
            return DEFAULT_WINDOW

        # Normalize the model name for lookup
        normalized = model_name.lower().strip()

        # Direct match
        if normalized in MODEL_WINDOWS:
            return MODEL_WINDOWS[normalized]

        # Partial match (e.g., "gpt-4o-2024-05-13" matches "gpt-4o")
        for key, value in MODEL_WINDOWS.items():
            if key in normalized or normalized in key:
                return value

        # Provider prefix match (e.g., "anthropic/claude-sonnet-4-20250514")
        if "/" in normalized:
            _, model_part = normalized.rsplit("/", 1)
            for key, value in MODEL_WINDOWS.items():
                if key in model_part or model_part in key:
                    return value

        logger.info(f"Unknown model '{model_name}', using default {DEFAULT_WINDOW} token window")
        return DEFAULT_WINDOW

    def calculate_budget(
        self,
        system_prompt_tokens: int = 0,
        memory_tokens: int = 0,
        skills_tokens: int = 0,
        session_notes_tokens: int = 0,
        current_messages_tokens: int = 0,
    ) -> ContextBudget:
        """
        Calculate the token budget allocation for the current conversation.

        Allocates the context window into regions:
        - System prompt (actual size, or 5% estimate)
        - Memory (actual size, or 5% estimate)
        - Skills (actual size, or 3% estimate)
        - Session notes (actual size, or 3% estimate)
        - Recent messages (40% of remaining)
        - Free space (20% of total for new messages)
        - Compressed older messages (whatever's left)
        """
        w = self.window_size

        # Calculate fixed reserves - use actual token counts when provided
        system_reserve = system_prompt_tokens if system_prompt_tokens > 0 else int(w * 0.05)
        memory_reserve = memory_tokens if memory_tokens > 0 else int(w * 0.05)
        skills_reserve = skills_tokens if skills_tokens > 0 else int(w * 0.03)
        session_reserve = session_notes_tokens if session_notes_tokens > 0 else int(w * 0.03)
        free_space = int(w * self.reserve_ratio)

        # Remaining after fixed allocations
        fixed = system_reserve + memory_reserve + skills_reserve + session_reserve + free_space
        remaining = max(0, w - fixed)

        # Recency window: 40% of remaining
        recency_window = int(remaining * 0.40)

        # Compression target: whatever's left
        compression_target = remaining - recency_window

        return ContextBudget(
            total_window=w,
            system_reserve=system_reserve,
            memory_reserve=memory_reserve,
            skills_reserve=skills_reserve,
            session_context=session_reserve,
            recency_window=recency_window,
            compression_target=compression_target,
            free_space=free_space,
        )

    def needs_compression(
        self,
        system_prompt_tokens: int = 0,
        memory_tokens: int = 0,
        skills_tokens: int = 0,
        session_notes_tokens: int = 0,
        current_messages_tokens: int = 0,
    ) -> bool:
        """Check if the conversation exceeds the budget and needs compression."""
        budget = self.calculate_budget(
            system_prompt_tokens=system_prompt_tokens,
            memory_tokens=memory_tokens,
            skills_tokens=skills_tokens,
            session_notes_tokens=session_notes_tokens,
            current_messages_tokens=current_messages_tokens,
        )

        # If current messages exceed the recency window, we need compression
        non_message_reserve = (
            budget.system_reserve
            + budget.memory_reserve
            + budget.skills_reserve
            + budget.session_context
            + budget.free_space
        )

        available_for_messages = budget.total_window - non_message_reserve
        return current_messages_tokens > available_for_messages

    def get_compression_ratio(
        self,
        system_prompt_tokens: int = 0,
        memory_tokens: int = 0,
        skills_tokens: int = 0,
        session_notes_tokens: int = 0,
        current_messages_tokens: int = 0,
    ) -> float:
        """
        Calculate how aggressively to compress.

        Returns a value between 0.0 (no compression needed) and 1.0
        (maximum compression). Values above 0.7 mean we're very tight
        on space and should compress aggressively.
        """
        budget = self.calculate_budget(
            system_prompt_tokens=system_prompt_tokens,
            memory_tokens=memory_tokens,
            skills_tokens=skills_tokens,
            session_notes_tokens=session_notes_tokens,
        )

        if current_messages_tokens <= budget.recency_window:
            return 0.0

        # How much we need to compress
        overflow = current_messages_tokens - budget.recency_window
        target = budget.compression_target

        if target == 0:
            return 1.0

        ratio = overflow / target
        return min(1.0, max(0.0, ratio))

    def get_context_stats(
        self,
        system_prompt: str = "",
        memory_text: str = "",
        skills_text: str = "",
        session_notes_text: str = "",
        messages: list = None,
    ) -> dict:
        """
        Get comprehensive context window statistics.

        Returns a dict with token counts, budget allocation, and recommendations.
        """
        # Estimate tokens (rough: 4 chars = 1 token)
        def est(text: str) -> int:
            return max(1, len(text) // 4) if text else 0

        system_tokens = est(system_prompt)
        memory_tokens = est(memory_text)
        skills_tokens = est(skills_text)
        session_tokens = est(session_notes_text)

        message_tokens = 0
        if messages:
            for msg in messages:
                content = msg.content if hasattr(msg, "content") else str(msg)
                message_tokens += est(content)

        total_used = system_tokens + memory_tokens + skills_tokens + session_tokens + message_tokens

        budget = self.calculate_budget(
            system_prompt_tokens=system_tokens,
            memory_tokens=memory_tokens,
            skills_tokens=skills_tokens,
            session_notes_tokens=session_tokens,
            current_messages_tokens=message_tokens,
        )

        compression_needed = self.needs_compression(
            system_prompt_tokens=system_tokens,
            memory_tokens=memory_tokens,
            skills_tokens=skills_tokens,
            session_notes_tokens=session_tokens,
            current_messages_tokens=message_tokens,
        )

        compression_ratio = self.get_compression_ratio(
            system_prompt_tokens=system_tokens,
            memory_tokens=memory_tokens,
            skills_tokens=skills_tokens,
            session_notes_tokens=session_tokens,
            current_messages_tokens=message_tokens,
        )

        return {
            "model": self.model_name,
            "window_size": self.window_size,
            "total_tokens_used": total_used,
            "percentage_used": round((total_used / self.window_size) * 100, 1) if self.window_size else 0,
            "budget": {
                "system_prompt": system_tokens,
                "memory": memory_tokens,
                "skills": skills_tokens,
                "session_notes": session_tokens,
                "messages": message_tokens,
                "free_space": budget.free_space,
                "recency_window": budget.recency_window,
                "compression_target": budget.compression_target,
            },
            "needs_compression": compression_needed,
            "compression_aggressiveness": round(compression_ratio, 2),
            "recommendation": self._get_recommendation(total_used, compression_needed, compression_ratio),
        }

    def _get_recommendation(self, total_used: int, needs_compression: bool, ratio: float) -> str:
        """Get a human-readable recommendation."""
        pct = (total_used / self.window_size) * 100 if self.window_size else 0

        if not needs_compression:
            if pct < 50:
                return "Plenty of context available. No action needed."
            elif pct < 70:
                return "Context is filling up. Consider compressing older messages soon."
            else:
                return "Context is getting tight but still fits. Compress on next message."
        else:
            if ratio < 0.3:
                return "Mild compression needed. Summarize oldest messages."
            elif ratio < 0.7:
                return "Moderate compression needed. Summarize older messages, drop verbose tool output."
            else:
                return "Aggressive compression needed. Summarize all but recent messages. Drop tool output."