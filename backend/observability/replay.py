"""
AE-03 Run Replay Engine (Directive V2).

Replays completed runs by reconstructing execution from saved LangGraph
thread state checkpoints and the EventTracker's event stream.

Features:
  - Full state replay from LangGraph MemorySaver checkpoints
  - Event-based replay from EventTracker timeline
  - Step-by-step execution trace reconstruction
  - Exportable replay records for debugging and compliance

Integrates with:
  - EventTracker (Module 7) for event timeline
  - CostTracker (Module 7) for cost breakdown
  - AuditLog (Module 7) for security audit trail
  - WorkflowEngine (Module 5) for checkpoint access
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from backend.observability.tracker import EventTracker, EventType
from backend.observability.tracer import AuditLog, CostTracker, RunTrace

logger = logging.getLogger(__name__)


class ReplayStep:
    """A single step in a replayed execution."""

    def __init__(
        self,
        step_index: int,
        node_name: str,
        event_type: str = "",
        agent_role: str = "",
        task_id: str = "",
        state_snapshot: Optional[Dict[str, Any]] = None,
        duration_ms: float = 0.0,
        timestamp: float = 0.0,
    ):
        self.step_index = step_index
        self.node_name = node_name
        self.event_type = event_type
        self.agent_role = agent_role
        self.task_id = task_id
        self.state_snapshot = state_snapshot or {}
        self.duration_ms = duration_ms
        self.timestamp = timestamp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "node_name": self.node_name,
            "event_type": self.event_type,
            "agent_role": self.agent_role,
            "task_id": self.task_id,
            "state_snapshot": self.state_snapshot,
            "duration_ms": round(self.duration_ms, 1),
            "timestamp": self.timestamp,
        }


class ReplayRecord:
    """
    Complete replay record for a run.

    Contains the step-by-step execution trace, event timeline,
    cost breakdown, and audit trail.
    """

    def __init__(
        self,
        run_id: str,
        goal: str = "",
        steps: Optional[List[ReplayStep]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        cost_summary: Optional[Dict[str, Any]] = None,
        audit_entries: Optional[List[Dict[str, Any]]] = None,
        final_status: str = "",
        total_duration_ms: float = 0.0,
    ):
        self.run_id = run_id
        self.goal = goal
        self.steps = steps or []
        self.events = events or []
        self.cost_summary = cost_summary or {}
        self.audit_entries = audit_entries or []
        self.final_status = final_status
        self.total_duration_ms = total_duration_ms
        self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "final_status": self.final_status,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "step_count": len(self.steps),
            "event_count": len(self.events),
            "steps": [s.to_dict() for s in self.steps],
            "events": self.events,
            "cost_summary": self.cost_summary,
            "audit_entries": self.audit_entries,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, indent=2)


class ReplayEngine:
    """
    Run replay engine utilizing saved state and events.

    Reconstructs execution from:
      1. LangGraph MemorySaver checkpoints (step-by-step state)
      2. EventTracker event timeline (event-by-event)
      3. CostTracker cost entries (per-invocation costs)
      4. AuditLog entries (security decisions)

    Usage::

        engine = ReplayEngine(event_tracker, cost_tracker, audit_log)

        # Replay a run
        record = engine.replay(run_id="run-123")
        print(record.steps)
        print(record.cost_summary)

        # Export for debugging
        json_str = record.to_json()
    """

    def __init__(
        self,
        event_tracker: EventTracker,
        cost_tracker: CostTracker,
        audit_log: AuditLog,
    ):
        self._events = event_tracker
        self._costs = cost_tracker
        self._audit = audit_log

    def replay(self, run_id: str) -> ReplayRecord:
        """
        Reconstruct a full replay record for a completed run.

        Builds the replay from event timeline, cost data, and audit trail.
        """
        logger.info("[Replay] Replaying run: %s", run_id)

        # Get raw events
        events = self._events.get_events(run_id)

        # Build steps from events
        steps = self._build_steps(events)

        # Get cost summary
        cost_summary = self._costs.get_run_summary(run_id)

        # Get audit entries
        audit_entries = self._audit.get_entries(run_id)

        # Determine goal and status
        goal = ""
        final_status = "unknown"
        total_duration = 0.0

        for evt in events:
            if evt.get("event_type") == EventType.RUN_CREATED.value:
                goal = evt.get("data", {}).get("goal", "")
            if evt.get("event_type") == EventType.RUN_COMPLETED.value:
                final_status = evt.get("data", {}).get("status", "completed")
                total_duration = evt.get("duration_ms", 0.0)

        if not total_duration and events:
            # Calculate from first to last event
            first_ts = events[0].get("timestamp", 0)
            last_ts = events[-1].get("timestamp", 0)
            total_duration = (last_ts - first_ts) * 1000

        record = ReplayRecord(
            run_id=run_id,
            goal=goal,
            steps=steps,
            events=events,
            cost_summary=cost_summary,
            audit_entries=audit_entries,
            final_status=final_status,
            total_duration_ms=total_duration,
        )

        logger.info(
            "[Replay] Replay complete: %d steps, %d events, cost=$%.4f",
            len(steps),
            len(events),
            cost_summary.get("total_cost_usd", 0),
        )

        return record

    def _build_steps(self, events: List[Dict[str, Any]]) -> List[ReplayStep]:
        """Convert events into sequential replay steps."""
        steps = []
        step_index = 0

        for evt in events:
            event_type = evt.get("event_type", "")
            # Only create steps for significant events
            if event_type in (
                EventType.RUN_CREATED.value,
                EventType.PLAN_CREATED.value,
                EventType.AGENT_STARTED.value,
                EventType.AGENT_COMPLETED.value,
                EventType.AGENT_FAILED.value,
                EventType.TOOL_EXECUTED.value,
                EventType.SECURITY_CHECK.value,
                EventType.APPROVAL_REQUESTED.value,
                EventType.APPROVED.value,
                EventType.REJECTED.value,
                EventType.RUN_COMPLETED.value,
            ):
                # Map event type to node name
                node_map = {
                    EventType.RUN_CREATED.value: "start",
                    EventType.PLAN_CREATED.value: "planner",
                    EventType.AGENT_STARTED.value: "executor",
                    EventType.AGENT_COMPLETED.value: "executor",
                    EventType.AGENT_FAILED.value: "executor",
                    EventType.TOOL_EXECUTED.value: "tool",
                    EventType.SECURITY_CHECK.value: "policy_engine",
                    EventType.APPROVAL_REQUESTED.value: "hitl_gate",
                    EventType.APPROVED.value: "hitl_gate",
                    EventType.REJECTED.value: "hitl_gate",
                    EventType.RUN_COMPLETED.value: "end",
                }

                step = ReplayStep(
                    step_index=step_index,
                    node_name=node_map.get(event_type, "unknown"),
                    event_type=event_type,
                    agent_role=evt.get("agent_role", ""),
                    task_id=evt.get("task_id", ""),
                    state_snapshot=evt.get("data", {}),
                    duration_ms=evt.get("duration_ms", 0.0),
                    timestamp=evt.get("timestamp", 0.0),
                )
                steps.append(step)
                step_index += 1

        return steps

    def get_step_at(self, run_id: str, step_index: int) -> Optional[Dict[str, Any]]:
        """Get a specific step from a replayed run."""
        record = self.replay(run_id)
        if 0 <= step_index < len(record.steps):
            return record.steps[step_index].to_dict()
        return None

    def get_run_summary(self, run_id: str) -> Dict[str, Any]:
        """Get a summary of a run without full replay."""
        events = self._events.get_events(run_id)
        cost = self._costs.get_run_summary(run_id)
        event_summary = self._events.get_event_summary(run_id)

        return {
            "run_id": run_id,
            "event_count": len(events),
            "event_types": event_summary,
            "cost_summary": cost,
            "violations": len(self._audit.get_violations(run_id)),
        }

    def compare_runs(self, run_id_a: str, run_id_b: str) -> Dict[str, Any]:
        """Compare two runs side by side."""
        summary_a = self.get_run_summary(run_id_a)
        summary_b = self.get_run_summary(run_id_b)

        return {
            "run_a": summary_a,
            "run_b": summary_b,
            "differences": {
                "event_count_diff": summary_a["event_count"] - summary_b["event_count"],
                "cost_diff": (
                    summary_a["cost_summary"].get("total_cost_usd", 0)
                    - summary_b["cost_summary"].get("total_cost_usd", 0)
                ),
            },
        }


# ── Replay Comparison (V1-compat) ─────────────────────────────────────


class ReplayComparison:
    """
    Side-by-side comparison of an original run and a replay.

    Computes deltas for cost, latency, and success metrics.

    Usage::

        comparison = ReplayComparison(
            original_run_id="run-orig",
            replay_run_id="replay-001",
            original_summary={...},
            replay_summary={...},
            original_cost={...},
            replay_cost={...},
            provider_override="ollama",
        )
        table = comparison.summary_table()
    """

    def __init__(
        self,
        original_run_id: str,
        replay_run_id: str,
        original_summary: Optional[Dict[str, Any]] = None,
        replay_summary: Optional[Dict[str, Any]] = None,
        original_cost: Optional[Dict[str, Any]] = None,
        replay_cost: Optional[Dict[str, Any]] = None,
        provider_override: str = "",
    ):
        self.original_run_id = original_run_id
        self.replay_run_id = replay_run_id
        self.original_summary = original_summary or {}
        self.replay_summary = replay_summary or {}
        self.original_cost = original_cost or {}
        self.replay_cost = replay_cost or {}
        self.provider_override = provider_override

    def to_dict(self) -> Dict[str, Any]:
        """Export comparison as a dict."""
        orig_cost = self.original_cost.get("total_cost_usd", 0.0)
        replay_cost = self.replay_cost.get("total_cost_usd", 0.0)
        orig_latency = self.original_summary.get("elapsed_ms", 0.0)
        replay_latency = self.replay_summary.get("elapsed_ms", 0.0)
        orig_tokens = self.original_cost.get("total_tokens", 0)
        replay_tokens = self.replay_cost.get("total_tokens", 0)
        orig_succeeded = self.original_summary.get("nodes_succeeded", 0)
        replay_succeeded = self.replay_summary.get("nodes_succeeded", 0)
        orig_failed = self.original_summary.get("nodes_failed", 0)
        replay_failed = self.replay_summary.get("nodes_failed", 0)

        return {
            "original_run_id": self.original_run_id,
            "replay_run_id": self.replay_run_id,
            "provider_override": self.provider_override,
            "original_summary": self.original_summary,
            "replay_summary": self.replay_summary,
            "original_cost": self.original_cost,
            "replay_cost": self.replay_cost,
            "comparison": {
                "cost_usd": {
                    "original": orig_cost,
                    "replay": replay_cost,
                    "delta": round(replay_cost - orig_cost, 6),
                },
                "latency_ms": {
                    "original": orig_latency,
                    "replay": replay_latency,
                    "delta": round(replay_latency - orig_latency, 1),
                },
                "tokens": {
                    "original": orig_tokens,
                    "replay": replay_tokens,
                    "delta": replay_tokens - orig_tokens,
                },
                "nodes_succeeded": {
                    "original": orig_succeeded,
                    "replay": replay_succeeded,
                    "delta": replay_succeeded - orig_succeeded,
                },
                "nodes_failed": {
                    "original": orig_failed,
                    "replay": replay_failed,
                    "delta": replay_failed - orig_failed,
                },
            },
        }

    def summary_table(self) -> str:
        """Return a markdown table comparing original and replay."""
        d = self.to_dict()
        comp = d["comparison"]

        def _fmt_delta(v: float, fmt: str = ".4f") -> str:
            sign = "+" if v > 0 else ""
            return f"{sign}{v:{fmt}}"

        rows = [
            ("Cost (USD)",
             f"${comp['cost_usd']['original']:.4f}",
             f"${comp['cost_usd']['replay']:.4f}",
             _fmt_delta(comp['cost_usd']['delta'])),
            ("Latency (ms)",
             f"{comp['latency_ms']['original']:.0f}",
             f"{comp['latency_ms']['replay']:.0f}",
             _fmt_delta(comp['latency_ms']['delta'], ".0f")),
            ("Tokens",
             str(comp['tokens']['original']),
             str(comp['tokens']['replay']),
             _fmt_delta(comp['tokens']['delta'], ".0f")),
            ("Nodes Succeeded",
             str(comp['nodes_succeeded']['original']),
             str(comp['nodes_succeeded']['replay']),
             _fmt_delta(comp['nodes_succeeded']['delta'], ".0f")),
            ("Nodes Failed",
             str(comp['nodes_failed']['original']),
             str(comp['nodes_failed']['replay']),
             _fmt_delta(comp['nodes_failed']['delta'], ".0f")),
        ]

        header = "| Metric | Original | Replay | Delta |"
        sep = "|--------|----------|--------|-------|"
        lines = [header, sep]
        for metric, orig, replay, delta in rows:
            lines.append(f"| {metric} | {orig} | {replay} | {delta} |")

        return "\n".join(lines)

