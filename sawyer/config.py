"""Sawyer configuration management."""

import os
from dataclasses import dataclass


@dataclass
class SawyerConfig:
    """Sawyer node configuration."""

    # Node settings
    node_name: str | None = None
    router_url: str = "https://router.sawyer.dev"
    inference_port: int = 8444

    # Bedrock integration
    bedrock_url: str = "https://localhost:8443"
    bedrock_license_key: str | None = None

    # Inference backend
    inference_backend: str = "auto"  # auto, vllm, llama_cpp

    # Backend URLs (override via env vars: SAWYER_OLLAMA_URL, etc.)
    ollama_url: str = "http://localhost:11434"
    llama_url: str = "http://localhost:8444"
    lm_studio_url: str = "http://localhost:1234"
    vllm_url: str = "http://localhost:8001"

    # Token settings
    api_key: str | None = None
    gemini_api_key: str | None = None

    # Expert hosting
    cache_dir: str = "~/.sawyer/cache"
    expert_cache_dir: str = "~/.sawyer/experts"
    max_experts: int = 2  # Max experts to host concurrently
    max_vram_gb: float | None = None  # Auto-detect if None

    # Storage
    database_url: str = "~/.sawyer/sawyer.db"  # SQLite database path

    # Performance
    request_timeout: float = 30.0  # seconds
    heartbeat_interval: float = 60.0  # seconds
    max_concurrent_requests: int = 10

    # Provider / Payout settings
    # Env vars: SAWYER_STRIPE_SECRET_KEY, etc.
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    provider_share_pct: float = 70.0  # Host share %
    platform_fee_pct: float = 30.0  # Platform share %
    earnings_rate_per_1k: float = 0.002  # USD per 1K tokens
    min_payout_monthly: float = 10.0  # Min monthly payout USD
    min_payout_quarterly: float = 25.0  # Min quarterly payout USD
    default_payout_schedule: str = "monthly"  # monthly or quarterly

    def __post_init__(self) -> None:
        """Load overrides from environment variables."""
        self.api_key = os.environ.get("SAWYER_API_KEY", self.api_key)
        self.gemini_api_key = os.environ.get("SAWYER_GEMINI_API_KEY", self.gemini_api_key)
        self.stripe_secret_key = os.environ.get("SAWYER_STRIPE_SECRET_KEY", self.stripe_secret_key)
        self.stripe_publishable_key = os.environ.get(
            "SAWYER_STRIPE_PUBLISHABLE_KEY", self.stripe_publishable_key
        )
        self.stripe_webhook_secret = os.environ.get(
            "SAWYER_STRIPE_WEBHOOK_SECRET", self.stripe_webhook_secret
        )
        if env_val := os.environ.get("SAWYER_PROVIDER_SHARE_PCT"):
            self.provider_share_pct = float(env_val)
        if env_val := os.environ.get("SAWYER_PLATFORM_FEE_PCT"):
            self.platform_fee_pct = float(env_val)
        if env_val := os.environ.get("SAWYER_EARNINGS_RATE"):
            self.earnings_rate_per_1k = float(env_val)
        if env_val := os.environ.get("SAWYER_MIN_PAYOUT_MONTHLY"):
            self.min_payout_monthly = float(env_val)
        if env_val := os.environ.get("SAWYER_MIN_PAYOUT_QUARTERLY"):
            self.min_payout_quarterly = float(env_val)
        if env_val := os.environ.get("SAWYER_DEFAULT_PAYOUT_SCHEDULE"):
            self.default_payout_schedule = env_val

    def validate(self) -> None:
        """Validate configuration values."""
        if self.inference_port < 1 or self.inference_port > 65535:
            raise ValueError(f"Invalid port: {self.inference_port}")
        if self.max_experts < 1:
            raise ValueError(f"max_experts must be >= 1, got {self.max_experts}")
        if self.request_timeout <= 0:
            raise ValueError(f"request_timeout must be > 0, got {self.request_timeout}")
        if self.provider_share_pct + self.platform_fee_pct != 100.0:
            raise ValueError(
                f"provider_share_pct ({self.provider_share_pct}) + "
                f"platform_fee_pct ({self.platform_fee_pct}) must equal 100"
            )
        if self.default_payout_schedule not in ("monthly", "quarterly"):
            raise ValueError(
                f"default_payout_schedule must be monthly or quarterly, "
                f"got {self.default_payout_schedule}"
            )
