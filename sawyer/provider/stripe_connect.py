"""Sawyer Provider — Stripe Connect integration for provider payouts.

Handles:
- Stripe Connect Express onboarding (providers connect their bank account)
- Account verification status checks
- Payout transfers to connected accounts
- Balance tracking
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

import stripe

from sawyer.provider.manager import (
    PayoutSchedule,
    ProviderManager,
    ProviderStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class ConnectOnboardingResult:
    """Result from Stripe Connect onboarding link creation."""

    url: str  # Onboarding URL to redirect provider to
    account_id: str  # Stripe Connect account ID
    expires_at: float  # When the link expires
    provider_id: str  # Our provider ID


@dataclass
class PayoutTransferResult:
    """Result from a Stripe payout transfer."""

    transfer_id: str  # Stripe transfer ID
    amount_usd: float  # Amount transferred
    provider_id: str  # Our provider ID
    status: str  # Stripe transfer status
    destination: str  # Provider's bank last4


class SawyerProviderStripe:
    """Stripe Connect integration for Sawyer providers.

    Uses Stripe Connect Express for provider onboarding — providers
    get a Stripe-hosted form to enter their bank details. Sawyer
    never sees or stores bank information.

    Payout flow:
    1. Provider registers → gets Stripe Connect onboarding link
    2. Provider completes onboarding → Stripe verifies identity
    3. Each billing period → Sawyer transfers earnings to their bank
    """

    def __init__(self, manager: ProviderManager) -> None:
        self._manager = manager

    # ── Onboarding ─────────────────────────────────────────────────

    def create_connect_account(
        self,
        provider_id: str,
        country: str = "US",
    ) -> ConnectOnboardingResult:
        """Create a Stripe Connect Express account for a provider.

        Args:
            provider_id: Sawyer provider ID
            country: Provider's country (ISO code)

        Returns:
            ConnectOnboardingResult with onboarding URL

        Raises:
            ValueError: If provider not found
        """
        provider = self._manager.get_provider(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")

        # Create Stripe Connect Express account
        account = stripe.Account.create(
            type="express",
            country=country,
            email=provider.email,
            metadata={
                "sawyer_provider_id": provider_id,
                "sawyer_display_name": provider.display_name,
            },
            business_profile={
                "name": provider.display_name,
                "product_description": "Sawyer distributed MoE inference provider",
                "mcc": "5734",  # Computer software
            },
        )

        # Update provider with Stripe Connect ID
        provider.stripe_connect_id = account.id
        provider.status = ProviderStatus.ONBOARDING
        provider.updated_at = time.time()

        # Create onboarding link
        account_link = stripe.AccountLink.create(
            account=account.id,
            refresh_url=f"https://sawyer.infill.systems/provider/onboarding?refresh={provider_id}",
            return_url=f"https://sawyer.infill.systems/provider/onboarding/complete?provider={provider_id}",
            type="account_onboarding",
        )

        logger.info(
            "Created Stripe Connect account %s for provider %s",
            account.id,
            provider_id,
        )

        return ConnectOnboardingResult(
            url=account_link.url,
            account_id=account.id,
            expires_at=time.time() + 3600,  # Link valid for 1 hour
            provider_id=provider_id,
        )

    def check_verification_status(self, provider_id: str) -> dict[str, Any]:
        """Check a provider's Stripe Connect verification status.

        Returns:
            Dict with verification requirements and status
        """
        provider = self._manager.get_provider(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")

        if not provider.stripe_connect_id:
            return {
                "connected": False,
                "verified": False,
                "requirements": ["Complete Stripe Connect onboarding"],
            }

        account = stripe.Account.retrieve(provider.stripe_connect_id)

        # Check if account is fully verified
        requirements = account.get("requirements", {})
        currently_due = requirements.get("currently_due", [])
        eventually_due = requirements.get("eventually_due", [])

        is_verified = (
            len(currently_due) == 0
            and len(eventually_due) == 0
            and account.get("charges_enabled", False)
        )

        if is_verified and not provider.stripe_account_verified:
            self._manager.verify_provider(
                provider_id, stripe_connect_id=provider.stripe_connect_id
            )

        return {
            "connected": True,
            "verified": is_verified,
            "charges_enabled": account.get("charges_enabled", False),
            "payouts_enabled": account.get("payouts_enabled", False),
            "currently_due": currently_due,
            "eventually_due": eventually_due,
            "country": account.get("country", ""),
        }

    def create_dashboard_link(self, provider_id: str) -> str:
        """Create a Stripe Express dashboard link for a provider.

        Providers use this to view earnings, update bank details, etc.
        """
        provider = self._manager.get_provider(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")

        if not provider.stripe_connect_id:
            raise ValueError(f"Provider {provider_id} has not completed onboarding")

        link = stripe.Account.create_login_link(
            account=provider.stripe_connect_id,
        )
        return link.url

    # ── Payouts ────────────────────────────────────────────────────

    def transfer_payout(
        self, provider_id: str, amount_usd: float | None = None
    ) -> PayoutTransferResult | None:
        """Transfer earnings to a provider's connected bank account.

        Args:
            provider_id: Provider to pay
            amount_usd: Specific amount to transfer (None = full balance)

        Returns:
            PayoutTransferResult if successful, None if not eligible
        """
        provider = self._manager.get_provider(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")

        if not provider.stripe_account_verified:
            raise ValueError(f"Provider {provider_id} Stripe account not verified")

        if not provider.stripe_connect_id:
            raise ValueError(f"Provider {provider_id} has no Stripe Connect account")

        # Determine amount
        transfer_amount = amount_usd or provider.available_balance
        if transfer_amount < provider.min_payout_usd:
            logger.info(
                "Provider %s below minimum payout ($%.2f < $%.2f)",
                provider_id,
                transfer_amount,
                provider.min_payout_usd,
            )
            return None

        # Create Stripe transfer
        transfer = stripe.Transfer.create(
            amount=int(transfer_amount * 100),  # Convert to cents
            currency="usd",
            destination=provider.stripe_connect_id,
            metadata={
                "sawyer_provider_id": provider_id,
                "sawyer_payout_type": provider.payout_schedule.value,
            },
            description=f"Sawyer provider earnings - {provider.payout_schedule.value}",
        )

        # Record payout
        payout = self._manager.process_payout(provider_id, stripe_transfer_id=transfer.id)

        if payout:
            self._manager.mark_payout_paid(payout.payout_id, stripe_transfer_id=transfer.id)

        logger.info(
            "Transferred $%.2f to provider %s (stripe: %s)",
            transfer_amount,
            provider_id,
            transfer.id,
        )

        return PayoutTransferResult(
            transfer_id=transfer.id,
            amount_usd=transfer_amount,
            provider_id=provider_id,
            status=transfer.status,
            destination=provider.stripe_connect_id[-4:],
        )

    def process_scheduled_payouts(self) -> list[PayoutTransferResult]:
        """Process all eligible payouts based on their schedule.

        Called by a cron job at the start of each month (monthly)
        or quarter (quarterly).

        Returns:
            List of PayoutTransferResults for successful transfers
        """

        now = time.time()
        results = []

        for provider in self._manager.list_providers(status=ProviderStatus.ACTIVE):
            # Check if payout is due based on schedule
            if not provider.is_eligible_for_payout:
                continue

            # Monthly payouts at start of month, quarterly at start of quarter
            if (
                provider.payout_schedule == PayoutSchedule.MONTHLY
                and provider.last_payout_at
                and (now - provider.last_payout_at) < 86400 * 28
            ):
                continue

            if (
                provider.payout_schedule == PayoutSchedule.QUARTERLY
                and provider.last_payout_at
                and (now - provider.last_payout_at) < 86400 * 88
            ):
                continue

            try:
                result = self.transfer_payout(provider.provider_id)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(
                    "Failed to process payout for %s: %s",
                    provider.provider_id,
                    e,
                )

        logger.info("Processed %d scheduled payouts", len(results))
        return results

    # ── Balance ────────────────────────────────────────────────────

    def get_stripe_balance(self, provider_id: str) -> dict[str, Any]:
        """Get a provider's Stripe Connect balance."""
        provider = self._manager.get_provider(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")

        if not provider.stripe_connect_id:
            return {"available": 0, "pending": 0}

        balance = stripe.Balance.retrieve(
            stripe_account=provider.stripe_connect_id,
        )

        available = (
            sum(b["amount"] for b in balance.get("available", []) if b.get("currency") == "usd")
            / 100.0
        )

        pending = (
            sum(b["amount"] for b in balance.get("pending", []) if b.get("currency") == "usd")
            / 100.0
        )

        return {
            "available_usd": available,
            "pending_usd": pending,
        }
