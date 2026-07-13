"""Tests for Sawyer Harness Telegram adapter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sawyer_harness.config import ChannelConfig, HarnessConfig
from sawyer_harness.telegram_adapter import (
    TelegramAdapter,
    TelegramRateLimiter,
    TelegramSession,
)


@pytest.fixture
def config():
    """Create a test HarnessConfig."""
    return HarnessConfig()


@pytest.fixture
def channel_config():
    """Create a test ChannelConfig for Telegram."""
    return ChannelConfig(
        name="telegram",
        enabled=True,
        config={
            "token": "test-token-123",
            "allowed_chats": [12345, 67890],
            "rate_limit": 5,
            "rate_window": 10,
            "system_prompt": "You are a test assistant.",
        },
    )


@pytest.fixture
def adapter(config, channel_config):
    """Create a TelegramAdapter with test config."""
    return TelegramAdapter(config, channel_config)


def test_rate_limiter_allows_within_limit():
    """Rate limiter allows messages within the limit."""
    limiter = TelegramRateLimiter(max_messages=3, window_seconds=60)

    assert limiter.check(1) is True
    assert limiter.check(1) is True
    assert limiter.check(1) is True


def test_rate_limiter_blocks_over_limit():
    """Rate limiter blocks messages over the limit."""
    limiter = TelegramRateLimiter(max_messages=2, window_seconds=60)

    assert limiter.check(1) is True
    assert limiter.check(1) is True
    assert limiter.check(1) is False  # Blocked


def test_rate_limiter_separate_chats():
    """Rate limiter tracks separate limits per chat."""
    limiter = TelegramRateLimiter(max_messages=1, window_seconds=60)

    assert limiter.check(1) is True
    assert limiter.check(2) is True  # Different chat, separate limit
    assert limiter.check(1) is False  # Chat 1 blocked
    assert limiter.check(2) is False  # Chat 2 blocked


def test_adapter_initialization(adapter):
    """Adapter initializes with config values."""
    assert adapter.token == "test-token-123"
    assert adapter.allowed_chat_ids == [12345, 67890]
    assert adapter.get_session_count() == 0


def test_adapter_stats(adapter):
    """Adapter returns correct stats."""
    stats = adapter.get_stats()
    assert stats["active_sessions"] == 0
    assert stats["total_messages"] == 0
    assert stats["allowed_chats"] == 2


def test_split_message_short(adapter):
    """Short messages are not split."""
    result = adapter._split_message("Hello, world!")
    assert result == ["Hello, world!"]


def test_split_message_long(adapter):
    """Long messages are split at paragraph boundaries."""
    # Create a message that's over 4096 chars
    paragraphs = [f"Paragraph {i} content. " * 50 for i in range(10)]
    long_msg = "\n\n".join(paragraphs)

    chunks = adapter._split_message(long_msg)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4096


def test_split_message_custom_limit(adapter):
    """Message splitting respects custom limit."""
    text = "A" * 200
    chunks = adapter._split_message(text, limit=100)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 100


def test_split_message_preserves_content(adapter):
    """All content is preserved after splitting."""
    text = "A" * 5000
    chunks = adapter._split_message(text, limit=1000)

    reconstructed = "".join(chunks)
    assert reconstructed == text


def test_telegram_session():
    """TelegramSession tracks message count and timestamps."""
    session = TelegramSession(
        chat_id=12345,
        agent=MagicMock(),
        created="2026-07-12T00:00:00",
    )

    assert session.message_count == 0

    session.touch()
    assert session.message_count == 1
    assert session.last_active != ""


def test_handle_command_start(adapter):
    """Handle /start command."""
    result = asyncio.run(adapter.handle_message(12345, "/start"))
    assert len(result) == 1
    assert "Sawyer Agent online" in result[0]


def test_handle_command_help(adapter):
    """Handle /help command."""
    result = asyncio.run(adapter.handle_message(12345, "/help"))
    assert len(result) == 1
    assert "/clear" in result[0]
    assert "/memory" in result[0]
    assert "/skills" in result[0]


def test_handle_command_clear(adapter):
    """Handle /clear command."""
    # Create a session first
    session = adapter._get_or_create_session(12345)
    assert session is not None

    result = asyncio.run(adapter.handle_message(12345, "/clear"))
    assert "cleared" in result[0].lower()


def test_handle_command_unknown(adapter):
    """Handle unknown command."""
    result = asyncio.run(adapter.handle_message(12345, "/unknown"))
    assert "Unknown command" in result[0]


def test_rate_limit_in_handle(adapter):
    """Rate limit blocks excessive messages."""
    # Our test adapter has rate_limit=5, window=10
    for i in range(5):
        result = asyncio.run(adapter.handle_message(12345, "/help"))
        assert len(result) > 0

    # 6th message should be rate limited
    result = asyncio.run(adapter.handle_message(12345, "/help"))
    assert result[0] == "Rate limit exceeded. Please wait a moment."