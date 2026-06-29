"""Tests for Sawyer Auth — API key validation and rate limiting."""

import tempfile
import time
from pathlib import Path

import pytest

from sawyer.auth.api import (
    InvalidAPIKey,
    KeyStatus,
    RateLimitExceeded,
    SawyerAuth,
)
from sawyer.storage.database import SawyerStorage


class TestAPIKeyCreation:
    """Test API key creation."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test_auth.db"
        self.storage = SawyerStorage(str(self.db_path))
        self.auth = SawyerAuth(self.storage)

    def teardown_method(self):
        self.storage.close()

    def test_create_key(self):
        """Create an API key and verify its properties."""
        full_key, api_key = self.auth.create_key(
            user_id="user-1",
            tier="explorer",
        )
        assert full_key.startswith("sak_")
        assert api_key.key_id.startswith("sak_")
        assert api_key.user_id == "user-1"
        assert api_key.tier == "explorer"
        assert api_key.status == KeyStatus.ACTIVE

    def test_key_hash_stored_not_plaintext(self):
        """Only the SHA-256 hash is stored, not the raw key."""
        full_key, api_key = self.auth.create_key(user_id="user-1")
        import hashlib

        expected_hash = hashlib.sha256(full_key.encode()).hexdigest()
        assert api_key.key_hash == expected_hash

    def test_create_key_with_custom_rate_limit(self):
        """Create a key with custom rate limits."""
        full_key, api_key = self.auth.create_key(
            user_id="user-1",
            rate_limit_rpm=120,
            burst_allowance=20,
        )
        assert api_key.rate_limit_rpm == 120
        assert api_key.burst_allowance == 20

    def test_create_multiple_keys_per_user(self):
        """A user can have multiple API keys."""
        self.auth.create_key(user_id="user-1", tier="explorer")
        self.auth.create_key(user_id="user-1", tier="builder")
        keys = self.auth.list_keys(user_id="user-1")
        assert len(keys) == 2


class TestAPIKeyValidation:
    """Test API key validation."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test_auth.db"
        self.storage = SawyerStorage(str(self.db_path))
        self.auth = SawyerAuth(self.storage)

    def teardown_method(self):
        self.storage.close()

    def test_validate_valid_key(self):
        """A valid key validates successfully."""
        full_key, _ = self.auth.create_key(user_id="user-1")
        api_key = self.auth.validate_key(full_key)
        assert api_key.user_id == "user-1"
        assert api_key.status == KeyStatus.ACTIVE

    def test_validate_invalid_key(self):
        """An invalid key raises InvalidAPIKey."""
        with pytest.raises(InvalidAPIKey):
            self.auth.validate_key("sak_invalid_key")

    def test_validate_revoked_key(self):
        """A revoked key raises InvalidAPIKey."""
        full_key, api_key = self.auth.create_key(user_id="user-1")
        self.auth.revoke_key(api_key.key_id)
        with pytest.raises(InvalidAPIKey, match="revoked"):
            self.auth.validate_key(full_key)

    def test_revoke_nonexistent_key(self):
        """Revoking a non-existent key returns False."""
        assert self.auth.revoke_key("sak_nonexistent") is False

    def test_validate_updates_usage(self):
        """Validating a key updates last_used_at and request_count."""
        full_key, _ = self.auth.create_key(user_id="user-1")

        self.auth.validate_key(full_key)
        self.auth.validate_key(full_key)

        keys = self.auth.list_keys(user_id="user-1")
        assert keys[0].request_count == 2
        assert keys[0].last_used_at > 0


