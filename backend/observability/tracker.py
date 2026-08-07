"""
AE-03 Structured Event Tracker (Directive V2).

Emits typed, structured events for every significant operation in the
execution pipeline. Events form the backbone of the observability system.

Event Types (23 total):
  RUN_CREATED, PLAN_CREATED, GRAPH_COMPILED, SECURITY_CHECK,
  TOOL_REQUESTED, TOOL_ALLOWED, TOOL_DENIED, TOOL_EXECUTED,
  AGENT_STARTED, AGENT_COMPLETED, AGENT_FAILED, RETRY, REPLAN,
  RAG_SEARCH, SOURCE_RETRIEVED, CRITIC_STARTED, CRITIC_COMPLETED,
  CRITIC_FAILED, VERIFICATION_STARTED, VERIFICATION_COMPLETED,
  APPROVAL_REQUESTED, APPROVED, REJECTED, REPORT_CREATED, RUN_COMPLETED

All events are immutable and append-only — they form the audit trail.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Event Types ──────────────────────────────────────────────────────


class EventType(str, Enum):
    """All structured event types emitted during execution."""
    RUN_CREATED = "RUN_CREATED"
    PLAN_CREATED = "PLAN_CREATED"
    GRAPH_COMPILED = "GRAPH_COMPILED"
    SECURITY_CHECK = "SECURITY_CHECK"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    TOOL_ALLOWED = "TOOL_ALLOWED"
    TOOL_DENIED = "TOOL_DENIED"
    TOOL_EXECUTED = "TOOL_EXECUTED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    RETRY = "RETRY"
    REPLAN = "REPLAN"
    RAG_SEARCH = "RAG_SEARCH"
    SOURCE_RETRIEVED = "SOURCE_RETRIEVED"
    CRITIC_STARTED = "CRITIC_STARTED"
    CRITIC_COMPLETED = "CRITIC_COMPLETED"
    CRITIC_FAILED = "CRITIC_FAILED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REPORT_CREATED = "REPORT_CREATED"
    RUN_COMPLETED = "RUN_COMPLETED"


# ── Event Model ──────────────────────────────────────────────────────


class TraceEvent:
    """
    An immutable structured event in the execution trace.

    Attributes:
        event_id: Unique event identifier.
        event_type: One of the 25 EventType values.
        run_id: Associated execution run.
        timestamp: Unix timestamp of emission.
        data: Event-specific payload.
        agent_role: Agent that produced this event (if applicable).
        task_id: Task that produced this event (if applicable).
        duration_ms: Duration of the operation (if applicable).
    """

    __slots__ = (
        "event_id", "event_type", "run_id", "timestamp",
        "data", "agent_role", "task_id", "duration_ms",
    )

    def __init__(
        self,
        event_type: EventType,
        run_id: str = "",
        data: Optional[Dict[str, Any]] = None,
        agent_role: str = "",
        task_id: str = "",
        duration_ms: float = 0.0,
    ):
        self.event_id = f"evt-{uuid.uuid4().hex[:8]}"
        self.event_type = event_type
        self.run_id = run_id
        self.timestamp = time.time()
        self.data = data or {}
        self.agent_role = agent_role
        self.task_id = task_id
        self.duration_ms = duration_ms

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "data": self.data,
            "agent_role": self.agent_role,
            "task_id": self.task_id,
            "duration_ms": round(self.duration_ms, 1),
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), default=str)

    def __repr__(self) -> str:
        return (
            f"TraceEvent({self.event_type.value}, run={self.run_id}, "
            f"agent={self.agent_role}, task={self.task_id})"
        )


# ── Event Tracker ────────────────────────────────────────────────────


class EventTracker:
    """
    Structured event emitter and collector.

    Collects all events for a run in chronological order.
    Supports event listeners for real-time SSE streaming.

    Usage::

        tracker = EventTracker()

        # Emit events
        tracker.emit(EventType.RUN_CREATED, run_id="run-123",
                     data={"goal": "Research AI"})
        tracker.emit(EventType.AGENT_STARTED, run_id="run-123",
                     agent_role="researcher", task_id="task-001")

        # Get events
        events = tracker.get_events("run-123")

        # Register listener for SSE
        tracker.add_listener(lambda event: send_sse(event))
    """

    def __init__(self) -> None:
        self._events: Dict[str, List[TraceEvent]] = {}  # run_id -> events
        self._global_events: List[TraceEvent] = []
        self._listeners: List[Callable[[TraceEvent], None]] = []

    def emit(
        self,
        event_type: EventType,
        run_id: str = "",
        data: Optional[Dict[str, Any]] = None,
        agent_role: str = "",
        task_id: str = "",
        duration_ms: float = 0.0,
    ) -> TraceEvent:
        """
        Emit a structured event.

        The event is:
          1. Stored in the run's event list
          2. Stored in the global event list
          3. Dispatched to all registered listeners

        Returns the created TraceEvent.
        """
        event = TraceEvent(
            event_type=event_type,
            run_id=run_id,
            data=data,
            agent_role=agent_role,
            task_id=task_id,
            duration_ms=duration_ms,
        )

        # Store
        if run_id:
            self._events.setdefault(run_id, []).append(event)
        self._global_events.append(event)

        # Log
        logger.info(
            "[Tracker] %s run=%s agent=%s task=%s",
            event_type.value,
            run_id or "-",
            agent_role or "-",
            task_id or "-",
        )

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.warning("Event listener error: %s", e)

        return event

    # ── Convenience Emitters ──────────────────────────────────────────

    def emit_run_created(self, run_id: str, goal: str, user_id: str = "") -> TraceEvent:
        return self.emit(EventType.RUN_CREATED, run_id, {"goal": goal, "user_id": user_id})

    def emit_plan_created(self, run_id: str, task_count: int, template: str = "") -> TraceEvent:
        return self.emit(EventType.PLAN_CREATED, run_id, {"task_count": task_count, "template": template})

    def emit_agent_started(self, run_id: str, agent_role: str, task_id: str, description: str = "") -> TraceEvent:
        return self.emit(EventType.AGENT_STARTED, run_id, {"description": description}, agent_role, task_id)

    def emit_agent_completed(self, run_id: str, agent_role: str, task_id: str, duration_ms: float = 0.0, tokens: int = 0) -> TraceEvent:
        return self.emit(EventType.AGENT_COMPLETED, run_id, {"tokens": tokens}, agent_role, task_id, duration_ms)

    def emit_agent_failed(self, run_id: str, agent_role: str, task_id: str, error: str = "") -> TraceEvent:
        return self.emit(EventType.AGENT_FAILED, run_id, {"error": error}, agent_role, task_id)

    def emit_tool_requested(self, run_id: str, tool_name: str, agent_role: str = "") -> TraceEvent:
        return self.emit(EventType.TOOL_REQUESTED, run_id, {"tool_name": tool_name}, agent_role)

    def emit_tool_allowed(self, run_id: str, tool_name: str, agent_role: str = "") -> TraceEvent:
        return self.emit(EventType.TOOL_ALLOWED, run_id, {"tool_name": tool_name}, agent_role)

    def emit_tool_denied(self, run_id: str, tool_name: str, agent_role: str = "", reason: str = "") -> TraceEvent:
        return self.emit(EventType.TOOL_DENIED, run_id, {"tool_name": tool_name, "reason": reason}, agent_role)

    def emit_security_check(self, run_id: str, verdict: str, rule: str = "", agent_role: str = "") -> TraceEvent:
        return self.emit(EventType.SECURITY_CHECK, run_id, {"verdict": verdict, "rule": rule}, agent_role)

    def emit_approval_requested(self, run_id: str, approval_id: str, tool_name: str = "") -> TraceEvent:
        return self.emit(EventType.APPROVAL_REQUESTED, run_id, {"approval_id": approval_id, "tool_name": tool_name})

    def emit_approved(self, run_id: str, approval_id: str) -> TraceEvent:
        return self.emit(EventType.APPROVED, run_id, {"approval_id": approval_id})

    def emit_rejected(self, run_id: str, approval_id: str, reason: str = "") -> TraceEvent:
        return self.emit(EventType.REJECTED, run_id, {"approval_id": approval_id, "reason": reason})

    def emit_run_completed(self, run_id: str, status: str, duration_ms: float = 0.0, total_cost: float = 0.0) -> TraceEvent:
        return self.emit(EventType.RUN_COMPLETED, run_id, {"status": status, "total_cost_usd": total_cost}, duration_ms=duration_ms)

    # ── Queries ───────────────────────────────────────────────────────

    def get_events(self, run_id: str) -> List[Dict[str, Any]]:
        """Return all events for a run in chronological order."""
        return [e.to_dict() for e in self._events.get(run_id, [])]

    def get_events_by_type(self, run_id: str, event_type: EventType) -> List[Dict[str, Any]]:
        """Return events of a specific type for a run."""
        return [
            e.to_dict()
            for e in self._events.get(run_id, [])
            if e.event_type == event_type
        ]

    def get_event_count(self, run_id: str) -> int:
        """Return total event count for a run."""
        return len(self._events.get(run_id, []))

    def get_all_run_ids(self) -> List[str]:
        """Return all run IDs with events."""
        return list(self._events.keys())

    def get_event_summary(self, run_id: str) -> Dict[str, int]:
        """Return event type counts for a run."""
        summary: Dict[str, int] = {}
        for e in self._events.get(run_id, []):
            summary[e.event_type.value] = summary.get(e.event_type.value, 0) + 1
        return summary

    def get_timeline(self, run_id: str) -> List[Dict[str, Any]]:
        """Return a simplified timeline for UI rendering."""
        events = self._events.get(run_id, [])
        if not events:
            return []
        start_time = events[0].timestamp
        return [
            {
                "event_type": e.event_type.value,
                "offset_ms": round((e.timestamp - start_time) * 1000, 1),
                "agent_role": e.agent_role,
                "task_id": e.task_id,
                "duration_ms": e.duration_ms,
            }
            for e in events
        ]

    # ── Listeners ─────────────────────────────────────────────────────

    def add_listener(self, callback: Callable[[TraceEvent], None]) -> None:
        """Register an event listener for real-time notifications."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[TraceEvent], None]) -> None:
        """Remove an event listener."""
        self._listeners = [l for l in self._listeners if l is not callback]

    def clear_listeners(self) -> None:
        """Remove all listeners."""
        self._listeners.clear()
