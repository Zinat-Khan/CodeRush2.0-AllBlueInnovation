"""
AE-03 Safety Interceptor — Pre-Execution Middleware.

Wraps every tool call with permission and content-safety checks before
allowing execution.  Logs all interception events to the tracer.

Usage::

    interceptor = SafetyInterceptor(policy_engine, trace_events)
    result = await interceptor.intercept(agent_config, tool_name, tool_input, run_id)
    if not result.allowed:
        # Block execution, log event
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.schemas.contracts import AgentConfig
from backend.schemas.artifacts import TraceEvent, TraceEventType
from backend.safety.policy_engine import PolicyEngine
from backend.safety.permissions import (
    PermissionResult,
    SafetyResult,
    ThreatSeverity,
)

logger = logging.getLogger(__name__)


# ── Interception Result ────────────────────────────────────────────────


class InterceptionResult(BaseModel):
    """Combined result of the safety interceptor's pre-execution check."""

    allowed: bool = Field(
        description="Whether the tool call may proceed.",
    )
    permission_result: PermissionResult = Field(
        description="Outcome of the tool-permission check.",
    )
    safety_result: SafetyResult = Field(
        description="Outcome of the content-safety check.",
    )
    blocked_reason: str = Field(
        default="",
        description="Human-readable reason if the call was blocked.",
    )
    intercepted_at: float = Field(
        default_factory=time.time,
        description="Unix timestamp of the interception.",
    )


# ── Safety Interceptor ────────────────────────────────────────────────


class SafetyInterceptor:
    """
    Pre-execution middleware that gates every tool call through the
    PolicyEngine before allowing execution.

    Responsibilities:
      1. Call ``PolicyEngine.check_permission()`` to verify tool access.
      2. Call ``PolicyEngine.check_content_safety()`` to detect adversarial
         input in tool arguments.
      3. Log all interception events (pass or fail) to the shared trace.
      4. Return an ``InterceptionResult`` that the executor checks before
         invoking the tool.

    Both checks must pass for a tool call to proceed.  If either fails,
    the call is blocked and a SECURITY_ALERT trace event is emitted.
    """

    def __init__(
        self,
        policy_engine: PolicyEngine,
        trace_events: Optional[List[TraceEvent]] = None,
    ):
        self._engine = policy_engine
        self._trace: List[TraceEvent] = trace_events if trace_events is not None else []
        self._interception_log: List[InterceptionResult] = []

    @property
    def interception_log(self) -> List[InterceptionResult]:
        """Read-only access to the full interception history."""
        return list(self._interception_log)

    @property
    def blocked_count(self) -> int:
        """Number of tool calls that were blocked."""
        return sum(1 for r in self._interception_log if not r.allowed)

    @property
    def allowed_count(self) -> int:
        """Number of tool calls that were allowed."""
        return sum(1 for r in self._interception_log if r.allowed)

    # ── Core Intercept ─────────────────────────────────────────────────

    async def intercept(
        self,
        agent_config: AgentConfig,
        tool_name: str,
        tool_input: str = "",
        *,
        run_id: str = "",
        node_id: Optional[str] = None,
    ) -> InterceptionResult:
        """
        Evaluate a tool call against the policy engine.

        Args:
            agent_config: Configuration of the requesting agent.
            tool_name: Name of the tool to be invoked.
            tool_input: Raw input/arguments for the tool (checked for
                adversarial content).
            run_id: Current execution run ID (for trace events).
            node_id: ID of the executing node (for trace events).

        Returns:
            InterceptionResult with the combined verdict.
        """
        perm_result, safety_result = self._engine.evaluate_tool_call(
            agent_config, tool_name, tool_input
        )

        allowed = perm_result.allowed and safety_result.safe
        blocked_reason = ""

        if not perm_result.allowed:
            blocked_reason = f"Permission denied: {perm_result.reason}"
        elif not safety_result.safe:
            blocked_reason = (
                f"Content safety violation: {safety_result.details}"
            )

        result = InterceptionResult(
            allowed=allowed,
            permission_result=perm_result,
            safety_result=safety_result,
            blocked_reason=blocked_reason,
        )

        self._interception_log.append(result)

        # Emit trace events
        if allowed:
            self._emit_trace(
                TraceEventType.TOOL_CALL,
                run_id=run_id,
                node_id=node_id,
                data={
                    "tool_name": tool_name,
                    "agent_id": agent_config.agent_id,
                    "role": agent_config.role.value,
                    "interceptor_verdict": "allowed",
                },
            )
        else:
            self._emit_trace(
                TraceEventType.SECURITY_ALERT,
                run_id=run_id,
                node_id=node_id,
                data={
                    "tool_name": tool_name,
                    "agent_id": agent_config.agent_id,
                    "role": agent_config.role.value,
                    "interceptor_verdict": "blocked",
                    "blocked_reason": blocked_reason,
                    "permission_allowed": perm_result.allowed,
                    "content_safe": safety_result.safe,
                    "threat_type": safety_result.threat_type,
                    "severity": (
                        safety_result.severity.value
                        if safety_result.severity
                        else None
                    ),
                },
            )
            logger.warning(
                "INTERCEPTOR BLOCKED: agent='%s' tool='%s' reason='%s'",
                agent_config.agent_id,
                tool_name,
                blocked_reason,
            )

        return result

    # ── Batch Intercept ────────────────────────────────────────────────

    async def intercept_batch(
        self,
        agent_config: AgentConfig,
        tool_calls: List[Dict[str, str]],
        *,
        run_id: str = "",
        node_id: Optional[str] = None,
    ) -> List[InterceptionResult]:
        """
        Evaluate multiple tool calls for a single agent.

        Args:
            agent_config: Configuration of the requesting agent.
            tool_calls: List of dicts with ``tool_name`` and optional
                ``tool_input`` keys.
            run_id: Current execution run ID.
            node_id: ID of the executing node.

        Returns:
            List of InterceptionResult, one per tool call.
        """
        results = []
        for call in tool_calls:
            result = await self.intercept(
                agent_config,
                call.get("tool_name", ""),
                call.get("tool_input", ""),
                run_id=run_id,
                node_id=node_id,
            )
            results.append(result)
        return results

    # ── Statistics ─────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return summary statistics of all intercepted calls."""
        return {
            "total_interceptions": len(self._interception_log),
            "allowed": self.allowed_count,
            "blocked": self.blocked_count,
            "block_rate_pct": (
                round(self.blocked_count / len(self._interception_log) * 100, 1)
                if self._interception_log
                else 0.0
            ),
        }

    def reset(self) -> None:
        """Clear the interception log (for testing or between runs)."""
        self._interception_log.clear()

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
