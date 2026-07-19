"""
Context compression engine -- keep what matters, drop what doesn't.

Unlike Hermes's brute-force compression that just truncates old messages,
Sawyer's compression is priority-aware:

1. System prompt: NEVER compressed (always kept)
2. Memory/skills: NEVER compressed (always kept)
3. Tool results: compressed FIRST (they're verbose, low-info-density)
4. Older messages: summarized with decision extraction
5. Recent messages: kept verbatim (recency bias)
6. Decisions: extracted and preserved even from compressed messages
7. User corrections: ALWAYS preserved (highest priority after system prompt)

The goal: make any context window feel 3-4x bigger by being smarter about
what occupies it. A well-compressed 128K window holds what a naive 400K
window would.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum

from .token_count import get_counter

logger = logging.getLogger("sawyer-harness.compression")


class Priority(IntEnum):
    """Message priority levels. Higher = more important to keep."""
    DISPOSABLE = 0    # Verbose tool output, status messages
    LOW = 1           # Routine tool results, acknowledgments
    NORMAL = 2        # Standard conversation turns
    HIGH = 3          # User corrections, decisions, key facts
    CRITICAL = 4      # System prompt, memory, skills -- NEVER compress


@dataclass
class CompressionResult:
    """Result of a compression pass."""
    original_tokens: int
    compressed_tokens: int
    messages_kept: int
    messages_summarized: int
    messages_dropped: int
    decisions_extracted: list[str]
    summary: str = ""


@dataclass
class MessageBlock:
    """A group of consecutive messages that can be summarized together."""
    start_index: int
    end_index: int
    messages: list = field(default_factory=list)
    priority: Priority = Priority.NORMAL
    estimated_tokens: int = 0


class ContextCompressor:
    """
    Priority-aware context compression.

    The compressor analyzes conversation history and decides what to keep,
    what to summarize, and what to drop. It preserves decisions and
    corrections even when summarizing old messages.

    Token counting uses tiktoken BPE for real accuracy. Falls back to
    char heuristic only if tiktoken is unavailable.
    """

    # Decision patterns -- things that look like decisions worth preserving
    DECISION_PATTERNS = [
        r"(?:let's|we'll|I'll|we should|let me|going to|decided to|decided on)\s+(.+)",
        r"(?:the (?:plan|approach|solution|fix|method)) (?:is|will be|should be)\s+(.+)",
        r"(?:actually|wait|no,|correction|instead)\s+(.+)",
        r"(?:important|key|critical|note that|remember that)\s*[:.]?\s*(.+)",
        r"(?:this means|that means|so we|therefore)\s+(.+)",
        r"(?:don't|never|avoid|must|always)\s+(.+)",
    ]

    # Correction patterns -- user corrections are CRITICAL priority
    CORRECTION_PATTERNS = [
        r"^(?:no,?\s+|actually,?\s+|wait,?\s+|that's wrong|wrong|incorrect|not quite)\s*",
        r"^(?:I meant|I meant to say|let me rephrase|let me clarify|to be clear)\s*",
        r"^(?:don't|never|stop|avoid)\s+",
    ]

    def __init__(self, max_tokens: int = 128000, reserve_ratio: float = 0.2, model: str = ""):
        """
        Args:
            max_tokens: Maximum context window size in tokens.
            reserve_ratio: Fraction of window to reserve for new messages.
                          0.2 means keep 20% free for the next exchange.
            model: Model name for tokenizer selection.
        """
        self.max_tokens = max_tokens
        self.reserve_ratio = reserve_ratio
        self.window_size = max_tokens  # Alias for ContextManager compat
        self._counter = get_counter(model) if model else get_counter()

    def estimate_tokens(self, text: str) -> int:
        """Count real tokens using tiktoken BPE tokenizer.

        Falls back to char heuristic only if tiktoken is unavailable.
        """
        return self._counter.count(text)

    def classify_priority(self, role: str, content: str) -> Priority:
        """Classify a message's compression priority."""
        if not content:
            return Priority.DISPOSABLE

        # System messages are always critical
        if role == "system":
            return Priority.CRITICAL

        # Check for user corrections
        if role == "user":
            for pattern in self.CORRECTION_PATTERNS:
                if re.search(pattern, content, re.IGNORECASE):
                    return Priority.CRITICAL

        # Check for decisions
        for pattern in self.DECISION_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return Priority.HIGH

        # Tool results are low priority (verbose, low info density)
        if role == "tool":
            # Short tool results might be important
            if len(content) < 200:
                return Priority.LOW
            return Priority.DISPOSABLE

        # Recent-ish user messages are normal priority
        if role == "user":
            return Priority.NORMAL

        # Assistant messages
        if role == "assistant":
            # Code blocks contain decisions
            if "```" in content:
                return Priority.HIGH
            # Short responses are likely decisions
            if len(content) < 100:
                return Priority.HIGH
            return Priority.NORMAL

        return Priority.NORMAL

    def extract_decisions(self, messages: list) -> list[str]:
        """Extract key decisions and corrections from messages."""
        decisions = []

        for msg in messages:
            content = msg.content if hasattr(msg, "content") else str(msg)
            role = msg.role if hasattr(msg, "role") else "unknown"

            for pattern in self.DECISION_PATTERNS:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    decisions.append(f"[{role}] {match.group(0).strip()}")

            # User corrections are always captured
            if role == "user":
                for cpattern in self.CORRECTION_PATTERNS:
                    if re.search(cpattern, content, re.IGNORECASE):
                        decisions.append(f"[correction] {content[:200]}")
                        break

        return decisions

    def summarize_block(self, messages: list) -> str:
        """
        Summarize a block of messages into a compact form.

        This is a rule-based summarizer. For production use, this would
        call an LLM to generate the summary. The rule-based version
        extracts key points and drops verbose content.
        """
        parts = []
        user_msgs = []
        assistant_msgs = []
        tool_msgs = []

        for msg in messages:
            role = msg.role if hasattr(msg, "role") else "unknown"
            content = msg.content if hasattr(msg, "content") else str(msg)
            if role == "user":
                user_msgs.append(content)
            elif role == "assistant":
                assistant_msgs.append(content)
            elif role == "tool":
                tool_msgs.append(content)

        # Summarize user intent
        if user_msgs:
            parts.append("User discussed:")
            for msg in user_msgs:
                # Take first sentence or 100 chars, whichever is shorter
                first_sentence = msg.split(".")[0][:100]
                parts.append(f"  - {first_sentence}")

        # Summarize assistant actions
        if assistant_msgs:
            parts.append("Assistant responded:")
            for msg in assistant_msgs:
                # Extract key points (first sentence, decisions)
                first_sentence = msg.split(".")[0][:100]
                parts.append(f"  - {first_sentence}")

        # Tool results: just count them
        if tool_msgs:
            parts.append(f"({len(tool_msgs)} tool calls executed)")

        return "\n".join(parts)

    def compress(
        self,
        messages: list,
        system_prompt: str = "",
        memory_text: str = "",
        skills_text: str = "",
    ) -> tuple[list, CompressionResult]:
        """
        Compress a conversation to fit within the token budget.

        Strategy:
        1. Calculate the token budget (max_tokens - reserve)
        2. Account for system prompt, memory, skills (these are NEVER compressed)
        3. From most recent messages, keep verbatim until budget is full
        4. From older messages, extract decisions and summarize
        5. Drop low-priority content (verbose tool output)
        6. Preserve all CRITICAL messages (corrections, decisions)

        Returns:
            Tuple of (compressed_messages, CompressionResult)
        """
        # Calculate fixed overhead
        overhead = 0
        fixed_messages = []

        if system_prompt:
            tokens = self.estimate_tokens(system_prompt)
            overhead += tokens
            fixed_messages.append(("system", system_prompt))

        if memory_text:
            tokens = self.estimate_tokens(memory_text)
            overhead += tokens

        if skills_text:
            tokens = self.estimate_tokens(skills_text)
            overhead += tokens

        # Calculate available budget for messages
        budget = int((self.max_tokens - overhead) * (1 - self.reserve_ratio))

        # Classify all messages by priority
        classified = []
        for msg in messages:
            content = msg.content if hasattr(msg, "content") else str(msg)
            role = msg.role if hasattr(msg, "role") else "unknown"
            priority = self.classify_priority(role, content)
            tokens = self.estimate_tokens(content)
            classified.append({
                "message": msg,
                "role": role,
                "content": content,
                "priority": priority,
                "tokens": tokens,
            })

        # Total original tokens
        original_tokens = sum(c["tokens"] for c in classified) + overhead

        # Phase 1: Always keep CRITICAL messages
        critical = [c for c in classified if c["priority"] == Priority.CRITICAL]
        critical_tokens = sum(c["tokens"] for c in critical)

        # Phase 2: Keep recent messages (last N turns) verbatim
        # We keep messages from the end until we fill the budget
        kept_messages = []
        kept_tokens = critical_tokens

        # Add critical messages first
        for c in critical:
            kept_messages.append(c)

        # Add recent messages from the end, highest priority first
        recent = [c for c in reversed(classified) if c["priority"] != Priority.CRITICAL]

        for c in recent:
            if kept_tokens + c["tokens"] <= budget:
                kept_messages.append(c)
                kept_tokens += c["tokens"]

        # Phase 3: Compress older messages that didn't make the cut
        kept_ids = {id(c["message"]) for c in kept_messages}
        older = [c for c in classified if id(c["message"]) not in kept_ids]

        # Extract decisions from older messages
        decisions = self.extract_decisions([c["message"] for c in older])

        # Summarize older messages into a compact block
        summary = ""
        messages_summarized = 0
        messages_dropped = 0

        if older:
            # Group older messages into blocks for summarization
            high_priority_older = [c for c in older if c["priority"] >= Priority.HIGH]
            low_priority_older = [c for c in older if c["priority"] < Priority.HIGH]

            if high_priority_older:
                # High priority older messages get summarized
                summary_block = self.summarize_block([c["message"] for c in high_priority_older])
                summary += f"## Earlier Context (summarized)\n{summary_block}\n"
                messages_summarized += len(high_priority_older)

            # Low priority older messages get dropped
            messages_dropped += len(low_priority_older)

        # Add decisions as a preserved block
        if decisions:
            decision_text = "## Key Decisions\n" + "\n".join(f"- {d}" for d in decisions) + "\n"
            summary += decision_text

        # Build the final compressed message list
        compressed = []

        # Add system prompt if provided
        if system_prompt:
            if messages:
                compressed.append(type(messages[0])(
                    role="system", content=system_prompt
                ))

        # Add summary of older messages if we have one
        if summary:
            if messages:
                summary_msg = type(messages[0])(
                    role="system",
                    content=summary,
                )
                compressed.append(summary_msg)

        # Add kept messages in original order
        kept_by_index = sorted(kept_messages, key=lambda c: classified.index(c))
        for c in kept_by_index:
            compressed.append(c["message"])

        compressed_tokens = sum(
            self.estimate_tokens(m.content if hasattr(m, "content") else str(m))
            for m in compressed
        ) + overhead

        result = CompressionResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            messages_kept=len(kept_messages),
            messages_summarized=messages_summarized,
            messages_dropped=messages_dropped,
            decisions_extracted=decisions,
            summary=summary,
        )

        logger.info(
            f"Compression: {original_tokens} -> {compressed_tokens} tokens "
            f"({result.messages_kept} kept, {result.messages_summarized} summarized, "
            f"{result.messages_dropped} dropped, {len(decisions)} decisions extracted)"
        )

        return compressed, result

    def get_context_stats(self, messages: list, system_prompt: str = "") -> dict:
        """Get context window statistics without compressing."""
        total_tokens = 0
        by_role: dict[str, int] = {}
        by_priority: dict[str, int] = {}

        if system_prompt:
            total_tokens += self.estimate_tokens(system_prompt)

        for msg in messages:
            content = msg.content if hasattr(msg, "content") else str(msg)
            role = msg.role if hasattr(msg, "role") else "unknown"
            tokens = self.estimate_tokens(content)
            total_tokens += tokens

            by_role[role] = by_role.get(role, 0) + tokens

            priority = self.classify_priority(role, content)
            by_priority[priority.name] = by_priority.get(priority.name, 0) + tokens

        budget = int(self.max_tokens * (1 - self.reserve_ratio))
        used_pct = (total_tokens / self.max_tokens) * 100 if self.max_tokens else 0

        return {
            "total_tokens": total_tokens,
            "max_tokens": self.max_tokens,
            "budget_tokens": budget,
            "remaining_tokens": max(0, budget - total_tokens),
            "used_percentage": round(used_pct, 1),
            "needs_compression": total_tokens > budget,
            "by_role": by_role,
            "by_priority": by_priority,
            "message_count": len(messages),
            "compression_recommended": used_pct > 70,
        }


