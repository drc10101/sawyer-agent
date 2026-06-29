"""Tests for Sawyer Provider — registration, earnings, and payouts."""

import pytest

from sawyer.provider.manager import (
    MIN_PAYOUT_USD_QUARTERLY,
    PayoutSchedule,
    PayoutStatus,
    ProviderManager,
    ProviderStatus,
)


class TestProviderRegistration:
    """Test provider registration."""

    def setup_method(self):
        self.mgr = ProviderManager()

    def test_register_provider(self):
        """Register a new provider."""
        provider = self.mgr.register(
            email="alice@example.com",
            display_name="Alice Compute",
            legal_name="Alice Smith",
        )
        assert provider.provider_id.startswith("prov_")
        assert provider.email == "alice@example.com"
        assert provider.display_name == "Alice Compute"
        assert provider.legal_name == "Alice Smith"
        assert provider.status == ProviderStatus.PENDING

    def test_register_with_payout_schedule(self):
        """Register with quarterly payout schedule."""
        provider = self.mgr.register(
            email="bob@example.com",
            display_name="Bob Servers",
            payout_schedule=PayoutSchedule.QUARTERLY,
        )
        assert provider.payout_schedule == PayoutSchedule.QUARTERLY
        assert provider.min_payout_usd == MIN_PAYOUT_USD_QUARTERLY

    def test_find_by_email(self):
        """Find provider by email."""
        self.mgr.register(email="carol@example.com", display_name="Carol")
        found = self.mgr.find_provider_by_email("carol@example.com")
        assert found is not None
        assert found.display_name == "Carol"

    def test_find_by_email_not_found(self):
        """Finding non-existent email returns None."""
        assert self.mgr.find_provider_by_email("nobody@example.com") is None

    def test_list_providers_by_status(self):
        """List providers filtered by status."""
        p1 = self.mgr.register(email="a@a.com", display_name="A")
        p2 = self.mgr.register(email="b@b.com", display_name="B")
        self.mgr.verify_provider(p1.provider_id, "acct_stripe1")

        pending = self.mgr.list_providers(status=ProviderStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].provider_id == p2.provider_id

        verified = self.mgr.list_providers(status=ProviderStatus.VERIFIED)
        assert len(verified) == 1


class TestProviderNodeAssociation:
    """Test provider-node association."""

    def setup_method(self):
        self.mgr = ProviderManager()
        self.provider = self.mgr.register(email="host@example.com", display_name="Host Co")

    def test_add_node(self):
        """Associate a node with a provider."""
        self.mgr.add_node(self.provider.provider_id, "node-1")
        assert "node-1" in self.provider.node_ids

    def test_add_duplicate_node(self):
        """Adding same node twice doesn't duplicate."""
        self.mgr.add_node(self.provider.provider_id, "node-1")
        self.mgr.add_node(self.provider.provider_id, "node-1")
        assert self.provider.node_ids.count("node-1") == 1

    def test_remove_node(self):
        """Remove a node from a provider."""
        self.mgr.add_node(self.provider.provider_id, "node-1")
        self.mgr.remove_node(self.provider.provider_id, "node-1")
        assert "node-1" not in self.provider.node_ids

    def test_find_provider_by_node(self):
        """Find provider that owns a node."""
        self.mgr.add_node(self.provider.provider_id, "node-42")
        found = self.mgr.find_provider_by_node("node-42")
        assert found is not None
        assert found.provider_id == self.provider.provider_id


class TestProviderEarnings:
    """Test provider earnings tracking."""

    def setup_method(self):
        self.mgr = ProviderManager()
        self.provider = self.mgr.register(email="earner@example.com", display_name="EarnCo")
        self.mgr.verify_provider(self.provider.provider_id, "acct_verified")

    def test_credit_earnings(self):
        """Credit provider for tokens served."""
        earned = self.mgr.credit_earnings(self.provider.provider_id, tokens_served=500_000)
        # 500K / 1K = 500 * $0.002 = $1.00 gross * 70% = $0.70
        assert abs(earned - 0.70) < 0.01
        assert abs(self.provider.total_usd_earned - 0.70) < 0.01
        assert self.provider.total_tokens_served == 500_000

    def test_credit_earnings_accumulates(self):
        """Multiple credits accumulate."""
        self.mgr.credit_earnings(self.provider.provider_id, 100_000)
        self.mgr.credit_earnings(self.provider.provider_id, 200_000)
        # 300K / 1K = 300 * $0.002 = $0.60 gross * 70% = $0.42
        assert abs(self.provider.total_usd_earned - 0.42) < 0.01

    def test_available_balance(self):
        """Available balance = earned - paid - pending."""
        self.mgr.credit_earnings(self.provider.provider_id, 1_000_000)
        # 1M / 1K = 1000 * $0.002 = $2.00 gross * 70% = $1.40 earned
        self.provider.total_usd_paid = 1.00
        # Available = $1.40 - $1.00 = $0.40
        assert abs(self.provider.available_balance - 0.40) < 0.01


