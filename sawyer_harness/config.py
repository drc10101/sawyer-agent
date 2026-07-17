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

from .paths import UserData

DEFAULT_CONFIG_PATH = UserData.config_file


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "glm-5.1:cloud"
    api_key: str = ""
    base_url: str = "https://ollama.com/v1"
    max_tokens: int = 4096
    temperature: float = 0.7
    context_length: int | None = None


# Verbosity levels: concise (short answers, no tool output streaming),
# normal (balanced), thorough (full detail, stream everything)
VERBOSITY_LEVELS = ("concise", "normal", "thorough")

# Agreeability: controls whether the agent prioritizes pleasing the user
# or giving honest/truthful feedback
AGREEABILITY_LEVELS = ("agreeable", "balanced", "honest")

# Reasoning depth: controls how deeply the model reasons before responding
REASONING_LEVELS = ("low", "medium", "medium_high", "high")

@dataclass
class AgentConfig:
    """Agent behavior settings — separate from LLM provider config."""
    max_tool_rounds: int = 20      # Safety ceiling for tool-call loops
    verbosity: str = "normal"      # concise | normal | thorough
    stream_tool_output: bool = True  # Show tool results in chat as they arrive
    mode: str = "direct"           # direct | orchestrator | auto
    agreeability: str = "balanced" # agreeable | balanced | honest
    reasoning: str = "medium"      # low | medium | medium_high | high


# Permission modes — replaces the old sandbox boolean with a capability hierarchy
PERMISSION_MODES = ("readonly", "readwrite", "all")

@dataclass
class SecurityConfig:
    sandbox: bool = True  # Kept for backward compat; permission_mode takes precedence
    permission_mode: str = "all"  # readonly | readwrite | all
    allowed_tools: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=lambda: [
        "/etc/passwd", "/etc/shadow", "/root/.ssh",
    ])
    max_command_timeout: int = 300
    audit_log: str = ""


@dataclass
class MemoryConfig:
    backend: str = "sqlite"
    path: str = ""  # defaults to UserData.memory_db at runtime


@dataclass
class ChannelConfig:
    name: str = ""
    enabled: bool = False
    config: dict[str, Any] = field(default_factory=dict)

@dataclass
class NotificationConfig:
    """Email notification settings for suggestions and alerts.
    If smtp_host is empty, email sending is silently skipped."""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""  # App password, not account password
    from_address: str = ""
    to_address: str = ""     # Where suggestions get sent
    use_tls: bool = True

# Compaction policy levels — maps to CompactionPolicy presets
COMPACTION_LEVELS = ("conservative", "balanced", "aggressive")

@dataclass
class CompactionConfig:
    """Controls when and how context is compressed.

    policy: preset name — conservative (70%), balanced (80%), aggressive (90%)
    threshold: override the compression trigger fraction (0.0-1.0)
    recency_pct: override the fraction of space for recent messages
    """
    policy: str = "balanced"
    threshold: float | None = None
    recency_pct: float | None = None

@dataclass
class HarnessConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    channels: list[ChannelConfig] = field(default_factory=list)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)

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
        agent_data = data.get("agent", {})
        chan_data = data.get("channels", [])
        notif_data = data.get("notifications", {})

        channels = [
            ChannelConfig(
                name=c.get("name", ""),
                enabled=c.get("enabled", False),
                config=c.get("config", c),
            )
            for c in chan_data
        ]

        # Validate verbosity
        verbosity = agent_data.get("verbosity", "normal")
        if verbosity not in VERBOSITY_LEVELS:
            verbosity = "normal"

        # Validate agreeability
        agreeability = agent_data.get("agreeability", "balanced")
        if agreeability not in AGREEABILITY_LEVELS:
            agreeability = "balanced"

        # Validate reasoning
        reasoning = agent_data.get("reasoning", "medium")
        if reasoning not in REASONING_LEVELS:
            reasoning = "medium"

        # Compaction config
        comp_data = data.get("compaction", {})
        compaction_policy = comp_data.get("policy", "balanced")
        if compaction_policy not in COMPACTION_LEVELS:
            compaction_policy = "balanced"

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
                permission_mode=sec_data.get("permission_mode", "all"),
                allowed_tools=sec_data.get("allowed_tools", []),
                denied_paths=sec_data.get("denied_paths", []),
                max_command_timeout=sec_data.get("max_command_timeout", 300),
                audit_log=sec_data.get("audit_log", ""),
            ),
            memory=MemoryConfig(
                backend=mem_data.get("backend", "sqlite"),
                path=mem_data.get("path", str(UserData.memory_db)),
            ),
            agent=AgentConfig(
                max_tool_rounds=agent_data.get("max_tool_rounds", 20),
                verbosity=verbosity,
                stream_tool_output=agent_data.get("stream_tool_output", True),
                agreeability=agreeability,
                reasoning=reasoning,
            ),
            compaction=CompactionConfig(
                policy=compaction_policy,
                threshold=comp_data.get("threshold"),
                recency_pct=comp_data.get("recency_pct"),
            ),
            channels=channels,
            notifications=NotificationConfig(
                smtp_host=notif_data.get("smtp_host", ""),
                smtp_port=notif_data.get("smtp_port", 587),
                smtp_user=notif_data.get("smtp_user", ""),
                smtp_password=notif_data.get("smtp_password", ""),
                from_address=notif_data.get("from_address", ""),
                to_address=notif_data.get("to_address", ""),
                use_tls=notif_data.get("use_tls", True),
            ),
        )

    def needs_setup(self) -> bool:
        """Check if the config is missing required values (first-run).

        Ollama and local providers don't require an API key, so an empty
        key is fine for them.  Only OpenAI and Anthropic need one.
        """
        if self.llm.provider in ("ollama", "local"):
            return not self.llm.base_url
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
                "permission_mode": self.security.permission_mode,
                "max_command_timeout": self.security.max_command_timeout,
            },
            "memory": {
                "backend": self.memory.backend,
                "path": self.memory.path,
            },
            "agent": {
                "max_tool_rounds": self.agent.max_tool_rounds,
                "verbosity": self.agent.verbosity,
                "stream_tool_output": self.agent.stream_tool_output,
                "agreeability": self.agent.agreeability,
                "reasoning": self.agent.reasoning,
            },
            "compaction": {
                "policy": self.compaction.policy,
            },
            "notifications": {
                "smtp_host": self.notifications.smtp_host,
                "smtp_port": self.notifications.smtp_port,
                "smtp_user": self.notifications.smtp_user,
                "smtp_password": self.notifications.smtp_password,
                "from_address": self.notifications.from_address,
                "to_address": self.notifications.to_address,
                "use_tls": self.notifications.use_tls,
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
    """Create a desktop shortcut with the Sawyer icon.

    The launcher simply starts the server.  The server itself opens
    the browser once it's ready (see run_server in web/server.py).
    """
    import subprocess
    import sys

    # Find the Sawyer icon that ships with the package
    icon_path = Path(__file__).parent / "web" / "static" / "sawyer.ico"

    # Launcher batch file -- starts server, waits for ready, then opens browser
    app_dir = UserData.home
    app_dir.mkdir(parents=True, exist_ok=True)
    launcher = UserData.launch_script

    launcher.write_text(
        '@echo off\n'
        'title Sawyer Agent\n'
        'echo.\n'
        'echo   Starting Sawyer Agent...\n'
        'echo   Browser will open automatically when ready.\n'
        'echo   Press Ctrl+C to stop.\n'
        'echo.\n'
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