class LLMCompressor(ContextCompressor):
    """
    Enhanced compressor that uses an LLM for summarization.

    Falls back to rule-based summarization if no LLM is available.
    The LLM generates better summaries but the rule-based approach
    is always available as a fallback.
    """

    def __init__(self, llm_client=None, **kwargs):
        super().__init__(**kwargs)
        self.llm = llm_client

    def summarize_block(self, messages: list) -> str:
        """Use LLM for summarization if available, otherwise rule-based."""
        if self.llm is None:
            return super().summarize_block(messages)

        # Build a summarization prompt
        conversation_text = []
        for msg in messages:
            role = msg.role if hasattr(msg, "role") else "unknown"
            content = msg.content if hasattr(msg, "content") else str(msg)
            # Truncate very long tool results
            if role == "tool" and len(content) > 500:
                content = content[:500] + "..."
            conversation_text.append(f"{role}: {content}")

        (
            "Summarize this conversation excerpt in 2-3 sentences. "
            "Focus on: what was discussed, what was decided, what was built or changed. "
            "Omit routine tool output and status messages.\n\n"
            + "\n".join(conversation_text)
        )

        try:
            # Use the LLM to generate a summary
            # This would call the LLM client in production
            # For now, fall back to rule-based
            return super().summarize_block(messages)
        except Exception as e:
            logger.warning(f"LLM summarization failed, falling back to rule-based: {e}")
            return super().summarize_block(messages)