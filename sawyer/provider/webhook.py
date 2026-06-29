"""Sawyer Provider Stripe Webhook Handler.

Processes Stripe Connect webhook events for provider accounts:
- account.updated: KYC/verification status changes
- transfer.created: Payout transfer initiated
- transfer.failed: Payout transfer failed
- payment_intent.succeeded: Provider received funds

Uses Stripe webhook signature verification for security.
"""

import logging
from typing import Any

import stripe

from sawyer.provider.manager import ProviderManager

logger = logging.getLogger(__name__)


class ProviderWebhookHandler:
    """Handle Stripe Connect webhook events for provider accounts.

    Verifies webhook signatures and dispatches events to the
    appropriate handler method. Updates provider status in the
    ProviderManager based on Stripe events.
    """

    def __init__(
        self,
        provider_manager: ProviderManager,
        webhook_secret: str,
    ) -> None:
        self._mgr = provider_manager
        self._webhook_secret = webhook_secret

    def handle(
        self,
        payload: str | bytes,
        sig_header: str,
    ) -> dict[str, Any]:
        """Verify and process a Stripe Connect webhook event.

        Args:
            payload: Raw request body from Stripe
            sig_header: Stripe-Signature header value

        Returns:
            Parsed event dict with processing result

        Raises:
            stripe.SignatureVerificationError: Invalid webhook signature
            ValueError: Unknown event type
        """
        event = stripe.Webhook.construct_event(payload, sig_header, self._webhook_secret)

        event_type = event["type"]
        logger.info("Processing Connect webhook: %s", event_type)

        handlers = {
            "account.updated": self._handle_account_updated,
            "transfer.created": self._handle_transfer_created,
            "transfer.failed": self._handle_transfer_failed,
            "payment_intent.succeeded": self._handle_payment_succeeded,
        }

        handler = handlers.get(event_type)
        if handler:
            result = handler(event)
        else:
            logger.debug("Unhandled Connect webhook: %s", event_type)
            result = {"status": "unhandled", "event_type": event_type}

        return {"event_type": event_type, **result}

    def _find_provider_by_stripe_id(self, stripe_account_id: str) -> Any | None:
        """Look up a provider by their Stripe Connect account ID."""
        for provider in self._mgr.list_providers():
            if provider.stripe_connect_id == stripe_account_id:
                return provider
        return None

    def _handle_account_updated(self, event: dict) -> dict[str, Any]:
        """Handle account.updated — KYC/verification changes.

        Stripe sends this when a Connect account's verification
        status changes (e.g., after completing onboarding, or
        when additional verification is required).
        """
        account = event["data"]["object"]
        stripe_account_id = account["id"]
        charges_enabled = account.get("charges_enabled", False)
        payouts_enabled = account.get("payouts_enabled", False)

        requirements = account.get("requirements", {})
        currently_due = requirements.get("currently_due", [])
        eventually_due = requirements.get("eventually_due", [])

        provider = self._find_provider_by_stripe_id(stripe_account_id)
        if provider is None:
            logger.warning(
                "account.updated for unknown Stripe account: %s",
                stripe_account_id,
            )
            return {
                "status": "unknown_account",
                "stripe_account_id": stripe_account_id,
            }

        # Determine verification status
        if charges_enabled and payouts_enabled:
            # Fully verified — can receive payouts
            if provider.status.value in ("pending", "onboarding", "verified"):
                self._mgr.activate_provider(provider.provider_id)
            logger.info(
                "Provider %s fully verified (Stripe: %s)",
                provider.provider_id,
                stripe_account_id,
            )
            verified = True
        elif len(currently_due) == 0 and len(eventually_due) == 0:
            # No outstanding requirements — pending but okay
            if provider.status.value == "pending":
                self._mgr.verify_provider(
                    provider.provider_id,
                    stripe_connect_id=stripe_account_id,
                )
            logger.info(
                "Provider %s pending verification (Stripe: %s)",
                provider.provider_id,
                stripe_account_id,
            )
            verified = False
        else:
            # Additional verification needed
            logger.info(
                "Provider %s needs more verification: due=%s",
                provider.provider_id,
                currently_due,
            )
            verified = False

        return {
            "status": "processed",
            "provider_id": provider.provider_id,
            "charges_enabled": charges_enabled,
            "payouts_enabled": payouts_enabled,
            "verified": verified,
            "requirements_currently_due": currently_due,
        }

    def _handle_transfer_created(self, event: dict) -> dict[str, Any]:
        """Handle transfer.created — payout transfer initiated.

        Stripe sends this when a transfer to a connected account
        is created. This is the start of the payout flow.
        """
        transfer = event["data"]["object"]
        transfer_id = transfer["id"]
        amount_cents = transfer["amount"]
        destination = transfer["destination"]

        provider = self._find_provider_by_stripe_id(destination)
        if provider is None:
            logger.warning(
                "transfer.created for unknown account: %s",
                destination,
            )
            return {
                "status": "unknown_account",
                "transfer_id": transfer_id,
            }

        amount_usd = amount_cents / 100.0
        logger.info(
            "Transfer created: %s → provider %s ($%.2f)",
            transfer_id,
            provider.provider_id,
            amount_usd,
        )

        return {
            "status": "processed",
            "provider_id": provider.provider_id,
            "transfer_id": transfer_id,
            "amount_usd": amount_usd,
        }

    def _handle_transfer_failed(self, event: dict) -> dict[str, Any]:
        """Handle transfer.failed — payout transfer failed.

        Stripe sends this when a transfer to a connected account
        fails. The provider should be notified and the payout
        marked as failed.
        """
        transfer = event["data"]["object"]
        transfer_id = transfer["id"]
        amount_cents = transfer["amount"]
        destination = transfer["destination"]
        failure_code = transfer.get("failure_code", "unknown")
        failure_message = transfer.get("failure_message", "")

        provider = self._find_provider_by_stripe_id(destination)
        if provider is None:
            logger.warning(
                "transfer.failed for unknown account: %s",
                destination,
            )
            return {
                "status": "unknown_account",
                "transfer_id": transfer_id,
            }

        amount_usd = amount_cents / 100.0
        logger.error(
            "Transfer failed: %s → provider %s ($%.2f) — %s: %s",
            transfer_id,
            provider.provider_id,
            amount_usd,
            failure_code,
            failure_message,
        )

        # Mark pending payouts as failed for this provider
        for payout in self._mgr.get_payout_history(provider.provider_id):
            if payout.status.value == "pending":
                self._mgr.mark_payout_failed(payout.payout_id)
                logger.info(
                    "Marked payout %s as failed (transfer: %s)",
                    payout.payout_id,
                    transfer_id,
                )

        return {
            "status": "processed",
            "provider_id": provider.provider_id,
            "transfer_id": transfer_id,
            "amount_usd": amount_usd,
            "failure_code": failure_code,
        }

    def _handle_payment_succeeded(self, event: dict) -> dict[str, Any]:
        """Handle payment_intent.succeeded — provider received funds.

        Confirms that a payment to a connected account has
        succeeded. This is the final confirmation that the
        provider has been paid.
        """
        payment_intent = event["data"]["object"]
        pi_id = payment_intent["id"]
        amount_cents = payment_intent["amount"]
        transfer_group = payment_intent.get("transfer_group", "")

        # Find the connected account from the transfer data
        transfer_data = payment_intent.get("transfer_data", {})
        destination = transfer_data.get("destination", "")

        if not destination:
            logger.debug(
                "payment_intent.succeeded with no transfer_data: %s",
                pi_id,
            )
            return {"status": "no_transfer_data", "payment_intent_id": pi_id}

        provider = self._find_provider_by_stripe_id(destination)
        if provider is None:
            logger.warning(
                "payment_intent.succeeded for unknown account: %s",
                destination,
            )
            return {
                "status": "unknown_account",
                "payment_intent_id": pi_id,
            }

        amount_usd = amount_cents / 100.0
        logger.info(
            "Payment confirmed: %s → provider %s ($%.2f, group: %s)",
            pi_id,
            provider.provider_id,
            amount_usd,
            transfer_group,
        )

        return {
            "status": "processed",
            "provider_id": provider.provider_id,
            "payment_intent_id": pi_id,
            "amount_usd": amount_usd,
        }
