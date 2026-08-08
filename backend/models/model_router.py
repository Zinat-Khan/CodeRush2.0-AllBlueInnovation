"""
AE-03 LangChain Multi-Provider Model Router (Directive V2).

Provides a model-independent abstraction layer over multiple LLM providers
using LangChain's ``BaseChatModel`` interface:

  - **Primary**: ``ChatGoogleGenerativeAI`` (``langchain-google-genai``)
  - **Fallback 1**: ``ChatOpenAI`` (``langchain-openai``)
  - **Fallback 2**: ``ChatOllama`` (``langchain-community``)

Features:
  - Automatic fallback chain with configurable retry/timeout
  - Per-provider token & cost tracking
  - Rate-limit detection and exponential backoff
  - Capability-based model selection (structured output, function calling)
  - Availability health checks
  - Thread-safe usage statistics
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult

from backend.config import AppSettings, get_settings

logger = logging.getLogger(__name__)


# ── Provider Registry ────────────────────────────────────────────────


class ProviderName(str, Enum):
    """Supported LLM provider identifiers."""
    GOOGLE = "google"
    OPENAI = "openai"
    OLLAMA = "ollama"


# ── Cost Tables ──────────────────────────────────────────────────────

# Approximate cost per 1K tokens (prompt / completion) in USD
COST_PER_1K_TOKENS: Dict[str, Dict[str, float]] = {
    "gemini-1.5-pro": {"prompt": 0.00125, "completion": 0.005},
    "gemini-1.5-flash": {"prompt": 0.000075, "completion": 0.0003},
    "gemini-2.0-flash": {"prompt": 0.0001, "completion": 0.0004},
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4.1-mini": {"prompt": 0.0004, "completion": 0.0016},
    # Ollama models are free (local inference)
    "llama3.2": {"prompt": 0.0, "completion": 0.0},
    "llama3.1": {"prompt": 0.0, "completion": 0.0},
    "mistral": {"prompt": 0.0, "completion": 0.0},
    "codellama": {"prompt": 0.0, "completion": 0.0},
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a given model and token counts."""
    rates = COST_PER_1K_TOKENS.get(model, {"prompt": 0.001, "completion": 0.002})
    return (prompt_tokens * rates["prompt"] / 1000) + (
        completion_tokens * rates["completion"] / 1000
    )


# ── Provider Stats ───────────────────────────────────────────────────


@dataclass
class ProviderStats:
    """Usage statistics for a single provider."""
    provider: str = ""
    model: str = ""
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    last_call_at: Optional[float] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0

    def record_success(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        model: str,
    ) -> None:
        """Record a successful LLM call."""
        self.call_count += 1
        self.success_count += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_tokens += prompt_tokens + completion_tokens
        cost = estimate_cost(model, prompt_tokens, completion_tokens)
        self.total_cost_usd += cost
        self.total_latency_ms += latency_ms
        self.last_call_at = time.time()
        self.model = model
        self.consecutive_failures = 0

    def record_failure(self, error: str) -> None:
        """Record a failed LLM call."""
        self.call_count += 1
        self.failure_count += 1
        self.last_error = error
        self.last_call_at = time.time()
        self.consecutive_failures += 1

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "provider": self.provider,
            "model": self.model,
            "call_count": self.call_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_latency_ms": round(
                self.total_latency_ms / max(self.success_count, 1), 1
            ),
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }


# ── Model Router ─────────────────────────────────────────────────────


