"""Sawyer Gateway — main router server entry point.

Receives inference requests, runs the gating network to select experts,
routes to the appropriate nodes, aggregates results, and returns to the user.

Supports both the legacy `route()` path (simple top-K) and the preferred
`route_with_gating()` path which uses GatingNetwork for intelligent expert
selection with automatic fallback on node failure.
"""

import logging

from sawyer.config import SawyerConfig
from sawyer.router.gating import GatingNetwork
from sawyer.router.scheduler import ExpertScheduler, RoutingStrategy

logger = logging.getLogger(__name__)


class SawyerGateway:
    """Main Sawyer router server.

    Authenticates users, checks token balances, runs gating networks
    to select experts, routes to nodes, and aggregates results.
    """

    def __init__(
        self,
        config: SawyerConfig,
        strategy: RoutingStrategy = RoutingStrategy.ADAPTIVE,
        gating: GatingNetwork | None = None,
    ) -> None:
        self.config = config
        self.scheduler = ExpertScheduler(
            strategy=strategy,
            gating=gating,
        )
        self._running = False

    async def start(self) -> None:
        """Start the gateway server."""
        logger.info(
            "Starting Sawyer Gateway on port %d (strategy=%s)",
            self.config.inference_port,
            self.scheduler.strategy.value,
        )
        self._running = True
        # TODO: gRPC/QUIC server setup

    async def stop(self) -> None:
        """Stop the gateway server."""
        logger.info("Stopping Sawyer Gateway")
        self._running = False

    async def handle_request(
        self,
        model: str,
        tokens: list,
        user_id: str,
        token_balance: float,
        use_gating: bool = True,
    ) -> dict:
        """Handle an inference request.

        1. Validate user token balance
        2. Run gating network to select experts
        3. Route to available nodes (with fallback on failure)
        4. Aggregate expert outputs
        5. Debit token balance

        Args:
            model: Model name (e.g., "mixtral-8x7b")
            tokens: Input token embeddings
            user_id: Authenticated user ID
            token_balance: User's remaining token balance
            use_gating: If True, use GatingNetwork for expert selection
                        with automatic fallback. If False, use simple top-K.

        Returns:
            Aggregated inference result
        """
        if not self._running:
            raise RuntimeError("Gateway is not running")

        # Check token balance
        if token_balance <= 0:
            raise ValueError("Insufficient token balance")

        if use_gating:
            # Preferred path: gating network + fallback routing
            result = await self.scheduler.route_with_gating(
                model_name=model,
                input_tokens=tokens if isinstance(tokens, list) and all(
                    isinstance(t, int) for t in tokens
                ) else None,
                user_id=user_id,
                request_id=f"gw-{user_id}-{id(tokens)}",
            )
        else:
            # Legacy path: simple top-K
            result = await self.scheduler.route(model, tokens, user_id)

        logger.info("Request completed for user %s, model %s", user_id, model)
        return result