"""
AE-03 Worker C — Executor / External API Calls.

Calls n8n webhook endpoint ``agent-worker-api``.
Input : target REST API URL, method, headers, parameters.
Output: HTTP status code, parsed response body, success flag.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from backend.integrations.n8n_client import N8nClient

logger = logging.getLogger(__name__)


# ── Input / Output Schemas ─────────────────────────────────────────────


class ApiWorkerPayload(BaseModel):
    """Request payload sent to the external-API n8n workflow."""

    target_api: str = Field(
        default="",
        description="Target REST API URL to call.",
    )
    method: str = Field(
        default="GET",
        description="HTTP method (GET | POST | PUT | DELETE).",
    )
    headers: Dict[str, str] = Field(
        default_factory=dict,
        description="Custom HTTP request headers.",
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Query parameters or JSON body payload.",
    )


class ApiWorkerResult(BaseModel):
    """Response payload returned by the external-API n8n workflow."""

    status_code: int = Field(
        default=200,
        description="HTTP status code returned by the target API.",
    )
    response_body: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parsed JSON response from the target API.",
    )
    success: bool = Field(
        default=True,
        description="Whether the external API call succeeded.",
    )


# ── Worker Agent ───────────────────────────────────────────────────────


class WorkerApi:
    """
    Worker C / Executor — External API calls.

    Triggers the ``agent-worker-api`` n8n webhook and returns a
    validated ``ApiWorkerResult``.
    """

    ENDPOINT = "agent-worker-api"

    def __init__(self, n8n_client: Optional[N8nClient] = None):
        self._client = n8n_client or N8nClient()

    async def execute(
        self,
        payload: ApiWorkerPayload,
        timeout: Optional[float] = None,
    ) -> ApiWorkerResult:
        """Post the payload to n8n and return a validated result."""
        logger.info(
            "WorkerApi executing: method=%s target=%.80s",
            payload.method,
            payload.target_api,
        )
        return await self._client.call_webhook(
            endpoint=self.ENDPOINT,
            payload=payload.model_dump(),
            response_model=ApiWorkerResult,
            timeout=timeout,
        )
