"""
AE-03 Abstract LLM Provider Interface.

Defines the contract that all concrete LLM providers must implement,
plus the standardised LLMResponse model returned by every provider call.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Standardised Response ──────────────────────────────────────────────


class LLMResponse(BaseModel):
    """Normalised response returned by every LLM provider."""

    content: str = Field(
        default="",
        description="Raw text content returned by the model.",
    )
    parsed_json: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Parsed JSON object if the response was valid JSON.",
    )
    tokens_prompt: int = Field(
        default=0,
        ge=0,
        description="Number of prompt / input tokens consumed.",
    )
    tokens_completion: int = Field(
        default=0,
        ge=0,
        description="Number of completion / output tokens consumed.",
    )
    total_tokens: int = Field(
        default=0,
        ge=0,
        description="Sum of prompt + completion tokens.",
    )
    model: str = Field(
        default="",
        description="Model identifier that actually served the request.",
    )
    provider: str = Field(
        default="",
        description="Provider name (openai | gemini | ollama).",
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock latency for the API call in milliseconds.",
    )
    finish_reason: str = Field(
        default="",
        description="Model-reported finish reason (stop, length, etc.).",
    )


# ── Abstract Base Provider ─────────────────────────────────────────────


class BaseLLMProvider(ABC):
    """
    Abstract interface for LLM providers.

    Every concrete provider (OpenAI, Gemini, Ollama) must implement
    `call_llm` with the same signature.  The router relies on this
    uniform interface for fallback chaining and hot-swapping.
    """

    provider_name: str = "base"

    @abstractmethod
    async def call_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        """
        Send a prompt to the LLM and return a normalised response.

        Args:
            prompt: The user / task prompt.
            system_prompt: Optional system-level instruction.
            model: Override model name; uses provider default if None.
            temperature: Sampling temperature (0.0 – 2.0).
            max_tokens: Maximum tokens to generate.
            json_mode: If True, request JSON-formatted output.

        Returns:
            LLMResponse with content, token counts, and metadata.

        Raises:
            ProviderError: On unrecoverable provider-side errors.
        """
        ...

    async def health_check(self) -> bool:
        """
        Quick connectivity check.  Returns True if the provider is
        reachable, False otherwise.  Default implementation calls
        call_llm with a trivial prompt.
        """
        try:
            resp = await self.call_llm(
                prompt="Reply with the single word OK.",
                max_tokens=10,
                temperature=0.0,
            )
            return bool(resp.content)
        except Exception:
            return False


# ── Provider Error ─────────────────────────────────────────────────────


class ProviderError(Exception):
    """Raised when an LLM provider encounters an unrecoverable error."""

    def __init__(
        self,
        message: str,
        provider: str = "",
        status_code: Optional[int] = None,
        retryable: bool = False,
    ):
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class RateLimitError(ProviderError):
    """Raised on HTTP 429 / rate-limit responses.  Always retryable."""

    def __init__(self, message: str, provider: str = ""):
        super().__init__(
            message, provider=provider, status_code=429, retryable=True
        )
