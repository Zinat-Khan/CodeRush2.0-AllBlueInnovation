"""
AE-03 Event Tracer — Append-Only Execution Trace Logger.

Provides:
  - ExecutionTracer: thread-safe, append-only event log keyed by run_id.
    Records every TraceEvent emitted during a run's lifecycle and
    supports export to structured JSON for replay and debugging.
  - RunStore: persistent in-memory store of completed run traces,
    indexed by run_id for retrieval via the REST API.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.schemas.artifacts import (
    ProviderCostBreakdown,
    RunReport,
    TraceEvent,
    TraceEventType,
)
from backend.schemas.contracts import ExecutionGraph

logger = logging.getLogger(__name__)


# ── Execution Tracer ───────────────────────────────────────────────────


class ExecutionTracer:
    """
    Thread-safe, append-only event trace logger for a single execution run.

    Generates a unique ``run_id`` at creation time and attaches it to
    every recorded event.  The full event log is exportable to JSON for
    replay, cost analysis, and debugging.

    Usage::

        tracer = ExecutionTracer()
        tracer.emit(TraceEventType.RUN_START, data={"graph_id": "g-1"})
        tracer.emit(TraceEventType.NODE_START, node_id="n-1")
        # ... execution ...
        trace_json = tracer.export_json()
    """

    def __init__(self, run_id: Optional[str] = None):
        self._run_id: str = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self._events: List[TraceEvent] = []
        self._lock = threading.Lock()
        self._created_at: float = time.time()

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def event_count(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def created_at(self) -> float:
        return self._created_at

    # ── Event Recording ────────────────────────────────────────────────

    def emit(
        self,
        event_type: TraceEventType,
        *,
        node_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        """
        Record a new trace event.

        Args:
            event_type: The category of event to record.
            node_id: Optional agent node ID this event relates to.
            data: Optional event-specific payload dictionary.

        Returns:
            The created TraceEvent instance.
        """
        event = TraceEvent(
            event_type=event_type,
            run_id=self._run_id,
            node_id=node_id,
            data=data or {},
        )

        with self._lock:
            self._events.append(event)

        logger.debug(
            "Trace [%s] %s node=%s data_keys=%s",
            self._run_id,
            event_type.value,
            node_id or "-",
            list((data or {}).keys()),
        )
        return event

    def emit_from_existing(self, event: TraceEvent) -> None:
        """
        Append a pre-created TraceEvent (e.g. from the executor's
        internal emit) into this tracer's log.  Overwrites event.run_id.
        """
        event.run_id = self._run_id
        with self._lock:
            self._events.append(event)

    def ingest_events(self, events: List[TraceEvent]) -> None:
        """
        Bulk-import a list of TraceEvents (e.g. from the executor's
        trace_events list after a run completes).
        """
        with self._lock:
            for evt in events:
                evt.run_id = self._run_id
                self._events.append(evt)

        logger.info(
            "Ingested %d trace events into run '%s'",
            len(events), self._run_id,
        )

    # ── Querying ───────────────────────────────────────────────────────

    def get_events(
        self,
        event_type: Optional[TraceEventType] = None,
        node_id: Optional[str] = None,
    ) -> List[TraceEvent]:
        """
        Retrieve events, optionally filtered by type and/or node.

        Args:
            event_type: Filter to only this event type.
            node_id: Filter to only events for this node.

        Returns:
            List of matching TraceEvent objects, in chronological order.
        """
        with self._lock:
            result = list(self._events)

        if event_type is not None:
            result = [e for e in result if e.event_type == event_type]
        if node_id is not None:
            result = [e for e in result if e.node_id == node_id]

        return result

    def get_all_events(self) -> List[TraceEvent]:
        """Return all events in chronological order."""
        with self._lock:
            return list(self._events)

    def get_timeline(self) -> List[Dict[str, Any]]:
        """
        Return a compact timeline view of all events.

        Each entry is a dict with event_id, type, node_id, timestamp,
        and a flattened data summary.
        """
        with self._lock:
            events = list(self._events)

        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "node_id": e.node_id,
                "timestamp": e.timestamp,
                "elapsed_s": round(e.timestamp - self._created_at, 3),
                "data_summary": _summarise_data(e.data),
            }
            for e in events
        ]

    # ── Export ─────────────────────────────────────────────────────────

    def export_dict(self) -> Dict[str, Any]:
        """
        Export the full trace as a serialisable dictionary.

        Suitable for JSON serialisation, persistence, and replay.
        """
        with self._lock:
            events = list(self._events)

        return {
            "run_id": self._run_id,
            "created_at": self._created_at,
            "event_count": len(events),
            "events": [e.model_dump(mode="json") for e in events],
        }

    def export_json(self, indent: int = 2) -> str:
        """Export the full trace as a formatted JSON string."""
        return json.dumps(self.export_dict(), indent=indent, default=str)

    def export_to_file(self, path: str | Path) -> Path:
        """
        Write the trace to a JSON file.

        Args:
            path: Destination file path.

        Returns:
            The resolved Path object.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.export_json(), encoding="utf-8")
        logger.info("Trace exported to %s (%d events)", out, self.event_count)
        return out

    def clear(self) -> None:
        """Remove all recorded events."""
        with self._lock:
            self._events.clear()


