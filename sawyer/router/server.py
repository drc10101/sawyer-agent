"""Sawyer Router Server — gRPC gateway that distributes inference to nodes.

Receives inference requests, runs the gating network to select experts,
routes to the appropriate nodes, aggregates results, and returns to the user.
"""

import logging
import time
from concurrent import futures

import grpc

from sawyer.config import SawyerConfig
from sawyer.model.registry import get_model, list_models
from sawyer.proto import sawyer_pb2, sawyer_pb2_grpc
from sawyer.router.scheduler import ExpertScheduler, NodeInfo
from sawyer.token.budget import TokenBalance

logger = logging.getLogger(__name__)


class SawyerRouterServicer(sawyer_pb2_grpc.SawyerRouterServicer):
    """gRPC Router service implementation."""

    def __init__(self, config: SawyerConfig) -> None:
        self.config = config
        self.scheduler = ExpertScheduler()
        self._session_tokens: dict[str, str] = {}  # node_id -> session_token
        self._token_balances: dict[str, TokenBalance] = {}  # user_id -> balance
        self._next_session_id = 1

    def RegisterNode(
        self, request: sawyer_pb2.RegisterNodeRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.RegisterNodeResponse:
        """Register a new node with the network."""
        caps = request.capabilities
        node = NodeInfo(
            node_id=f"sawyer-node-{self._next_session_id}",
            experts=[],
            gpu=caps.gpu_name,
            vram_gb=caps.vram_bytes / (1024**3) if caps.vram_bytes else 0,
            bandwidth_mbps=caps.bandwidth_mbps,
            latency_ms=0.0,
        )
        self._next_session_id += 1

        session_token = f"st-{node.node_id}-{int(time.time())}"
        self._session_tokens[node.node_id] = session_token
        self.scheduler.register_node(node)

        logger.info("Registered node %s (%s, %.0f GB VRAM)", node.node_id, node.gpu, node.vram_gb)

        # Build initial assignments (empty — router will assign via LoadExpert)
        assignments = [
            sawyer_pb2.ExpertAssignment(
                model_name=model_name,
                expert_id=0,
                status=sawyer_pb2.EXPERT_STATUS_UNSPECIFIED,
            )
            for model_name in caps.supported_models
        ]

        return sawyer_pb2.RegisterNodeResponse(
            node_id=node.node_id,
            session_token=session_token,
            assignments=assignments,
        )

    def DeregisterNode(
        self, request: sawyer_pb2.DeregisterNodeRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.DeregisterNodeResponse:
        """Remove a node from the network."""
        self._verify_session(request.node_id, request.session_token, context)
        self.scheduler.unregister_node(request.node_id)
        self._session_tokens.pop(request.node_id, None)
        logger.info("Deregistered node %s", request.node_id)
        return sawyer_pb2.DeregisterNodeResponse(acknowledged=True)

    def Heartbeat(
        self, request: sawyer_pb2.HeartbeatRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.HeartbeatResponse:
        """Receive heartbeat from a node."""
        node = self.scheduler.nodes.get(request.node_id)
        if node:
            node.last_heartbeat = time.time()
            node.healthy = True
            if request.status:
                node.avg_response_ms = request.status.avg_latency_ms
                node.requests_served = request.status.total_inferences

        return sawyer_pb2.HeartbeatResponse(
            acknowledged=True,
            updated_assignments=[],
        )

    def Infer(
        self, request: sawyer_pb2.InferRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.InferResponse:
        """Handle an inference request."""
        # Check token balance
        balance = self._token_balances.get(request.user_id)
        if not balance:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Unknown user")
            return sawyer_pb2.InferResponse()

        if balance.total_available <= 0:
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Insufficient tokens")
            return sawyer_pb2.InferResponse()

        # Look up the model
        try:
            model = get_model(request.model_name)
        except ValueError as e:
            context.abort(grpc.StatusCode.NOT_FOUND, str(e))
            return sawyer_pb2.InferResponse()

        # Select expert nodes via scheduler

        input_token_count = len(request.input_tokens) // 4  # int32 = 4 bytes
        active_expert_ids = list(range(model.active_experts))

        routing = []
        selected_nodes = {}
        for expert_id in active_expert_ids:
            node = self.scheduler.select_node(expert_id)
            if node is None:
                context.abort(
                    grpc.StatusCode.UNAVAILABLE,
                    f"No available node for expert {expert_id}",
                )
                return sawyer_pb2.InferResponse()
            selected_nodes[expert_id] = node
            routing.append(
                sawyer_pb2.ExpertRouting(
                    model_name=request.model_name,
                    expert_id=expert_id,
                    node_id=node.node_id,
                    expert_latency_ms=node.avg_response_ms,
                )
            )

        # TODO: Forward inference to nodes via gRPC and aggregate
        # For now, return a placeholder response

        # Debit tokens
        estimated_tokens = input_token_count + request.max_output_tokens
        try:
            remaining = balance.debit(estimated_tokens)
        except ValueError:
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Insufficient tokens")
            return sawyer_pb2.InferResponse()

        logger.info(
            "Inference: user=%s model=%s experts=%s tokens=%d",
            request.user_id,
            request.model_name,
            active_expert_ids,
            estimated_tokens,
        )

        return sawyer_pb2.InferResponse(
            output_text="[Sawyer] Inference placeholder — router received request",
            metrics=sawyer_pb2.InferMetrics(
                input_tokens=input_token_count,
                output_tokens=request.max_output_tokens,
                latency_ms=0.0,
                routing=routing,
            ),
            tokens_remaining=remaining,
        )

    def ListModels(
        self, request: sawyer_pb2.ListModelsRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.ListModelsResponse:
        """List available models and their expert layouts."""
        models = list_models()
        model_infos = []
        for m in models:
            # Count available nodes for this model
            available = sum(
                1
                for n in self.scheduler.nodes.values()
                if n.healthy and any(exp in n.experts for exp in range(m.num_experts))
            )
            model_infos.append(
                sawyer_pb2.ModelInfo(
                    name=m.name,
                    display_name=m.display_name,
                    total_params=int(m.total_params_b * 1e9),
                    num_experts=m.num_experts,
                    active_experts=m.active_experts,
                    model_size_bytes=int(m.model_size_gb_q4 * 1024**3),
                    expert_size_bytes=int(m.expert_size_gb_q4 * 1024**3),
                    context_length=m.context_length,
                    gating_type=m.gating_type,
                    hf_repo=m.hf_repo,
                    available_nodes=available,
                    active_nodes=available,
                )
            )

        return sawyer_pb2.ListModelsResponse(models=model_infos)

    def _verify_session(
        self, node_id: str, session_token: str, context: grpc.ServicerContext
    ) -> None:
        """Verify a node's session token."""
        expected = self._session_tokens.get(node_id)
        if expected is None or expected != session_token:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid session token")


class SawyerNodeServicer(sawyer_pb2_grpc.SawyerNodeServicer):
    """gRPC Node service implementation — runs on each volunteer node."""

    def __init__(self, config: SawyerConfig) -> None:
        self.config = config
        self._loaded_experts: dict[tuple[str, int], dict] = {}  # (model, expert_id) -> metadata

    def LoadExpert(
        self, request: sawyer_pb2.LoadExpertRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.LoadExpertResponse:
        """Load an expert weight file into VRAM."""
        key = (request.model_name, request.expert_id)
        if key in self._loaded_experts:
            return sawyer_pb2.LoadExpertResponse(
                success=True,
                load_time_seconds=0.0,
                vram_used_bytes=self._loaded_experts[key]["vram_bytes"],
            )

        logger.info(
            "Loading expert %s:%d from %s",
            request.model_name,
            request.expert_id,
            request.weight_url,
        )

        # TODO: Download weight file from weight_url, verify checksum, load into inference backend
        self._loaded_experts[key] = {
            "model_name": request.model_name,
            "expert_id": request.expert_id,
            "vram_bytes": int(1.5 * 1024**3),  # Placeholder
            "loaded_at": time.time(),
            "inference_count": 0,
        }

        return sawyer_pb2.LoadExpertResponse(
            success=True,
            load_time_seconds=2.0,  # Placeholder
            vram_used_bytes=self._loaded_experts[key]["vram_bytes"],
        )

    def UnloadExpert(
        self, request: sawyer_pb2.UnloadExpertRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.UnloadExpertResponse:
        """Unload an expert from VRAM."""
        key = (request.model_name, request.expert_id)
        if key not in self._loaded_experts:
            return sawyer_pb2.UnloadExpertResponse(
                success=False,
                error_message=f"Expert {request.model_name}:{request.expert_id} not loaded",
            )

        vram_freed = self._loaded_experts[key]["vram_bytes"]
        del self._loaded_experts[key]

        logger.info("Unloaded expert %s:%d", request.model_name, request.expert_id)

        return sawyer_pb2.UnloadExpertResponse(
            success=True,
            vram_freed_bytes=vram_freed,
        )

    def ExpertInfer(
        self, request: sawyer_pb2.ExpertInferRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.ExpertInferResponse:
        """Run inference on a loaded expert."""
        key = (request.model_name, request.expert_id)
        if key not in self._loaded_experts:
            context.abort(grpc.StatusCode.NOT_FOUND, f"Expert {key} not loaded")
            return sawyer_pb2.ExpertInferResponse()

        self._loaded_experts[key]["inference_count"] += 1

        # TODO: Forward pass through the expert via inference backend
        return sawyer_pb2.ExpertInferResponse(
            output_states=b"",  # Placeholder
            output_shape=sawyer_pb2.TensorShape(dims=[1, 1, 4096], dtype="float16"),
            latency_ms=10.0,  # Placeholder
        )

    def HealthCheck(
        self, request: sawyer_pb2.HealthCheckRequest, context: grpc.ServicerContext
    ) -> sawyer_pb2.HealthCheckResponse:
        """Health check from router to node."""
        loaded = [
            sawyer_pb2.LoadedExpert(
                model_name=meta["model_name"],
                expert_id=meta["expert_id"],
                vram_bytes=meta["vram_bytes"],
                inference_count=meta["inference_count"],
            )
            for meta in self._loaded_experts.values()
        ]

        return sawyer_pb2.HealthCheckResponse(
            healthy=True,
            loaded_experts=loaded,
            status=sawyer_pb2.NodeStatus(
                cpu_usage=0.0,
                gpu_usage=0.0,
                vram_used_bytes=sum(m["vram_bytes"] for m in self._loaded_experts.values()),
                vram_total_bytes=(
                    int(self.config.max_vram_gb * 1024**3) if self.config.max_vram_gb else 0
                ),
                active_requests=0,
                total_inferences=sum(m["inference_count"] for m in self._loaded_experts.values()),
                avg_latency_ms=10.0,
            ),
        )


def serve_router(config: SawyerConfig, port: int | None = None) -> grpc.Server:
    """Create and return a gRPC router server (does not start it)."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    sawyer_pb2_grpc.add_SawyerRouterServicer_to_server(
        SawyerRouterServicer(config),
        server,
    )
    server.add_insecure_port(f"[::]:{port or config.inference_port}")
    return server


def serve_node(config: SawyerConfig, port: int | None = None) -> grpc.Server:
    """Create and return a gRPC node server (does not start it)."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    sawyer_pb2_grpc.add_SawyerNodeServicer_to_server(
        SawyerNodeServicer(config),
        server,
    )
    node_port = port or (config.inference_port + 1)
    server.add_insecure_port(f"[[:]:{node_port}")
    return server
