"""Sawyer Local Router — runs on localhost, coordinates expert nodes.

The LocalRouter is the development and single-machine deployment entry point.
It runs both the router and one or more node agents on localhost, coordinating
expert selection via the gating network, routing via the scheduler, and
providing a unified API for inference requests.

For production distributed setups, use SawyerGateway + gRPC separately.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sawyer.config import SawyerConfig
from sawyer.model.registry import MoEModel, get_model, list_models
from sawyer.router.gating import GatingDecision, GatingNetwork
from sawyer.router.scheduler import ExpertScheduler, NodeInfo, RoutingStrategy

logger = logging.getLogger(__name__)


@dataclass
class LocalRouterStatus:
    """Status of the local router."""

    running: bool = False
    uptime_seconds: float = 0.0
    total_requests: int = 0
    total_tokens: int = 0
    active_nodes: int = 0
    models_available: list[str] = field(default_factory=list)
    strategy: str = "adaptive"


class LocalRouter:
    """Local Sawyer router for development and single-machine deployment.

    Runs on localhost, coordinates expert nodes via the gating network
    and scheduler, and provides a simple API for inference requests.

    Usage:
        config = SawyerConfig()
        router = LocalRouter(config)

        # Register local nodes
        router.add_local_node(
            node_id="local-rtx-4090",
            gpu="NVIDIA RTX 4090",
            vram_gb=24.0,
            experts=[0, 1, 2, 3],  # Which experts this node hosts
        )

        # Start the router
        await router.start()

        # Run inference
        result = await router.infer("mixtral-8x7b", "Hello, world!")

        # Check status
        status = router.get_status()

        # Stop
        await router.stop()
    """

    def __init__(
        self,
        config: SawyerConfig | None = None,
        strategy: RoutingStrategy = RoutingStrategy.ADAPTIVE,
        gating: GatingNetwork | None = None,
    ) -> None:
        self.config = config or SawyerConfig()
        self.scheduler = ExpertScheduler(
            strategy=strategy,
            gating=gating,
        )
        self._running = False
        self._start_time: float = 0.0
        self._total_requests = 0
        self._total_tokens = 0

    async def start(self) -> None:
        """Start the local router."""
        if self._running:
            logger.warning("LocalRouter already running")
            return

        logger.info(
            "Starting LocalRouter on port %d (strategy=%s)",
            self.config.inference_port,
            self.scheduler.strategy.value,
        )
        self._running = True
        self._start_time = time.time()

    async def stop(self) -> None:
        """Stop the local router."""
        logger.info("Stopping LocalRouter")
        self._running = False

    def add_local_node(
        self,
        node_id: str,
        gpu: str = "local",
        vram_gb: float = 0.0,
        experts: list[int] | None = None,
        bandwidth_mbps: float = 1000.0,
        latency_ms: float = 1.0,
        region: str = "local",
        max_concurrent_requests: int = 10,
    ) -> NodeInfo:
        """Register a local node with the router.

        Args:
            node_id: Unique identifier for this node.
            gpu: GPU name (e.g., "NVIDIA RTX 4090").
            vram_gb: Available VRAM in GB.
            experts: Expert IDs this node hosts.
            bandwidth_mbps: Network bandwidth (high for local).
            latency_ms: Latency in ms (near-zero for local).
            region: Region tag.
            max_concurrent_requests: Maximum concurrent requests.

        Returns:
            The registered NodeInfo.
        """
        node = NodeInfo(
            node_id=node_id,
            experts=experts or [],
            gpu=gpu,
            vram_gb=vram_gb,
            bandwidth_mbps=bandwidth_mbps,
            latency_ms=latency_ms,
            region=region,
            max_concurrent_requests=max_concurrent_requests,
        )
        self.scheduler.register_node(node)
        logger.info(
            "Added local node %s (%s, %.0f GB VRAM, experts=%s)",
            node_id, gpu, vram_gb, experts,
        )
        return node

    def remove_local_node(self, node_id: str) -> None:
        """Remove a local node from the router."""
        self.scheduler.unregister_node(node_id)
        logger.info("Removed local node %s", node_id)

    async def infer(
        self,
        model_name: str,
        prompt: str,
        input_tokens: list[int] | None = None,
        user_id: str = "local-user",
        request_id: str = "",
        max_output_tokens: int = 256,
    ) -> dict[str, Any]:
        """Run an inference request through the local router.

        Uses the gating network to select experts, the scheduler to
        route to the best available nodes, and falls back to redundant
        nodes if primary nodes fail.

        Args:
            model_name: Model to run (e.g., "mixtral-8x7b").
            prompt: Text prompt for inference.
            input_tokens: Pre-tokenized input (if available).
            user_id: User ID for token accounting.
            request_id: Unique request ID (auto-generated if empty).
            max_output_tokens: Maximum tokens to generate.

        Returns:
            Dict with inference result, routing info, and metrics.
        """
        if not self._running:
            raise RuntimeError("LocalRouter not running. Call start() first.")

        if not request_id:
            request_id = f"req-{self._total_requests + 1}"

        model = get_model(model_name)

        # Route through the gating network with fallback
        routing = await self.scheduler.route_with_gating(
            model_name=model_name,
            input_tokens=input_tokens,
            user_id=user_id,
            request_id=request_id,
        )

        if routing["status"] == "no_nodes_available":
            return {
                "error": "no_nodes_available",
                "message": f"No nodes available for model {model_name}",
                "model": model_name,
                "request_id": request_id,
            }

        # Track request
        self._total_requests += 1
        estimated_tokens = (len(input_tokens) if input_tokens else len(prompt) // 4) + max_output_tokens
        self._total_tokens += estimated_tokens

        # Mark nodes as serving requests
        nodes_used = routing.get("nodes_used", {})
        for expert_id_str, node_id in nodes_used.items():
            # In a real implementation, we'd forward to the node here
            # and call complete_request() after the response
            logger.info(
                "Routing expert %s to node %s for request %s",
                expert_id_str, node_id, request_id,
            )

        # Simulate completion (in production, this happens after node response)
        for expert_id_str, node_id in nodes_used.items():
            self.scheduler.complete_request(node_id, response_ms=50.0)

        return {
            "model": model_name,
            "prompt": prompt,
            "request_id": request_id,
            "routing": routing,
            "tokens_used": estimated_tokens,
            "status": "completed",
        }

    def assign_experts(
        self,
        model_name: str,
        node_assignments: dict[str, list[int]],
    ) -> None:
        """Assign specific experts to specific nodes.

        For local development, this lets you control which expert shards
        each node hosts. In production, the router assigns experts based
        on node capabilities.

        Args:
            model_name: Model to assign experts for.
            node_assignments: Dict of node_id -> list of expert IDs.
        """
        model = get_model(model_name)
        for node_id, expert_ids in node_assignments.items():
            node = self.scheduler.nodes.get(node_id)
            if node is None:
                logger.warning("Unknown node %s, skipping assignment", node_id)
                continue
            # Validate expert IDs are within model's expert range
            valid_ids = [eid for eid in expert_ids if 0 <= eid < model.num_experts]
            if len(valid_ids) != len(expert_ids):
                invalid = set(expert_ids) - set(valid_ids)
                logger.warning(
                    "Skipping invalid expert IDs for %s: %s (model has %d experts)",
                    node_id, invalid, model.num_experts,
                )
            node.experts = valid_ids
            logger.info(
                "Assigned experts %s to node %s for model %s",
                valid_ids, node_id, model_name,
            )

    def auto_assign_experts(self, model_name: str) -> dict[str, list[int]]:
        """Automatically distribute model experts across available nodes.

        Distributes experts evenly across nodes based on their VRAM and
        max_concurrent_requests capacity. Nodes with more capacity get
        more experts.

        Args:
            model_name: Model to distribute experts for.

        Returns:
            Dict of node_id -> assigned expert IDs.
        """
        model = get_model(model_name)
        nodes = list(self.scheduler.nodes.values())
        if not nodes:
            logger.warning("No nodes registered, cannot auto-assign experts")
            return {}

        # Sort nodes by capacity (VRAM * max_concurrent) descending
        nodes.sort(key=lambda n: n.vram_gb * n.max_concurrent_requests, reverse=True)

        # Distribute experts round-robin with capacity weighting
        total_capacity = sum(n.vram_gb * n.max_concurrent_requests for n in nodes)
        assignments: dict[str, list[int]] = {n.node_id: [] for n in nodes}
        expert_ids = list(range(model.num_experts))

        # Weighted round-robin
        for expert_id in expert_ids:
            # Pick the node with the most remaining capacity that can host this expert
            best_node = None
            best_remaining = -1
            for node in nodes:
                if model.min_vram_per_expert_gb > 0:
                    used_vram = len(assignments[node.node_id]) * model.expert_size_gb_q4
                    remaining = node.vram_gb - used_vram
                    if remaining >= model.min_vram_per_expert_gb and remaining > best_remaining:
                        best_remaining = remaining
                        best_node = node
                else:
                    # No VRAM constraint, round-robin
                    if len(assignments[node.node_id]) < node.max_concurrent_requests:
                        if best_node is None:
                            best_node = node

            if best_node is None:
                # All nodes full, distribute round-robin
                lightest = min(nodes, key=lambda n: len(assignments[n.node_id]))
                best_node = lightest

            assignments[best_node.node_id].append(expert_id)

        # Apply assignments
        for node_id, expert_list in assignments.items():
            node = self.scheduler.nodes[node_id]
            node.experts = expert_list

        logger.info(
            "Auto-assigned %s experts: %s",
            model_name,
            {nid: f"{len(eids)} experts" for nid, eids in assignments.items()},
        )

        return assignments

    def get_status(self) -> LocalRouterStatus:
        """Get the current status of the local router."""
        cluster = self.scheduler.get_cluster_status()
        return LocalRouterStatus(
            running=self._running,
            uptime_seconds=time.time() - self._start_time if self._running else 0.0,
            total_requests=self._total_requests,
            total_tokens=self._total_tokens,
            active_nodes=cluster["healthy_nodes"],
            models_available=[m.name for m in list_models()],
            strategy=self.scheduler.strategy.value,
        )

    def get_routing_plan(self, model_name: str) -> dict[str, Any]:
        """Preview the routing plan for a model without executing inference.

        Useful for debugging and testing expert distribution.

        Args:
            model_name: Model to plan routing for.

        Returns:
            Dict with expert assignments per node.
        """
        model = get_model(model_name)
        plan = {}
        for node_id, node in self.scheduler.nodes.items():
            hosted = [eid for eid in node.experts if eid < model.num_experts]
            if hosted:
                plan[node_id] = {
                    "experts": hosted,
                    "gpu": node.gpu,
                    "vram_gb": node.vram_gb,
                    "load": f"{self.scheduler._load_fraction(node) * 100:.0f}%",
                    "latency_ms": node.latency_ms,
                }
        return plan