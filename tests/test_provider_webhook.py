"""Tests for ProviderWebhookHandler — Stripe Connect webhook processing."""

from unittest.mock import patch

from sawyer.provider.manager import PayoutStatus, ProviderManager
from sawyer.provider.webhook import ProviderWebhookHandler


class TestWebhookHandler:
    """Test Stripe Connect webhook event handling."""

    def setup_method(self):
        self.mgr = ProviderManager()
        self.handler = ProviderWebhookHandler(
            provider_manager=self.mgr,
            webhook_secret="whsec_test_secret",
        )

        # Register and fully activate a provider
        self.provider = self.mgr.register(
            email="host@example.com", display_name="HostCo"
        )
        self.mgr.verify_provider(
            self.provider.provider_id,
            stripe_connect_id="acct_test_123",
        )
        self.mgr.activate_provider(self.provider.provider_id)

    def _make_event(self, event_type: str, obj: dict) -> dict:
        """Build a Stripe-style event dict."""
        return {
            "type": event_type,
            "data": {"object": obj},
        }

    def test_account_updated_fully_verified(self):
        """account.updated with charges+payouts enabled activates provider."""
        event = self._make_event(
            "account.updated",
            {
                "id": "acct_test_123",
                "charges_enabled": True,
                "payouts_enabled": True,
                "requirements": {
                    "currently_due": [],
                    "eventually_due": [],
                },
            },
        )

        result = self.handler._handle_account_updated(event)
        assert result["status"] == "processed"
        assert result["verified"] is True
        assert result["charges_enabled"] is True

        # Provider should be ACTIVE
        provider = self.mgr.get_provider(self.provider.provider_id)
        assert provider.status.value == "active"

    def test_account_updated_needs_more_verification(self):
        """account.updated with requirements due keeps provider pending."""
        event = self._make_event(
            "account.updated",
            {
                "id": "acct_test_123",
                "charges_enabled": False,
                "payouts_enabled": False,
                "requirements": {
                    "currently_due": ["individual.id_number"],
                    "eventually_due": ["tos_acceptance"],
                },
            },
        )

        result = self.handler._handle_account_updated(event)
        assert result["verified"] is False
        assert result["requirements_currently_due"] == ["individual.id_number"]

    def test_account_updated_unknown_account(self):
        """account.updated for unknown Stripe account returns unknown."""
        event = self._make_event(
            "account.updated",
            {
                "id": "acct_unknown",
                "charges_enabled": True,
                "payouts_enabled": True,
                "requirements": {"currently_due": [], "eventually_due": []},
            },
        )

        result = self.handler._handle_account_updated(event)
        assert result["status"] == "unknown_account"

    def test_transfer_created(self):
        """transfer.created logs the payout transfer."""
        event = self._make_event(
            "transfer.created",
            {
                "id": "tr_abc123",
                "amount": 2100,  # $21.00
                "destination": "acct_test_123",
            },
        )

        result = self.handler._handle_transfer_created(event)
        assert result["status"] == "processed"
        assert result["transfer_id"] == "tr_abc123"
        assert result["amount_usd"] == 21.00

    def test_transfer_created_unknown_account(self):
        """transfer.created for unknown account returns unknown."""
        event = self._make_event(
            "transfer.created",
            {
                "id": "tr_xyz",
                "amount": 5000,
                "destination": "acct_unknown",
            },
        )

        result = self.handler._handle_transfer_created(event)
        assert result["status"] == "unknown_account"

    def test_transfer_failed_marks_payout_failed(self):
        """transfer.failed marks pending payouts as failed and refunds."""
        # Credit earnings and create a pending payout
        self.mgr.credit_earnings(
            self.provider.provider_id, tokens_served=15_000_000
        )
        payout = self.mgr.process_payout(self.provider.provider_id)
        assert payout is not None
        assert payout.status == PayoutStatus.PENDING

        # Simulate transfer failed
        event = self._make_event(
            "transfer.failed",
            {
                "id": "tr_fail123",
                "amount": 1050,  # $10.50
                "destination": "acct_test_123",
                "failure_code": "insufficient_funds",
                "failure_message": "Insufficient funds in platform account",
            },
        )

        result = self.handler._handle_transfer_failed(event)
        assert result["status"] == "processed"
        assert result["failure_code"] == "insufficient_funds"

        # Payout should now be failed
        payouts = self.mgr.get_payout_history(self.provider.provider_id)
        failed = [p for p in payouts if p.status == PayoutStatus.FAILED]
        assert len(failed) >= 1

        # Provider should have been refunded
        provider = self.mgr.get_provider(self.provider.provider_id)
        assert provider.available_balance > 0

    def test_transfer_failed_unknown_account(self):
        """transfer.failed for unknown account returns unknown."""
        event = self._make_event(
            "transfer.failed",
            {
                "id": "tr_fail",
                "amount": 1000,
                "destination": "acct_unknown",
                "failure_code": "bank_account_rejected",
                "failure_message": "Rejected",
            },
        )

        result = self.handler._handle_transfer_failed(event)
        assert result["status"] == "unknown_account"

    def test_payment_succeeded(self):
        """payment_intent.succeeded confirms provider payment."""
        event = self._make_event(
            "payment_intent.succeeded",
            {
                "id": "pi_test123",
                "amount": 2100,
                "transfer_data": {
                    "destination": "acct_test_123",
                },
                "transfer_group": "GROUP_2024Q1",
            },
        )

        result = self.handler._handle_payment_succeeded(event)
        assert result["status"] == "processed"
        assert result["payment_intent_id"] == "pi_test123"
        assert result["amount_usd"] == 21.00

    def test_payment_succeeded_no_transfer_data(self):
        """payment_intent.succeeded with no transfer_data returns no_transfer."""
        event = self._make_event(
            "payment_intent.succeeded",
            {
                "id": "pi_notransfer",
                "amount": 1500,
                "transfer_group": "",
            },
        )

        result = self.handler._handle_payment_succeeded(event)
        assert result["status"] == "no_transfer_data"

    def test_payment_succeeded_unknown_account(self):
        """payment_intent.succeeded for unknown account returns unknown."""
        event = self._make_event(
            "payment_intent.succeeded",
            {
                "id": "pi_unknown",
                "amount": 1000,
                "transfer_data": {"destination": "acct_unknown"},
                "transfer_group": "GROUP_1",
            },
        )

        result = self.handler._handle_payment_succeeded(event)
        assert result["status"] == "unknown_account"

    @patch("sawyer.provider.webhook.stripe.Webhook.construct_event")
    def test_handle_dispatches_to_handler(self, mock_construct):
        """handle() verifies signature and dispatches to correct handler."""
        mock_construct.return_value = {
            "type": "account.updated",
            "data": {
                "object": {
                    "id": "acct_test_123",
                    "charges_enabled": True,
                    "payouts_enabled": True,
                    "requirements": {"currently_due": [], "eventually_due": []},
                }
            },
        }

        result = self.handler.handle(
            payload=b"test",
            sig_header="t=1,v1=sig",
        )

        assert result["event_type"] == "account.updated"
        assert result["status"] == "processed"
        mock_construct.assert_called_once_with(
            b"test", "t=1,v1=sig", "whsec_test_secret"
        )

    @patch("sawyer.provider.webhook.stripe.Webhook.construct_event")
    def test_handle_unhandled_event_type(self, mock_construct):
        """handle() returns unhandled for unknown event types."""
        mock_construct.return_value = {
            "type": "account.application.deauthorized",
            "data": {"object": {"id": "acct_test_123"}},
        }

        result = self.handler.handle(
            payload=b"test",
            sig_header="t=1,v1=sig",
        )

        assert result["status"] == "unhandled"
