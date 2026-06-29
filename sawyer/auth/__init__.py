"""Sawyer Auth package — API key validation and rate limiting."""

from sawyer.auth.api import (
    APIKey,
    AuthError,
    InvalidAPIKey,
    KeyStatus,
    RateLimitExceeded,
    SawyerAuth,
)

__all__ = [
    "SawyerAuth",
    "APIKey",
    "KeyStatus",
    "AuthError",
    "InvalidAPIKey",
    "RateLimitExceeded",
]
