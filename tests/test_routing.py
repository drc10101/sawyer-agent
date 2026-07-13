"""Tests for Sawyer Harness multi-model routing."""

import asyncio

import pytest

from sawyer_harness.routing import (
    ModelRouter,
    ProviderEndpoint,
    ProviderHealth,
    RouteDecision,
    TaskType,
    create_default_router,
)


@pytest.fixture
def router():
    """Create a router with test providers."""
    providers = [
        ProviderEndpoint(
            name="sawyer",
            provider_type="sawyer",
            base_url="https://api.sawyernetwork.ai/v1",
            priority=0,
            model="sawyer-default",
        ),
        ProviderEndpoint(
            name="openai",
            provider_type="openai",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            priority=1,
        ),
        ProviderEndpoint(
            name="anthropic",
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            model="claude-sonnet-4-20250514",
            priority=2,
        ),
        ProviderEndpoint(
            name="local",
            provider_type="ollama",
            base_url="http://localhost:11434/v1",
            model="llama3",
            priority=3,
        ),
    ]
    return ModelRouter(providers)


def test_provider_endpoint_error_rate():
    """ProviderEndpoint error rate calculation."""
    ep = ProviderEndpoint(
        name="test",
        provider_type="openai",
        base_url="https://api.test.com/v1",
    )
    assert ep.error_rate == 0.0

    ep.total_requests = 10
    ep.total_errors = 2
    assert ep.error_rate == 0.2


def test_router_default_preferences(router):
    """Router builds default preferences sorted by priority."""
    # Sawyer should be first (priority 0)
    prefs = router._task_preferences[TaskType.DEFAULT]
    assert prefs[0] == "sawyer"
    assert prefs[1] == "openai"
    assert prefs[2] == "anthropic"
    assert prefs[3] == "local"


def test_router_set_task_preference(router):
    """Router allows custom task preferences."""
    router.set_task_preference(TaskType.CODE, ["openai", "sawyer", "local"])
    prefs = router._task_preferences[TaskType.CODE]
    assert prefs == ["openai", "sawyer", "local"]


def test_router_has_all_providers(router):
    """Router has all configured providers."""
    assert len(router.providers) == 4
    assert "sawyer" in router.providers
    assert "openai" in router.providers
    assert "anthropic" in router.providers
    assert "local" in router.providers


def test_record_request(router):
    """Recording requests updates provider stats."""
    router.record_request("openai", success=True, cost_cents=0.5)
    router.record_request("openai", success=True, cost_cents=0.5)
    router.record_request("openai", success=False)

    assert router.providers["openai"].total_requests == 3
    assert router.providers["openai"].total_errors == 1
    assert router.providers["openai"].total_cost_cents == 1.0


def test_record_request_unknown_provider(router):
    """Recording request for unknown provider is a no-op."""
    router.record_request("nonexistent", success=True)
    # Should not raise


def test_get_routing_stats(router):
    """Routing stats include all providers."""
    router.record_request("sawyer", success=True, cost_cents=0.3)
    stats = router.get_routing_stats()

    assert "sawyer" in stats
    assert stats["sawyer"]["total_requests"] == 1
    assert stats["sawyer"]["total_cost_cents"] == 0.3
    assert "openai" in stats
    assert "anthropic" in stats


def test_get_llm_config(router):
    """Converting a provider to LLMConfig preserves fields."""
    config = router.get_llm_config("openai")

    assert config.provider == "openai"
    assert config.model == "gpt-4o"
    assert config.base_url == "https://api.openai.com/v1"
    assert config.max_tokens == 4096
    assert config.temperature == 0.7


def test_route_with_healthy_provider(router):
    """Route selects the first healthy provider."""
    # Mark sawyer as healthy
    router.providers["sawyer"].health = ProviderHealth.HEALTHY
    router.providers["sawyer"].last_health_check = 9999999999  # Skip actual health check

    decision = asyncio.run(router.route("debug my code", TaskType.CODE))

    assert decision.provider.name == "sawyer"
    assert decision.task_type == TaskType.CODE


def test_route_skip_provider(router):
    """Route skips providers in the skip list."""
    router.providers["sawyer"].health = ProviderHealth.HEALTHY
    router.providers["sawyer"].last_health_check = 9999999999
    router.providers["openai"].health = ProviderHealth.HEALTHY
    router.providers["openai"].last_health_check = 9999999999

    decision = asyncio.run(router.route("test", skip_providers=["sawyer"]))

    assert decision.provider.name == "openai"


def test_route_fallback(router):
    """Route falls back when primary providers are down."""
    router.providers["sawyer"].health = ProviderHealth.DOWN
    router.providers["sawyer"].last_health_check = 9999999999
    router.providers["openai"].health = ProviderHealth.DOWN
    router.providers["openai"].last_health_check = 9999999999
    router.providers["anthropic"].health = ProviderHealth.HEALTHY
    router.providers["anthropic"].last_health_check = 9999999999

    decision = asyncio.run(router.route("test"))

    assert decision.provider.name == "anthropic"
    assert len(decision.fallback_chain) >= 2  # sawyer and openai are in fallback list


def test_route_all_down(router):
    """Route returns first provider even when all are down."""
    for p in router.providers.values():
        p.health = ProviderHealth.DOWN
        p.last_health_check = 9999999999

    decision = asyncio.run(router.route("test"))

    # Should still return a provider (will likely error at call time)
    assert decision.provider is not None
    assert "No healthy providers" in decision.reason


def test_create_default_router():
    """Default router has the expected providers."""
    router = create_default_router()
    assert len(router.providers) == 4
    assert "sawyer" in router.providers
    assert router.providers["sawyer"].priority == 0


def test_task_type_enum():
    """TaskType enum values are correct."""
    assert TaskType.CODE.value == "code"
    assert TaskType.CONVERSATION.value == "conversation"
    assert TaskType.ANALYSIS.value == "analysis"
    assert TaskType.CREATIVE.value == "creative"
    assert TaskType.DEFAULT.value == "default"