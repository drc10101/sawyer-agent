"""Sawyer Expert Scheduler — intelligent routing to the best available expert nodes.

Routing strategies:
1. LOWEST_LATENCY — classic nearest-server (default baseline)
2. LEAST_LOADED — always pick the underutilized server
3. ADAPTIVE — blend latency + load with configurable weights
4. POWER_OF_TWO — pick the less-loaded of two random candidates (C2/P2)

Adaptive is the recommended default. It avoids stampeding the nearest node
while still respecting latency — underutilized servers get preferenced,
but a server on the other side of the planet won't win just because it's idle.

Fallback routing:
When a node hosting an expert fails or times out, the scheduler automatically
selects a redundant node hosting the same expert. If no redundant node exists,
the gating network selects an alternative expert from the model's pool.
"""

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sawyer.model.registry import get_model, MoEModel
from sawyer.router.gating import GatingDecision, GatingNetwork

logger = logging.getLogger(__name__)


class RoutingStrategy(Enum):
    """How to select which node handles an inference request."""

    LOWEST_LATENCY = "lowest_latency"
    LEAST_LOADED = "least_loaded"
    ADAPTIVE = "adaptive"
    POWER_OF_TWO = "power_of_two"


@dataclass
class NodeInfo:
    """Information about a registered Sawyer node."""

    node_id: str
    experts: list[int]  # expert IDs this node hosts
    gpu: str
    vram_gb: float
    bandwidth_mbps: float
    latency_ms: float
    healthy: bool = True
    last_heartbeat: float = field(default_factory=time.time)
    requests_served: int = 0
    avg_response_ms: float = 0.0
    active_requests: int = 0  # concurrent requests currently being served
    max_concurrent_requests: int = 10  # capacity limit
    region: str = ""  # geographic region tag (e.g., "us-east", "eu-west")


