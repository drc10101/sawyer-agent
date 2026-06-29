"""Tests for Sawyer Identity — Bedrock integration."""

import pytest

from sawyer.config import SawyerConfig
from sawyer.identity.bedrock import (
    AuditEntry,
    NodeCertificate,
    SawyerIdentity,
)


class TestSawyerIdentity:
    """Test SawyerIdentity with no Bedrock connection (local mode)."""

    def setup_method(self):
        self.config = SawyerConfig(node_name="test-node")
        # No bedrock_license_key — forces local mode
        self.identity = SawyerIdentity(self.config)

    @pytest.mark.asyncio
    async def test_register_node_local(self):
        """Register a node in local mode (no Bedrock)."""
        node_id = await self.identity.register_node("test-node")
        assert node_id == "sawyer-local-test-node"
        assert self.identity.node_id == "sawyer-local-test-node"

    @pytest.mark.asyncio
    async def test_verify_node_local(self):
        """Local nodes are always verified as valid."""
        result = await self.identity.verify_node("sawyer-local-test-node")
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_node_any_id_local(self):
        """In local mode, any node ID is valid."""
        result = await self.identity.verify_node("some-random-id")
        assert result is True

    @pytest.mark.asyncio
    async def test_log_inference_local(self):
        """Log an inference event in local mode."""
        entry = await self.identity.log_inference(
            node_id="sawyer-local-test",
            model="mixtral-8x7b",
            expert_id=2,
            tokens=100,
            user_id="user-1",
        )
        assert entry is not None
        assert entry.action == "sawyer.inference"
        assert entry.actor_id == "sawyer-local-test"
        assert entry.details["model"] == "mixtral-8x7b"
        assert entry.details["tokens"] == 100

    @pytest.mark.asyncio
    async def test_flag_node_local(self):
        """Flag a node in local mode."""
        result = await self.identity.flag_node(
            source_node_id="node-a",
            target_node_id="node-b",
            signal_type="timeout",
            details="Node not responding",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_get_mesh_state_local(self):
        """Get mesh state returns local state when not connected."""
        state = await self.identity.get_mesh_state("test-node")
        assert state["node_id"] == "test-node"
        assert state["state"] == "local"

    def test_is_connected_false_without_bedrock(self):
        """is_connected is False when no Bedrock license key."""
        assert self.identity.is_connected is False


class TestSawyerIdentityWithBedrock:
    """Test SawyerIdentity with Bedrock configured but SDK may be missing."""

    def setup_method(self):
        self.config = SawyerConfig(
            node_name="prod-node",
            bedrock_url="https://bedrock.example.com",
            bedrock_license_key="BR-DEV-test-test-test",
        )
        self.identity = SawyerIdentity(self.config)

    def test_bedrock_url_configured(self):
        assert self.config.bedrock_url == "https://bedrock.example.com"
        assert self.config.bedrock_license_key == "BR-DEV-test-test-test"

    @pytest.mark.asyncio
    async def test_register_falls_back_to_local(self):
        """Without a real Bedrock server, registration falls back to local."""
        # The SDK is installed but no server is running, so this will
        # catch the connection error and fall back to local mode.
        node_id = await self.identity.register_node("prod-node")
        assert "sawyer" in node_id  # Either real or local ID


class TestNodeCertificate:
    """Test NodeCertificate dataclass."""

    def test_defaults(self):
        cert = NodeCertificate(
            node_uuid="test-uuid",
            node_name="test-node",
            public_key_hash="abc123",
        )
        assert cert.capabilities == ["sawyer-inference"]
        assert cert.certificate_id == ""
        assert cert.issued_at == 0.0

    def test_custom_capabilities(self):
        cert = NodeCertificate(
            node_uuid="test-uuid",
            node_name="test-node",
            public_key_hash="abc123",
            capabilities=["sawyer-inference", "sawyer-routing"],
        )
        assert "sawyer-routing" in cert.capabilities


class TestAuditEntry:
    """Test AuditEntry dataclass."""

    def test_creation(self):
        entry = AuditEntry(
            action="sawyer.inference",
            actor_id="node-1",
            target_id="mixtral-8x7b:expert-2",
            silo="sawyer-inference",
            timestamp=1234567890.0,
            details={"tokens": 100},
        )
        assert entry.action == "sawyer.inference"
        assert entry.details["tokens"] == 100
        assert entry.entry_hash == ""
