"""Sawyer Authentication — API key validation and rate limiting.

Provides:
- API key creation, validation, and revocation
- Per-key rate limiting with token bucket algorithm
- Per-key usage tracking persisted to SQLite
- Router middleware that validates keys before accepting requests
"""

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum

from sawyer.storage.database import SawyerStorage

logger = logging.getLogger(__name__)

# Rate limit defaults
DEFAULT_RATE_LIMIT_RPM = 60  # requests per minute
DEFAULT_RATE_LIMIT_BURST = 10  # burst allowance


class KeyStatus(Enum):
    """API key status."""

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AuthError(Exception):
    """Authentication error."""

    pass


class RateLimitExceeded(AuthError):
    """Rate limit exceeded."""

    pass


class InvalidAPIKey(AuthError):
    """Invalid or revoked API key."""

    pass


@dataclass
class APIKey:
    """An API key with metadata."""

    key_id: str  # Short identifier (e.g., "sak_abc123")
    key_hash: str  # SHA-256 hash of the full key
    user_id: str
    tier: str  # explorer, builder, operator
    status: KeyStatus = KeyStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    last_used_at: float = 0.0
    request_count: int = 0
    rate_limit_rpm: int = DEFAULT_RATE_LIMIT_RPM
    burst_allowance: int = DEFAULT_RATE_LIMIT_BURST
    metadata: dict = field(default_factory=dict)


@dataclass
class RateLimitState:
    """Token bucket rate limit state for a single key."""

    key_id: str
    tokens: float  # Current tokens in bucket
    last_refill: float  # Last refill timestamp
    max_tokens: float  # Bucket capacity (rate_limit_rpm + burst)
    refill_rate: float  # Tokens per second (rate_limit_rpm / 60)


