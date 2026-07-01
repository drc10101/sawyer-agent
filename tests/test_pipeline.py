"""Tests for Sawyer Inference Pipeline — end-to-end distributed inference."""

import pytest

from sawyer.config import SawyerConfig
from sawyer.router.pipeline import InferencePipeline, InferenceRequest, InferenceResponse
from sawyer.router.scheduler import RoutingStrategy
from sawyer.token.budget import SubscriptionTier, TokenBalance


# --- Fixtures ---

@pytest.fixture
def config():
    return SawyerConfig(max_vram_gb=24.0, max_experts=8)


@pytest.fixture
def pipeline(config):
    """Pipeline with 4 nodes covering all 8 Mixtral experts."""
    p = InferencePipeline(config)
    p.add_local_node("node-a", gpu="RTX 4090", vram_gb=24.0, experts=[0, 1])
    p.add_local_node("node-b", gpu="RTX 3090", vram_gb=24.0, experts=[2, 3])
    p.add_local_node("node-c", gpu="A100", vram_gb=40.0, experts=[4, 5])
    p.add_local_node("node-d", gpu="RTX 4090", vram_gb=24.0, experts=[6, 7])
    return p


@pytest.fixture
def alice_balance():
    return TokenBalance(
        tier=SubscriptionTier.BUILDER,
        monthly_budget=2_000_000,
        current_balance=2_000_000,
    )


# --- Pipeline tests ---

class TestInferencePipeline:
    def test_init(self, pipeline):
        assert not pipeline._running
        assert pipeline.scheduler.strategy == RoutingStrategy.ADAPTIVE

    def test_add_and_remove_node(self, pipeline):
        node = pipeline.add_local_node("node-e", gpu="H100", vram_gb=80.0, experts=[0, 1, 2])
        assert "node-e" in pipeline.scheduler.nodes
        pipeline.remove_local_node("node-e")
        assert "node-e" not in pipeline.scheduler.nodes

    def test_set_token_balance(self, pipeline, alice_balance):
        pipeline.set_token_balance("alice", alice_balance)
        assert pipeline.get_user_balance("alice") is alice_balance

    def test_get_or_create_balance(self, pipeline):
        balance = pipeline.get_or_create_balance("bob", "builder")
        assert balance.tier == SubscriptionTier.BUILDER
        assert balance.current_balance == 2_000_000

        # Second call returns same balance
        same_balance = pipeline.get_or_create_balance("bob")
        assert same_balance is balance

    def test_infer_without_start_raises(self, pipeline):
        request = InferenceRequest(
            model_name="mixtral-8x7b",
            prompt="Hello",
            user_id="alice",
        )
        with pytest.raises(RuntimeError, match="not running"):
            import asyncio
            asyncio.run(pipeline.infer(request))

    def test_e2e_infer(self, pipeline, alice_balance):
        """Full end-to-end inference: balance, route, aggregate, debit."""
        pipeline.set_token_balance("alice", alice_balance)

        import asyncio
        asyncio.run(pipeline.start())

        request = InferenceRequest(
            model_name="mixtral-8x7b",
            prompt="Hello, world!",
            user_id="alice",
            input_tokens=[1, 2, 3, 4, 5],
        )

        response = asyncio.run(pipeline.infer(request))

        assert response.model_name == "mixtral-8x7b"
        assert response.status in ("completed", "partial")
        assert len(response.experts_used) > 0
        assert len(response.nodes_used) > 0
        assert response.tokens_remaining < alice_balance.monthly_budget  # Tokens were debited
        assert response.request_id.startswith("inf-")

        asyncio.run(pipeline.stop())

    def test_e2e_no_balance(self, pipeline):
        """Inference with no token balance gets default explorer tier."""
        import asyncio
        asyncio.run(pipeline.start())

        request = InferenceRequest(
            model_name="mixtral-8x7b",
            prompt="Hello",
            user_id="newuser",
        )
        response = asyncio.run(pipeline.infer(request))

        # Should succeed with auto-created balance
        assert response.status in ("completed", "partial")
        assert response.tokens_remaining >= 0

        asyncio.run(pipeline.stop())

    def test_e2e_zero_balance(self, pipeline):
        """Inference with zero balance should return failed status."""
        balance = TokenBalance(
            tier=SubscriptionTier.EXPLORER,
            monthly_budget=500_000,
            current_balance=0,
        )
        pipeline.set_token_balance("broke_user", balance)

        import asyncio
        asyncio.run(pipeline.start())

        request = InferenceRequest(
            model_name="mixtral-8x7b",
            prompt="Hello",
            user_id="broke_user",
        )
        response = asyncio.run(pipeline.infer(request))

        assert response.status == "failed"
        assert response.tokens_remaining == 0

        asyncio.run(pipeline.stop())

    def test_e2e_fallback_routing(self, pipeline, alice_balance):
        """Test that fallback routing works when a node is marked as failed."""
        pipeline.set_token_balance("alice", alice_balance)

        import asyncio
        asyncio.run(pipeline.start())

        # Add a redundant node for expert 0
        pipeline.add_local_node("node-redundant", gpu="RTX 3080", vram_gb=10.0, experts=[0, 2])

        # Mark node-a as failed (hosts experts 0 and 1)
        pipeline.scheduler.mark_node_failed("node-a")

        request = InferenceRequest(
            model_name="mixtral-8x7b",
            prompt="Test fallback",
            user_id="alice",
        )
        response = asyncio.run(pipeline.infer(request))

        # Should still route, potentially with fallbacks
        assert response.status in ("completed", "partial")
        assert len(response.nodes_used) > 0

        asyncio.run(pipeline.stop())

    def test_get_status(self, pipeline):
        """Pipeline status includes nodes, strategy, and request count."""
        import asyncio
        asyncio.run(pipeline.start())

        status = pipeline.get_status()
        assert status["running"] is True
        assert status["mode"] == "local"
        assert status["router"]["strategy"] == "adaptive"
        assert status["router"]["active_nodes"] == 4

        asyncio.run(pipeline.stop())

    def test_debit_tokens_per_request(self, pipeline, alice_balance):
        """Tokens are debited from the user's balance after each request."""
        pipeline.set_token_balance("alice", alice_balance)
        initial_balance = alice_balance.total_available

        import asyncio
        asyncio.run(pipeline.start())

        request = InferenceRequest(
            model_name="mixtral-8x7b",
            prompt="Hello!",
            user_id="alice",
        )
        response = asyncio.run(pipeline.infer(request))

        assert response.tokens_remaining < initial_balance
        assert response.total_tokens > 0

        # Check the balance was actually debited
        assert pipeline.get_user_balance("alice").total_available < initial_balance

        asyncio.run(pipeline.stop())

    def test_multiple_requests(self, pipeline, alice_balance):
        """Multiple requests track cumulative token usage."""
        pipeline.set_token_balance("alice", alice_balance)

        import asyncio
        asyncio.run(pipeline.start())

        for i in range(3):
            request = InferenceRequest(
                model_name="mixtral-8x7b",
                prompt=f"Request {i}",
                user_id="alice",
                max_tokens=100,
            )
            response = asyncio.run(pipeline.infer(request))
            assert response.status in ("completed", "partial")

        # Pipeline should track 3 total requests
        assert pipeline._total_requests == 3

        asyncio.run(pipeline.stop())

    def test_routing_plan_accessible(self, pipeline):
        """The routing plan can be inspected without running inference."""
        plan = pipeline._local_router.get_routing_plan("mixtral-8x7b")
        assert len(plan) > 0
        for node_id, info in plan.items():
            assert "experts" in info
            assert "gpu" in info