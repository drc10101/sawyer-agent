"""Key Storage -- encrypted-at-rest credential management for Sawyer Agent.

All keys, tokens, and SSH configs are stored in ~/.sawyer-harness/keys.yaml
with file permissions 0600. Values are masked when returned through the API.

Permission levels (per key):
  - ask: Agent must ask the user every time before using this key.
  - allowlist: Agent can use this key for pre-approved operations (defined in allow_for).
  - session: Key is available for the current session only; cleared on restart.
  - approve: Agent requests once-per-session approval; after that it's auto-allowed.

Categories:
  - ssh: SSH keys, host configs, known hosts
  - api: API keys (OpenAI, Anthropic, RunPod, etc.)
  - tokens: OAuth tokens, session tokens, refresh tokens
  - custom: user-defined categories

The file format:
  ssh:
    - name: runpod-production
      host: ssh.runpod.io
      user: root
      key_path: ~/.ssh/id_ed25519
      permission: allowlist
      allow_for: [ssh-connect, file-transfer]
      note: Main RunPod instance

  api:
    - name: openai
      provider: openai
      key: sk-...
      permission: session
      note: Main OpenAI key

  tokens:
    - name: telegram-bot
      service: telegram
      token: "123456:ABC..."
      permission: ask
      note: Jade bot token
"""

import os
import stat
from pathlib import Path

import yaml

from .paths import UserData


KEYS_DIR = UserData.home
KEYS_FILE = UserData.keys_file

CATEGORIES = ["ssh", "api", "vision", "tokens", "custom"]
PERMISSIONS = ["ask", "allowlist", "session", "approve"]
PERMISSION_LABELS = {
    "ask": "Ask Every Time",
    "allowlist": "Permanent Allowlist",
    "session": "This Session Only",
    "approve": "Approve Once Per Session",
}

# Preset providers for auto-fill in the UI
# Each preset defines the category, fields, and defaults.
KEY_PRESETS = {
    "ssh": {
        "label": "SSH Connection",
        "fields": ["host", "user", "key_path", "port"],
        "defaults": {"port": "22"},
    },
    "api": {
        "label": "API Key",
        "fields": ["provider", "key", "base_url"],
        "defaults": {},
        "providers": {
            "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "fields": ["key"]},
            "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com", "fields": ["key"]},
            "google": {"label": "Google AI", "base_url": "https://generativelanguage.googleapis.com", "fields": ["key"]},
            "mistral": {"label": "Mistral", "base_url": "https://api.mistral.ai/v1", "fields": ["key"]},
            "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "fields": ["key"]},
            "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "fields": ["key"]},
            "runpod": {"label": "RunPod", "base_url": "https://api.runpod.ai", "fields": ["key"]},
            "github": {"label": "GitHub", "base_url": "https://api.github.com", "fields": ["key"]},
            "huggingface": {"label": "Hugging Face", "base_url": "https://huggingface.co/api", "fields": ["key"]},
            "cohere": {"label": "Cohere", "base_url": "https://api.cohere.ai", "fields": ["key"]},
            "together": {"label": "Together AI", "base_url": "https://api.together.xyz/v1", "fields": ["key"]},
            "fireworks": {"label": "Fireworks AI", "base_url": "https://api.fireworks.ai/inference/v1", "fields": ["key"]},
            "perplexity": {"label": "Perplexity", "base_url": "https://api.perplexity.ai", "fields": ["key"]},
            "xai": {"label": "xAI (Grok)", "base_url": "https://api.x.ai/v1", "fields": ["key"]},
            "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "fields": ["key"]},
            "replicate": {"label": "Replicate", "base_url": "https://api.replicate.com/v1", "fields": ["key"]},
            "custom": {"label": "Custom Provider", "base_url": "", "fields": ["key", "base_url"]},
        },
    },
    "tokens": {
        "label": "Token / OAuth",
        "fields": ["service", "token"],
        "defaults": {},
        "services": {
            "telegram": {"label": "Telegram Bot", "fields": ["token"]},
            "discord": {"label": "Discord Bot", "fields": ["token"]},
            "slack": {"label": "Slack Bot", "fields": ["token"]},
            "stripe": {"label": "Stripe", "fields": ["token"]},
            "twilio": {"label": "Twilio", "fields": ["token"]},
            "sendgrid": {"label": "SendGrid", "fields": ["token"]},
            "github-oauth": {"label": "GitHub OAuth", "fields": ["token"]},
            "google-oauth": {"label": "Google OAuth", "fields": ["token"]},
            "custom": {"label": "Custom Service", "fields": ["token"]},
        },
    },
    "custom": {
        "label": "Custom Key",
        "fields": [],
        "defaults": {},
    },
    "vision": {
        "label": "Vision (Google AI Studio)",
        "description": "API key for the vision bridge -- lets the agent see and verify web pages, screenshots, and UI output. Requires a Google Developer account. Get a free key at https://ai.google.dev/",
        "fields": ["key"],
        "defaults": {},
        "providers": {
            "google-ai-studio": {
                "label": "Google AI Studio (Gemini)",
                "env_var": "GEMINI_API_KEY",
                "url": "https://ai.google.dev/",
                "fields": ["key"],
                "note": "Free tier available. Create a project at ai.google.dev, generate an API key, and paste it here. The vision bridge uses Gemini to evaluate screenshots and provide visual feedback.",
            },
        },
    },
}

# Mask all but last 4 chars for display
def mask_value(value: str, visible: int = 4) -> str:
    if not value or len(value) <= visible:
        return "****"
    return "*" * (len(value) - visible) + value[-visible:]


