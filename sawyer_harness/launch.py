"""
Self-activating launch -- parse a model string, auto-configure, and start.

Usage:
    sawyer launch glm-5.1:cloud              # Recognized: Ollama cloud
    sawyer launch gpt-4o                    # Recognized: OpenAI
    sawyer launch claude-sonnet-4            # Recognized: Anthropic
    sawyer launch llama3                    # Bare name: local Ollama
    sawyer launch --model gpt-4o --key sk-xxx   # Explicit, zero prompts

The model string is parsed to determine provider and base URL.
If an API key is required and not provided, Sawyer checks environment
variables first, then the config file, then prompts once.

Supported model string patterns:
    glm-*:cloud         -> ollama, https://ollama.com/v1
    glm-*:local         -> ollama, http://localhost:11434/v1
    *:cloud             -> ollama, https://ollama.com/v1
    *:local             -> ollama, http://localhost:11434/v1
    gpt-*               -> openai, https://api.openai.com/v1
    o1-*                -> openai, https://api.openai.com/v1
    o3-*                -> openai, https://api.openai.com/v1
    claude-*            -> anthropic, https://api.anthropic.com
    deepseek-*          -> openai-compat, https://api.deepseek.com/v1
    gemini-*            -> openai-compat, https://generativelanguage.googleapis.com/v1beta/openai
   Anything else with no prefix pattern defaults to local Ollama.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# ============================================================
# Model string parsing
# ============================================================

# Provider fingerprint rules. Evaluated in order; first match wins.
# Each rule: (match_func, provider_id, base_url, needs_key)
# match_func takes the model string and returns True if this rule applies.

def _starts_with(prefix: str):
    """Match model strings that start with a prefix."""
    def matcher(model: str) -> bool:
        return model.split(":")[0].startswith(prefix)
    return matcher


def _exact(name: str):
    """Match model strings that are exactly this name (before the :suffix)."""
    def matcher(model: str) -> bool:
        return model.split(":")[0] == name
    return matcher


def _has_suffix(suffix: str):
    """Match model strings with a :suffix tag (e.g. :cloud, :local)."""
    def matcher(model: str) -> bool:
        return ":" in model and model.rsplit(":", 1)[-1] == suffix
    return matcher


@dataclass
class LaunchConfig:
    """Resolved launch configuration from a model string."""
    provider: str
    model: str       # The model name (suffix stripped if it was a routing hint)
    base_url: str
    needs_key: bool
    key_env_var: str  # Environment variable to check for API key


# Rules in priority order. More specific rules first.
_LAUNCH_RULES: list[tuple] = [
    # (matcher, provider, base_url, needs_key, key_env_var)
    (_has_suffix("cloud"), "ollama", "https://ollama.com/v1", True, "SAWYER_API_KEY"),
    (_has_suffix("local"), "ollama", "http://localhost:11434/v1", False, ""),
    (_starts_with("gpt-"), "openai", "https://api.openai.com/v1", True, "OPENAI_API_KEY"),
    (_starts_with("o1-"), "openai", "https://api.openai.com/v1", True, "OPENAI_API_KEY"),
    (_starts_with("o3-"), "openai", "https://api.openai.com/v1", True, "OPENAI_API_KEY"),
    (_starts_with("chatgpt-"), "openai", "https://api.openai.com/v1", True, "OPENAI_API_KEY"),
    (_starts_with("claude-"), "anthropic", "https://api.anthropic.com", True, "ANTHROPIC_API_KEY"),
    (_starts_with("deepseek-"), "openai", "https://api.deepseek.com/v1", True, "DEEPSEEK_API_KEY"),
    (_starts_with("gemini-"), "openai", "https://generativelanguage.googleapis.com/v1beta/openai", True, "GEMINI_API_KEY"),
    (_starts_with("mistral-"), "openai", "https://api.mistral.ai/v1", True, "MISTRAL_API_KEY"),
    (_starts_with("qwen"), "ollama", "http://localhost:11434/v1", False, ""),
    (_starts_with("llama"), "ollama", "http://localhost:11434/v1", False, ""),
    (_starts_with("codellama"), "ollama", "http://localhost:11434/v1", False, ""),
    (_starts_with("phi"), "ollama", "http://localhost:11434/v1", False, ""),
    (_starts_with("gemma"), "ollama", "http://localhost:11434/v1", False, ""),
    (_starts_with("mistral"), "ollama", "http://localhost:11434/v1", False, ""),
    (_starts_with("glm-"), "ollama", "http://localhost:11434/v1", False, ""),
    (_starts_with("sawyer-"), "sawyer", "https://api.sawyernetwork.ai/v1", True, "SAWYER_API_KEY"),
    (_exact("local"), "ollama", "http://localhost:11434/v1", False, ""),
    (_exact("cloud"), "ollama", "https://ollama.com/v1", True, "SAWYER_API_KEY"),
]

# Display names for providers
_PROVIDER_DISPLAY = {
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "sawyer": "Sawyer Network",
}


def parse_model_string(model: str) -> LaunchConfig:
    """Parse a model string and return the resolved configuration.

    The model string can be:
      - A plain name: llama3, gpt-4o, claude-sonnet-4
      - A name with routing suffix: glm-5.1:cloud, llama3:local
      - A full provider prefix: openai/gpt-4o, anthropic/claude-sonnet-4

    The routing suffix (:cloud, :local) is stripped from the model name
    after determining the provider -- it's a hint for Sawyer, not the LLM.
    """
    original = model.strip()
    if not original:
        raise ValueError("Model string cannot be empty")

    # Handle explicit provider prefix: openai/gpt-4o
    if "/" in original:
        provider_prefix, model_name = original.split("/", 1)
        provider_map = {
            "ollama": ("ollama", "http://localhost:11434/v1", False, ""),
            "openai": ("openai", "https://api.openai.com/v1", True, "OPENAI_API_KEY"),
            "anthropic": ("anthropic", "https://api.anthropic.com", True, "ANTHROPIC_API_KEY"),
            "sawyer": ("sawyer", "https://api.sawyernetwork.ai/v1", True, "SAWYER_API_KEY"),
            "deepseek": ("openai", "https://api.deepseek.com/v1", True, "DEEPSEEK_API_KEY"),
            "gemini": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai", True, "GEMINI_API_KEY"),
            "mistral": ("openai", "https://api.mistral.ai/v1", True, "MISTRAL_API_KEY"),
            "local": ("ollama", "http://localhost:11434/v1", False, ""),
            "cloud": ("ollama", "https://ollama.com/v1", True, "SAWYER_API_KEY"),
        }
        if provider_prefix.lower() in provider_map:
            pid, url, needs_key, env_var = provider_map[provider_prefix.lower()]
            # Strip routing suffix from model name if present
            clean_model = model_name.split(":")[0] if ":" in model_name else model_name
            return LaunchConfig(
                provider=pid,
                model=clean_model,
                base_url=url,
                needs_key=needs_key,
                key_env_var=env_var,
            )
        # Unknown provider prefix -- treat the whole thing as a model name
        # and fall through to rule matching

    # Check suffix rules first (most specific)
    for matcher, provider, base_url, needs_key, env_var in _LAUNCH_RULES:
        if matcher(original):
            # Strip the routing suffix (:cloud, :local) from the model name
            if ":" in original and original.rsplit(":", 1)[-1] in ("cloud", "local"):
                clean_model = original.rsplit(":", 1)[0]
            else:
                clean_model = original
            return LaunchConfig(
                provider=provider,
                model=clean_model,
                base_url=base_url,
                needs_key=needs_key,
                key_env_var=env_var,
            )

    # Default: local Ollama (no API key needed)
    return LaunchConfig(
        provider="ollama",
        model=original,
        base_url="http://localhost:11434/v1",
        needs_key=False,
        key_env_var="",
    )


def resolve_api_key(launch: LaunchConfig, explicit_key: str = "", existing_key: str = "") -> str:
    """Resolve the API key from multiple sources.

    Priority: explicit --key flag > environment variable > existing config > prompt
    """
    # 1. Explicit key from command line
    if explicit_key:
        return explicit_key

    # 2. Don't need a key at all
    if not launch.needs_key:
        return ""

    # 3. Check environment variable
    if launch.key_env_var:
        env_val = os.environ.get(launch.key_env_var, "")
        if env_val:
            return env_val

    # 4. Check generic Sawyer API key env var
    env_val = os.environ.get("SAWYER_API_KEY", "")
    if env_val:
        return env_val

    # 5. Use existing config key
    if existing_key:
        return existing_key

    # 6. Prompt the user
    provider_display = _PROVIDER_DISPLAY.get(launch.provider, launch.provider.title())
    print(f"\n  {provider_display} requires an API key for {launch.model}.")
    print(f"  Get one at:", end="")
    key_urls = {
        "openai": "https://platform.openai.com/api-keys",
        "anthropic": "https://console.anthropic.com/settings/keys",
        "ollama": "https://ollama.com",
        "sawyer": "https://sawyer.infill.systems",
        "deepseek": "https://platform.deepseek.com/api_keys",
        "gemini": "https://aistudio.google.com/apikey",
        "mistral": "https://console.mistral.ai/api-keys",
    }
    url = key_urls.get(launch.provider, "")
    if url:
        print(f" {url}")
    else:
        print()

    try:
        key = input(f"  API Key: ").strip()
    except (EOFError, KeyboardInterrupt):
        key = ""
        print()

    if not key:
        print(f"\n  No API key provided. Sawyer will start but won't connect until you add one.")
        print(f"  Set {launch.key_env_var or 'SAWYER_API_KEY'} or run: python -m sawyer_harness setup\n")

    return key


def auto_launch(model: str, api_key: str = "", host: str = "127.0.0.1", port: int = 8765,
                verbose: bool = False, config_path: str | None = None) -> None:
    """Parse model string, resolve config, save, and launch the server.

    This is the self-activating entry point. It:
    1. Parses the model string to determine provider, base URL, model name
    2. Resolves the API key from env vars, existing config, or prompt
    3. Saves the configuration
    4. Starts the web server
    """
    from .config import HarnessConfig, LLMConfig, DEFAULT_CONFIG_PATH
    from .web.server import run_server
    from .cli import _cmd_web
    import argparse
    import logging

    # Parse the model string
    try:
        launch = parse_model_string(model)
    except ValueError as e:
        print(f"Error: {e}")
        return

    # Load existing config for fallback key
    config_path = config_path or str(DEFAULT_CONFIG_PATH)
    existing_key = ""
    if DEFAULT_CONFIG_PATH.exists():
        try:
            existing_config = HarnessConfig.from_file(DEFAULT_CONFIG_PATH)
            existing_key = existing_config.llm.api_key
        except Exception:
            pass

    # Resolve API key
    resolved_key = resolve_api_key(launch, explicit_key=api_key, existing_key=existing_key)

    # Build config
    config = HarnessConfig(
        llm=LLMConfig(
            provider=launch.provider,
            model=launch.model,
            api_key=resolved_key,
            base_url=launch.base_url,
        ),
    )

    # Save config
    saved_path = config.save(DEFAULT_CONFIG_PATH)

    # Print what we resolved
    provider_display = _PROVIDER_DISPLAY.get(launch.provider, launch.provider.title())
    key_status = f"{resolved_key[:8]}..." if resolved_key else "no key (local model)"
    print(f"\n  Sawyer Agent -- self-activating launch")
    print(f"  Provider:  {provider_display}")
    print(f"  Model:     {launch.model}")
    print(f"  Base URL:  {launch.base_url}")
    print(f"  API Key:   {key_status}")
    print(f"  Config:    {saved_path}")
    print(f"\n  Starting server at http://{host}:{port}")
    print()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Launch the server
    run_server(config, host=host, port=port)