# ── Run Store ──────────────────────────────────────────────────────────


class RunRecord:
    """
    A stored record of a completed execution run, including its trace,
    graph configuration, and cost summary.
    """

    def __init__(
        self,
        run_id: str,
        tracer: ExecutionTracer,
        graph: ExecutionGraph,
        *,
        goal_text: str = "",
        run_report: Optional[RunReport] = None,
        cost_summary: Optional[Dict[str, Any]] = None,
    ):
        self.run_id = run_id
        self.tracer = tracer
        self.graph = graph
        self.goal_text = goal_text
        self.run_report = run_report
        self.cost_summary = cost_summary or {}
        self.stored_at = time.time()

    def to_summary_dict(self) -> Dict[str, Any]:
        """Compact summary for listing endpoints."""
        return {
            "run_id": self.run_id,
            "graph_id": self.graph.graph_id,
            "goal_text": self.goal_text[:200],
            "event_count": self.tracer.event_count,
            "stored_at": self.stored_at,
            "total_cost_usd": self.cost_summary.get("total_cost_usd", 0.0),
            "total_tokens": self.cost_summary.get("total_tokens", 0),
        }


class RunStore:
    """
    In-memory store of completed execution runs, indexed by run_id.

    Provides the backing store for the REST API endpoints:
      - GET /api/runs
      - GET /api/runs/{run_id}
      - GET /api/runs/{run_id}/export
    """

    def __init__(self) -> None:
        self._runs: Dict[str, RunRecord] = {}
        self._lock = threading.Lock()

    def store(self, record: RunRecord) -> None:
        """Store a completed run record."""
        with self._lock:
            self._runs[record.run_id] = record
        logger.info("Stored run '%s' (%d events)", record.run_id, record.tracer.event_count)

    def get(self, run_id: str) -> Optional[RunRecord]:
        """Retrieve a run record by ID."""
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self) -> List[Dict[str, Any]]:
        """Return summary dicts for all stored runs, newest first."""
        with self._lock:
            records = list(self._runs.values())
        records.sort(key=lambda r: r.stored_at, reverse=True)
        return [r.to_summary_dict() for r in records]

    def delete(self, run_id: str) -> bool:
        """Remove a run record. Returns True if it existed."""
        with self._lock:
            return self._runs.pop(run_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)

    def __contains__(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._runs


# ── Helpers ────────────────────────────────────────────────────────────


def _summarise_data(data: Dict[str, Any], max_len: int = 120) -> str:
    """
    Create a short string summary of a data dict for timeline views.

    Truncates long values to keep the timeline compact.
    """
    if not data:
        return ""
    parts = []
    for k, v in data.items():
        v_str = str(v)
        if len(v_str) > max_len:
            v_str = v_str[:max_len] + "…"
        parts.append(f"{k}={v_str}")
    return "; ".join(parts)
