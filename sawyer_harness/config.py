"""
Configuration loader.

Loads from YAML config file with env var interpolation.
All paths are relative to the config file location or absolute.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class LLMConfig:
    provider: str = "openai"  # sawyer, openai, anthropic, ollama, local
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    context_length: int | None = None  # Override model context window; None = lookup


@dataclass
class SecurityConfig:
    sandbox: bool = True
    allowed_tools: list[str] = field(default_factory=list)  # empty = all
    denied_paths: list[str] = field(default_factory=lambda: [
        "/etc/passwd", "/etc/shadow", "/root/.ssh",
    ])
    max_command_timeout: int = 300  # seconds
    audit_log: str = ""  # path to audit log, empty = stderr


@dataclass
class MemoryConfig:
    backend: str = "sqlite"
    path: str = "~/.sawyer-harness/memory.db"


@dataclass
class ChannelConfig:
    name: str = ""
    enabled: bool = False
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class HarnessConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    channels: list[ChannelConfig] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> HarnessConfig:
        """Load config from YAML file with env var interpolation."""
        path = Path(path)
        if not path.exists():
            return cls()

        raw = path.read_text()
        # Interpolate environment variables: ${VAR_NAME}
        interpolated = re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), ""),
            raw,
        )
        data = yaml.safe_load(interpolated) or {}

        llm_data = data.get("llm", {})
        sec_data = data.get("security", {})
        mem_data = data.get("memory", {})
        chan_data = data.get("channels", [])

        channels = []
        for c in chan_data:
            channels.append(ChannelConfig(
                name=c.get("name", ""),
                enabled=c.get("enabled", False),
                config=c.get("config", c),  # pass through all channel-specific keys
            ))

        return cls(
            llm=LLMConfig(
                provider=llm_data.get("provider", "openai"),
                model=llm_data.get("model", "gpt-4o"),
                api_key=llm_data.get("api_key", ""),
                base_url=llm_data.get("base_url", ""),
                max_tokens=llm_data.get("max_tokens", 4096),
                temperature=llm_data.get("temperature", 0.7),
                context_length=llm_data.get("context_length", None),
            ),
            security=SecurityConfig(
                sandbox=sec_data.get("sandbox", True),
                allowed_tools=sec_data.get("allowed_tools", []),
                denied_paths=sec_data.get("denied_paths", []),
                max_command_timeout=sec_data.get("max_command_timeout", 300),
                audit_log=sec_data.get("audit_log", ""),
            ),
            memory=MemoryConfig(
                backend=mem_data.get("backend", "sqlite"),
                path=mem_data.get("path", "~/.sawyer-harness/memory.db"),
            ),
            channels=channels,
        )

    def needs_setup(self) -> bool:
        """Check if the config is missing required values (first-run)."""
        return not self.llm.api_key

    def save(self, path: str | Path) -> None:
        """Save config to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "llm": {
                "provider": self.llm.provider,
                "model": self.llm.model,
                "api_key": self.llm.api_key,
                "base_url": self.llm.base_url,
                "max_tokens": self.llm.max_tokens,
                "temperature": self.llm.temperature,
            },
            "security": {
                "sandbox": self.security.sandbox,
                "max_command_timeout": self.security.max_command_timeout,
            },
            "memory": {
                "backend": self.memory.backend,
                "path": self.memory.path,
            },
        }
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")


def setup_wizard(config_path: str = "config.yaml") -> HarnessConfig:
    """Interactive first-run setup. Prompts for provider, model, and API key, then saves config."""
    print("\n=== Sawyer Agent Setup ===\n")
    print("First run detected -- let's configure your AI provider.\n")

    providers = {
        "1": ("ollama", "Ollama (cloud or local)", "glm-5.1:cloud", "https://ollama.com/v1"),
        "2": ("openai", "OpenAI (GPT-4o, GPT-4.1, etc.)", "gpt-4o", "https://api.openai.com/v1"),
        "3": ("anthropic", "Anthropic (Claude)", "claude-sonnet-4-20250514", "https://api.anthropic.com"),
        "4": ("custom", "Custom OpenAI-compatible endpoint", "", ""),
    }

    for key, (pid, label, _, _) in providers.items():
        print(f"  {key}. {label}")

    choice = input("\nProvider [1]: ").strip() or "1"
    provider_id, _, default_model, default_url = providers.get(choice, providers["1"])

    model = input(f"Model [{default_model}]: ").strip() or default_model
    base_url = input(f"Base URL [{default_url}]: ").strip() or default_url

    print()
    api_key = input("API Key: ").strip()
    if not api_key:
        print("\nNo API key provided. Sawyer will start but won't be able to chat until you add one.")
        print(f"Edit {config_path} later to add your key.\n")

    config = HarnessConfig(
        llm=LLMConfig(
            provider=provider_id,
            model=model,
            api_key=api_key,
            base_url=base_url,
        ),
    )
    config.save(config_path)
    print(f"\nConfig saved to {config_path}")

    # Offer to create a desktop shortcut
    try:
        desktop_choice = input("\nCreate a desktop shortcut? [Y/n]: ").strip().lower()
        if desktop_choice in ("", "y", "yes"):
            from .setup_desktop import create_shortcut
            create_shortcut()
    except (EOFError, KeyboardInterrupt):
        pass

    print()
    return config