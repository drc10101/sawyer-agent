"""Tests for Sawyer Gating Network — expert selection and fallback."""

import pytest

from sawyer.model.registry import MODELS, MoEModel
from sawyer.router.gating import GatingDecision, GatingMode, GatingNetwork
from sawyer.router.scheduler import ExpertScheduler, NodeInfo, RoutingStrategy


# --- Fixtures ---

@pytest.fixture
def mixtral():
    return MODELS["mixtral-8x7b"]


@pytest.fixture
def deepseek():
    return MODELS["deepseek-v2-lite"]


@pytest.fixture
def gating():
    return GatingNetwork(seed=42)


@pytest.fixture
def scheduler_with_nodes():
    """Scheduler with 4 nodes hosting different Mixtral experts."""
    s = ExpertScheduler(strategy=RoutingStrategy.ADAPTIVE)
    nodes = [
        NodeInfo(
            node_id="node-0",
            experts=[0, 1],
            gpu="RTX 4090",
            vram_gb=24.0,
            bandwidth_mbps=1000,
            latency_ms=5.0,
        ),
        NodeInfo(
            node_id="node-1",
            experts=[2, 3],
            gpu="RTX 3090",
            vram_gb=24.0,
            bandwidth_mbps=500,
            latency_ms=10.0,
        ),
        NodeInfo(
            node_id="node-2",
            experts=[4, 5],
            gpu="A100",
            vram_gb=40.0,
            bandwidth_mbps=2000,
            latency_ms=2.0,
        ),
        NodeInfo(
            node_id="node-3",
            experts=[6, 7],
            gpu="RTX 4090",
            vram_gb=24.0,
            bandwidth_mbps=1000,
            latency_ms=5.0,
        ),
        # Redundant node hosting experts 0, 2, 4 for fallback
        NodeInfo(
            node_id="node-redundant",
            experts=[0, 2, 4],
            gpu="RTX 3080",
            vram_gb=10.0,
            bandwidth_mbps=300,
            latency_ms=20.0,
        ),
    ]
    for node in nodes:
        s.register_node(node)
    return s


# --- GatingNetwork tests ---