class TestProviderPayouts:
    """Test payout processing."""

    def setup_method(self):
        self.mgr = ProviderManager()
        self.provider = self.mgr.register(email="payer@example.com", display_name="PayCo")
        self.mgr.verify_provider(self.provider.provider_id, "acct_verified")

    def test_payout_eligible(self):
        """Provider is eligible for payout after earning enough."""
        # Need >= $10 available. 15M tokens = $30 gross * 70% = $21
        self.mgr.credit_earnings(self.provider.provider_id, tokens_served=15_000_000)
        assert self.provider.is_eligible_for_payout

    def test_payout_not_eligible_insufficient_balance(self):
        """Provider with small balance is not eligible."""
        # 100K tokens = $0.20 gross * 70% = $0.14 — below $10 minimum
        self.mgr.credit_earnings(self.provider.provider_id, tokens_served=100_000)
        assert not self.provider.is_eligible_for_payout

    def test_process_payout(self):
        """Process a payout for an eligible provider."""
        # 15M tokens = $30 gross * 70% = $21, well above $10 min
        self.mgr.credit_earnings(self.provider.provider_id, tokens_served=15_000_000)
        payout = self.mgr.process_payout(self.provider.provider_id)
        assert payout is not None
        assert payout.amount_usd > 0
        assert payout.status == PayoutStatus.PENDING
        assert payout.provider_id == self.provider.provider_id

    def test_payout_not_eligible_returns_none(self):
        """Processing payout for ineligible provider returns None."""
        # 50K tokens = $0.10 gross * 70% = $0.07 — way below $10
        self.mgr.credit_earnings(self.provider.provider_id, tokens_served=50_000)
        payout = self.mgr.process_payout(self.provider.provider_id)
        assert payout is None

    def test_payout_history(self):
        """Get payout history for a provider."""
        self.mgr.credit_earnings(self.provider.provider_id, tokens_served=15_000_000)
        self.mgr.process_payout(self.provider.provider_id)
        history = self.mgr.get_payout_history(self.provider.provider_id)
        assert len(history) >= 1

    def test_mark_payout_failed(self):
        """Mark a payout as failed releases pending amount."""
        self.mgr.credit_earnings(self.provider.provider_id, tokens_served=15_000_000)
        payout = self.mgr.process_payout(self.provider.provider_id)
        assert payout is not None

        failed = self.mgr.mark_payout_failed(payout.payout_id, reason="bank rejected")
        assert failed is not None
        assert failed.status == PayoutStatus.FAILED
        assert failed.failure_reason == "bank rejected"

    def test_get_pending_payouts(self):
        """Get all pending payouts."""
        self.mgr.credit_earnings(self.provider.provider_id, tokens_served=15_000_000)
        # Process creates PENDING payout (Stripe confirms later)
        self.mgr.process_payout(self.provider.provider_id)
        pending = self.mgr.get_pending_payouts()
        assert len(pending) == 1


class TestProviderStatusTransitions:
    """Test provider status transitions."""

    def setup_method(self):
        self.mgr = ProviderManager()

    def test_register_starts_pending(self):
        """New providers start as PENDING."""
        provider = self.mgr.register(email="new@example.com", display_name="New")
        assert provider.status == ProviderStatus.PENDING

    def test_verify_transitions_to_verified(self):
        """Verification transitions to VERIFIED."""
        provider = self.mgr.register(email="verify@example.com", display_name="Verify")
        self.mgr.verify_provider(provider.provider_id, "acct_123")
        assert provider.status == ProviderStatus.VERIFIED
        assert provider.stripe_account_verified is True
        assert provider.stripe_connect_id == "acct_123"

    def test_activate_verified_provider(self):
        """Activating a verified provider transitions to ACTIVE."""
        provider = self.mgr.register(email="active@example.com", display_name="Active")
        self.mgr.verify_provider(provider.provider_id)
        self.mgr.activate_provider(provider.provider_id)
        assert provider.status == ProviderStatus.ACTIVE

    def test_activate_pending_provider_raises(self):
        """Activating a PENDING provider raises."""
        provider = self.mgr.register(email="pending@example.com", display_name="Pending")
        with pytest.raises(ValueError, match="verified before activation"):
            self.mgr.activate_provider(provider.provider_id)

    def test_suspend_provider(self):
        """Suspending a provider."""
        provider = self.mgr.register(email="suspend@example.com", display_name="Suspend")
        self.mgr.suspend_provider(provider.provider_id, reason="abuse")
        assert provider.status == ProviderStatus.SUSPENDED

    def test_offboard_provider(self):
        """Offboarding a provider."""
        provider = self.mgr.register(email="off@example.com", display_name="Offboard")
        self.mgr.offboard_provider(provider.provider_id)
        assert provider.status == ProviderStatus.OFFBOARDED


class TestProviderSummary:
    """Test provider and network summaries."""

    def setup_method(self):
        self.mgr = ProviderManager()

    def test_provider_summary(self):
        """Get a provider's summary."""
        provider = self.mgr.register(email="summary@example.com", display_name="SummaryCo")
        self.mgr.verify_provider(provider.provider_id, "acct_summary")
        self.mgr.credit_earnings(provider.provider_id, 1_000_000)

        summary = self.mgr.get_provider_summary(provider.provider_id)
        assert summary["provider_id"] == provider.provider_id
        assert summary["status"] == "verified"
        assert summary["earnings"]["total_usd_earned"] > 0

    def test_network_summary(self):
        """Get aggregate network summary."""
        p1 = self.mgr.register(email="a@a.com", display_name="A")
        p2 = self.mgr.register(email="b@b.com", display_name="B")
        self.mgr.verify_provider(p1.provider_id, "acct_1")
        self.mgr.activate_provider(p1.provider_id)
        self.mgr.credit_earnings(p1.provider_id, 500_000)
        self.mgr.credit_earnings(p2.provider_id, 200_000)

        summary = self.mgr.get_network_summary()
        assert summary["total_providers"] == 2
        assert summary["active_providers"] == 1
        assert summary["total_tokens_served"] == 700_000
