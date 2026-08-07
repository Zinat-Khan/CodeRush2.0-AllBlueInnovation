"""
AE-03 OpenAI LLM Provider.

Wraps the OpenAI Python SDK to provide GPT-4o and GPT-4o-mini access
with structured JSON output support and token usage tracking.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from openai import AsyncOpenAI, APIError, RateLimitError as OpenAIRateLimit

from backend.config import get_settings
from backend.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI provider supporting gpt-4o and gpt-4o-mini."""

    provider_name = "openai"

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        settings = get_settings()
        self._api_key = api_key or settings.openai_api_key
        self._default_model = default_model or settings.default_model_openai
        self._client = AsyncOpenAI(api_key=self._api_key)

    async def call_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Call OpenAI Chat Completions API."""
        model_id = model or self._default_model
        start_time = time.perf_counter()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except OpenAIRateLimit as e:
            raise RateLimitError(
                f"OpenAI rate limit: {e}", provider=self.provider_name
            ) from e
        except APIError as e:
            retryable = e.status_code in (429, 500, 502, 503, 504) if e.status_code else False
            raise ProviderError(
                f"OpenAI API error: {e}",
                provider=self.provider_name,
                status_code=e.status_code,
                retryable=retryable,
            ) from e
        except Exception as e:
            raise ProviderError(
                f"OpenAI unexpected error: {e}",
                provider=self.provider_name,
            ) from e

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage

        # Attempt JSON parse
        parsed_json = None
        if json_mode and content.strip():
            try:
                parsed_json = json.loads(content)
            except json.JSONDecodeError:
                logger.warning("OpenAI returned invalid JSON despite json_mode.")

        return LLMResponse(
            content=content,
            parsed_json=parsed_json,
            tokens_prompt=usage.prompt_tokens if usage else 0,
            tokens_completion=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            model=response.model,
            provider=self.provider_name,
            latency_ms=elapsed_ms,
            finish_reason=choice.finish_reason or "",
        )
