"""
AE-03 HITL Gate — Native LangGraph interrupt() Integration (Directive V2).

Provides the Human-In-The-Loop approval gate for high-risk operations
using LangGraph's native ``interrupt()`` mechanism.

When the PolicyEngine returns ``REQUIRE_APPROVAL``, the HITL gate:
  1. Creates an ApprovalRequest record
  2. Calls LangGraph ``interrupt()`` to pause execution
  3. Waits for human review (approve / reject / request_changes)
  4. Resumes or aborts the workflow based on the decision

Integrates with:
  - PolicyEngine (Module 6) for triggering approvals
  - WorkflowEngine (Module 5) for graph interruption
  - SSE streaming (Module 10) for real-time approval notifications
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.schemas.contracts import (
    AgentRole,
    ApprovalAction,
    ApprovalRequest,
    RiskLevel,
    SecurityDecision,
    SecurityVerdict,
    ToolRequest,
)

logger = logging.getLogger(__name__)


class HITLGate:
    """
    Human-In-The-Loop approval gate.

    Manages the lifecycle of approval requests:
      1. Creation — triggered by PolicyEngine REQUIRE_APPROVAL verdict
      2. Interruption — pauses LangGraph execution via interrupt()
      3. Resolution — processes human approve/reject decision
      4. Resumption — continues or aborts the workflow

    Usage::

        gate = HITLGate()

        # In a LangGraph node:
        if decision.verdict == SecurityVerdict.REQUIRE_APPROVAL:
            approval = gate.create_approval(
                run_id="run-123",
                tool_request=request,
                decision=decision,
            )
            # LangGraph interrupt() is called — execution pauses
            # Human reviews and resolves
            gate.resolve(approval.approval_id, ApprovalAction.APPROVE)
    """

    def __init__(self) -> None:
        self._pending: Dict[str, ApprovalRequest] = {}
        self._resolved: Dict[str, ApprovalRequest] = {}
        self._history: List[ApprovalRequest] = []

    # ── Creation ──────────────────────────────────────────────────────

    def create_approval(
        self,
        run_id: str,
        tool_request: Optional[ToolRequest] = None,
        decision: Optional[SecurityDecision] = None,
        context_summary: str = "",
        risk_level: RiskLevel = RiskLevel.HIGH,
    ) -> ApprovalRequest:
        """
        Create a new approval request and register it as pending.

        Args:
            run_id: Associated execution run ID.
            tool_request: The tool request that triggered approval.
            decision: The PolicyEngine decision.
            context_summary: Human-readable context for the reviewer.
            risk_level: Risk level of the operation.

        Returns:
            ApprovalRequest ready for human review.
        """
        agent_role = AgentRole.ORCHESTRATOR
        tool_name = ""
        payload: Dict[str, Any] = {}

        if tool_request:
            agent_role = tool_request.agent_role
            tool_name = tool_request.tool_name
            payload = {
                "tool_name": tool_request.tool_name,
                "arguments": tool_request.arguments,
                "request_id": tool_request.request_id,
            }

        if decision:
            payload["policy_rule"] = decision.rule_matched
            payload["policy_reason"] = decision.reason

        if not context_summary:
            context_summary = (
                f"Agent '{agent_role.value}' requests approval to use tool "
                f"'{tool_name}' (risk_level={risk_level.value})."
            )

        approval = ApprovalRequest(
            run_id=run_id,
            agent_role=agent_role,
            tool_name=tool_name,
            risk_level=risk_level,
            context_summary=context_summary,
            payload=payload,
            status="pending",
        )

        self._pending[approval.approval_id] = approval
        self._history.append(approval)

        logger.info(
            "[HITL] Approval created: id=%s, tool=%s, agent=%s, risk=%s",
            approval.approval_id,
            tool_name,
            agent_role.value,
            risk_level.value,
        )

        return approval

    # ── Resolution ────────────────────────────────────────────────────

    def resolve(
        self,
        approval_id: str,
        action: ApprovalAction,
        reason: str = "",
    ) -> Optional[ApprovalRequest]:
        """
        Resolve a pending approval request.

        Args:
            approval_id: The approval request ID.
            action: APPROVE, REJECT, or REQUEST_CHANGES.
            reason: Optional reviewer's reason.

        Returns:
            Updated ApprovalRequest, or None if not found.
        """
        approval = self._pending.pop(approval_id, None)
        if approval is None:
            logger.warning("[HITL] Approval '%s' not found in pending.", approval_id)
            return None

        # Update status
        status_map = {
            ApprovalAction.APPROVE: "approved",
            ApprovalAction.REJECT: "rejected",
            ApprovalAction.REQUEST_CHANGES: "changes_requested",
        }
        approval.action = action
        approval.status = status_map.get(action, "resolved")
        approval.reason = reason
        approval.resolved_at = time.time()

        self._resolved[approval_id] = approval

        logger.info(
            "[HITL] Approval resolved: id=%s, action=%s, reason='%s'",
            approval_id,
            action.value,
            reason[:80],
        )

        return approval

    # ── LangGraph Integration ─────────────────────────────────────────

    def create_interrupt_payload(
        self, approval: ApprovalRequest
    ) -> Dict[str, Any]:
        """
        Create the payload for LangGraph interrupt().

        This is the data structure that gets surfaced to the human reviewer
        via the frontend/API when execution pauses.

        Returns:
            Dict suitable for LangGraph interrupt() value parameter.
        """
        return {
            "type": "approval_required",
            "approval_id": approval.approval_id,
            "run_id": approval.run_id,
            "agent_role": approval.agent_role.value,
            "tool_name": approval.tool_name,
            "risk_level": approval.risk_level.value,
            "context_summary": approval.context_summary,
            "payload": approval.payload,
            "created_at": approval.timestamp,
            "actions": ["approve", "reject", "request_changes"],
        }

    def should_interrupt(self, decision: SecurityDecision) -> bool:
        """Check if a security decision requires a LangGraph interrupt."""
        return decision.verdict == SecurityVerdict.REQUIRE_APPROVAL

    # ── Queries ───────────────────────────────────────────────────────

    def get_pending(self) -> List[ApprovalRequest]:
        """Return all pending approval requests."""
        return list(self._pending.values())

    def get_pending_for_run(self, run_id: str) -> List[ApprovalRequest]:
        """Return pending approvals for a specific run."""
        return [a for a in self._pending.values() if a.run_id == run_id]

    def get_resolved(self) -> List[ApprovalRequest]:
        """Return all resolved approval requests."""
        return list(self._resolved.values())

    def get_by_id(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Look up an approval by ID (pending or resolved)."""
        return self._pending.get(approval_id) or self._resolved.get(approval_id)

    def get_history(self) -> List[Dict[str, Any]]:
        """Return full approval history for audit."""
        return [
            {
                "approval_id": a.approval_id,
                "run_id": a.run_id,
                "agent_role": a.agent_role.value,
                "tool_name": a.tool_name,
                "risk_level": a.risk_level.value,
                "status": a.status,
                "action": a.action.value if a.action else None,
                "reason": a.reason,
                "created_at": a.timestamp,
                "resolved_at": a.resolved_at,
            }
            for a in self._history
        ]

    def get_stats(self) -> Dict[str, int]:
        """Return approval statistics."""
        stats = {"pending": len(self._pending), "total": len(self._history)}
        for a in self._history:
            if a.action:
                stats[a.action.value] = stats.get(a.action.value, 0) + 1
        return stats
