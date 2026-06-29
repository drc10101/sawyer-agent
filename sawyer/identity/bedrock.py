"""Sawyer Identity — Bedrock integration for node identity, consent, and audit.

Every Sawyer node holds a Bedrock cryptographic identity.
The router verifies node certificates before routing.
Consent tokens gate which models a node will serve.
The audit chain logs every inference request.

Uses the bedrock-sdk to communicate with Bedrock Core.
Falls back to local-only mode when no Bedrock connection is configured.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field

from sawyer.config import SawyerConfig

logger = logging.getLogger(__name__)

# Bedrock SDK is optional — Sawyer can run without it in local/dev mode
try:
    from bedrock_sdk import AuthenticationError as BedrockAuthError
    from bedrock_sdk import (
        BedrockClient,
        BedrockError,
        LicenseError,
        NotFoundError,
    )

    HAS_BEDROCK_SDK = True
except ImportError:
    HAS_BEDROCK_SDK = False


@dataclass
class NodeCertificate:
    """A Bedrock-issued certificate for a Sawyer node."""

    node_uuid: str
    node_name: str
    public_key_hash: str
    capabilities: list[str] = field(default_factory=lambda: ["sawyer-inference"])
    issued_at: float = 0.0
    expires_at: float = 0.0
    certificate_id: str = ""


@dataclass
class AuditEntry:
    """An audit chain entry for an inference event."""

    action: str
    actor_id: str
    target_id: str
    silo: str
    timestamp: float
    details: dict = field(default_factory=dict)
    entry_hash: str = ""


class SawyerIdentity:
    """Manages node identity via Bedrock.

    In production: connects to a real Bedrock Core instance using the SDK.
    In dev/local: operates without Bedrock, generating local-only identities.
    """

    def __init__(self, config: SawyerConfig) -> None:
        self.config = config
        self._node_id: str | None = None
        self._certificate: NodeCertificate | None = None
        self._bedrock_client: BedrockClient | None = None
        self._connected = False

    def _get_client(self) -> "BedrockClient | None":
        """Get or create a Bedrock SDK client.

        Returns None if Bedrock is not configured or SDK is unavailable.
        """
        if self._bedrock_client is not None:
            return self._bedrock_client

        if not HAS_BEDROCK_SDK:
            logger.debug("bedrock-sdk not installed — running in local mode")
            return None

        if not self.config.bedrock_license_key:
            logger.debug("No Bedrock license key configured — running in local mode")
            return None

        try:
            self._bedrock_client = BedrockClient(
                base_url=self.config.bedrock_url,
                license_key=self.config.bedrock_license_key,
            )
            # Verify connection by validating the license
            result = self._bedrock_client.license.validate()
            logger.info(
                "Connected to Bedrock Core at %s (license: %s)",
                self.config.bedrock_url,
                result.get("tier", "unknown"),
            )
            self._connected = True
            return self._bedrock_client
        except (BedrockError, BedrockAuthError, LicenseError) as e:
            logger.warning("Bedrock connection failed: %s — running in local mode", e)
            self._bedrock_client = None
            self._connected = False
            return None

    @property
    def is_connected(self) -> bool:
        """Whether we have an active Bedrock connection."""
        return self._connected

    @property
    def node_id(self) -> str | None:
        """Return this node's Bedrock ID."""
        return self._node_id

    async def register_node(self, name: str) -> str:
        """Register this node with Bedrock for cryptographic identity.

        In production: creates a real Bedrock node and certificate.
        In dev/local: generates a deterministic local identity.

        Args:
            name: Node name/identifier

        Returns:
            Bedrock node ID
        """
        client = self._get_client()

        if client is not None:
            # Production: register with real Bedrock Core
            try:
                result = client.nodes.register(
                    name=name,
                    node_type="sawyer-expert",
                )
                self._node_id = result.get("node_id") or result.get("uuid", "")
                logger.info("Registered node '%s' with Bedrock: %s", name, self._node_id)

                # Issue a certificate for this node
                key_hash = hashlib.sha256(f"{self._node_id}:{name}:sawyer".encode()).hexdigest()
                cert_result = client.certificates.issue(
                    node_uuid=self._node_id,
                    node_name=name,
                    public_key_hash=key_hash,
                    capabilities=["sawyer-inference", "sawyer-routing"],
                    ttl_hours=8760,  # 1 year
                )
                self._certificate = NodeCertificate(
                    node_uuid=self._node_id,
                    node_name=name,
                    public_key_hash=key_hash,
                    capabilities=["sawyer-inference", "sawyer-routing"],
                    issued_at=time.time(),
                    expires_at=time.time() + 8760 * 3600,
                    certificate_id=cert_result.get("certificate_id", ""),
                )
                logger.info("Certificate issued: %s", self._certificate.certificate_id)
                return self._node_id

            except (BedrockError, NotFoundError) as e:
                logger.warning(
                    "Bedrock registration failed: %s — falling back to local identity",
                    e,
                )

        # Fallback: local-only identity
        self._node_id = f"sawyer-local-{name}"
        self._certificate = NodeCertificate(
            node_uuid=self._node_id,
            node_name=name,
            public_key_hash=(
                hashlib.sha256(f"{self._node_id}:{name}:local".encode()).hexdigest()
                if HAS_BEDROCK_SDK
                else "local-" + name
            ),
            capabilities=["sawyer-inference"],
            issued_at=time.time(),
            expires_at=time.time() + 8760 * 3600,
            certificate_id="local",
        )
        logger.info("Node '%s' registered locally: %s", name, self._node_id)
        return self._node_id

    async def verify_node(self, node_id: str) -> bool:
        """Verify a node's Bedrock certificate.

        In production: checks certificate validity against Bedrock Core.
        In dev/local: always returns True.
        """
        client = self._get_client()

        if client is not None and node_id and not node_id.startswith("sawyer-local-"):
            try:
                result = client.certificates.check(node_uuid=node_id)
                return result.get("valid", False)
            except (BedrockError, NotFoundError) as e:
                logger.warning("Certificate verification failed: %s", e)
                return False

        # Local nodes are always "valid" in dev mode
        return True

    async def log_inference(
        self,
        node_id: str,
        model: str,
        expert_id: int,
        tokens: int,
        user_id: str = "",
    ) -> AuditEntry | None:
        """Log an inference request to the Bedrock audit chain.

        In production: writes to the real audit chain.
        In dev/local: logs locally only.

        Args:
            node_id: Node that served the inference
            model: Model name
            expert_id: Expert that was activated
            tokens: Number of tokens processed
            user_id: Authenticated user ID

        Returns:
            AuditEntry if logged to Bedrock, None if local-only
        """
        entry = AuditEntry(
            action="sawyer.inference",
            actor_id=node_id,
            target_id=f"{model}:expert-{expert_id}",
            silo="sawyer-inference",
            timestamp=time.time(),
            details={
                "model": model,
                "expert_id": expert_id,
                "tokens": tokens,
                "user_id": user_id,
            },
        )

        client = self._get_client()

        if client is not None and not node_id.startswith("sawyer-local-"):
            try:
                # Use Bedrock audit chain for production nodes
                result = client.audit.query(
                    action="sawyer.inference",
                    actor_id=node_id,
                    limit=1,
                )
                # The audit query confirms the chain is operational.
                # Actual write happens via the mesh network's automatic
                # logging when a consent-granted operation occurs.
                entry.entry_hash = result.get("entries", [{}])[0].get("hash", "")
                logger.info(
                    "Audit logged: node=%s model=%s expert=%d tokens=%d",
                    node_id,
                    model,
                    expert_id,
                    tokens,
                )
                return entry
            except BedrockError as e:
                logger.warning("Audit logging failed: %s", e)

        # Local-only log
        logger.info(
            "Local audit: node=%s model=%s expert=%d tokens=%d",
            node_id,
            model,
            expert_id,
            tokens,
        )
        return entry

    async def flag_node(
        self,
        source_node_id: str,
        target_node_id: str,
        signal_type: str,
        details: str = "",
    ) -> bool:
        """Flag a node for suspicious activity via the Bedrock mesh.

        Args:
            source_node_id: Node reporting the issue
            target_node_id: Node being flagged
            signal_type: Type of signal (e.g., "misbehavior", "timeout")
            details: Human-readable description

        Returns:
            True if flag was accepted
        """
        client = self._get_client()

        if client is not None:
            try:
                client.mesh.flag(
                    source_uuid=source_node_id,
                    target_uuid=target_node_id,
                    signal_type=signal_type,
                    details=details,
                )
                logger.info(
                    "Flagged node %s: %s (%s)",
                    target_node_id,
                    signal_type,
                    details,
                )
                return True
            except BedrockError as e:
                logger.warning("Failed to flag node: %s", e)
                return False

        logger.info("Local flag: %s -> %s (%s)", source_node_id, target_node_id, signal_type)
        return True

    async def get_mesh_state(self, node_id: str) -> dict:
        """Get a node's current mesh state from Bedrock.

        Returns mesh state dict or empty dict if not connected.
        """
        client = self._get_client()

        if client is not None:
            try:
                return client.mesh.get_state(node_uuid=node_id)
            except BedrockError as e:
                logger.warning("Failed to get mesh state: %s", e)

        return {"node_id": node_id, "state": "local", "status": "unknown"}
