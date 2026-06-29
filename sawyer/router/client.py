"""Sawyer gRPC client — connects node agents to the router."""

import logging

import grpc

from sawyer.config import SawyerConfig
from sawyer.proto import sawyer_pb2, sawyer_pb2_grpc

logger = logging.getLogger(__name__)


class RouterClient:
    """Client for connecting a Sawyer node to the router gateway."""

    def __init__(self, config: SawyerConfig) -> None:
        self.config = config
        self._channel: grpc.Channel | None = None
        self._router_stub: sawyer_pb2_grpc.SawyerRouterStub | None = None
        self._node_id: str | None = None
        self._session_token: str | None = None

    def connect(self) -> None:
        """Open a gRPC channel to the router."""
        self._channel = grpc.insecure_channel(self.config.router_url)
        self._router_stub = sawyer_pb2_grpc.SawyerRouterStub(self._channel)
        logger.info("Connected to router at %s", self.config.router_url)

    def close(self) -> None:
        """Close the gRPC channel."""
        if self._channel:
            self._channel.close()
            self._channel = None
            logger.info("Disconnected from router")

    def register(
        self,
        name: str,
        gpu_name: str = "",
        vram_bytes: int = 0,
        max_experts: int = 2,
        bandwidth_mbps: float = 100.0,
        region: str = "us-east-1",
        supported_models: list[str] | None = None,
    ) -> str:
        """Register this node with the router.

        Returns:
            Node ID assigned by the router.
        """
        if not self._router_stub:
            raise RuntimeError("Not connected to router. Call connect() first.")

        if supported_models is None:
            from sawyer.model.registry import MODELS

            supported_models = list(MODELS.keys())

        request = sawyer_pb2.RegisterNodeRequest(
            node_name=name,
            capabilities=sawyer_pb2.NodeCapabilities(
                gpu_name=gpu_name,
                vram_bytes=vram_bytes,
                max_experts=max_experts,
                bandwidth_mbps=bandwidth_mbps,
                region=region,
                supported_models=supported_models,
            ),
        )

        response = self._router_stub.RegisterNode(request)
        self._node_id = response.node_id
        self._session_token = response.session_token

        logger.info(
            "Registered as %s with %d model assignments",
            response.node_id,
            len(response.assignments),
        )
        return response.node_id

    def deregister(self) -> None:
        """Remove this node from the router."""
        if not self._router_stub or not self._node_id:
            return

        request = sawyer_pb2.DeregisterNodeRequest(
            node_id=self._node_id,
            session_token=self._session_token or "",
        )
        response = self._router_stub.DeregisterNode(request)
        if response.acknowledged:
            logger.info("Deregistered node %s", self._node_id)
        self._node_id = None
        self._session_token = None

    def heartbeat(self, status: sawyer_pb2.NodeStatus | None = None) -> None:
        """Send a heartbeat to the router."""
        if not self._router_stub or not self._node_id:
            return

        request = sawyer_pb2.HeartbeatRequest(
            node_id=self._node_id,
            status=status,
        )
        response = self._router_stub.Heartbeat(request)
        if response.updated_assignments:
            logger.info("Received %d assignment updates", len(response.updated_assignments))

    def list_models(self) -> list[sawyer_pb2.ModelInfo]:
        """List available models on the network."""
        if not self._router_stub:
            raise RuntimeError("Not connected to router")

        response = self._router_stub.ListModels(sawyer_pb2.ListModelsRequest())
        return list(response.models)

    @property
    def node_id(self) -> str | None:
        """Return this node's ID."""
        return self._node_id

    @property
    def session_token(self) -> str | None:
        """Return this node's session token."""
        return self._session_token


class NodeClient:
    """Client for the router to call node services."""

    def __init__(self, node_address: str) -> None:
        self._address = node_address
        self._channel: grpc.Channel | None = None
        self._stub: sawyer_pb2_grpc.SawyerNodeStub | None = None

    def connect(self) -> None:
        """Open a gRPC channel to the node."""
        self._channel = grpc.insecure_channel(self._address)
        self._stub = sawyer_pb2_grpc.SawyerNodeStub(self._channel)

    def close(self) -> None:
        """Close the gRPC channel."""
        if self._channel:
            self._channel.close()

    def load_expert(
        self, model_name: str, expert_id: int, weight_url: str = "", weight_checksum: str = ""
    ) -> bool:
        """Ask a node to load an expert."""
        if not self._stub:
            raise RuntimeError("Not connected to node")

        request = sawyer_pb2.LoadExpertRequest(
            model_name=model_name,
            expert_id=expert_id,
            weight_url=weight_url,
            weight_checksum=weight_checksum,
        )
        response = self._stub.LoadExpert(request)
        if not response.success:
            logger.error(
                "Failed to load expert %s:%d: %s",
                model_name,
                expert_id,
                response.error_message,
            )
        return response.success

    def expert_infer(
        self,
        model_name: str,
        expert_id: int,
        hidden_states: bytes,
        hidden_shape: sawyer_pb2.TensorShape,
        layer_idx: int = 0,
    ) -> sawyer_pb2.ExpertInferResponse:
        """Run inference on an expert."""
        if not self._stub:
            raise RuntimeError("Not connected to node")

        request = sawyer_pb2.ExpertInferRequest(
            model_name=model_name,
            expert_id=expert_id,
            hidden_states=hidden_states,
            hidden_shape=hidden_shape,
            layer_idx=layer_idx,
        )
        return self._stub.ExpertInfer(request)

    def health_check(self, node_id: str = "") -> sawyer_pb2.HealthCheckResponse:
        """Check node health."""
        if not self._stub:
            raise RuntimeError("Not connected to node")

        request = sawyer_pb2.HealthCheckRequest(node_id=node_id)
        return self._stub.HealthCheck(request)
