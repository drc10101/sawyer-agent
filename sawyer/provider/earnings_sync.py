"""Sawyer Provider Earnings — wires inference events into provider payouts.

When a node serves tokens during inference, the provider who owns that
node earns money. This module bridges the token accounting system and
the provider manager:

- TokenAccountant tracks per-node host earnings (in-memory)
- ProviderManager tracks per-provider USD earnings (persistent)
- This module syncs host earnings to provider payouts
"""

import logging
from typing import Any

from sawyer.provider.manager import ProviderManager
from sawyer.token.accounting import TokenAccountant

logger = logging.getLogger(__name__)


class ProviderEarningsSync:
    """Synchronize token accounting host earnings to provider payouts.

    Bridges the gap between:
    - TokenAccountant._credit_host() — tracks per-node token credits
    - ProviderManager.credit_earnings() — tracks per-provider USD earnings

    Usage:
        sync = ProviderEarningsSync(accountant, provider_mgr)
        # After inference batches, flush host earnings to providers
        sync.flush_host_earnings()
    """

    def __init__(
        self,
        accountant: TokenAccountant,
        provider_mgr: ProviderManager,
    ) -> None:
        self._accountant = accountant
        self._provider_mgr = provider_mgr
        self._last_flush_tokens: dict[str, int] = {}

    def flush_host_earnings(self) -> dict[str, float]:
        """Flush all host earnings from TokenAccountant to ProviderManager.

        Iterates over all nodes with earnings, finds the provider that
        owns each node, and credits their account.

        Returns:
            Dict mapping provider_id to USD credited this flush
        """
        all_earnings = self._accountant.get_all_host_earnings()
        credited: dict[str, float] = {}

        for host_earning in all_earnings:
            node_id = host_earning.node_id

            # Find the provider that owns this node
            provider = self._provider_mgr.find_provider_by_node(node_id)
            if provider is None:
                # No provider registered for this node — skip
                logger.debug(
                    "No provider for node %s, skipping earnings",
                    node_id,
                )
                continue

            # Only credit tokens since last flush
            last_tokens = self._last_flush_tokens.get(node_id, 0)
            new_tokens = host_earning.tokens_served - last_tokens

            if new_tokens <= 0:
                continue

            # Credit the provider (70% of gross)
            usd = self._provider_mgr.credit_earnings(
                provider.provider_id, tokens_served=new_tokens
            )
            self._last_flush_tokens[node_id] = host_earning.tokens_served

            credited[provider.provider_id] = credited.get(provider.provider_id, 0.0) + usd
            logger.info(
                "Flushed %d tokens from node %s → provider %s ($%.4f)",
                new_tokens,
                node_id,
                provider.provider_id,
                usd,
            )

        return credited

    def flush_single_node(self, node_id: str) -> float | None:
        """Flush earnings for a single node to its provider.

        Args:
            node_id: Node whose earnings to flush

        Returns:
            USD credited to the provider, or None if no provider
        """
        host_earning = self._accountant.get_host_earnings(node_id)
        if host_earning is None:
            return None

        provider = self._provider_mgr.find_provider_by_node(node_id)
        if provider is None:
            logger.debug("No provider for node %s", node_id)
            return None

        last_tokens = self._last_flush_tokens.get(node_id, 0)
        new_tokens = host_earning.tokens_served - last_tokens

        if new_tokens <= 0:
            return 0.0

        usd = self._provider_mgr.credit_earnings(provider.provider_id, tokens_served=new_tokens)
        self._last_flush_tokens[node_id] = host_earning.tokens_served
        return usd

    def get_eligible_providers(self) -> list[dict[str, Any]]:
        """Get all providers eligible for payout.

        Returns:
            List of dicts with provider_id, display_name, available_balance
        """
        eligible = []
        for provider in self._provider_mgr.list_providers():
            if provider.is_eligible_for_payout:
                eligible.append(
                    {
                        "provider_id": provider.provider_id,
                        "display_name": provider.display_name,
                        "available_balance": provider.available_balance,
                        "payout_schedule": provider.payout_schedule.value,
                        "node_count": len(provider.node_ids),
                    }
                )
        return eligible

    def get_provider_for_node(self, node_id: str) -> dict[str, Any] | None:
        """Get provider info for a node.

        Args:
            node_id: Node to look up

        Returns:
            Dict with provider details, or None if no provider
        """
        provider = self._provider_mgr.find_provider_by_node(node_id)
        if provider is None:
            return None

        summary = self._provider_mgr.get_provider_summary(provider.provider_id)
        host_earning = self._accountant.get_host_earnings(node_id)

        result = {
            **summary,
            "node_id": node_id,
            "node_tokens_served": host_earning.tokens_served if host_earning else 0,
            "node_usd_earned": host_earning.usd_earned if host_earning else 0.0,
        }
        return result
