"""
Configuration loader.

Loads from YAML config file with env var interpolation.
Default config path: ~/.sawyer-harness/config.yaml
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path.home() / ".sawyer-harness" / "config.yaml"


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "glm-5.1:cloud"
    api_key: str = ""
    base_url: str = "https://ollama.com/v1"
    max_tokens: int = 4096
    temperature: float = 0.7
    context_length: int | None = None


@dataclass
class SecurityConfig:
    sandbox: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=lambda: [
        "/etc/passwd", "/etc/shadow", "/root/.ssh",
    ])
    max_command_timeout: int = 300
    audit_log: str = ""


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
    def from_file(cls, path: str | Path | None = None) -> HarnessConfig:
        """Load config from YAML file with env var interpolation."""
        if path is None:
            path = DEFAULT_CONFIG_PATH
        path = Path(path).expanduser().resolve()
        if not path.exists():
            return cls()

        raw = path.read_text(encoding="utf-8")
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

        channels = [
            ChannelConfig(
                name=c.get("name", ""),
                enabled=c.get("enabled", False),
                config=c.get("config", c),
            )
            for c in chan_data
        ]

        return cls(
            llm=LLMConfig(
                provider=llm_data.get("provider", "ollama"),
                model=llm_data.get("model", "glm-5.1:cloud"),
                api_key=llm_data.get("api_key", ""),
                base_url=llm_data.get("base_url", "https://ollama.com/v1"),
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

    def save(self, path: str | Path | None = None) -> Path:
        """Save config to a YAML file. Returns the resolved path."""
        if path is None:
            path = DEFAULT_CONFIG_PATH
        path = Path(path).expanduser().resolve()
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
        return path


def setup_wizard(config_path: str | Path | None = None) -> HarnessConfig:
    """Interactive setup wizard. Works for first run and reconfiguration.

    On first run: prompts for provider, model, and API key.
    On reconfiguration: shows current values as defaults (API key masked).
    Press Enter to keep any value.
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    config_path = Path(config_path).expanduser().resolve()

    # Load existing config for defaults when reconfiguring
    existing = None
    if config_path.exists():
        existing = HarnessConfig.from_file(config_path)

    print("\n=== Sawyer Agent Setup ===\n")

    if existing and existing.llm.api_key:
        print("Reconfiguring existing setup. Press Enter to keep current values.\n")
    else:
        print("First run detected -- let's configure your AI provider.\n")

    providers = {
        "1": ("ollama", "Ollama (cloud or local)", "glm-5.1:cloud", "https://ollama.com/v1"),
        "2": ("openai", "OpenAI (GPT-4o, GPT-4.1, etc.)", "gpt-4o", "https://api.openai.com/v1"),
        "3": ("anthropic", "Anthropic (Claude)", "claude-sonnet-4-20250514", "https://api.anthropic.com"),
        "4": ("custom", "Custom OpenAI-compatible endpoint", "", ""),
    }

    # Map provider name back to menu number for pre-selection
    provider_to_num = {v[0]: k for k, v in providers.items()}

    for key, (pid, label, _, _) in providers.items():
        marker = " (current)" if existing and existing.llm.provider == pid else ""
        print(f"  {key}. {label}{marker}")

    default_choice = provider_to_num.get(existing.llm.provider, "1") if existing else "1"
    choice = input(f"\nProvider [{default_choice}]: ").strip() or default_choice
    provider_id, _, default_model, default_url = providers.get(choice, providers[default_choice])

    # Use existing values as defaults when reconfiguring
    cur_model = existing.llm.model if existing else default_model
    cur_url = existing.llm.base_url if existing and existing.llm.base_url else default_url
    cur_key = existing.llm.api_key if existing else ""

    model = input(f"Model [{cur_model}]: ").strip() or cur_model
    base_url = input(f"Base URL [{cur_url}]: ").strip() or cur_url

    print()
    key_prompt = f"API Key [{cur_key[:4]}...]: " if cur_key else "API Key: "
    api_key = input(key_prompt).strip()
    if not api_key:
        api_key = cur_key
    if not api_key:
        print(f"\nNo API key provided. Sawyer will start but won't chat until you add one.")
        print(f"Run `python -m sawyer_harness setup` later to reconfigure.\n")

    config = HarnessConfig(
        llm=LLMConfig(
            provider=provider_id,
            model=model,
            api_key=api_key,
            base_url=base_url,
        ),
    )
    saved_path = config.save(config_path)
    print(f"\nConfig saved to {saved_path}")

    # Offer to create a desktop shortcut
    try:
        desktop_choice = input("\nCreate a desktop shortcut? [Y/n]: ").strip().lower()
        if desktop_choice in ("", "y", "yes"):
            _create_desktop_shortcut()
    except (EOFError, KeyboardInterrupt):
        pass

    print()
    return config


def _create_desktop_shortcut() -> None:
    """Create a desktop shortcut with the Sawyer icon."""
    import subprocess
    import sys

    # Find the Sawyer icon that ships with the package
    icon_path = Path(__file__).parent / "web" / "static" / "sawyer.ico"

    # Launcher batch file
    app_dir = Path.home() / ".sawyer-harness"
    app_dir.mkdir(parents=True, exist_ok=True)
    launcher = app_dir / "launch.bat"

    launcher.write_text(
        '@echo off\n'
        'title Sawyer Agent\n'
        'echo.\n'
        'echo   Sawyer Agent -- http://127.0.0.1:8765\n'
        'echo   Press Ctrl+C to stop.\n'
        'echo.\n'
        'start http://127.0.0.1:8765\n'
        f'"{sys.executable}" -m sawyer_harness --host 127.0.0.1 --port 8765\n'
        'pause\n',
        encoding="utf-8",
    )

    # Create .lnk shortcut with icon via PowerShell
    shortcut_path = Path.home() / "Desktop" / "Sawyer Agent.lnk"
    icon_arg = str(icon_path) if icon_path.exists() else ""
    ps_cmd = (
        f"$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{shortcut_path}');"
        f"$s.TargetPath='{launcher}';"
        f"$s.WorkingDirectory='{app_dir}';"
        f"$s.Description='Sawyer Agent';"
    )
    if icon_arg:
        ps_cmd += f"$s.IconLocation='{icon_arg}';"
    ps_cmd += "$s.Save()"

    result = subprocess.run(
        ["powershell", "-Command", ps_cmd],
        capture_output=True, timeout=10,
    )
    if result.returncode == 0:
        print("  Desktop shortcut created with Sawyer icon.")
    else:
        # Fallback: just copy the bat to desktop
        desktop_bat = Path.home() / "Desktop" / "Sawyer Agent.bat"
        desktop_bat.write_text(launcher.read_text(), encoding="utf-8")
        print("  Desktop shortcut created.")