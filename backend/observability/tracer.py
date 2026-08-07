"""
AE-03 Cost Tracker & Immutable Audit Log (Directive V2).

Provides:
  - ``CostTracker``: Per-run and per-provider token & cost aggregation
  - ``AuditLog``: Immutable, append-only log of all security and workflow events
  - ``RunTrace``: Complete trace record for a finished run

The CostTracker integrates with the ModelRouter's ProviderStats and the
EventTracker to provide real-time and post-hoc cost visibility.

The AuditLog stores security decisions, approval events, and policy
violations as immutable records for compliance and debugging.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Cost Entry ────────────────────────────────────────────────────────


class CostEntry:
    """A single cost record for an LLM invocation."""

    __slots__ = (
        "entry_id", "run_id", "provider", "model", "agent_role",
        "task_id", "prompt_tokens", "completion_tokens", "total_tokens",
        "cost_usd", "latency_ms", "timestamp",
    )

    def __init__(
        self,
        run_id: str = "",
        provider: str = "",
        model: str = "",
        agent_role: str = "",
        task_id: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
    ):
        self.entry_id = f"cost-{uuid.uuid4().hex[:8]}"
        self.run_id = run_id
        self.provider = provider
        self.model = model
        self.agent_role = agent_role
        self.task_id = task_id
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "run_id": self.run_id,
            "provider": self.provider,
            "model": self.model,
            "agent_role": self.agent_role,
            "task_id": self.task_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": round(self.latency_ms, 1),
            "timestamp": self.timestamp,
        }


# ── Cost Tracker ──────────────────────────────────────────────────────


class CostTracker:
    """
    Per-run and per-provider token & cost aggregation.

    Tracks every LLM invocation's token usage and cost, providing
    real-time totals and per-provider breakdowns.

    Usage::

        tracker = CostTracker()
        tracker.record(run_id="run-123", provider="google",
                       model="gemini-1.5-pro", prompt_tokens=500,
                       completion_tokens=200, cost_usd=0.0016)
        summary = tracker.get_run_summary("run-123")
    """

    def __init__(self) -> None:
        self._entries: Dict[str, List[CostEntry]] = {}  # run_id -> entries
        self._all_entries: List[CostEntry] = []

    def record(
        self,
        run_id: str,
        provider: str = "",
        model: str = "",
        agent_role: str = "",
        task_id: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
    ) -> CostEntry:
        """Record a single LLM invocation cost."""
        entry = CostEntry(
            run_id=run_id,
            provider=provider,
            model=model,
            agent_role=agent_role,
            task_id=task_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        self._entries.setdefault(run_id, []).append(entry)
        self._all_entries.append(entry)
        return entry

    def get_run_summary(self, run_id: str) -> Dict[str, Any]:
        """Get cost summary for a specific run."""
        entries = self._entries.get(run_id, [])
        if not entries:
            return {"run_id": run_id, "total_cost_usd": 0.0, "total_tokens": 0, "calls": 0}

        total_tokens = sum(e.total_tokens for e in entries)
        total_cost = sum(e.cost_usd for e in entries)
        total_latency = sum(e.latency_ms for e in entries)

        # Per-provider breakdown
        provider_breakdown: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            p = entry.provider or "unknown"
            if p not in provider_breakdown:
                provider_breakdown[p] = {
                    "calls": 0, "tokens": 0, "cost_usd": 0.0, "latency_ms": 0.0,
                }
            provider_breakdown[p]["calls"] += 1
            provider_breakdown[p]["tokens"] += entry.total_tokens
            provider_breakdown[p]["cost_usd"] += entry.cost_usd
            provider_breakdown[p]["latency_ms"] += entry.latency_ms

        # Round values
        for p in provider_breakdown:
            provider_breakdown[p]["cost_usd"] = round(provider_breakdown[p]["cost_usd"], 6)
            provider_breakdown[p]["latency_ms"] = round(provider_breakdown[p]["latency_ms"], 1)

        return {
            "run_id": run_id,
            "total_cost_usd": round(total_cost, 6),
            "total_tokens": total_tokens,
            "total_prompt_tokens": sum(e.prompt_tokens for e in entries),
            "total_completion_tokens": sum(e.completion_tokens for e in entries),
            "total_latency_ms": round(total_latency, 1),
            "avg_latency_ms": round(total_latency / len(entries), 1),
            "calls": len(entries),
            "provider_breakdown": provider_breakdown,
        }

    def get_global_summary(self) -> Dict[str, Any]:
        """Get cost summary across all runs."""
        total_tokens = sum(e.total_tokens for e in self._all_entries)
        total_cost = sum(e.cost_usd for e in self._all_entries)
        return {
            "total_runs": len(self._entries),
            "total_calls": len(self._all_entries),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
        }

    def get_entries(self, run_id: str) -> List[Dict[str, Any]]:
        """Return all cost entries for a run."""
        return [e.to_dict() for e in self._entries.get(run_id, [])]

    def is_over_budget(self, run_id: str, max_cost: float) -> bool:
        """Check if a run has exceeded its budget."""
        entries = self._entries.get(run_id, [])
        return sum(e.cost_usd for e in entries) > max_cost

    def is_over_token_limit(self, run_id: str, max_tokens: int) -> bool:
        """Check if a run has exceeded its token limit."""
        entries = self._entries.get(run_id, [])
        return sum(e.total_tokens for e in entries) > max_tokens


# ── Audit Log ─────────────────────────────────────────────────────────


class AuditEntry:
    """An immutable audit log entry."""

    __slots__ = (
        "entry_id", "timestamp", "run_id", "event_type",
        "agent_role", "action", "details", "severity",
    )

    def __init__(
        self,
        event_type: str,
        run_id: str = "",
        agent_role: str = "",
        action: str = "",
        details: Optional[Dict[str, Any]] = None,
        severity: str = "info",
    ):
        self.entry_id = f"audit-{uuid.uuid4().hex[:8]}"
        self.timestamp = time.time()
        self.run_id = run_id
        self.event_type = event_type
        self.agent_role = agent_role
        self.action = action
        self.details = details or {}
        self.severity = severity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "agent_role": self.agent_role,
            "action": self.action,
            "details": self.details,
            "severity": self.severity,
        }


class AuditLog:
    """
    Immutable, append-only audit log for security and workflow events.

    Stores security decisions, approval events, policy violations,
    and significant workflow transitions as permanent records.

    Usage::

        audit = AuditLog()
        audit.log_security_decision(run_id="run-123", verdict="deny",
                                     rule="TOOL_NOT_REGISTERED", ...)
        audit.log_approval(run_id="run-123", approval_id="apr-456",
                           action="approve", ...)
        entries = audit.get_entries(run_id="run-123")
    """

    def __init__(self) -> None:
        self._entries: List[AuditEntry] = []
        self._by_run: Dict[str, List[AuditEntry]] = {}

    def _append(self, entry: AuditEntry) -> AuditEntry:
        """Append an entry (immutable add)."""
        self._entries.append(entry)
        if entry.run_id:
            self._by_run.setdefault(entry.run_id, []).append(entry)
        return entry

    def log_security_decision(
        self,
        run_id: str,
        verdict: str,
        rule: str = "",
        agent_role: str = "",
        tool_name: str = "",
        reason: str = "",
    ) -> AuditEntry:
        """Log a PolicyEngine security decision."""
        severity = "warning" if verdict == "deny" else "info"
        return self._append(AuditEntry(
            event_type="SECURITY_DECISION",
            run_id=run_id,
            agent_role=agent_role,
            action=verdict,
            details={"rule": rule, "tool_name": tool_name, "reason": reason},
            severity=severity,
        ))

    def log_approval(
        self,
        run_id: str,
        approval_id: str,
        action: str,
        agent_role: str = "",
        tool_name: str = "",
        reason: str = "",
    ) -> AuditEntry:
        """Log an HITL approval event."""
        return self._append(AuditEntry(
            event_type="APPROVAL_EVENT",
            run_id=run_id,
            agent_role=agent_role,
            action=action,
            details={"approval_id": approval_id, "tool_name": tool_name, "reason": reason},
            severity="info",
        ))

    def log_injection_detected(
        self,
        run_id: str,
        pattern_name: str,
        source: str = "",
        matched_text: str = "",
    ) -> AuditEntry:
        """Log a prompt injection detection."""
        return self._append(AuditEntry(
            event_type="INJECTION_DETECTED",
            run_id=run_id,
            action="blocked",
            details={"pattern": pattern_name, "source": source, "matched": matched_text[:100]},
            severity="critical",
        ))

    def log_budget_exceeded(
        self,
        run_id: str,
        current_cost: float,
        max_cost: float,
    ) -> AuditEntry:
        """Log a budget limit violation."""
        return self._append(AuditEntry(
            event_type="BUDGET_EXCEEDED",
            run_id=run_id,
            action="budget_violation",
            details={"current_cost_usd": current_cost, "max_cost_usd": max_cost},
            severity="warning",
        ))

    def log_workflow_event(
        self,
        run_id: str,
        event_type: str,
        agent_role: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """Log a general workflow event."""
        return self._append(AuditEntry(
            event_type=event_type,
            run_id=run_id,
            agent_role=agent_role,
            action="recorded",
            details=details or {},
            severity="info",
        ))

    # ── Queries ───────────────────────────────────────────────────────

    def get_entries(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return audit entries, optionally filtered by run_id."""
        entries = self._by_run.get(run_id, []) if run_id else self._entries
        return [e.to_dict() for e in entries]

    def get_security_events(self, run_id: str) -> List[Dict[str, Any]]:
        """Return security-related entries for a run."""
        return [
            e.to_dict()
            for e in self._by_run.get(run_id, [])
            if e.event_type in ("SECURITY_DECISION", "INJECTION_DETECTED", "APPROVAL_EVENT")
        ]

    def get_violations(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return violation entries (denials, injections, budget exceeded)."""
        source = self._by_run.get(run_id, []) if run_id else self._entries
        return [
            e.to_dict()
            for e in source
            if e.severity in ("warning", "critical") or e.action in ("deny", "blocked")
        ]

    def get_summary(self) -> Dict[str, Any]:
        """Return audit log summary."""
        severity_counts: Dict[str, int] = {}
        type_counts: Dict[str, int] = {}
        for e in self._entries:
            severity_counts[e.severity] = severity_counts.get(e.severity, 0) + 1
            type_counts[e.event_type] = type_counts.get(e.event_type, 0) + 1
        return {
            "total_entries": len(self._entries),
            "total_runs": len(self._by_run),
            "by_severity": severity_counts,
            "by_type": type_counts,
        }


# ── Run Trace ─────────────────────────────────────────────────────────


class RunTrace:
    """
    Complete trace record for a finished run.

    Aggregates events, cost entries, and audit entries into a single
    exportable object for post-hoc analysis and replay.
    """

    def __init__(
        self,
        run_id: str,
        events: List[Dict[str, Any]],
        cost_summary: Dict[str, Any],
        audit_entries: List[Dict[str, Any]],
        final_state: Optional[Dict[str, Any]] = None,
    ):
        self.run_id = run_id
        self.events = events
        self.cost_summary = cost_summary
        self.audit_entries = audit_entries
        self.final_state = final_state or {}
        self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "events": self.events,
            "cost_summary": self.cost_summary,
            "audit_entries": self.audit_entries,
            "final_state": self.final_state,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, indent=2)
