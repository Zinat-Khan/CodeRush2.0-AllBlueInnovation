"""
AE-03 Google Gemini LLM Provider.

Wraps the google-generativeai SDK to provide Gemini 1.5 Pro access
with JSON mode and token usage tracking.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError

from backend.config import get_settings
from backend.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    """Google Gemini provider supporting gemini-1.5-pro."""

    provider_name = "gemini"

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        settings = get_settings()
        self._api_key = api_key or settings.gemini_api_key
        self._default_model = default_model or settings.default_model_gemini
        genai.configure(api_key=self._api_key)

    async def call_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Call Google Gemini GenerativeAI API."""
        model_id = model or self._default_model
        start_time = time.perf_counter()

        generation_config = genai.types.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        if json_mode:
            generation_config.response_mime_type = "application/json"

        gm = genai.GenerativeModel(
            model_name=model_id,
            system_instruction=system_prompt if system_prompt else None,
            generation_config=generation_config,
        )

        try:
            response = await gm.generate_content_async(prompt)
        except ResourceExhausted as e:
            raise RateLimitError(
                f"Gemini rate limit: {e}", provider=self.provider_name
            ) from e
        except GoogleAPIError as e:
            raise ProviderError(
                f"Gemini API error: {e}",
                provider=self.provider_name,
                retryable=True,
            ) from e
        except Exception as e:
            raise ProviderError(
                f"Gemini unexpected error: {e}",
                provider=self.provider_name,
            ) from e

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Extract content
        content = ""
        try:
            content = response.text
        except (ValueError, AttributeError):
            if response.candidates:
                parts = response.candidates[0].content.parts
                content = "".join(p.text for p in parts if hasattr(p, "text"))

        # Token usage from usage_metadata
        tokens_prompt = 0
        tokens_completion = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            meta = response.usage_metadata
            tokens_prompt = getattr(meta, "prompt_token_count", 0) or 0
            tokens_completion = getattr(meta, "candidates_token_count", 0) or 0

        # Attempt JSON parse
        parsed_json = None
        if json_mode and content.strip():
            try:
                parsed_json = json.loads(content)
            except json.JSONDecodeError:
                logger.warning("Gemini returned invalid JSON despite json_mode.")

        # Finish reason
        finish_reason = ""
        try:
            if response.candidates:
                fr = response.candidates[0].finish_reason
                finish_reason = str(fr.name) if hasattr(fr, "name") else str(fr)
        except Exception:
            pass

        return LLMResponse(
            content=content,
            parsed_json=parsed_json,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            total_tokens=tokens_prompt + tokens_completion,
            model=model_id,
            provider=self.provider_name,
            latency_ms=elapsed_ms,
            finish_reason=finish_reason,
        )