class SawyerAuth:
    """API key authentication and rate limiting.

    Keys are stored as SHA-256 hashes. The full key is only shown once
    at creation time — we never store plaintext keys.

    Rate limiting uses a token bucket algorithm:
    - Each key gets rate_limit_rpm tokens per minute
    - Burst allowance adds extra capacity for spikes
    - Tokens refill at a steady rate (rate_limit_rpm / 60 per second)
    """

    KEY_PREFIX = "sak_"  # Sawyer API Key prefix

    def __init__(self, storage: SawyerStorage) -> None:
        self._storage = storage
        self._rate_limits: dict[str, RateLimitState] = {}
        self._init_schema()

    def _init_schema(self) -> None:
        """Ensure API key tables exist."""
        conn = self._storage._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                tier TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                expires_at REAL,
                last_used_at REAL DEFAULT 0,
                request_count INTEGER DEFAULT 0,
                rate_limit_rpm INTEGER DEFAULT 60,
                burst_allowance INTEGER DEFAULT 10,
                metadata_json TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
            CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
            """)
        conn.commit()

    def create_key(
        self,
        user_id: str,
        tier: str = "explorer",
        rate_limit_rpm: int = DEFAULT_RATE_LIMIT_RPM,
        burst_allowance: int = DEFAULT_RATE_LIMIT_BURST,
        expires_at: float | None = None,
    ) -> tuple[str, APIKey]:
        """Create a new API key.

        Returns:
            Tuple of (full_key, APIKey). The full_key is shown ONCE —
            the caller must save it. Only the hash is stored.

        Raises:
            AuthError: If key creation fails.
        """
        # Generate a cryptographically random key
        raw_key = secrets.token_urlsafe(32)
        full_key = f"{self.KEY_PREFIX}{raw_key}"
        key_hash = hashlib.sha256(full_key.encode()).hexdigest()
        key_id = f"{self.KEY_PREFIX}{raw_key[:8]}"

        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            user_id=user_id,
            tier=tier,
            status=KeyStatus.ACTIVE,
            created_at=time.time(),
            expires_at=expires_at,
            rate_limit_rpm=rate_limit_rpm,
            burst_allowance=burst_allowance,
        )

        # Persist to database
        import json

        conn = self._storage._get_conn()
        conn.execute(
            """
            INSERT INTO api_keys (
                key_id, key_hash, user_id, tier, status,
                created_at, expires_at, last_used_at, request_count,
                rate_limit_rpm, burst_allowance, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                api_key.key_id,
                api_key.key_hash,
                api_key.user_id,
                api_key.tier,
                api_key.status.value,
                api_key.created_at,
                api_key.expires_at,
                api_key.last_used_at,
                api_key.request_count,
                api_key.rate_limit_rpm,
                api_key.burst_allowance,
                json.dumps(api_key.metadata),
            ),
        )
        conn.commit()

        # Initialize rate limit state
        max_tokens = rate_limit_rpm + burst_allowance
        self._rate_limits[key_id] = RateLimitState(
            key_id=key_id,
            tokens=max_tokens,
            last_refill=time.time(),
            max_tokens=max_tokens,
            refill_rate=rate_limit_rpm / 60.0,
        )

        logger.info(
            "Created API key %s for user %s (tier=%s)",
            key_id,
            user_id,
            tier,
        )
        return full_key, api_key

    def validate_key(self, full_key: str) -> APIKey:
        """Validate an API key and check rate limits.

        Args:
            full_key: The full API key string (sak_...)

        Returns:
            The validated APIKey object

        Raises:
            InvalidAPIKey: If the key is invalid, revoked, or expired
            RateLimitExceeded: If the key has exceeded its rate limit
        """
        key_hash = hashlib.sha256(full_key.encode()).hexdigest()

        conn = self._storage._get_conn()
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()

        if row is None:
            raise InvalidAPIKey("API key not found")

        api_key = APIKey(
            key_id=row["key_id"],
            key_hash=row["key_hash"],
            user_id=row["user_id"],
            tier=row["tier"],
            status=KeyStatus(row["status"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"] if row["expires_at"] else None,
            last_used_at=row["last_used_at"],
            request_count=row["request_count"],
            rate_limit_rpm=row["rate_limit_rpm"],
            burst_allowance=row["burst_allowance"],
        )

        # Check status
        if api_key.status == KeyStatus.REVOKED:
            raise InvalidAPIKey("API key has been revoked")
        if api_key.status == KeyStatus.EXPIRED:
            raise InvalidAPIKey("API key has expired")

        # Check expiration
        if api_key.expires_at and time.time() > api_key.expires_at:
            self._update_status(api_key.key_id, KeyStatus.EXPIRED)
            raise InvalidAPIKey("API key has expired")

        # Check rate limit
        if not self._check_rate_limit(api_key):
            raise RateLimitExceeded(
                f"Rate limit exceeded for key {api_key.key_id} "
                f"(limit: {api_key.rate_limit_rpm} rpm)"
            )

        # Update usage stats
        self._update_usage(api_key.key_id)

        return api_key

    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key.

        Returns:
            True if the key was found and revoked, False if not found.
        """
        return self._update_status(key_id, KeyStatus.REVOKED)

    def list_keys(self, user_id: str | None = None) -> list[APIKey]:
        """List API keys, optionally filtered by user."""
        conn = self._storage._get_conn()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at").fetchall()

        keys = []
        for row in rows:
            import json

            keys.append(
                APIKey(
                    key_id=row["key_id"],
                    key_hash=row["key_hash"],
                    user_id=row["user_id"],
                    tier=row["tier"],
                    status=KeyStatus(row["status"]),
                    created_at=row["created_at"],
                    expires_at=row["expires_at"] if row["expires_at"] else None,
                    last_used_at=row["last_used_at"],
                    request_count=row["request_count"],
                    rate_limit_rpm=row["rate_limit_rpm"],
                    burst_allowance=row["burst_allowance"],
                    metadata=json.loads(row["metadata_json"]),
                )
            )
        return keys

    def _check_rate_limit(self, api_key: APIKey) -> bool:
        """Token bucket rate limit check.

        Returns True if the request is allowed, False if rate limited.
        """
        key_id = api_key.key_id
        now = time.time()

        # Initialize rate limit state if needed
        if key_id not in self._rate_limits:
            max_tokens = api_key.rate_limit_rpm + api_key.burst_allowance
            self._rate_limits[key_id] = RateLimitState(
                key_id=key_id,
                tokens=max_tokens,
                last_refill=now,
                max_tokens=max_tokens,
                refill_rate=api_key.rate_limit_rpm / 60.0,
            )

        state = self._rate_limits[key_id]

        # Refill tokens based on elapsed time
        elapsed = now - state.last_refill
        state.tokens = min(
            state.max_tokens,
            state.tokens + elapsed * state.refill_rate,
        )
        state.last_refill = now

        # Check if we have a token available
        if state.tokens >= 1.0:
            state.tokens -= 1.0
            return True
        return False

    def _update_usage(self, key_id: str) -> None:
        """Update last_used_at and increment request_count."""
        conn = self._storage._get_conn()
        now = time.time()
        conn.execute(
            """
            UPDATE api_keys
            SET last_used_at = ?, request_count = request_count + 1
            WHERE key_id = ?
            """,
            (now, key_id),
        )
        conn.commit()

    def _update_status(self, key_id: str, status: KeyStatus) -> bool:
        """Update a key's status."""
        conn = self._storage._get_conn()
        cursor = conn.execute(
            "UPDATE api_keys SET status = ? WHERE key_id = ?",
            (status.value, key_id),
        )
        conn.commit()
        return cursor.rowcount > 0