class TestGatingNetwork:
    def test_top_k_selection(self, gating, mixtral):
        """Top-K gating selects exactly active_experts experts."""
        decision = gating.select_experts(mixtral)
        assert decision.mode == GatingMode.TOP_K
        assert len(decision.expert_ids) == mixtral.active_experts  # 2 for Mixtral
        assert all(0 <= eid < mixtral.num_experts for eid in decision.expert_ids)

    def test_top_k_deterministic_with_same_input(self, gating, mixtral):
        """Same input tokens produce the same expert selection."""
        tokens = [1, 2, 3, 4, 5]
        d1 = gating.select_experts(mixtral, input_tokens=tokens, user_id="alice")
        d2 = gating.select_experts(mixtral, input_tokens=tokens, user_id="alice")
        assert d1.expert_ids == d2.expert_ids

    def test_top_k_different_for_different_users(self, gating, mixtral):
        """Different users may get different expert selections."""
        tokens = [1, 2, 3]
        d1 = gating.select_experts(mixtral, input_tokens=tokens, user_id="alice")
        d2 = gating.select_experts(mixtral, input_tokens=tokens, user_id="bob")
        # Not guaranteed to be different, but the seed is different
        # This just tests it doesn't crash
        assert len(d1.expert_ids) == mixtral.active_experts
        assert len(d2.expert_ids) == mixtral.active_experts

    def test_shared_plus_top_k(self, gating, deepseek):
        """Shared+Top-K gating includes shared experts + routed experts."""
        decision = gating.select_experts(deepseek)
        assert decision.mode == GatingMode.SHARED_PLUS_TOP_K
        # Should have shared experts (always active) + routed experts
        assert len(decision.expert_ids) > deepseek.active_experts
        # All expert IDs should be valid
        assert all(0 <= eid < deepseek.num_experts for eid in decision.expert_ids)
        # Scores for shared experts should be 1.0
        shared_count = max(1, deepseek.num_experts // 16)
        for i in range(shared_count):
            assert decision.scores.get(i) == 1.0

    def test_manual_override(self, gating, mixtral):
        """Manual mode lets you force specific experts."""
        gating.set_manual("mixtral-8x7b", [3, 7])
        decision = gating.select_experts(mixtral)
        assert decision.mode == GatingMode.MANUAL
        assert decision.expert_ids == [3, 7]

    def test_no_tokens_round_robin(self, gating, mixtral):
        """Without input tokens, gating still produces a valid selection."""
        decision = gating.select_experts(mixtral, input_tokens=None)
        assert len(decision.expert_ids) == mixtral.active_experts

    def test_scores_decrease_with_rank(self, gating, mixtral):
        """Top-K scores should decrease (rank 1 > rank 2)."""
        tokens = [10, 20, 30, 40]
        decision = gating.select_experts(mixtral, input_tokens=tokens, user_id="test")
        scores_list = [decision.scores[eid] for eid in decision.expert_ids]
        for i in range(len(scores_list) - 1):
            assert scores_list[i] >= scores_list[i + 1]


# --- Fallback tests ---

class TestGatingFallback:
    def test_fallback_replaces_failed_experts(self, gating, mixtral):
        """fallback_experts replaces failed IDs with alternatives."""
        original = [0, 1]
        failed = [0]
        revised = gating.fallback_experts(mixtral, failed, original)
        # Original expert 0 should be replaced
        assert 0 not in revised
        # Should still have 2 experts
        assert len(revised) == 2

    def test_fallback_no_duplicates(self, gating, mixtral):
        """Fallback experts should not duplicate remaining experts."""
        original = [0, 1]
        failed = [0]
        revised = gating.fallback_experts(mixtral, failed, original)
        # No duplicates
        assert len(revised) == len(set(revised))

    def test_fallback_all_experts_failed(self, gating, mixtral):
        """If all original experts fail, we still get alternatives."""
        original = [0, 1]
        failed = [0, 1]
        revised = gating.fallback_experts(mixtral, failed, original)
        # Should pick from the remaining experts
        assert all(eid not in failed for eid in revised)
        assert all(0 <= eid < mixtral.num_experts for eid in revised)


# --- Scheduler with gating tests ---

class TestSchedulerWithGating:
    def test_route_with_gating(self, scheduler_with_nodes, mixtral):
        """route_with_gating produces a valid routing plan."""
        routing = asyncio_run(
            scheduler_with_nodes.route_with_gating(
                model_name="mixtral-8x7b",
                input_tokens=[1, 2, 3, 4, 5],
                user_id="alice",
                request_id="test-001",
            )
        )
        assert routing["model"] == "mixtral-8x7b"
        assert routing["status"] in ("routed", "routed_with_fallbacks")
        assert len(routing["experts_routed"]) > 0
        assert len(routing["nodes_used"]) > 0
        assert "gating" in routing
        assert routing["gating"]["mode"] in ("top_k", "shared")

    def test_route_with_gating_fallback(self, scheduler_with_nodes, mixtral):
        """When a node fails, route_with_gating falls back to alternatives."""
        # Mark node-0 as failed
        scheduler_with_nodes.mark_node_failed("node-0")

        routing = asyncio_run(
            scheduler_with_nodes.route_with_gating(
                model_name="mixtral-8x7b",
                input_tokens=[1, 2, 3],
                user_id="bob",
                request_id="test-002",
            )
        )
        # Should still route, potentially with fallbacks
        assert routing["status"] in ("routed", "routed_with_fallbacks", "routed_with_fallbacks")

    def test_select_node_with_fallback(self, scheduler_with_nodes):
        """select_node_with_fallback prefers non-failed nodes."""
        # Expert 0 is hosted by node-0 and node-redundant
        node_primary, fellback = scheduler_with_nodes.select_node_with_fallback(
            expert_id=0,
            model=MODELS["mixtral-8x7b"],
        )
        assert node_primary is not None
        assert fellback is False

        # Mark primary as failed
        scheduler_with_nodes.mark_node_failed("node-0")
        node_fallback, fellback = scheduler_with_nodes.select_node_with_fallback(
            expert_id=0,
            model=MODELS["mixtral-8x7b"],
        )
        assert node_fallback is not None
        # Should route to node-redundant instead
        assert node_fallback.node_id == "node-redundant"

    def test_auto_assign_experts(self, scheduler_with_nodes, mixtral):
        """auto_assign_experts distributes experts via LocalRouter."""
        from sawyer.router.local_router import LocalRouter
        router = LocalRouter()
        # Copy nodes from scheduler to local router
        for node in scheduler_with_nodes.nodes.values():
            router.add_local_node(
                node_id=node.node_id,
                gpu=node.gpu,
                vram_gb=node.vram_gb,
                experts=node.experts,
                latency_ms=node.latency_ms,
            )
        assignments = router.auto_assign_experts("mixtral-8x7b")
        # All 8 experts should be assigned
        total_assigned = sum(len(eids) for eids in assignments.values())
        assert total_assigned == mixtral.num_experts


# --- LocalRouter tests ---

class TestLocalRouter:
    def test_create_local_router(self):
        from sawyer.router.local_router import LocalRouter
        router = LocalRouter()
        assert not router._running
        status = router.get_status()
        assert not status.running
        assert status.total_requests == 0

    def test_add_and_remove_nodes(self):
        from sawyer.router.local_router import LocalRouter
        router = LocalRouter()
        node = router.add_local_node(
            node_id="test-node",
            gpu="RTX 4090",
            vram_gb=24.0,
            experts=[0, 1, 2, 3],
        )
        assert node.node_id == "test-node"
        assert node.experts == [0, 1, 2, 3]
        assert "test-node" in router.scheduler.nodes

        router.remove_local_node("test-node")
        assert "test-node" not in router.scheduler.nodes

    def test_infer_without_start_raises(self):
        from sawyer.router.local_router import LocalRouter
        router = LocalRouter()
        with pytest.raises(RuntimeError):
            asyncio_run(router.infer("mixtral-8x7b", "Hello"))

    def test_infer_with_local_nodes(self):
        from sawyer.router.local_router import LocalRouter
        router = LocalRouter()
        router.add_local_node("node-a", gpu="RTX 4090", vram_gb=24.0, experts=[0, 1])
        router.add_local_node("node-b", gpu="RTX 3090", vram_gb=24.0, experts=[2, 3])
        router.add_local_node("node-c", gpu="A100", vram_gb=40.0, experts=[4, 5])
        router.add_local_node("node-d", gpu="RTX 4090", vram_gb=24.0, experts=[6, 7])

        asyncio_run(router.start())
        result = asyncio_run(
            router.infer("mixtral-8x7b", "Hello, world!", input_tokens=[1, 2, 3])
        )
        assert result["status"] == "completed"
        assert result["model"] == "mixtral-8x7b"
        assert len(result["routing"]["experts_routed"]) > 0
        asyncio_run(router.stop())

    def test_routing_plan_preview(self):
        from sawyer.router.local_router import LocalRouter
        router = LocalRouter()
        router.add_local_node("node-a", gpu="RTX 4090", vram_gb=24.0, experts=[0, 1, 2, 3])
        router.add_local_node("node-b", gpu="RTX 3090", vram_gb=24.0, experts=[4, 5, 6, 7])

        plan = router.get_routing_plan("mixtral-8x7b")
        assert "node-a" in plan
        assert "node-b" in plan
        assert len(plan["node-a"]["experts"]) == 4
        assert len(plan["node-b"]["experts"]) == 4


def asyncio_run(coro):
    """Helper to run async functions in tests."""
    import asyncio
    return asyncio.run(coro)