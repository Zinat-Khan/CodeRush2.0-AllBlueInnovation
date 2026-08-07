"""
AE-03 n8n Webhook Client.

Provides N8nClient for sending async HTTP POST requests to n8n webhook
endpoints with configurable timeout, Pydantic response validation, and
structured error handling.

Posts to: {N8N_WEBHOOK_BASE_URL}/{endpoint}
Default timeout: 30 seconds
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from backend.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ── Custom Exceptions ──────────────────────────────────────────────────


class N8nClientError(Exception):
    """Base exception for all n8n webhook client errors."""

    def __init__(
        self,
        message: str,
        endpoint: str = "",
        status_code: Optional[int] = None,
    ):
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(message)


class WebhookTimeoutError(N8nClientError):
    """Raised when an n8n webhook call exceeds the configured timeout."""

    def __init__(self, message: str, endpoint: str = ""):
        super().__init__(message, endpoint=endpoint, status_code=408)


class SchemaValidationError(N8nClientError):
    """Raised when the webhook response fails Pydantic schema validation."""

    def __init__(
        self,
        message: str,
        endpoint: str = "",
        validation_error: Optional[ValidationError] = None,
    ):
        self.validation_error = validation_error
        super().__init__(message, endpoint=endpoint)


# ── n8n Webhook Client ─────────────────────────────────────────────────


class N8nClient:
    """
    Async HTTP client for triggering n8n webhook workflows.

    Usage::

        client = N8nClient()
        result = await client.call_webhook(
            "agent-worker-data",
            {"raw_text": "Audit the REST API"},
        )
    """

    DEFAULT_TIMEOUT_SECONDS = 30.0

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        settings = get_settings()
        self._base_url = (base_url or settings.n8n_webhook_base_url).rstrip("/")
        self._default_timeout = default_timeout

    def _build_url(self, endpoint: str) -> str:
        """Construct the full webhook URL for an endpoint path."""
        clean = endpoint.lstrip("/")
        return f"{self._base_url}/{clean}"

    async def call_webhook(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        response_model: Optional[Type[T]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any] | T:
        """
        POST a JSON payload to an n8n webhook endpoint.

        Args:
            endpoint: Webhook path (e.g. ``agent-worker-data``).
            payload: JSON-serialisable dictionary.
            response_model: Optional Pydantic model to validate the response.
            timeout: Custom timeout in seconds (default 30s).

        Returns:
            Raw dict response, or validated ``response_model`` instance.

        Raises:
            WebhookTimeoutError: On HTTP timeout.
            SchemaValidationError: When response fails Pydantic validation.
            N8nClientError: On HTTP errors or unexpected failures.
        """
        url = self._build_url(endpoint)
        req_timeout = timeout or self._default_timeout

        logger.info(
            "n8n POST to '%s' (timeout=%.1fs) payload_keys=%s",
            endpoint,
            req_timeout,
            list(payload.keys()),
        )

        try:
            async with httpx.AsyncClient(timeout=req_timeout) as client:
                response = await client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            msg = f"n8n webhook '{endpoint}' timed out after {req_timeout:.1f}s"
            logger.error(msg)
            raise WebhookTimeoutError(msg, endpoint=endpoint) from exc
        except httpx.ConnectError as exc:
            msg = f"Cannot connect to n8n at '{url}': {exc}"
            logger.error(msg)
            raise N8nClientError(msg, endpoint=endpoint) from exc
        except Exception as exc:
            msg = f"Unexpected error calling n8n webhook '{endpoint}': {exc}"
            logger.error(msg)
            raise N8nClientError(msg, endpoint=endpoint) from exc

        # Handle HTTP error status codes
        if response.status_code >= 400:
            body_preview = response.text[:300]
            msg = (
                f"n8n webhook '{endpoint}' returned HTTP {response.status_code}: "
                f"{body_preview}"
            )
            logger.error(msg)
            raise N8nClientError(
                msg, endpoint=endpoint, status_code=response.status_code
            )

        # Parse JSON body
        try:
            data = response.json()
        except Exception as exc:
            msg = (
                f"n8n webhook '{endpoint}' returned non-JSON body: "
                f"{response.text[:200]}"
            )
            logger.error(msg)
            raise N8nClientError(msg, endpoint=endpoint) from exc

        # Optionally validate against a Pydantic model
        if response_model is not None:
            return self._validate_response(data, response_model, endpoint)

        return data

    @staticmethod
    def _validate_response(
        data: Any,
        model: Type[T],
        endpoint: str,
    ) -> T:
        """Parse raw response data into a Pydantic model, handling list wrapping."""
        try:
            # n8n webhook nodes frequently wrap results in a JSON array
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict):
                    return model(**data[0])
                raise ValueError("Webhook returned an empty or non-object list.")
            return model(**data)
        except ValidationError as ve:
            logger.error(
                "SchemaValidationError on '%s' against %s: %s",
                endpoint,
                model.__name__,
                ve,
            )
            raise SchemaValidationError(
                f"Response from '{endpoint}' failed validation "
                f"against {model.__name__}.",
                endpoint=endpoint,
                validation_error=ve,
            ) from ve