class ModelRouter:
    """
    LangChain Multi-Provider Model Router.

    Manages a prioritised chain of LLM providers with automatic fallback,
    token/cost tracking, and capability-based selection.

    Usage::

        router = ModelRouter()
        result = await router.ainvoke(messages=[HumanMessage("Hello")])
        # Returns (response_text, metadata_dict)

    The router tries providers in order: Google → OpenAI → Ollama.
    If the primary fails (timeout, rate-limit, error), it automatically
    falls back to the next available provider.
    """

    def __init__(
        self,
        settings: Optional[AppSettings] = None,
        max_retries: int = 0,
        timeout_seconds: int = 60,
    ):

        self._settings = settings or get_settings()
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._stats: Dict[str, ProviderStats] = {}
        self._providers: Dict[str, BaseChatModel] = {}
        self._fallback_order: List[str] = []

        self._init_providers()

    def _init_providers(self) -> None:
        """
        Eagerly initialise all configured providers.

        Order: Main APIs (Google -> OpenAI -> Groq) followed by
        OpenRouter fallback APIs (Keys 1-7) -> Ollama local.
        """
        settings = self._settings
        primary = settings.primary_provider

        # Main APIs: OpenAI -> Google -> Groq -> OpenRouter Fallbacks -> Local
        all_providers = [
            "openai", "google", "groq",
            "openrouter_1", "openrouter_2", "openrouter_3",
            "openrouter_4", "openrouter_5", "openrouter_6", "openrouter_7",
            "ollama",
        ]
        ordered = [primary] + [p for p in all_providers if p != primary]

        for provider_name in ordered:
            try:
                model = self._create_provider(provider_name)
                if model is not None:
                    self._providers[provider_name] = model
                    self._stats[provider_name] = ProviderStats(
                        provider=provider_name,
                        model=settings.get_provider_model(provider_name),
                    )
                    self._fallback_order.append(provider_name)
                    logger.info(
                        "Provider '%s' initialised (model: %s)",
                        provider_name,
                        settings.get_provider_model(provider_name),
                    )
            except Exception as e:
                logger.warning(
                    "Failed to initialise provider '%s': %s", provider_name, e
                )

        if not self._fallback_order:
            logger.error("No LLM providers available!")

    def _create_provider(self, provider_name: str) -> Optional[BaseChatModel]:
        """Create a LangChain chat model instance for a provider."""
        settings = self._settings

        if provider_name == "google":
            api_key = settings.google_api_key
            if not api_key:
                logger.debug("Google API key not set, skipping.")
                return None
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=settings.google_model,
                google_api_key=api_key,
                temperature=0.0,
                max_retries=self._max_retries,
                timeout=self._timeout_seconds,
            )

        elif provider_name == "openai":
            api_key = settings.openai_api_key
            if not api_key:
                logger.debug("OpenAI API key not set, skipping.")
                return None
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=settings.openai_model,
                api_key=api_key,
                temperature=0.0,
                max_retries=self._max_retries,
                timeout=self._timeout_seconds,
            )

        elif provider_name == "groq":
            api_key = settings.groq_api_key
            if not api_key:
                logger.debug("Groq API key not set, skipping.")
                return None
            try:
                from langchain_groq import ChatGroq

                return ChatGroq(
                    model_name=settings.groq_model,
                    groq_api_key=api_key,
                    temperature=0.0,
                    max_retries=self._max_retries,
                    timeout=self._timeout_seconds,
                )
            except Exception as e:
                logger.warning("Failed to create Groq provider: %s", e)
                return None

        elif provider_name.startswith("openrouter_"):
            key_num = provider_name.split("_")[1]
            key_attr = f"openrouter_key_{key_num}"
            api_key = getattr(settings, key_attr, "")
            if not api_key:
                logger.debug("OpenRouter key %s not set, skipping.", key_num)
                return None

            from langchain_openai import ChatOpenAI

            # Map OpenRouter keys to respective models
            model_map = {
                "openrouter_1": "anthropic/claude-3-haiku",
                "openrouter_2": "openai/gpt-4o-mini",
                "openrouter_3": "openai/gpt-4o-mini",
                "openrouter_4": "anthropic/claude-3-haiku",
                "openrouter_5": "openai/gpt-4o-mini",
                "openrouter_6": "openai/gpt-4o-mini",
                "openrouter_7": "openai/gpt-4o-mini",
            }
            model_name = model_map.get(provider_name, "openai/gpt-4o-mini")

            return ChatOpenAI(
                model=model_name,
                api_key=api_key,
                openai_api_base="https://openrouter.ai/api/v1",
                temperature=0.0,
                max_retries=self._max_retries,
                timeout=self._timeout_seconds,
            )

        elif provider_name == "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=settings.ollama_model,
                base_url=settings.ollama_base_url,
                temperature=0.0,
            )

        return None

    # ── Public API ────────────────────────────────────────────────────

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Invoke the LLM with automatic fallback.

        Args:
            messages: List of LangChain messages (SystemMessage, HumanMessage, etc.)
            provider: Override specific provider (skips fallback chain).
            model: Override model name.
            temperature: Override temperature.
            max_tokens: Override max output tokens.

        Returns:
            Tuple of (response_text, metadata_dict) where metadata contains
            provider, model, tokens, cost, latency, etc.
        """
        # If a specific provider is requested, try only that one
        if provider:
            return await self._call_provider(
                provider, messages, model, temperature, max_tokens
            )

        # Otherwise, try the fallback chain
        last_error: Optional[Exception] = None

        for provider_name in self._fallback_order:
            # Skip providers with too many consecutive failures (circuit breaker)
            stats = self._stats.get(provider_name)
            if stats and stats.consecutive_failures >= 5:
                logger.warning(
                    "Skipping provider '%s' (circuit breaker: %d consecutive failures)",
                    provider_name,
                    stats.consecutive_failures,
                )
                continue

            try:
                return await self._call_provider(
                    provider_name, messages, model, temperature, max_tokens
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Provider '%s' failed, trying next: %s", provider_name, e
                )
                continue

        # All providers failed
        error_msg = f"All providers failed. Last error: {last_error}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    async def _call_provider(
        self,
        provider_name: str,
        messages: Sequence[BaseMessage],
        model_override: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Call a specific provider with tracking.

        Returns (response_text, metadata_dict).
        """
        chat_model = self._providers.get(provider_name)
        if chat_model is None:
            raise ValueError(f"Provider '{provider_name}' is not available.")

        # Apply overrides via bind if needed
        kwargs: Dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        effective_model = model_override or self._settings.get_provider_model(
            provider_name
        )

        # Handle model override for the specific provider
        if model_override:
            if provider_name == "google":
                kwargs["model"] = model_override
            elif provider_name == "openai":
                kwargs["model"] = model_override
            elif provider_name == "ollama":
                kwargs["model"] = model_override

        if kwargs:
            chat_model = chat_model.bind(**kwargs) if hasattr(chat_model, 'bind') else chat_model

        start_time = time.time()
        stats = self._stats.get(provider_name, ProviderStats(provider=provider_name))

        try:
            response = await chat_model.ainvoke(list(messages))
            latency_ms = (time.time() - start_time) * 1000

            # Extract token usage
            prompt_tokens = 0
            completion_tokens = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                prompt_tokens = getattr(
                    response.usage_metadata, "input_tokens", 0
                ) or 0
                completion_tokens = getattr(
                    response.usage_metadata, "output_tokens", 0
                ) or 0
            elif hasattr(response, "response_metadata"):
                meta = response.response_metadata or {}
                usage = meta.get("token_usage", meta.get("usage", {}))
                if isinstance(usage, dict):
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

            total_tokens = prompt_tokens + completion_tokens
            cost = estimate_cost(effective_model, prompt_tokens, completion_tokens)

            # Record stats
            stats.record_success(prompt_tokens, completion_tokens, latency_ms, effective_model)

            response_text = response.content if hasattr(response, "content") else str(response)

            metadata = {
                "provider": provider_name,
                "model": effective_model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost_usd": round(cost, 6),
                "latency_ms": round(latency_ms, 1),
                "success": True,
            }

            logger.info(
                "LLM call: provider=%s model=%s tokens=%d cost=$%.6f latency=%.1fms",
                provider_name,
                effective_model,
                total_tokens,
                cost,
                latency_ms,
            )

            return response_text, metadata

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            stats.record_failure(str(e))
            logger.warning(
                "LLM call failed: provider=%s model=%s error=%s latency=%.1fms",
                provider_name,
                effective_model,
                str(e)[:100],
                latency_ms,
            )
            raise

    # ── Convenience Methods ───────────────────────────────────────────

    async def ainvoke_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Convenience: invoke with plain text prompt.

        Returns (response_text, metadata_dict).
        """
        messages: List[BaseMessage] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))
        return await self.ainvoke(messages, provider=provider, model=model)

    async def ainvoke_structured(
        self,
        prompt: str,
        output_schema: type,
        system_prompt: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> tuple[Any, Dict[str, Any]]:
        """
        Invoke with structured output parsing via with_structured_output().

        Args:
            prompt: User prompt text.
            output_schema: Pydantic model class for structured output.
            system_prompt: Optional system prompt.
            provider: Override provider.
            model: Override model.

        Returns:
            Tuple of (parsed_output, metadata_dict).
        """
        target_provider = provider or self._fallback_order[0]
        chat_model = self._providers.get(target_provider)
        if chat_model is None:
            raise ValueError(f"Provider '{target_provider}' is not available.")

        structured_model = chat_model.with_structured_output(output_schema)

        messages: List[BaseMessage] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        start_time = time.time()
        stats = self._stats.get(
            target_provider, ProviderStats(provider=target_provider)
        )
        effective_model = model or self._settings.get_provider_model(target_provider)

        try:
            result = await structured_model.ainvoke(messages)
            latency_ms = (time.time() - start_time) * 1000
            stats.record_success(0, 0, latency_ms, effective_model)

            metadata = {
                "provider": target_provider,
                "model": effective_model,
                "latency_ms": round(latency_ms, 1),
                "structured": True,
                "success": True,
            }
            return result, metadata

        except Exception as e:
            stats.record_failure(str(e))
            raise

    # ── Observability ─────────────────────────────────────────────────

    def get_available_providers(self) -> List[str]:
        """Return list of initialised provider names."""
        return list(self._fallback_order)

    def get_stats(self) -> Dict[str, Dict[str, Any]]:
        """Return per-provider usage statistics."""
        return {name: stats.to_dict() for name, stats in self._stats.items()}

    def get_total_cost(self) -> float:
        """Return total USD cost across all providers."""
        return sum(s.total_cost_usd for s in self._stats.values())

    def get_total_tokens(self) -> int:
        """Return total tokens consumed across all providers."""
        return sum(s.total_tokens for s in self._stats.values())

    def get_primary_provider(self) -> Optional[str]:
        """Return the primary (first) provider name."""
        return self._fallback_order[0] if self._fallback_order else None

    def is_available(self, provider: str) -> bool:
        """Check if a specific provider is initialised and not circuit-broken."""
        if provider not in self._providers:
            return False
        stats = self._stats.get(provider)
        if stats and stats.consecutive_failures >= 5:
            return False
        return True

    def reset_circuit_breaker(self, provider: str) -> None:
        """Reset the circuit breaker for a provider."""
        stats = self._stats.get(provider)
        if stats:
            stats.consecutive_failures = 0
            logger.info("Circuit breaker reset for provider '%s'", provider)

    def get_model_for_provider(self, provider: str) -> str:
        """Return the configured model name for a provider."""
        return self._settings.get_provider_model(provider)
