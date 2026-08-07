"""
AE-03 Provider Router — Unified LLM Access with Fallback & Retry.

Provides:
  - Provider registry and factory pattern
  - Exponential backoff retry for rate-limit / transient errors
  - Automatic fallback chain: OpenAI → Gemini → Ollama
  - Structured event logging for observability integration
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from backend.config import get_settings
from backend.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


# ── Provider Pricing Table (USD per 1M tokens) ────────────────────────

PRICING: Dict[str, Dict[str, Tuple[float, float]]] = {
    # provider: { model: (input_per_1M, output_per_1M) }
    "openai": {
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
    },
    "gemini": {
        "gemini-1.5-pro": (1.25, 5.00),
    },
    "ollama": {
        # Local — no cost
        "_default": (0.0, 0.0),
    },
}


def estimate_cost(
    provider: str, model: str, tokens_prompt: int, tokens_completion: int
) -> float:
    """Estimate USD cost for a given LLM call."""
    provider_prices = PRICING.get(provider, {})
    input_price, output_price = provider_prices.get(
        model, provider_prices.get("_default", (0.0, 0.0))
    )
    cost = (tokens_prompt / 1_000_000 * input_price) + (
        tokens_completion / 1_000_000 * output_price
    )
    return round(cost, 8)


# ── Fallback Event (for observability) ─────────────────────────────────


class FallbackEvent:
    """Records a single fallback event for tracing."""

    def __init__(
        self,
        from_provider: str,
        to_provider: str,
        reason: str,
        attempt: int,
    ):
        self.from_provider = from_provider
        self.to_provider = to_provider
        self.reason = reason
        self.attempt = attempt
        self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "from_provider": self.from_provider,
            "to_provider": self.to_provider,
            "reason": self.reason,
            "attempt": self.attempt,
            "timestamp": self.timestamp,
        }


# ── Provider Router ────────────────────────────────────────────────────


class ProviderRouter:
    """
    Unified LLM router with retry and automatic fallback.

    Usage:
        router = ProviderRouter()
        response = await router.call(prompt="Hello", provider="openai")

    Fallback chain (configurable):
        OpenAI → Gemini → Ollama
    """

    DEFAULT_FALLBACK_CHAIN = ["openai", "gemini", "ollama"]
    MAX_RETRIES_PER_PROVIDER = 3
    RETRY_DELAYS = [1.0, 2.0, 4.0]  # Exponential backoff seconds

    def __init__(
        self,
        fallback_chain: Optional[List[str]] = None,
        max_retries: int = 3,
    ):
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._fallback_chain = fallback_chain or self.DEFAULT_FALLBACK_CHAIN
        self._max_retries = max_retries
        self._fallback_events: List[FallbackEvent] = []
        self._call_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0
        self._initialised = False

    def _ensure_init(self) -> None:
        """Lazy-initialise providers on first use."""
        if self._initialised:
            return

        settings = get_settings()

        # Only register providers with valid credentials
        if settings.has_provider_key("openai"):
            from backend.providers.openai_provider import OpenAIProvider
            self._providers["openai"] = OpenAIProvider()

        if settings.has_provider_key("gemini"):
            from backend.providers.gemini_provider import GeminiProvider
            self._providers["gemini"] = GeminiProvider()

        # Ollama is always available (local, no key)
        from backend.providers.ollama_provider import OllamaProvider
        self._providers["ollama"] = OllamaProvider()

        self._initialised = True
        logger.info(
            "ProviderRouter initialised with providers: %s",
            list(self._providers.keys()),
        )

    def get_provider(self, name: str) -> BaseLLMProvider:
        """Get a specific provider by name."""
        self._ensure_init()
        if name not in self._providers:
            available = list(self._providers.keys())
            raise ProviderError(
                f"Provider '{name}' not available. Registered: {available}",
                provider=name,
            )
        return self._providers[name]

    def get_available_providers(self) -> List[str]:
        """List all registered provider names."""
        self._ensure_init()
        return list(self._providers.keys())

    async def call(
        self,
        prompt: str,
        system_prompt: str = "",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
        enable_fallback: bool = True,
    ) -> LLMResponse:
        """
        Call an LLM provider with automatic retry and fallback.

        Args:
            prompt: User/task prompt.
            system_prompt: System-level instruction.
            provider: Preferred provider. Falls back if it fails.
            model: Override model. Uses provider default if None.
            temperature: Sampling temperature.
            max_tokens: Max output tokens.
            json_mode: Request JSON-formatted output.
            enable_fallback: If True, try fallback chain on failure.

        Returns:
            LLMResponse from whichever provider succeeded.

        Raises:
            ProviderError: If all providers in the fallback chain fail.
        """
        self._ensure_init()
        settings = get_settings()
        preferred = provider or settings.default_provider

        # Build the chain: preferred first, then remaining fallbacks
        chain = [preferred]
        if enable_fallback:
            for p in self._fallback_chain:
                if p != preferred and p in self._providers:
                    chain.append(p)

        last_error: Optional[Exception] = None

        for chain_idx, provider_name in enumerate(chain):
            if provider_name not in self._providers:
                logger.warning("Provider '%s' not registered, skipping.", provider_name)
                continue

            prov = self._providers[provider_name]

            for attempt in range(self._max_retries):
                try:
                    response = await prov.call_llm(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        model=model if chain_idx == 0 else None,  # Only use override model for preferred
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_mode=json_mode,
                    )

                    # Track aggregates
                    self._call_count += 1
                    self._total_tokens += response.total_tokens
                    cost = estimate_cost(
                        response.provider,
                        response.model,
                        response.tokens_prompt,
                        response.tokens_completion,
                    )
                    self._total_cost += cost

                    logger.info(
                        "LLM call succeeded: provider=%s model=%s tokens=%d cost=$%.6f latency=%.0fms",
                        response.provider,
                        response.model,
                        response.total_tokens,
                        cost,
                        response.latency_ms,
                    )
                    return response

                except RateLimitError as e:
                    delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                    logger.warning(
                        "Rate limit on %s (attempt %d/%d), retrying in %.1fs: %s",
                        provider_name, attempt + 1, self._max_retries, delay, e,
                    )
                    last_error = e
                    await asyncio.sleep(delay)

                except ProviderError as e:
                    if e.retryable and attempt < self._max_retries - 1:
                        delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                        logger.warning(
                            "Retryable error on %s (attempt %d/%d), retrying in %.1fs: %s",
                            provider_name, attempt + 1, self._max_retries, delay, e,
                        )
                        last_error = e
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "Non-retryable error on %s: %s", provider_name, e
                        )
                        last_error = e
                        break

                except Exception as e:
                    logger.error(
                        "Unexpected error on %s: %s", provider_name, e
                    )
                    last_error = e
                    break

            # If we get here, this provider exhausted retries → fallback
            if chain_idx < len(chain) - 1:
                next_provider = chain[chain_idx + 1]
                event = FallbackEvent(
                    from_provider=provider_name,
                    to_provider=next_provider,
                    reason=str(last_error),
                    attempt=chain_idx + 1,
                )
                self._fallback_events.append(event)
                logger.warning(
                    "Falling back from %s → %s (reason: %s)",
                    provider_name, next_provider, last_error,
                )

        # All providers exhausted
        raise ProviderError(
            f"All providers in fallback chain exhausted. Last error: {last_error}",
            provider="router",
        )

    # ── Metrics ────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return aggregated router statistics."""
        return {
            "call_count": self._call_count,
            "total_tokens": self._total_tokens,
            "total_cost_usd": round(self._total_cost, 6),
            "fallback_events": [e.to_dict() for e in self._fallback_events],
            "available_providers": self.get_available_providers(),
        }

    def reset_stats(self) -> None:
        """Reset aggregated counters."""
        self._call_count = 0
        self._total_tokens = 0
        self._total_cost = 0.0
        self._fallback_events.clear()