class ExpertScheduler:
    """Routes inference requests to the best available expert nodes.

    Supports multiple routing strategies. Recommended: ADAPTIVE, which
    balances latency and load to route traffic to underutilized servers
    without ignoring geography.
    """

    def __init__(
        self,
        strategy: RoutingStrategy = RoutingStrategy.ADAPTIVE,
        latency_weight: float = 0.4,
        load_weight: float = 0.6,
        gating: GatingNetwork | None = None,
        fallback_timeout_ms: float = 5000.0,
        max_fallback_attempts: int = 2,
    ) -> None:
        self.nodes: dict[str, NodeInfo] = {}
        self.strategy = strategy
        # For ADAPTIVE: how much to weight latency vs load (0-1 each, sum = 1)
        self.latency_weight = latency_weight
        self.load_weight = load_weight
        # Gating network for expert selection
        self.gating = gating or GatingNetwork(
            fallback_timeout_ms=fallback_timeout_ms,
            max_fallback_attempts=max_fallback_attempts,
        )
        # Track failed nodes for fallback routing
        self._failed_nodes: dict[str, float] = {}  # node_id -> timestamp of failure
        self._fallback_timeout_ms = fallback_timeout_ms
        self._max_fallback_attempts = max_fallback_attempts

    def register_node(self, node: NodeInfo) -> None:
        """Register a new node or update an existing one."""
        self.nodes[node.node_id] = node
        logger.info("Registered node %s with experts %s", node.node_id, node.experts)

    def unregister_node(self, node_id: str) -> None:
        """Remove a node from the scheduler."""
        self.nodes.pop(node_id, None)
        logger.info("Unregistered node %s", node_id)

    def find_expert_nodes(self, expert_id: int) -> list[NodeInfo]:
        """Find all healthy nodes hosting a specific expert."""
        return [
            node
            for node in self.nodes.values()
            if node.healthy
            and expert_id in node.experts
            and (time.time() - node.last_heartbeat) < 120
        ]

    def _load_fraction(self, node: NodeInfo) -> float:
        """What fraction of a node's capacity is in use (0.0 to 1.0+)."""
        if node.max_concurrent_requests <= 0:
            return 1.0
        return node.active_requests / node.max_concurrent_requests

    def _adaptive_score(self, node: NodeInfo) -> float:
        """Score a node for ADAPTIVE routing. Lower = better.

        Combines normalized latency and load:
        score = (latency_weight * latency_norm) + (load_weight * load_norm)

        A node that's nearby AND idle gets the best score.
        An underutilized far-away node can still beat a nearby overloaded one.
        """
        # Normalize latency: 0ms = 0.0, 500ms+ = 1.0
        latency_norm = min(node.latency_ms / 500.0, 1.0)

        # Normalize load: 0% = 0.0, 100%+ = 1.0
        load_norm = min(self._load_fraction(node), 1.0)

        score = (self.latency_weight * latency_norm) + (self.load_weight * load_norm)
        logger.debug(
            "Node %s: latency_norm=%.3f load_norm=%.3f score=%.3f",
            node.node_id,
            latency_norm,
            load_norm,
            score,
        )
        return score

    def select_node(
        self,
        expert_id: int,
        strategy: RoutingStrategy | None = None,
        exclude_nodes: set[str] | None = None,
    ) -> NodeInfo | None:
        """Select the best node for a given expert using the configured strategy.

        Args:
            expert_id: Which expert to route to
            strategy: Override the default strategy for this request
            exclude_nodes: Node IDs to skip (for fallback routing)

        Returns:
            The selected NodeInfo, or None if no healthy nodes
        """
        candidates = self.find_expert_nodes(expert_id)
        if exclude_nodes:
            candidates = [n for n in candidates if n.node_id not in exclude_nodes]
        if not candidates:
            logger.warning(
                "No healthy nodes found for expert %d (excluded=%s)",
                expert_id,
                exclude_nodes,
            )
            return None

        strat = strategy or self.strategy

        if strat == RoutingStrategy.LOWEST_LATENCY:
            candidates.sort(key=lambda n: n.latency_ms)

        elif strat == RoutingStrategy.LEAST_LOADED:
            candidates.sort(key=lambda n: self._load_fraction(n))

        elif strat == RoutingStrategy.ADAPTIVE:
            candidates.sort(key=lambda n: self._adaptive_score(n))

        elif strat == RoutingStrategy.POWER_OF_TWO:
            # Pick 2 random candidates, choose the less-loaded one
            if len(candidates) == 1:
                return candidates[0]
            pair = random.sample(candidates, min(2, len(candidates)))
            pair.sort(key=lambda n: self._load_fraction(n))
            return pair[0]

        selected = candidates[0]
        logger.info(
            "Selected node %s for expert %d (strategy=%s, load=%.0f%%, latency=%.0fms)",
            selected.node_id,
            expert_id,
            strat.value,
            self._load_fraction(selected) * 100,
            selected.latency_ms,
        )
        return selected

    def select_node_with_fallback(
        self,
        expert_id: int,
        model: MoEModel,
        strategy: RoutingStrategy | None = None,
    ) -> tuple[NodeInfo | None, bool]:
        """Select a node for an expert, with automatic fallback on failure.

        Tries the primary node selection first. If that node has recently
        failed (tracked in _failed_nodes), tries redundant nodes hosting
        the same expert. If no redundant nodes exist, selects an alternative
        expert via the gating network and routes to that.

        Args:
            expert_id: Which expert to route to
            model: The MoE model (needed for alternative expert selection)
            strategy: Override routing strategy

        Returns:
            Tuple of (selected_node, used_fallback). Node may be None if
            no healthy nodes are available at all.
        """
        # Clean stale failures (older than 60s)
        cutoff = time.time() - 60.0
        self._failed_nodes = {
            nid: ts for nid, ts in self._failed_nodes.items() if ts > cutoff
        }

        # Try primary selection, excluding recently failed nodes
        exclude = set(self._failed_nodes.keys())
        node = self.select_node(expert_id, strategy=strategy, exclude_nodes=exclude)

        if node is not None:
            return node, False

        # Primary nodes all failed — try without exclusion as last resort
        node = self.select_node(expert_id, strategy=strategy)
        if node is not None:
            logger.warning(
                "Fallback to node %s for expert %d (all preferred nodes failed)",
                node.node_id,
                expert_id,
            )
            return node, True

        # No nodes at all for this expert — try an alternative expert
        logger.warning(
            "No nodes available for expert %d, selecting alternative expert",
            expert_id,
        )
        # The caller (route_with_gating) handles expert-level fallback
        return None, True

    async def route(
        self,
        model_name: str,
        tokens: list,
        user_id: str,
    ) -> dict[str, Any]:
        """Route an inference request to the appropriate expert nodes.

        For MoE models, the gating network selects which experts activate
        for each token. This method finds the best node for each active
        expert and returns a routing plan.

        Args:
            model_name: Model identifier (e.g., "mixtral-8x7b")
            tokens: Input token embeddings
            user_id: Authenticated user ID

        Returns:
            Routing plan with selected nodes per expert
        """
        model = get_model(model_name)

        # In production, the gating network would select which experts
        # activate for these specific tokens. For now, top-K.
        active_expert_ids = list(range(model.active_experts))
        logger.info(
            "Routing %s request for user %s to experts %s",
            model_name,
            user_id,
            active_expert_ids,
        )

        # Select nodes for each active expert
        selected_nodes: dict[int, NodeInfo] = {}
        for expert_id in active_expert_ids:
            node = self.select_node(expert_id)
            if node is None:
                raise RuntimeError(f"No available node for expert {expert_id}")
            selected_nodes[expert_id] = node
            # Track that this node has an incoming request
            node.active_requests += 1

        return {
            "model": model_name,
            "experts_routed": active_expert_ids,
            "nodes_used": {str(eid): n.node_id for eid, n in selected_nodes.items()},
            "strategy": self.strategy.value,
            "status": "routed",
        }

    async def route_with_gating(
        self,
        model_name: str,
        input_tokens: list[int] | None = None,
        user_id: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        """Route an inference request using the gating network for expert selection.

        This is the preferred routing method. It uses the GatingNetwork to
        determine which experts should be activated, then selects the best
        node for each expert with automatic fallback on node failure.

        Args:
            model_name: Model identifier (e.g., "mixtral-8x7b")
            input_tokens: Input token IDs for deterministic gating
            user_id: Authenticated user ID
            request_id: Unique request ID for logging

        Returns:
            Routing plan with gating decision, selected nodes, and fallback info
        """
        model = get_model(model_name)

        # Step 1: Use gating network to select experts
        gating_decision = self.gating.select_experts(
            model=model,
            input_tokens=input_tokens,
            user_id=user_id,
            request_id=request_id,
        )

        # Step 2: Select nodes for each expert with fallback
        selected_nodes: dict[int, NodeInfo] = {}
        fallbacks: dict[int, str] = {}  # expert_id -> reason for fallback
        failed_expert_ids: list[int] = []

        for expert_id in gating_decision.expert_ids:
            node, used_fallback = self.select_node_with_fallback(
                expert_id, model
            )
            if node is not None:
                selected_nodes[expert_id] = node
                node.active_requests += 1
                if used_fallback:
                    fallbacks[expert_id] = "node_failed"
            else:
                failed_expert_ids.append(expert_id)

        # Step 3: If some experts couldn't be routed, try alternative experts
        if failed_expert_ids:
            alternative_ids = self.gating.fallback_experts(
                model, failed_expert_ids, gating_decision.expert_ids
            )
            for alt_id in alternative_ids:
                if alt_id not in selected_nodes and alt_id not in failed_expert_ids:
                    node, used_fallback = self.select_node_with_fallback(
                        alt_id, model
                    )
                    if node is not None:
                        selected_nodes[alt_id] = node
                        node.active_requests += 1
                        fallbacks[alt_id] = "expert_fallback"

        # Build the routing plan
        all_routed = list(selected_nodes.keys())
        status = "routed" if not failed_expert_ids else "routed_with_fallbacks"

        if len(selected_nodes) == 0:
            status = "no_nodes_available"

        result = {
            "model": model_name,
            "experts_routed": all_routed,
            "nodes_used": {
                str(eid): n.node_id for eid, n in selected_nodes.items()
            },
            "strategy": self.strategy.value,
            "gating": {
                "mode": gating_decision.mode.value,
                "scores": gating_decision.scores,
                "original_experts": gating_decision.expert_ids,
            },
            "fallbacks": fallbacks,
            "status": status,
        }

        if failed_expert_ids:
            result["unrouted_experts"] = failed_expert_ids

        logger.info(
            "Routed %s: experts=%s, nodes=%s, fallbacks=%d, status=%s (request=%s)",
            model_name,
            all_routed,
            list(result["nodes_used"].values()),
            len(fallbacks),
            status,
            request_id,
        )

        return result

    def complete_request(self, node_id: str, response_ms: float) -> None:
        """Mark a request as completed and update node metrics.

        Call this after inference completes to decrement active_requests
        and update the rolling average response time.
        """
        node = self.nodes.get(node_id)
        if node is None:
            return

        node.active_requests = max(0, node.active_requests - 1)
        node.requests_served += 1

        # Exponential moving average for response time
        alpha = 0.3
        node.avg_response_ms = alpha * response_ms + (1 - alpha) * node.avg_response_ms

        # Remove from failed list if it was there
        self._failed_nodes.pop(node_id, None)

        logger.debug(
            "Node %s: completed request (%.0fms), active=%d, served=%d",
            node_id,
            response_ms,
            node.active_requests,
            node.requests_served,
        )

    def mark_node_failed(self, node_id: str) -> None:
        """Mark a node as failed for fallback routing.

        The node will be excluded from selection for up to 60 seconds.
        Subsequent route calls will prefer redundant nodes hosting
        the same experts.
        """
        self._failed_nodes[node_id] = time.time()
        logger.warning("Marked node %s as failed, will prefer alternatives", node_id)

    def get_cluster_status(self) -> dict[str, Any]:
        """Get a summary of all registered nodes and their current load."""
        healthy = sum(1 for n in self.nodes.values() if n.healthy)
        total_capacity = sum(n.max_concurrent_requests for n in self.nodes.values() if n.healthy)
        total_active = sum(n.active_requests for n in self.nodes.values() if n.healthy)

        return {
            "total_nodes": len(self.nodes),
            "healthy_nodes": healthy,
            "total_capacity": total_capacity,
            "active_requests": total_active,
            "utilization": (
                f"{total_active / total_capacity * 100:.1f}%" if total_capacity > 0 else "0%"
            ),
            "nodes": {
                nid: {
                    "experts": n.experts,
                    "region": n.region,
                    "latency_ms": n.latency_ms,
                    "load": f"{self._load_fraction(n) * 100:.0f}%",
                    "active_requests": n.active_requests,
                    "requests_served": n.requests_served,
                    "avg_response_ms": round(n.avg_response_ms, 1),
                }
                for nid, n in self.nodes.items()
            },
        }