class KeyStorage:
    """Manages encrypted-at-rest credentials for Sawyer Agent."""

    def __init__(self, path: Path | None = None):
        self.path = path or KEYS_FILE
        self._data: dict[str, list[dict]] = {cat: [] for cat in CATEGORIES}
        self._version: int = 0  # Incremented on every write; UI polls to detect changes
        self._ensure_dir()
        self._load()

    def _ensure_dir(self):
        """Ensure ~/.sawyer-harness exists with correct permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Directory should be 700
        os.chmod(str(self.path.parent), stat.S_IRWXU)

    def _load(self):
        """Load keys from YAML file."""
        if self.path.exists():
            with open(self.path, "r") as f:
                data = yaml.safe_load(f) or {}
            for cat in CATEGORIES:
                self._data[cat] = data.get(cat, []) or []
        else:
            self._data = {cat: [] for cat in CATEGORIES}

    def _save(self):
        """Save keys to YAML file with restricted permissions."""
        with open(self.path, "w") as f:
            yaml.dump(self._data, f, default_flow_style=False, sort_keys=False)
        # File must be 600 (owner read/write only)
        os.chmod(str(self.path), stat.S_IRUSR | stat.S_IWUSR)
        self._version += 1

    @property
    def version(self) -> int:
        """Monotonically increasing version counter. UI polls this to detect key changes."""
        return self._version

    def list_entries(self, category: str | None = None, masked: bool = True) -> dict[str, list[dict]]:
        """List all entries, optionally filtered by category.

        Args:
            category: If provided, only return entries for this category.
            masked: If True, mask sensitive values (key, token, secret fields).

        Returns:
            Dict of category -> list of entry dicts.
        """
        categories = [category] if category else CATEGORIES
        result = {}
        for cat in categories:
            entries = []
            for entry in self._data.get(cat, []):
                e = dict(entry)
                if masked:
                    e = self._mask_entry(e)
                entries.append(e)
            result[cat] = entries
        return result

    def get_entry(self, category: str, name: str) -> dict | None:
        """Get a specific entry by category and name. Returns None if not found."""
        for entry in self._data.get(category, []):
            if entry.get("name") == name:
                return dict(entry)
        return None

    def get_value(self, category: str, name: str, field: str = "key") -> str | None:
        """Get a specific field value from an entry. Returns the raw unmasked value."""
        entry = self.get_entry(category, name)
        if entry:
            return entry.get(field)
        return None

    def add_entry(self, category: str, entry: dict) -> dict:
        """Add a new entry to a category.

        Args:
            category: One of ssh, api, tokens, custom.
            entry: Dict with at least a 'name' key. May include:
                - permission: One of 'ask', 'allowlist', 'session', 'approve'.
                  Defaults to 'ask' if not provided.
                - allow_for: List of approved operations (only used with 'allowlist').

        Returns:
            The added entry (masked).

        Raises:
            ValueError: If category is invalid or name already exists.
        """
        if category not in CATEGORIES:
            raise ValueError(f"Invalid category: {category}. Must be one of {CATEGORIES}")

        name = entry.get("name", "").strip()
        if not name:
            raise ValueError("Entry must have a 'name' field")

        # Validate permission
        perm = entry.get("permission", "ask")
        if perm not in PERMISSIONS:
            raise ValueError(f"Invalid permission: {perm}. Must be one of {PERMISSIONS}")
        entry.setdefault("permission", perm)

        # Check for duplicates
        for existing in self._data[category]:
            if existing.get("name") == name:
                raise ValueError(f"Entry '{name}' already exists in {category}")

        self._data[category].append(entry)
        self._save()
        return self._mask_entry(dict(entry))

    def update_entry(self, category: str, name: str, updates: dict) -> dict:
        """Update an existing entry.

        Args:
            category: Category of the entry.
            name: Name of the entry to update.
            updates: Dict of fields to update.

        Returns:
            The updated entry (masked).

        Raises:
            KeyError: If entry not found.
        """
        for i, entry in enumerate(self._data.get(category, [])):
            if entry.get("name") == name:
                self._data[category][i].update(updates)
                # Don't allow name change via update (that's delete + add)
                self._data[category][i]["name"] = name
                self._save()
                return self._mask_entry(dict(self._data[category][i]))

        raise KeyError(f"Entry '{name}' not found in {category}")

    def delete_entry(self, category: str, name: str) -> dict:
        """Delete an entry by name.

        Returns:
            The deleted entry (masked).

        Raises:
            KeyError: If entry not found.
        """
        for i, entry in enumerate(self._data.get(category, [])):
            if entry.get("name") == name:
                removed = self._data[category].pop(i)
                self._save()
                return self._mask_entry(dict(removed))

        raise KeyError(f"Entry '{name}' not found in {category}")

    def _mask_entry(self, entry: dict) -> dict:
        """Mask sensitive fields in an entry for display."""
        sensitive_fields = {"key", "token", "secret", "password", "api_key", "access_token",
                           "refresh_token", "private_key", "passphrase"}
        masked = {}
        for k, v in entry.items():
            if k in sensitive_fields and isinstance(v, str):
                masked[k] = mask_value(v)
            else:
                masked[k] = v
        return masked

    def categories(self) -> list[str]:
        """Return available categories."""
        return CATEGORIES

    def count(self) -> dict[str, int]:
        """Return count of entries per category."""
        return {cat: len(self._data.get(cat, [])) for cat in CATEGORIES}