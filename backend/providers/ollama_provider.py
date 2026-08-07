"""
AE-03 Local Ollama LLM Provider.

Wraps the Ollama HTTP API to provide local model access (llama3, etc.)
with JSON mode and approximate token usage tracking.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from backend.config import get_settings
from backend.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Local Ollama provider via HTTP API. No API key required."""

    provider_name = "ollama"

    def __init__(self, host: Optional[str] = None, default_model: Optional[str] = None):
        settings = get_settings()
        self._host = (host or settings.ollama_host).rstrip("/")
        self._default_model = default_model or settings.default_model_ollama

    async def call_llm(
        self,
        prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Call Ollama /api/generate or /api/chat endpoint."""
        model_id = model or self._default_model
        start_time = time.perf_counter()

        # Use /api/chat for system prompt support
        url = f"{self._host}/api/chat"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model_id,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload)
        except httpx.ConnectError as e:
            raise ProviderError(
                f"Ollama connection failed at {self._host}: {e}",
                provider=self.provider_name,
                retryable=False,
            ) from e
        except httpx.TimeoutException as e:
            raise ProviderError(
                f"Ollama timeout: {e}",
                provider=self.provider_name,
                retryable=True,
            ) from e
        except Exception as e:
            raise ProviderError(
                f"Ollama unexpected error: {e}",
                provider=self.provider_name,
            ) from e

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        if resp.status_code == 429:
            raise RateLimitError(
                "Ollama rate limit hit", provider=self.provider_name
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Ollama HTTP {resp.status_code}: {resp.text}",
                provider=self.provider_name,
                status_code=resp.status_code,
                retryable=resp.status_code >= 500,
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise ProviderError(
                f"Ollama returned non-JSON response: {resp.text[:200]}",
                provider=self.provider_name,
            ) from e

        # Extract content from chat response
        content = ""
        if "message" in data:
            content = data["message"].get("content", "")
        elif "response" in data:
            content = data["response"]

        # Token usage (Ollama provides these in the response)
        tokens_prompt = data.get("prompt_eval_count", 0) or 0
        tokens_completion = data.get("eval_count", 0) or 0

        # Attempt JSON parse
        parsed_json = None
        if json_mode and content.strip():
            try:
                parsed_json = json.loads(content)
            except json.JSONDecodeError:
                logger.warning("Ollama returned invalid JSON despite json format.")

        return LLMResponse(
            content=content,
            parsed_json=parsed_json,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            total_tokens=tokens_prompt + tokens_completion,
            model=model_id,
            provider=self.provider_name,
            latency_ms=elapsed_ms,
            finish_reason=data.get("done_reason", "stop") if data.get("done") else "",
        )

    async def health_check(self) -> bool:
        """Check if the Ollama server is running and reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._host}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List locally available models on the Ollama server."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._host}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []
