"""
AE-03 Human-in-the-Loop (HITL) Approval Gate.

Provides an asynchronous approval mechanism that pauses node execution
when high-risk operations or verification failures require human sign-off.

Gate lifecycle:
  1. ``request_approval()`` — sets node status to WAITING_FOR_APPROVAL
     and emits an SSE-compatible event.
  2. The gate suspends execution via ``asyncio.Event``.
  3. ``resolve()`` is called (from a FastAPI endpoint or programmatically)
     with APPROVE or REJECT.
  4. On APPROVE: resume execution from the paused node.
  5. On REJECT: mark node as FAILED, trigger compensation branch.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.schemas.contracts import ExecutionStatus
from backend.schemas.artifacts import TraceEvent, TraceEventType

logger = logging.getLogger(__name__)


# ── Approval Action ────────────────────────────────────────────────────


class ApprovalAction(str, Enum):
    """Actions that a human reviewer can take."""

    APPROVE = "approve"
    REJECT = "reject"


# ── Approval Request Model ────────────────────────────────────────────


class ApprovalRequest(BaseModel):
    """Represents a pending approval request for a specific node."""

    request_id: str = Field(
        default_factory=lambda: f"approval-{uuid.uuid4().hex[:8]}",
        description="Unique identifier for this approval request.",
    )
    run_id: str = Field(description="ID of the execution run.")
    node_id: str = Field(description="ID of the node awaiting approval.")
    agent_id: str = Field(
        default="",
        description="ID of the agent that triggered the gate.",
    )
    reason: str = Field(
        default="",
        description="Why approval is required.",
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="Tool that triggered the approval gate (if applicable).",
    )
    payload_preview: Dict[str, Any] = Field(
        default_factory=dict,
        description="Preview of the payload for human review.",
    )
    requested_at: float = Field(
        default_factory=time.time,
        description="Unix timestamp when approval was requested.",
    )
    resolved_at: Optional[float] = Field(
        default=None,
        description="Unix timestamp when the request was resolved.",
    )
    action: Optional[ApprovalAction] = Field(
        default=None,
        description="The action taken by the reviewer.",
    )
    reviewer_notes: str = Field(
        default="",
        description="Optional notes from the reviewer.",
    )

    @property
    def is_pending(self) -> bool:
        return self.action is None

    @property
    def is_approved(self) -> bool:
        return self.action == ApprovalAction.APPROVE

    @property
    def is_rejected(self) -> bool:
        return self.action == ApprovalAction.REJECT

    @property
    def wait_time_seconds(self) -> float:
        end = self.resolved_at or time.time()
        return end - self.requested_at


# ── Approval Gate ──────────────────────────────────────────────────────


class ApprovalGate:
    """
    Asynchronous Human-in-the-Loop approval gate.

    Manages pending approval requests and provides ``asyncio.Event``-based
    suspension/resumption of node execution.

    Usage::

        gate = ApprovalGate(trace_events)

        # In the executor (when approval is needed):
        request = await gate.request_approval(
            run_id="run-abc123",
            node_id="node-critic-01",
            agent_id="agent-xyz",
            reason="High-risk tool invocation detected",
        )

        # Wait for human decision:
        action = await gate.wait_for_decision(request.request_id, timeout=300)

        # From FastAPI endpoint (when human responds):
        gate.resolve(request.request_id, ApprovalAction.APPROVE)
    """

    def __init__(
        self,
        trace_events: Optional[List[TraceEvent]] = None,
        *,
        on_approval_requested: Optional[
            Callable[[ApprovalRequest], Coroutine[Any, Any, None]]
        ] = None,
        on_approval_resolved: Optional[
            Callable[[ApprovalRequest], Coroutine[Any, Any, None]]
        ] = None,
    ):
        self._trace: List[TraceEvent] = trace_events if trace_events is not None else []
        self._pending: Dict[str, ApprovalRequest] = {}
        self._events: Dict[str, asyncio.Event] = {}
        self._history: List[ApprovalRequest] = []
        self._on_requested = on_approval_requested
        self._on_resolved = on_approval_resolved

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def pending_count(self) -> int:
        return sum(1 for r in self._pending.values() if r.is_pending)

    @property
    def pending_requests(self) -> List[ApprovalRequest]:
        return [r for r in self._pending.values() if r.is_pending]

    @property
    def history(self) -> List[ApprovalRequest]:
        return list(self._history)

    # ── Request Approval ───────────────────────────────────────────────

    async def request_approval(
        self,
        run_id: str,
        node_id: str,
        *,
        agent_id: str = "",
        reason: str = "",
        tool_name: Optional[str] = None,
        payload_preview: Optional[Dict[str, Any]] = None,
    ) -> ApprovalRequest:
        """
        Create an approval request and suspend execution.

        This emits a ``HUMAN_APPROVAL_REQUESTED`` trace event and
        registers the request in the pending queue.

        Args:
            run_id: Current execution run ID.
            node_id: ID of the node requiring approval.
            agent_id: ID of the agent that triggered the gate.
            reason: Human-readable explanation of why approval is needed.
            tool_name: The tool that triggered the gate (if applicable).
            payload_preview: Data preview for the reviewer.

        Returns:
            The created ApprovalRequest.
        """
        request = ApprovalRequest(
            run_id=run_id,
            node_id=node_id,
            agent_id=agent_id,
            reason=reason,
            tool_name=tool_name,
            payload_preview=payload_preview or {},
        )

        self._pending[request.request_id] = request
        self._events[request.request_id] = asyncio.Event()

        self._emit_trace(
            TraceEventType.HUMAN_APPROVAL_REQUESTED,
            run_id=run_id,
            node_id=node_id,
            data={
                "request_id": request.request_id,
                "agent_id": agent_id,
                "reason": reason,
                "tool_name": tool_name,
            },
        )

        logger.info(
            "Approval requested: request_id='%s' node='%s' reason='%s'",
            request.request_id,
            node_id,
            reason,
        )

        if self._on_requested:
            await self._on_requested(request)

        return request

    # ── Wait for Decision ──────────────────────────────────────────────

    async def wait_for_decision(
        self,
        request_id: str,
        *,
        timeout: float = 300.0,
    ) -> ApprovalAction:
        """
        Block until the approval request is resolved or timeout.

        Args:
            request_id: ID of the approval request to wait on.
            timeout: Maximum seconds to wait (default: 5 minutes).

        Returns:
            The ApprovalAction taken by the reviewer.

        Raises:
            TimeoutError: If no decision is made within ``timeout``.
            KeyError: If ``request_id`` is not a known pending request.
        """
        if request_id not in self._events:
            raise KeyError(f"Unknown approval request: {request_id}")

        event = self._events[request_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Auto-reject on timeout
            logger.warning(
                "Approval request '%s' timed out after %.0fs — auto-rejecting.",
                request_id,
                timeout,
            )
            self.resolve(
                request_id,
                ApprovalAction.REJECT,
                reviewer_notes=f"Auto-rejected: timeout after {timeout}s",
            )

        request = self._pending.get(request_id)
        if request and request.action:
            return request.action

        return ApprovalAction.REJECT

    # ── Resolve ────────────────────────────────────────────────────────

    def resolve(
        self,
        request_id: str,
        action: ApprovalAction,
        *,
        reviewer_notes: str = "",
    ) -> ApprovalRequest:
        """
        Resolve a pending approval request.

        Called from a FastAPI endpoint or programmatically:

            POST /api/workflow/approve/{run_id}
            Body: {"action": "approve" | "reject"}

        Args:
            request_id: ID of the approval request.
            action: APPROVE or REJECT.
            reviewer_notes: Optional notes from the reviewer.

        Returns:
            The resolved ApprovalRequest.

        Raises:
            KeyError: If ``request_id`` is not found.
            ValueError: If the request was already resolved.
        """
        if request_id not in self._pending:
            raise KeyError(f"Unknown approval request: {request_id}")

        request = self._pending[request_id]
        if not request.is_pending:
            raise ValueError(
                f"Request '{request_id}' already resolved as "
                f"'{request.action.value}'."
            )

        request.action = action
        request.resolved_at = time.time()
        request.reviewer_notes = reviewer_notes

        # Emit trace event
        event_type = (
            TraceEventType.HUMAN_APPROVAL_GRANTED
            if action == ApprovalAction.APPROVE
            else TraceEventType.HUMAN_APPROVAL_REJECTED
        )
        self._emit_trace(
            event_type,
            run_id=request.run_id,
            node_id=request.node_id,
            data={
                "request_id": request_id,
                "action": action.value,
                "reviewer_notes": reviewer_notes,
                "wait_time_seconds": round(request.wait_time_seconds, 2),
            },
        )

        logger.info(
            "Approval resolved: request_id='%s' action='%s' "
            "wait_time=%.1fs",
            request_id,
            action.value,
            request.wait_time_seconds,
        )

        # Move to history
        self._history.append(request)

        # Signal the waiting coroutine
        if request_id in self._events:
            self._events[request_id].set()

        return request

    # ── Query ──────────────────────────────────────────────────────────

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Look up an approval request by ID (pending or historical)."""
        if request_id in self._pending:
            return self._pending[request_id]
        for req in self._history:
            if req.request_id == request_id:
                return req
        return None

    def get_pending_for_run(self, run_id: str) -> List[ApprovalRequest]:
        """Return all pending approval requests for a specific run."""
        return [
            r for r in self._pending.values()
            if r.run_id == run_id and r.is_pending
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Return summary statistics."""
        approvals = sum(1 for r in self._history if r.is_approved)
        rejections = sum(1 for r in self._history if r.is_rejected)
        avg_wait = 0.0
        if self._history:
            avg_wait = sum(r.wait_time_seconds for r in self._history) / len(
                self._history
            )
        return {
            "pending": self.pending_count,
            "total_resolved": len(self._history),
            "approvals": approvals,
            "rejections": rejections,
            "avg_wait_time_seconds": round(avg_wait, 2),
        }

    # ── Trace Emission ─────────────────────────────────────────────────

    def _emit_trace(
        self,
        event_type: TraceEventType,
        *,
        run_id: str = "",
        node_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = TraceEvent(
            event_type=event_type,
            run_id=run_id,
            node_id=node_id,
            data=data or {},
        )
        self._trace.append(event)
