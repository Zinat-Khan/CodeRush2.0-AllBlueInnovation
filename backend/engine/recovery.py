"""
AE-03 Recovery Engine — Retry Logic & Compensation Branches.

Provides:
  - RetryPolicy: configurable per-node retry with exponential backoff
    and error-context injection into the agent's system prompt.
  - CompensationRouter: routes to a designated compensation node when
    retries are exhausted, or escalates to the human-approval gate.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, Callable, Coroutine, Dict, Optional

from backend.schemas.contracts import ExecutionStatus

logger = logging.getLogger(__name__)


# ── Retry Policy ───────────────────────────────────────────────────────


class RetryPolicy:
    """
    Configurable retry logic with exponential backoff.

    Default: max 2 retries, delays [1s, 3s].
    After each failure the error message is appended to an ``error_context``
    list so the agent can receive richer debugging information on its next
    attempt.
    """

    DEFAULT_MAX_RETRIES = 2
    DEFAULT_DELAYS = [1.0, 3.0]  # Exponential backoff schedule

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        delays: Optional[list[float]] = None,
    ):
        self.max_retries = max_retries
        self.delays = delays or self.DEFAULT_DELAYS

    def get_delay(self, attempt: int) -> float:
        """Return the backoff delay for the given attempt (0-indexed)."""
        if attempt < len(self.delays):
            return self.delays[attempt]
        return self.delays[-1]  # cap at last entry

    async def execute_with_retry(
        self,
        node_id: str,
        fn: Callable[..., Coroutine[Any, Any, Dict[str, Any]]],
        *,
        on_retry: Optional[
            Callable[[str, int, str], Coroutine[Any, Any, None]]
        ] = None,
        on_exhausted: Optional[
            Callable[[str, list[str]], Coroutine[Any, Any, None]]
        ] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Execute *fn* up to ``max_retries + 1`` times.

        Args:
            node_id: Identifier for logging / callback context.
            fn: Async callable to execute.
            on_retry: ``async callback(node_id, attempt, error_msg)``
                called before each retry sleep.
            on_exhausted: ``async callback(node_id, error_history)``
                called when all retries are used up.
            **kwargs: Forwarded to *fn*.

        Returns:
            The dict returned by *fn* on success.

        Raises:
            NodeExecutionError: When all retries are exhausted.
        """
        error_history: list[str] = []

        for attempt in range(self.max_retries + 1):
            try:
                result = await fn(**kwargs)
                if attempt > 0:
                    logger.info(
                        "Node '%s' succeeded on retry attempt %d",
                        node_id,
                        attempt,
                    )
                return result

            except Exception as exc:
                error_msg = f"[attempt {attempt + 1}] {type(exc).__name__}: {exc}"
                error_history.append(error_msg)
                logger.warning(
                    "Node '%s' failed (attempt %d/%d): %s",
                    node_id,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )

                if attempt < self.max_retries:
                    delay = self.get_delay(attempt)
                    if on_retry:
                        await on_retry(node_id, attempt + 1, error_msg)
                    logger.info(
                        "Retrying node '%s' in %.1fs …", node_id, delay
                    )
                    await asyncio.sleep(delay)
                else:
                    # All retries exhausted
                    if on_exhausted:
                        await on_exhausted(node_id, error_history)

        raise NodeExecutionError(node_id, error_history)


class NodeExecutionError(Exception):
    """Raised when a node exhausts all retry attempts."""

    def __init__(self, node_id: str, error_history: list[str]):
        self.node_id = node_id
        self.error_history = error_history
        combined = " | ".join(error_history)
        super().__init__(
            f"Node '{node_id}' failed after {len(error_history)} attempts: "
            f"{combined}"
        )


# ── Compensation Router ───────────────────────────────────────────────


class CompensationRouter:
    """
    Routes failed nodes to compensation branches or human-approval gates.

    Compensation mapping is defined in the ExecutionGraph metadata or
    per-node AgentConfig.  If no compensation target exists, the router
    escalates to the HITL approval gate.
    """

    def __init__(
        self,
        compensation_map: Optional[Dict[str, str]] = None,
    ):
        # node_id → compensation_node_id
        self._map: Dict[str, str] = compensation_map or {}

    def register(self, failed_node_id: str, compensation_node_id: str) -> None:
        """Register a compensation target for a node."""
        self._map[failed_node_id] = compensation_node_id

    def has_compensation(self, node_id: str) -> bool:
        """Check whether a compensation branch exists for *node_id*."""
        return node_id in self._map

    def get_compensation_target(self, node_id: str) -> Optional[str]:
        """Return the compensation node ID, or None."""
        return self._map.get(node_id)

    async def route(
        self,
        node_id: str,
        error_history: list[str],
        *,
        on_compensate: Optional[
            Callable[[str, str, list[str]], Coroutine[Any, Any, None]]
        ] = None,
        on_escalate: Optional[
            Callable[[str, list[str]], Coroutine[Any, Any, None]]
        ] = None,
    ) -> str:
        """
        Determine compensation strategy for a failed node.

        Args:
            node_id: The node that exhausted retries.
            error_history: All error messages collected during retries.
            on_compensate: ``async callback(node_id, comp_node_id, errors)``
            on_escalate: ``async callback(node_id, errors)`` when no
                compensation exists.

        Returns:
            ``"compensating"`` if a branch was triggered,
            ``"waiting_for_approval"`` if escalated to HITL.
        """
        target = self.get_compensation_target(node_id)

        if target:
            logger.info(
                "Routing node '%s' to compensation branch '%s'",
                node_id,
                target,
            )
            if on_compensate:
                await on_compensate(node_id, target, error_history)
            return ExecutionStatus.COMPENSATING.value

        # No compensation defined — escalate to HITL
        logger.warning(
            "No compensation for node '%s' — escalating to human approval.",
            node_id,
        )
        if on_escalate:
            await on_escalate(node_id, error_history)
        return ExecutionStatus.WAITING_FOR_APPROVAL.value


# ── Error-Context Prompt Injection ─────────────────────────────────────


def build_retry_context(
    original_prompt: str,
    error_history: list[str],
) -> str:
    """
    Append error context to an agent's system prompt so it can learn
    from prior failures on retry.
    """
    if not error_history:
        return original_prompt

    context_block = "\n".join(
        f"  - {err}" for err in error_history
    )
    return (
        f"{original_prompt}\n\n"
        f"[RETRY CONTEXT — previous attempts failed with these errors:\n"
        f"{context_block}\n"
        f"Adjust your approach to avoid repeating these failures.]"
    )
