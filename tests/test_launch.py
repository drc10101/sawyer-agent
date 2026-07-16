"""Tests for self-activating launch module."""

import os
import pytest
from sawyer_harness.launch import parse_model_string, resolve_api_key, LaunchConfig


class TestParseModelString:
    """Model string parsing determines provider, base URL, and model name."""

    # -- Ollama routing suffixes --

    def test_cloud_suffix_sets_ollama_cloud(self):
        result = parse_model_string("glm-5.1:cloud")
        assert result.provider == "ollama"
        assert result.model == "glm-5.1"
        assert result.base_url == "https://ollama.com/v1"
        assert result.needs_key is True

    def test_local_suffix_sets_ollama_local(self):
        result = parse_model_string("llama3:local")
        assert result.provider == "ollama"
        assert result.model == "llama3"
        assert result.base_url == "http://localhost:11434/v1"
        assert result.needs_key is False

    def test_cloud_suffix_with_any_model(self):
        result = parse_model_string("mistral-small:cloud")
        assert result.provider == "ollama"
        assert result.model == "mistral-small"
        assert result.base_url == "https://ollama.com/v1"

    # -- OpenAI patterns --

    def test_gpt_prefix(self):
        result = parse_model_string("gpt-4o")
        assert result.provider == "openai"
        assert result.model == "gpt-4o"
        assert result.base_url == "https://api.openai.com/v1"
        assert result.needs_key is True

    def test_o1_prefix(self):
        result = parse_model_string("o1-preview")
        assert result.provider == "openai"
        assert result.model == "o1-preview"
        assert result.needs_key is True

    def test_o3_prefix(self):
        result = parse_model_string("o3-mini")
        assert result.provider == "openai"
        assert result.model == "o3-mini"

    def test_chatgpt_prefix(self):
        result = parse_model_string("chatgpt-4o-latest")
        assert result.provider == "openai"
        assert result.model == "chatgpt-4o-latest"

    # -- Anthropic patterns --

    def test_claude_prefix(self):
        result = parse_model_string("claude-sonnet-4-20250514")
        assert result.provider == "anthropic"
        assert result.model == "claude-sonnet-4-20250514"
        assert result.base_url == "https://api.anthropic.com"
        assert result.needs_key is True

    # -- DeepSeek patterns --

    def test_deepseek_prefix(self):
        result = parse_model_string("deepseek-chat")
        assert result.provider == "openai"
        assert result.model == "deepseek-chat"
        assert result.base_url == "https://api.deepseek.com/v1"

    # -- Gemini patterns --

    def test_gemini_prefix(self):
        result = parse_model_string("gemini-2.5-pro")
        assert result.provider == "openai"
        assert result.model == "gemini-2.5-pro"
        assert result.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"

    # -- Mistral patterns --

    def test_mistral_prefix(self):
        result = parse_model_string("mistral-large-latest")
        assert result.provider == "openai"
        assert result.model == "mistral-large-latest"
        assert result.base_url == "https://api.mistral.ai/v1"

    # -- Ollama default patterns --

    def test_llama_prefix(self):
        result = parse_model_string("llama3")
        assert result.provider == "ollama"
        assert result.model == "llama3"
        assert result.base_url == "http://localhost:11434/v1"
        assert result.needs_key is False

    def test_qwen_prefix(self):
        result = parse_model_string("qwen2.5-coder-32b")
        assert result.provider == "ollama"
        assert result.model == "qwen2.5-coder-32b"
        assert result.needs_key is False

    def test_phi_prefix(self):
        result = parse_model_string("phi-3-medium")
        assert result.provider == "ollama"
        assert result.model == "phi-3-medium"
        assert result.needs_key is False

    def test_gemma_prefix(self):
        result = parse_model_string("gemma2-27b")
        assert result.provider == "ollama"
        assert result.model == "gemma2-27b"
        assert result.needs_key is False

    def test_glm_prefix(self):
        result = parse_model_string("glm-5.1")
        assert result.provider == "ollama"
        assert result.model == "glm-5.1"
        assert result.needs_key is False

    # -- Sawyer network --

    def test_sawyer_prefix(self):
        result = parse_model_string("sawyer-moe-pro")
        assert result.provider == "sawyer"
        assert result.model == "sawyer-moe-pro"
        assert result.base_url == "https://api.sawyernetwork.ai/v1"

    # -- Bare names default to local Ollama --

    def test_unknown_model_defaults_to_local(self):
        result = parse_model_string("my-custom-model")
        assert result.provider == "ollama"
        assert result.model == "my-custom-model"
        assert result.base_url == "http://localhost:11434/v1"
        assert result.needs_key is False

    # -- Explicit provider prefix (provider/model) --

    def test_explicit_openai_prefix(self):
        result = parse_model_string("openai/gpt-4o")
        assert result.provider == "openai"
        assert result.model == "gpt-4o"
        assert result.base_url == "https://api.openai.com/v1"
        assert result.needs_key is True

    def test_explicit_anthropic_prefix(self):
        result = parse_model_string("anthropic/claude-sonnet-4")
        assert result.provider == "anthropic"
        assert result.model == "claude-sonnet-4"
        assert result.base_url == "https://api.anthropic.com"

    def test_explicit_ollama_prefix(self):
        result = parse_model_string("ollama/llama3")
        assert result.provider == "ollama"
        assert result.model == "llama3"
        assert result.base_url == "http://localhost:11434/v1"
        assert result.needs_key is False

    def test_explicit_local_prefix(self):
        result = parse_model_string("local/phi3")
        assert result.provider == "ollama"
        assert result.model == "phi3"
        assert result.base_url == "http://localhost:11434/v1"
        assert result.needs_key is False

    def test_explicit_cloud_prefix(self):
        result = parse_model_string("cloud/glm-5.1")
        assert result.provider == "ollama"
        assert result.model == "glm-5.1"
        assert result.base_url == "https://ollama.com/v1"
        assert result.needs_key is True

    def test_explicit_deepseek_prefix(self):
        result = parse_model_string("deepseek/deepseek-chat")
        assert result.provider == "openai"
        assert result.model == "deepseek-chat"
        assert result.base_url == "https://api.deepseek.com/v1"

    def test_explicit_gemini_prefix(self):
        result = parse_model_string("gemini/gemini-2.5-pro")
        assert result.provider == "openai"
        assert result.model == "gemini-2.5-pro"

    def test_explicit_sawyer_prefix(self):
        result = parse_model_string("sawyer/sawyer-moe-pro")
        assert result.provider == "sawyer"
        assert result.model == "sawyer-moe-pro"

    # -- Explicit prefix with routing suffix strips suffix --

    def test_explicit_prefix_strips_suffix(self):
        result = parse_model_string("ollama/llama3:cloud")
        assert result.provider == "ollama"
        assert result.model == "llama3"
        assert result.base_url == "http://localhost:11434/v1"  # local because "ollama/" prefix

    # -- Error cases --

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_model_string("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_model_string("   ")


class TestResolveApiKey:
    """API key resolution from multiple sources."""

    def test_explicit_key_takes_priority(self):
        launch = LaunchConfig("openai", "gpt-4o", "https://api.openai.com/v1", True, "OPENAI_API_KEY")
        result = resolve_api_key(launch, explicit_key="sk-explicit123")
        assert result == "sk-explicit123"

    def test_env_var_used_when_no_explicit_key(self):
        launch = LaunchConfig("openai", "gpt-4o", "https://api.openai.com/v1", True, "OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-envvar456"
        try:
            result = resolve_api_key(launch)
            assert result == "sk-envvar456"
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_sawyer_api_key_fallback(self):
        launch = LaunchConfig("openai", "gpt-4o", "https://api.openai.com/v1", True, "OPENAI_API_KEY")
        os.environ["SAWYER_API_KEY"] = "sk-sawyer789"
        try:
            # No specific env var set, no explicit key -- should fall back to SAWYER_API_KEY
            result = resolve_api_key(launch)
            assert result == "sk-sawyer789"
        finally:
            del os.environ["SAWYER_API_KEY"]

    def test_existing_config_key_used(self):
        launch = LaunchConfig("openai", "gpt-4o", "https://api.openai.com/v1", True, "OPENAI_API_KEY")
        result = resolve_api_key(launch, existing_key="sk-config321")
        assert result == "sk-config321"

    def test_no_key_needed_for_local(self):
        launch = LaunchConfig("ollama", "llama3", "http://localhost:11434/v1", False, "")
        result = resolve_api_key(launch)
        assert result == ""

    def test_explicit_key_overrides_env_var(self):
        launch = LaunchConfig("openai", "gpt-4o", "https://api.openai.com/v1", True, "OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-envvar456"
        try:
            result = resolve_api_key(launch, explicit_key="sk-explicit123")
            assert result == "sk-explicit123"
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_priority_order_explicit_then_env(self):
        launch = LaunchConfig("openai", "gpt-4o", "https://api.openai.com/v1", True, "OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-envvar"
        try:
            # explicit > env
            assert resolve_api_key(launch, explicit_key="sk-explicit") == "sk-explicit"
            # env > existing
            assert resolve_api_key(launch, existing_key="sk-existing") == "sk-envvar"
        finally:
            del os.environ["OPENAI_API_KEY"]