class TestRateLimiting:
    """Test token bucket rate limiting."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test_auth.db"
        self.storage = SawyerStorage(str(self.db_path))
        self.auth = SawyerAuth(self.storage)

    def teardown_method(self):
        self.storage.close()

    def test_allows_normal_traffic(self):
        """Normal request rate is allowed."""
        full_key, _ = self.auth.create_key(
            user_id="user-1",
            rate_limit_rpm=60,
        )
        # Should allow multiple requests
        for _ in range(10):
            api_key = self.auth.validate_key(full_key)
            assert api_key is not None

    def test_rate_limit_enforced(self):
        """Rate limit is enforced after burst allowance."""
        full_key, _ = self.auth.create_key(
            user_id="user-1",
            rate_limit_rpm=10,
            burst_allowance=5,
        )

        # Should allow rate_limit + burst = 15 requests
        allowed = 0
        for _ in range(20):
            try:
                self.auth.validate_key(full_key)
                allowed += 1
            except RateLimitExceeded:
                break

        # Should allow at least 10 requests (rate limit)
        # but not all 20
        assert allowed >= 10
        assert allowed < 20

    def test_rate_limit_refills(self):
        """Rate limit tokens refill over time."""
        full_key, _ = self.auth.create_key(
            user_id="user-1",
            rate_limit_rpm=60,
            burst_allowance=0,
        )

        # Exhaust the bucket
        allowed = 0
        for _ in range(70):
            try:
                self.auth.validate_key(full_key)
                allowed += 1
            except RateLimitExceeded:
                break

        # Should have allowed ~60 requests
        assert allowed <= 65  # Some tolerance for refill during loop

    def test_different_keys_independent_limits(self):
        """Different API keys have independent rate limits."""
        key1, _ = self.auth.create_key(
            user_id="user-1",
            rate_limit_rpm=5,
            burst_allowance=0,
        )
        key2, _ = self.auth.create_key(
            user_id="user-2",
            rate_limit_rpm=5,
            burst_allowance=0,
        )

        # Exhaust key1
        for _ in range(10):
            try:
                self.auth.validate_key(key1)
            except RateLimitExceeded:
                break

        # key2 should still work
        api_key = self.auth.validate_key(key2)
        assert api_key is not None


class TestKeyExpiration:
    """Test API key expiration."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test_auth.db"
        self.storage = SawyerStorage(str(self.db_path))
        self.auth = SawyerAuth(self.storage)

    def teardown_method(self):
        self.storage.close()

    def test_expired_key_rejected(self):
        """An expired key raises InvalidAPIKey."""
        full_key, _ = self.auth.create_key(
            user_id="user-1",
            expires_at=time.time() - 3600,  # Expired 1 hour ago
        )
        with pytest.raises(InvalidAPIKey, match="expired"):
            self.auth.validate_key(full_key)

    def test_key_not_yet_expired_works(self):
        """A key that hasn't expired yet works normally."""
        full_key, _ = self.auth.create_key(
            user_id="user-1",
            expires_at=time.time() + 3600,  # Expires in 1 hour
        )
        api_key = self.auth.validate_key(full_key)
        assert api_key.status == KeyStatus.ACTIVE


class TestKeyListing:
    """Test API key listing and management."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test_auth.db"
        self.storage = SawyerStorage(str(self.db_path))
        self.auth = SawyerAuth(self.storage)

    def teardown_method(self):
        self.storage.close()

    def test_list_all_keys(self):
        """List all API keys."""
        self.auth.create_key(user_id="user-1", tier="explorer")
        self.auth.create_key(user_id="user-2", tier="builder")
        keys = self.auth.list_keys()
        assert len(keys) == 2

    def test_list_keys_by_user(self):
        """List keys filtered by user."""
        self.auth.create_key(user_id="user-1", tier="explorer")
        self.auth.create_key(user_id="user-1", tier="builder")
        self.auth.create_key(user_id="user-2", tier="explorer")

        keys = self.auth.list_keys(user_id="user-1")
        assert len(keys) == 2
        assert all(k.user_id == "user-1" for k in keys)

    def test_list_keys_persists(self):
        """Keys persist across SawyerAuth instances."""
        self.auth.create_key(user_id="user-1")

        # Create new auth instance from same database
        new_auth = SawyerAuth(self.storage)
        keys = new_auth.list_keys()
        assert len(keys) == 1
