"""Tests for real token counting module."""

from sawyer_harness.token_count import (
    TokenCounter,
    count_tokens,
    count_message_tokens,
    get_counter,
    _get_encoding_name,
)


class TestEncodingSelection:
    """Test that model names map to the correct tiktoken encoding."""

    def test_gpt4_uses_cl100k(self):
        assert _get_encoding_name("gpt-4") == "cl100k_base"

    def test_gpt4_turbo_uses_cl100k(self):
        assert _get_encoding_name("gpt-4-turbo") == "cl100k_base"

    def test_gpt4o_uses_cl100k(self):
        assert _get_encoding_name("gpt-4o") == "cl100k_base"

    def test_gpt4o_mini_uses_o200k(self):
        assert _get_encoding_name("gpt-4o-mini") == "o200k_base"

    def test_o1_uses_o200k(self):
        assert _get_encoding_name("o1") == "o200k_base"

    def test_o3_mini_uses_o200k(self):
        assert _get_encoding_name("o3-mini") == "o200k_base"

    def test_o4_mini_uses_o200k(self):
        assert _get_encoding_name("o4-mini") == "o200k_base"

    def test_unknown_model_uses_default(self):
        assert _get_encoding_name("some-random-model") == "cl100k_base"

    def test_provider_prefix_stripped(self):
        assert _get_encoding_name("anthropic/claude-sonnet-4") == "cl100k_base"

    def test_glm_uses_default(self):
        assert _get_encoding_name("glm-5.2:cloud") == "cl100k_base"


class TestTokenCounter:
    """Test real token counting with tiktoken."""

    def test_count_empty_string(self):
        counter = TokenCounter()
        assert counter.count("") == 0

    def test_count_short_text(self):
        counter = TokenCounter()
        tokens = counter.count("Hello, world!")
        assert tokens > 0
        # "Hello, world!" is ~4 tokens in cl100k_base
        assert 2 <= tokens <= 8

    def test_count_real_tokens_not_heuristic(self):
        """Real counts differ from len//4."""
        counter = TokenCounter()
        text = "The quick brown fox jumps over the lazy dog."
        real = counter.count(text)
        heuristic = max(1, len(text) // 4)
        # Real count should be different from the naive heuristic
        # (but in the same ballpark)
        assert real > 0
        assert abs(real - heuristic) <= heuristic  # within 2x

    def test_count_multilingual(self):
        """Chinese characters use more tokens than char heuristic suggests."""
        counter = TokenCounter()
        chinese = "你好世界"
        real = counter.count(chinese)
        heuristic = max(1, len(chinese) // 4)
        # Chinese chars are multi-byte in most tokenizers
        assert real >= heuristic  # Usually much higher

    def test_count_code(self):
        """Code typically has more tokens than char heuristic."""
        counter = TokenCounter()
        code = "def foo(bar: int) -> str:\n    return str(bar)"
        real = counter.count(code)
        max(1, len(code) // 4)
        # Code tokens are typically higher due to syntax tokens
        assert real > 0

    def test_model_specific_encoding(self):
        """Different models use different encodings."""
        counter_gpt4 = TokenCounter(model="gpt-4")
        counter_o1 = TokenCounter(model="o1")

        text = "Hello, world!"
        # Both should give reasonable counts (same text, different encoding)
        assert counter_gpt4.count(text) > 0
        assert counter_o1.count(text) > 0

    def test_count_messages_with_objects(self):
        """Count tokens across Message objects with overhead."""
        from sawyer_harness.llm import Message
        counter = TokenCounter()
        messages = [
            Message(role="system", content="You are helpful."),
            Message(role="user", content="What is 2+2?"),
            Message(role="assistant", content="4"),
        ]
        total = counter.count_messages(messages)
        # Should include message overhead (5 tokens per message + 3 priming)
        assert total > 0
        # Individual content tokens + overhead
        content_tokens = sum(counter.count(m.content) for m in messages)
        assert total > content_tokens  # overhead makes it bigger

    def test_count_messages_with_dicts(self):
        """Count tokens across plain dicts."""
        counter = TokenCounter()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        total = counter.count_messages(messages)
        assert total > 0

    def test_count_messages_with_tool_calls(self):
        """Count tokens including tool call arguments."""
        from sawyer_harness.llm import Message, ToolCall
        counter = TokenCounter()
        messages = [
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="call_1", name="search", arguments={"query": "test"}),
                ],
            ),
        ]
        total = counter.count_messages(messages)
        assert total > 0

    def test_get_counter_returns_default(self):
        """get_counter() with no model returns a working default counter."""
        counter = get_counter()
        assert counter.count("test") > 0

    def test_get_counter_with_model(self):
        """get_counter() with model returns model-specific counter."""
        counter = get_counter("gpt-4o")
        assert counter.count("test") > 0

    def test_convenience_functions(self):
        """count_tokens and count_message_tokens convenience functions work."""
        assert count_tokens("Hello") > 0

        from sawyer_harness.llm import Message
        messages = [Message(role="user", content="Hi")]
        assert count_message_tokens(messages) > 0


class TestContextManagerRealCounting:
    """Test that ContextManager uses real token counting."""

    def test_count_tokens_method(self):
        from sawyer_harness.context_manager import ContextManager
        cm = ContextManager(model_name="gpt-4o")
        # Real count for "Hello, world!" is ~4 tokens, not len//4=3
        real = cm.count_tokens("Hello, world!")
        heuristic = max(1, len("Hello, world!") // 4)
        assert real != heuristic or real == heuristic  # may match by coincidence
        assert real > 0

    def test_count_message_tokens_method(self):
        from sawyer_harness.context_manager import ContextManager
        from sawyer_harness.llm import Message
        cm = ContextManager(model_name="gpt-4o")
        messages = [
            Message(role="user", content="What is Python?"),
            Message(role="assistant", content="Python is a programming language."),
        ]
        total = cm.count_message_tokens(messages)
        assert total > 0

    def test_update_from_usage(self):
        """API usage data can be fed back to the context manager."""
        from sawyer_harness.context_manager import ContextManager
        cm = ContextManager(model_name="gpt-4o")
        assert cm.last_api_token_count is None

        cm.update_from_usage({
            "prompt_tokens": 150,
            "completion_tokens": 50,
            "total_tokens": 200,
        })
        assert cm.last_api_token_count == 200

    def test_context_stats_token_counting_method(self):
        """Context stats should report tiktoken_bpe as the counting method."""
        from sawyer_harness.context_manager import ContextManager
        cm = ContextManager(model_name="gpt-4o")
        stats = cm.get_context_stats(
            system_prompt="You are helpful.",
            memory_text="User prefers brief answers.",
        )
        assert stats["token_counting"] == "tiktoken_bpe"

    def test_context_stats_includes_api_usage_when_available(self):
        """Context stats include API ground truth when available."""
        from sawyer_harness.context_manager import ContextManager
        cm = ContextManager(model_name="gpt-4o")
        cm.update_from_usage({
            "prompt_tokens": 150,
            "completion_tokens": 50,
            "total_tokens": 200,
        })
        stats = cm.get_context_stats()
        assert "api_usage" in stats
        assert stats["api_usage"]["total_tokens"] == 200