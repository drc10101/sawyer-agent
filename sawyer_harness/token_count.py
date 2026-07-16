"""
Token counting -- real tokenizers, not guesses.

The old approach was `len(text) // 4` (4 chars per token). That's wildly
inaccurate -- it overcounts code and structured data, undercounts dense
prose, and has no model awareness.

This module uses tiktoken (OpenAI's actual BPE tokenizer) for real counts.
For OpenAI models, the exact encoding is selected. For other models,
cl100k_base (GPT-4's encoding) is used as a universal approximation that's
far more accurate than char-count heuristics.

If tiktoken isn't installed, falls back to the char heuristic with a clear
warning logged once.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger("sawyer-harness.token_count")

# ---------------------------------------------------------------------------
# Encoding selection by model
# ---------------------------------------------------------------------------

# Maps model name patterns to tiktoken encoding names.
# More specific patterns should come first.
_MODEL_ENCODINGS: list[tuple[str, str]] = [
    # o200k_base models (GPT-4o-mini, o1, o3, o4-mini)
    ("o4-mini", "o200k_base"),
    ("o3-mini", "o200k_base"),
    ("o3-", "o200k_base"),
    ("o1-mini", "o200k_base"),
    ("o1-pro", "o200k_base"),
    ("o1-", "o200k_base"),
    ("o1", "o200k_base"),
    ("gpt-4o-mini", "o200k_base"),
    # cl100k_base models (GPT-4, GPT-4-turbo, GPT-4o, GPT-3.5-turbo)
    ("gpt-4o", "cl100k_base"),
    ("gpt-4", "cl100k_base"),
    ("gpt-3.5", "cl100k_base"),
    # Everything else defaults to cl100k_base as best general approximation
]

# For non-OpenAI models, cl100k_base is the best general-purpose approximation.
# It's what GPT-4 uses and is closer to how most modern LLMs tokenize than
# any char-count heuristic.
_DEFAULT_ENCODING = "cl100k_base"


def _get_encoding_name(model: str) -> str:
    """Pick the tiktoken encoding for a model name.

    For unknown models, returns cl100k_base (GPT-4's encoding), which is
    the most accurate general-purpose approximation available.
    """
    normalized = model.lower().strip()
    # Strip provider prefix (e.g. "anthropic/claude-sonnet-4" -> "claude-sonnet-4")
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[1]

    for pattern, encoding in _MODEL_ENCODINGS:
        if pattern in normalized:
            return encoding
    return _DEFAULT_ENCODING


# ---------------------------------------------------------------------------
# TokenCounter -- the real deal
# ---------------------------------------------------------------------------

class TokenCounter:
    """Real token counting using tiktoken BPE tokenizers.

    Usage:
        counter = TokenCounter()  # uses cl100k_base by default
        n = counter.count("Hello, world!")  # returns actual token count

        # Or with a specific model:
        counter = TokenCounter(model="gpt-4o")
        n = counter.count(some_text)

    If tiktoken isn't installed, falls back to char-count heuristic
    and logs a warning once.
    """

    def __init__(self, model: str = ""):
        self._model = model
        self._encoder = None
        self._fallback = False
        self._fallback_warned = False
        self._encoding_name = _get_encoding_name(model) if model else _DEFAULT_ENCODING
        self._init_encoder()

    def _init_encoder(self):
        """Try to load tiktoken, fall back to heuristic on failure."""
        try:
            import tiktoken
            self._encoder = tiktoken.get_encoding(self._encoding_name)
        except ImportError:
            self._fallback = True
            self._encoder = None
        except Exception as e:
            logger.warning(f"Failed to load tiktoken encoding '{self._encoding_name}': {e}")
            self._fallback = True
            self._encoder = None

    def count(self, text: str) -> int:
        """Count the actual number of tokens in text.

        Uses tiktoken BPE tokenizer for real counts. Falls back to
        len(text) // 4 only if tiktoken is unavailable.
        """
        if not text:
            return 0

        if self._encoder is not None:
            try:
                return len(self._encoder.encode(text))
            except Exception as e:
                logger.debug(f"tiktoken encode failed, falling back: {e}")
                return max(1, len(text) // 4)

        # Fallback: char heuristic (less accurate)
        if not self._fallback_warned:
            logger.warning(
                "tiktoken not installed -- using char heuristic for token counts. "
                "Install tiktoken for accurate counts: pip install tiktoken"
            )
            self._fallback_warned = True
        return max(1, len(text) // 4)

    def count_messages(self, messages: list) -> int:
        """Count tokens across a list of Message objects or dicts.

        Handles both sawyer_harness.llm.Message objects and plain dicts.
        Includes overhead per message (role tokens, formatting) based on
        OpenAI's documented message overhead of ~4-7 tokens per message.
        """
        total = 0
        MESSAGE_OVERHEAD = 5  # tokens for role markers, separators, etc.

        for msg in messages:
            # Extract content
            if hasattr(msg, "content"):
                content = msg.content or ""
            elif isinstance(msg, dict):
                content = msg.get("content", "") or ""
            else:
                content = str(msg)

            total += self.count(content)

            # Role tokens
            role = ""
            if hasattr(msg, "role"):
                role = msg.role
            elif isinstance(msg, dict):
                role = msg.get("role", "")
            if role:
                total += self.count(role) + MESSAGE_OVERHEAD

            # Tool call tokens (these can be large)
            tool_calls = None
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = msg.tool_calls
            elif isinstance(msg, dict) and msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]

            if tool_calls:
                import json
                for tc in tool_calls:
                    if hasattr(tc, "arguments"):
                        # ToolCall object
                        total += self.count(tc.name or "")
                        total += self.count(json.dumps(tc.arguments, default=str))
                    elif isinstance(tc, dict):
                        # Dict format
                        func = tc.get("function", tc)
                        total += self.count(func.get("name", ""))
                        total += self.count(func.get("arguments", "") if isinstance(func.get("arguments"), str) else json.dumps(func.get("arguments", {}), default=str))
                    total += MESSAGE_OVERHEAD  # tool call overhead

        # Every conversation has a few tokens of priming overhead
        total += 3  # <|endofprompt|> or similar
        return total


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

# Global default counter -- uses cl100k_base (GPT-4 encoding, best general
# approximation). Create a model-specific counter if you need exact counts
# for a specific OpenAI model.
_default_counter: Optional[TokenCounter] = None


def get_counter(model: str = "") -> TokenCounter:
    """Get a TokenCounter instance.

    If model is provided, returns a counter with the appropriate encoding.
    If model is empty, returns the default cl100k_base counter.
    Caches the default counter for reuse.
    """
    global _default_counter
    if model:
        return TokenCounter(model=model)
    if _default_counter is None:
        _default_counter = TokenCounter()
    return _default_counter


def count_tokens(text: str, model: str = "") -> int:
    """Convenience function: count tokens in a string.

    Args:
        text: The text to count tokens in.
        model: Optional model name for encoding selection.

    Returns:
        Actual token count (or heuristic estimate if tiktoken unavailable).
    """
    return get_counter(model).count(text)


def count_message_tokens(messages: list, model: str = "") -> int:
    """Convenience function: count tokens across messages.

    Args:
        messages: List of Message objects or dicts.
        model: Optional model name for encoding selection.

    Returns:
        Total token count including message overhead.
    """
    return get_counter(model).count_messages(messages)