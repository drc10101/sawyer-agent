"""
Multi-model routing -- intelligent request routing across LLM providers.

Sawyer is first-class: requests route to the Sawyer network first.
If Sawyer nodes are unavailable or slow, fallback to OpenAI/Anthropic/Ollama.

Routing strategy:
1. Check Sawyer network availability (health check)
2. If available, route to Sawyer with priority headers
3. If unavailable, fallback to next configured provider
4. Track cost per request for budget awareness
5. Task-type hints: code -> code model, conversation -> chat model, analysis -> reasoning model

This module integrates with the existing LLMClient but adds a routing layer
that decides WHICH client to use for each request.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import httpx

from .config import LLMConfig

logger = logging.getLogger("sawyer-harness.routing")


class TaskType(str, Enum):
    CODE = "code"
    CONVERSATION = "conversation"
    ANALYSIS = "analysis"
    CREATIVE = "creative"
    DEFAULT = "default"


class ProviderHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


@dataclass
class ProviderEndpoint:
    """A configured LLM provider endpoint."""

    name: str
    provider_type: str  # sawyer, openai, anthropic, ollama, local
    base_url: str
    api_key: str = ""
    model: str = ""
    priority: int = 0  # Lower = higher priority (Sawyer = 0)
    max_tokens: int = 4096
    temperature: float = 0.7
    health: ProviderHealth = ProviderHealth.UNKNOWN
    last_health_check: float = 0.0
    total_requests: int = 0
    total_errors: int = 0
    total_cost_cents: float = 0.0

    @property
    def error_rate(self) -> float:
        """Recent error rate (0.0 to 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests


@dataclass
class RouteDecision:
    """The result of a routing decision."""

    provider: ProviderEndpoint
    task_type: TaskType
    reason: str
    fallback_chain: list[str] = field(default_factory=list)
    estimated_cost_cents: float = 0.0


class ModelRouter:
    """
    Multi-model router with Sawyer priority and cost tracking.

    Routes each request to the best available provider based on:
    - Provider health (recent error rate)
    - Task type (code, conversation, analysis)
    - Cost awareness
    - Fallback chain
    """

    def __init__(self, providers: list[ProviderEndpoint], health_check_interval: int = 60):
        self.providers: dict[str, ProviderEndpoint] = {}
        for p in providers:
            self.providers[p.name] = p
        self.health_check_interval = health_check_interval
        self._client = httpx.AsyncClient(timeout=10.0)

        # Task type -> preferred provider mapping (configurable)
        self._task_preferences: dict[TaskType, list[str]] = {
            TaskType.CODE: [],        # Will be filled with provider names in priority order
            TaskType.CONVERSATION: [],
            TaskType.ANALYSIS: [],
            TaskType.CREATIVE: [],
            TaskType.DEFAULT: [],
        }
        self._build_default_preferences()

    def _build_default_preferences(self):
        """Build default provider preference order by priority."""
        # Sort providers by priority (lower = higher priority)
        sorted_providers = sorted(self.providers.values(), key=lambda p: p.priority)
        provider_names = [p.name for p in sorted_providers]

        # Default: all task types use the same priority order
        # Users can customize per task type
        for task_type in TaskType:
            self._task_preferences[task_type] = list(provider_names)

    def set_task_preference(self, task_type: TaskType, providers: list[str]):
        """Override the provider preference order for a task type."""
        self._task_preferences[task_type] = providers

    async def health_check(self, provider_name: str) -> ProviderHealth:
        """
        Check if a provider is healthy by hitting its health endpoint.

        For OpenAI-compatible providers, uses /v1/models.
        For Sawyer, uses /health.
        """
        provider = self.providers.get(provider_name)
        if not provider:
            return ProviderHealth.DOWN

        now = time.time()
        # Skip if checked recently
        if now - provider.last_health_check < self.health_check_interval:
            return provider.health

        provider.last_health_check = now

        try:
            if provider.provider_type == "sawyer":
                url = f"{provider.base_url.rstrip('/')}/health"
            elif provider.provider_type == "anthropic":
                # Anthropic doesn't have a health endpoint, just check DNS
                url = f"https://api.anthropic.com"
            else:
                url = f"{provider.base_url.rstrip('/')}/models"

            headers = {}
            if provider.api_key:
                headers["Authorization"] = f"Bearer {provider.api_key}"

            response = await self._client.get(url, headers=headers, timeout=5.0)

            if response.status_code < 500:
                provider.health = ProviderHealth.HEALTHY
            else:
                provider.health = ProviderHealth.DEGRADED

        except (httpx.TimeoutException, httpx.ConnectError):
            provider.health = ProviderHealth.DOWN
            logger.warning(f"Provider {provider_name} is DOWN (connection failed)")
        except Exception as e:
            provider.health = ProviderHealth.DEGRADED
            logger.warning(f"Provider {provider_name} health check error: {e}")

        return provider.health

    async def route(
        self,
        prompt: str,
        task_type: TaskType = TaskType.DEFAULT,
        skip_providers: list[str] | None = None,
    ) -> RouteDecision:
        """
        Route a request to the best available provider.

        Strategy:
        1. Get preference order for the task type
        2. Check health of each provider in order
        3. Skip providers in the skip list (e.g., if they just failed)
        4. Return the first healthy provider
        """
        skip = set(skip_providers or [])
        preferences = self._task_preferences.get(task_type, [])
        fallback_chain = []

        for provider_name in preferences:
            if provider_name in skip:
                continue

            provider = self.providers.get(provider_name)
            if not provider:
                continue

            # Check health
            health = await self.health_check(provider_name)

            if health == ProviderHealth.HEALTHY:
                return RouteDecision(
                    provider=provider,
                    task_type=task_type,
                    reason=f"First healthy provider for {task_type.value}",
                    fallback_chain=fallback_chain,
                )
            elif health == ProviderHealth.DEGRADED:
                fallback_chain.append(f"{provider_name} (degraded)")
            else:
                fallback_chain.append(f"{provider_name} (down)")

        # All preferred providers failed -- try any that aren't in skip list
        for provider_name, provider in self.providers.items():
            if provider_name in skip:
                continue
            health = await self.health_check(provider_name)
            if health in (ProviderHealth.HEALTHY, ProviderHealth.DEGRADED):
                return RouteDecision(
                    provider=provider,
                    task_type=task_type,
                    reason=f"Fallback: {provider_name} (preferred providers unavailable)",
                    fallback_chain=fallback_chain,
                )

        # Nothing available -- return the first provider anyway (will likely error)
        first_provider = next(iter(self.providers.values()))
        return RouteDecision(
            provider=first_provider,
            task_type=task_type,
            reason="No healthy providers available, attempting first configured provider",
            fallback_chain=fallback_chain,
        )

    def record_request(self, provider_name: str, success: bool, cost_cents: float = 0.0):
        """Record the result of a request for routing statistics."""
        provider = self.providers.get(provider_name)
        if not provider:
            return

        provider.total_requests += 1
        if not success:
            provider.total_errors += 1
        provider.total_cost_cents += cost_cents

    def get_routing_stats(self) -> dict[str, dict]:
        """Get routing statistics for all providers."""
        stats = {}
        for name, provider in self.providers.items():
            stats[name] = {
                "type": provider.provider_type,
                "model": provider.model,
                "health": provider.health.value,
                "priority": provider.priority,
                "total_requests": provider.total_requests,
                "total_errors": provider.total_errors,
                "error_rate": f"{provider.error_rate:.1%}",
                "total_cost_cents": provider.total_cost_cents,
            }
        return stats

    def get_llm_config(self, provider_name: str) -> LLMConfig:
        """Convert a ProviderEndpoint back to an LLMConfig for the LLMClient."""
        provider = self.providers[provider_name]
        return LLMConfig(
            provider=provider.provider_type,
            model=provider.model,
            api_key=provider.api_key,
            base_url=provider.base_url,
            max_tokens=provider.max_tokens,
            temperature=provider.temperature,
            context_length=getattr(provider, 'context_length', None),
        )

    async def close(self):
        await self._client.aclose()


def create_default_router() -> ModelRouter:
    """Create a router with default provider configurations."""
    providers = [
        ProviderEndpoint(
            name="sawyer",
            provider_type="sawyer",
            base_url="https://api.sawyernetwork.ai/v1",
            priority=0,  # Highest priority
            max_tokens=8192,
            temperature=0.7,
        ),
        ProviderEndpoint(
            name="openai",
            provider_type="openai",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            priority=1,
            max_tokens=4096,
            temperature=0.7,
        ),
        ProviderEndpoint(
            name="anthropic",
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            model="claude-sonnet-4-20250514",
            priority=2,
            max_tokens=4096,
            temperature=0.7,
        ),
        ProviderEndpoint(
            name="ollama",
            provider_type="ollama",
            base_url="http://localhost:11434/v1",
            model="llama3",
            priority=3,
            max_tokens=4096,
            temperature=0.7,
        ),
    ]
    return ModelRouter(providers)