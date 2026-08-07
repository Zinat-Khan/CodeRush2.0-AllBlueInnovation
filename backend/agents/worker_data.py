"""
AE-03 Worker A — Researcher / Data Ingestion & Entity Extraction.

Calls n8n webhook endpoint ``agent-worker-data``.
Input : raw text or URL for analysis.
Output: structured entity JSON + summary.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.integrations.n8n_client import N8nClient

logger = logging.getLogger(__name__)


# ── Input / Output Schemas ─────────────────────────────────────────────


class DataWorkerPayload(BaseModel):
    """Request payload sent to the data-ingestion n8n workflow."""

    raw_text: str = Field(
        default="",
        description="Text content or natural-language query to analyse.",
    )
    url: Optional[str] = Field(
        default=None,
        description="Optional URL to fetch and parse.",
    )
    extract_entities: List[str] = Field(
        default_factory=lambda: ["api_endpoints", "schemas", "technologies"],
        description="Entity categories to extract.",
    )


class DataWorkerResult(BaseModel):
    """Response payload returned by the data-ingestion n8n workflow."""

    status: str = Field(default="success")
    entities: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted entities keyed by category.",
    )
    summary: str = Field(
        default="",
        description="Natural-language summary of ingested data.",
    )
    raw_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional raw data produced by the workflow.",
    )


# ── Worker Agent ───────────────────────────────────────────────────────


class WorkerData:
    """
    Worker A / Researcher.

    Triggers the ``agent-worker-data`` n8n webhook and returns a
    validated ``DataWorkerResult``.
    """

    ENDPOINT = "agent-worker-data"

    def __init__(self, n8n_client: Optional[N8nClient] = None):
        self._client = n8n_client or N8nClient()

    async def execute(
        self,
        payload: DataWorkerPayload,
        timeout: Optional[float] = None,
    ) -> DataWorkerResult:
        """Post the payload to n8n and return a validated result."""
        logger.info(
            "WorkerData executing: entities=%s", payload.extract_entities
        )
        return await self._client.call_webhook(
            endpoint=self.ENDPOINT,
            payload=payload.model_dump(),
            response_model=DataWorkerResult,
            timeout=timeout,
        )
