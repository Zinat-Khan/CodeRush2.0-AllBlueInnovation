"""
AE-03 Worker B — Executor / Code Generation & Execution.

Calls n8n webhook endpoint ``agent-worker-code``.
Input : task description + language + context.
Output: generated source code + optional execution stdout.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from backend.integrations.n8n_client import N8nClient

logger = logging.getLogger(__name__)


# ── Input / Output Schemas ─────────────────────────────────────────────


class CodeWorkerPayload(BaseModel):
    """Request payload sent to the code-generation n8n workflow."""

    task_description: str = Field(
        default="",
        description="Natural-language description of the coding task.",
    )
    language: str = Field(
        default="python",
        description="Target language (python | typescript | bash).",
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context variables or code snippets.",
    )


class CodeWorkerResult(BaseModel):
    """Response payload returned by the code-generation n8n workflow."""

    status: str = Field(default="success")
    generated_code: str = Field(
        default="",
        description="Source code produced by the workflow.",
    )
    execution_output: Optional[str] = Field(
        default=None,
        description="Console / stdout if the code was executed.",
    )
    success: bool = Field(
        default=True,
        description="Whether generation + optional execution succeeded.",
    )


# ── Worker Agent ───────────────────────────────────────────────────────


class WorkerCode:
    """
    Worker B / Executor — Code generation.

    Triggers the ``agent-worker-code`` n8n webhook and returns a
    validated ``CodeWorkerResult``.
    """

    ENDPOINT = "agent-worker-code"

    def __init__(self, n8n_client: Optional[N8nClient] = None):
        self._client = n8n_client or N8nClient()

    async def execute(
        self,
        payload: CodeWorkerPayload,
        timeout: Optional[float] = None,
    ) -> CodeWorkerResult:
        """Post the payload to n8n and return a validated result."""
        logger.info(
            "WorkerCode executing: lang=%s task=%.60s",
            payload.language,
            payload.task_description,
        )
        return await self._client.call_webhook(
            endpoint=self.ENDPOINT,
            payload=payload.model_dump(),
            response_model=CodeWorkerResult,
            timeout=timeout,
        )
