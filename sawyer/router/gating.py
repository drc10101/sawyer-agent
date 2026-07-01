"""Sawyer Gating Network — selects which experts activate for each input.

In a real MoE model, the gating network is a small neural network (usually
a linear projection + softmax) that computes router probabilities over all
experts and activates the top-K. At inference time, only the top-K experts
process each token.

Since we don't have the actual model weights for the gating network, Sawyer
uses a simulated gating network that:
1. For top-K models (Mixtral, DBRX): activates the model's active_experts
   count, distributed across available nodes
2. For shared models (DeepSeek-V2): activates shared + top-K routed experts
3. Allows manual expert override for debugging and testing
4. Supports deterministic expert assignment for reproducible routing

The real gating network runs inside the inference backend (llama.cpp, vLLM).
This module provides the *routing* decision — which expert IDs to activate —
so the router can assign them to nodes before the forward pass.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sawyer.model.registry import MoEModel

logger = logging.getLogger(__name__)


class GatingMode(Enum):
    """How the gating network selects experts."""

    TOP_K = "top_k"         # Standard top-K: pick K of N experts
    SHARED_PLUS_TOP_K = "shared"  # Shared experts + top-K routed (DeepSeek-V2 style)
    MANUAL = "manual"       # Explicitly specified experts (for testing)


@dataclass
class GatingDecision:
    """Result of a gating network decision."""

    expert_ids: list[int]          # Which experts to activate
    scores: dict[int, float]       # Gating scores for each expert (0-1)
    mode: GatingMode               # How the decision was made
    model_name: str                # Model this decision is for
    fallback: bool = False         # Whether this used fallback experts
    original_experts: list[int] | None = None  # Original choices before fallback


class GatingNetwork:
    """Simulated MoE gating network for expert selection.

    In production, the real gating scores come from the model's router
    weights during the forward pass. This class provides the routing
    decision — which expert IDs should be active — so the scheduler
    can assign them to available nodes.

    For testing and development, it can produce deterministic or
    random expert assignments. For production, it will be replaced
    by scores from the actual inference backend.
    """

    def __init__(
        self,
        seed: int | None = None,
        fallback_timeout_ms: float = 5000.0,
        max_fallback_attempts: int = 2,
    ) -> None:
        """Initialize the gating network.

        Args:
            seed: Random seed for deterministic expert selection.
                  None = use current time (non-deterministic).
            fallback_timeout_ms: How long to wait for a primary expert
                                  node before trying a fallback (ms).
            max_fallback_attempts: How many alternative experts to try
                                    before giving up.
        """
        self._seed = seed
        self._fallback_timeout_ms = fallback_timeout_ms
        self._max_fallback_attempts = max_fallback_attempts
        self._manual_selections: dict[str, list[int]] = {}

    def set_manual(self, model_name: str, expert_ids: list[int]) -> None:
        """Set a manual expert selection for a model (for testing).

        Args:
            model_name: Model to override.
            expert_ids: Expert IDs to activate, in preference order.
        """
        self._manual_selections[model_name] = expert_ids

    def select_experts(
        self,
        model: MoEModel,
        input_tokens: list[int] | None = None,
        user_id: str = "",
        request_id: str = "",
    ) -> GatingDecision:
        """Select which experts should be activated for an inference request.

        In a real MoE model, the gating network examines each token's hidden
        state and computes router logits. Here, we simulate this by:
        1. Using deterministic hash-based selection (seeded by input)
        2. Falling back to round-robin if no tokens provided
        3. Respecting manual overrides for testing

        Args:
            model: The MoE model being routed.
            input_tokens: Input token IDs (used for deterministic selection).
            user_id: User making the request (for consistent routing).
            request_id: Unique request ID (for logging).

        Returns:
            GatingDecision with the selected expert IDs and scores.
        """
        # Check manual override first
        if model.name in self._manual_selections:
            experts = self._manual_selections[model.name]
            scores = {eid: 1.0 / (i + 1) for i, eid in enumerate(experts)}
            logger.info(
                "Manual gating for %s: experts=%s (request=%s)",
                model.name, experts, request_id,
            )
            return GatingDecision(
                expert_ids=experts,
                scores=scores,
                mode=GatingMode.MANUAL,
                model_name=model.name,
            )

        # Determine how many experts to activate
        k = model.active_experts

        if model.gating_type == "top_k":
            return self._select_top_k(model, k, input_tokens, user_id, request_id)
        elif model.gating_type == "shared":
            return self._select_shared_plus_top_k(
                model, k, input_tokens, user_id, request_id,
            )
        else:
            # Default: simple top-K
            return self._select_top_k(model, k, input_tokens, user_id, request_id)

    def _select_top_k(
        self,
        model: MoEModel,
        k: int,
        input_tokens: list[int] | None,
        user_id: str,
        request_id: str,
    ) -> GatingDecision:
        """Standard top-K expert selection.

        Uses a hash of input tokens to deterministically select K experts
        from the model's expert pool. This provides consistent routing
        for the same input while distributing load across experts.
        """
        n = model.num_experts

        if input_tokens and len(input_tokens) > 0:
            # Deterministic selection based on input content
            # Hash the first N tokens to seed the selection
            seed_input = f"{model.name}:{user_id}:{input_tokens[:64]}"
            h = hashlib.sha256(seed_input.encode()).digest()
            # Use bytes of the hash to pick experts
            expert_indices = []
            seen = set()
            for i in range(len(h)):
                idx = h[i] % n
                if idx not in seen:
                    seen.add(idx)
                    expert_indices.append(idx)
                    if len(expert_indices) == k:
                        break

            # If the hash didn't give us enough unique indices, fill from sequential
            idx = 0
            while len(expert_indices) < k and idx < n:
                if idx not in seen:
                    seen.add(idx)
                    expert_indices.append(idx)
                idx += 1
        else:
            # Round-robin fallback when no tokens provided
            base = int(time.time() * 100) % n if self._seed is None else self._seed
            expert_indices = []
            for i in range(k):
                idx = (base + i) % n
                if idx not in expert_indices:
                    expert_indices.append(idx)

        # Generate synthetic scores (decreasing, as in real gating)
        scores = {}
        for i, eid in enumerate(expert_indices):
            scores[eid] = round(1.0 / (i + 1), 4)

        logger.info(
            "Top-K gating for %s: experts=%s (request=%s, user=%s)",
            model.name, expert_indices, request_id, user_id,
        )

        return GatingDecision(
            expert_ids=expert_indices,
            scores=scores,
            mode=GatingMode.TOP_K,
            model_name=model.name,
        )

    def _select_shared_plus_top_k(
        self,
        model: MoEModel,
        k: int,
        input_tokens: list[int] | None,
        user_id: str,
        request_id: str,
    ) -> GatingDecision:
        """Shared + top-K selection (DeepSeek-V2 style).

        Some experts are always active (shared), and the top-K routed
        experts are selected based on input. The shared experts provide
        baseline quality, while routed experts add specialization.
        """
        n = model.num_experts

        # In shared models, some experts are always active
        # For simplicity, treat the first N/K as shared and select the rest
        shared_count = max(1, n // 16)  # ~6% shared, minimum 1
        shared_experts = list(range(shared_count))

        # Select top-K routed experts from the remaining pool
        routed_pool = list(range(shared_count, n))
        routed_k = min(k, len(routed_pool))

        if input_tokens and len(input_tokens) > 0:
            seed_input = f"{model.name}:{user_id}:{input_tokens[:64]}"
            h = hashlib.sha256(seed_input.encode()).digest()
            routed_indices = []
            seen = set()
            for i in range(len(h)):
                pool_idx = h[i] % len(routed_pool)
                expert_id = routed_pool[pool_idx]
                if expert_id not in seen:
                    seen.add(expert_id)
                    routed_indices.append(expert_id)
                    if len(routed_indices) == routed_k:
                        break
        else:
            base = int(time.time() * 100) % len(routed_pool)
            routed_indices = [
                routed_pool[(base + i) % len(routed_pool)]
                for i in range(routed_k)
            ]

        all_experts = shared_experts + routed_indices
        scores = {}
        for eid in shared_experts:
            scores[eid] = 1.0  # Shared experts always score max
        for i, eid in enumerate(routed_indices):
            scores[eid] = round(1.0 / (i + 1), 4)

        logger.info(
            "Shared+Top-K gating for %s: shared=%s, routed=%s (request=%s)",
            model.name, shared_experts, routed_indices, request_id,
        )

        return GatingDecision(
            expert_ids=all_experts,
            scores=scores,
            mode=GatingMode.SHARED_PLUS_TOP_K,
            model_name=model.name,
        )

    def fallback_experts(
        self,
        model: MoEModel,
        failed_expert_ids: list[int],
        all_expert_ids: list[int],
    ) -> list[int]:
        """Select alternative experts when primary nodes fail.

        When a node hosting a primary expert times out or fails, this
        method selects replacement experts from the same model's pool.
        It avoids the failed experts and picks the next-best ones.

        Args:
            model: The MoE model being routed.
            failed_expert_ids: Expert IDs whose nodes failed.
            all_expert_ids: The original full selection of expert IDs.

        Returns:
            Revised expert IDs with failures replaced by alternatives.
        """
        failed_set = set(failed_expert_ids)
        available = [i for i in range(model.num_experts) if i not in failed_set]
        # Sort by hash for deterministic but distributed selection
        available.sort(key=lambda x: hashlib.sha256(str(x).encode()).hexdigest())

        revised = list(all_expert_ids)
        replacements_made = 0
        for i, eid in enumerate(revised):
            if eid in failed_set and available:
                revised[i] = available.pop(0)
                replacements_made += 1

        if replacements_made > 0:
            logger.info(
                "Fallback: replaced %d failed experts with alternatives %s",
                replacements_made,
                [revised[i] for i, eid in enumerate(all_expert_ids) if eid in failed_set],
            )

        return revised

    @property
    def fallback_timeout_ms(self) -> float:
        """Timeout in ms before trying a fallback node."""
        return self._fallback_timeout_ms

    @property
    def max_fallback_attempts(self) -> int:
        """Maximum number of fallback attempts per expert."""
        return self._max_fallback_attempts