"""
AE-03 Cost Tracker & Event Tracker — Observability Module.

Provides:
  - PROVIDER_PRICING: Per-provider, per-model token pricing table.
  - calculate_cost(): Compute USD cost from token counts.
  - CostTracker: Per-run token & cost aggregation with V1-compat API.
  - EventTracker: Run-scoped event recording (V2 infrastructure).
  - EventType: Event type enum for the tracker.
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Provider Pricing Table ────────────────────────────────────────────

PROVIDER_PRICING: Dict[str, Dict[str, Dict[str, float]]] = {
    "openai": {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4-turbo": {"input": 10.00, "output": 30.00},
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    },
    "gemini": {
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    },
    "google": {
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    },
    "ollama": {},  # Local — always free
}
"""
Pricing per 1M tokens.  Keys: provider → model → {input, output}.
Ollama models are free (local inference).
"""


# ── calculate_cost ────────────────────────────────────────────────────


def calculate_cost(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """
    Calculate USD cost for a single LLM invocation.

    Args:
        provider: Provider name (openai, gemini, ollama, etc.).
        model: Model name (gpt-4o, gemini-1.5-pro, etc.).
        prompt_tokens: Number of input/prompt tokens.
        completion_tokens: Number of output/completion tokens.

    Returns:
        Cost in USD, rounded to 6 decimal places.
    """
    provider_models = PROVIDER_PRICING.get(provider, {})
    if not provider_models:
        return 0.0

    model_pricing = provider_models.get(model, {})
    if not model_pricing:
        return 0.0

    input_cost = (prompt_tokens / 1_000_000) * model_pricing.get("input", 0.0)
    output_cost = (completion_tokens / 1_000_000) * model_pricing.get("output", 0.0)
    return round(input_cost + output_cost, 6)


# ── CostTracker (V1-compat API) ──────────────────────────────────────


class _CostEntry:
    """A single cost record."""

    __slots__ = (
        "node_id", "provider", "model",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "cost_usd", "timestamp",
    )

    def __init__(
        self,
        node_id: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ):
        self.node_id = node_id
        self.provider = provider
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.cost_usd = calculate_cost(provider, model, prompt_tokens, completion_tokens)
        self.timestamp = time.time()


class CostTracker:
    """
    Per-run token & cost aggregation.

    V1-compat API used by routes.py and test_module7::

        tracker = CostTracker("run-id")
        tracker.record("researcher", "openai", "gpt-4o", 800, 300)
        summary = tracker.get_run_summary()
    """

    def __init__(self, run_id: str = ""):
        self._run_id = run_id
        self._entries: List[_CostEntry] = []

    def __len__(self) -> int:
        return len(self._entries)

    def record(
        self,
        node_id: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Record a single LLM invocation cost."""
        entry = _CostEntry(node_id, provider, model, prompt_tokens, completion_tokens)
        self._entries.append(entry)
        logger.debug(
            "CostTracker: node=%s provider=%s model=%s tokens=%d cost=$%.6f",
            node_id, provider, model, entry.total_tokens, entry.cost_usd,
        )

    def get_provider_breakdown(self) -> List[Dict[str, Any]]:
        """
        Get per-provider/model cost breakdown.

        Returns list of dicts with: provider, model, call_count, total_tokens, cost_usd.
        """
        groups: Dict[str, Dict[str, Any]] = {}
        for e in self._entries:
            key = f"{e.provider}/{e.model}"
            if key not in groups:
                groups[key] = {
                    "provider": e.provider,
                    "model": e.model,
                    "call_count": 0,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            groups[key]["call_count"] += 1
            groups[key]["total_tokens"] += e.total_tokens
            groups[key]["prompt_tokens"] += e.prompt_tokens
            groups[key]["completion_tokens"] += e.completion_tokens
            groups[key]["cost_usd"] = round(groups[key]["cost_usd"] + e.cost_usd, 6)

        return list(groups.values())

    def get_node_summary(self, node_id: str) -> Dict[str, Any]:
        """Get cost summary for a specific node."""
        entries = [e for e in self._entries if e.node_id == node_id]
        if not entries:
            return {
                "node_id": node_id,
                "call_count": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
            }
        return {
            "node_id": node_id,
            "call_count": len(entries),
            "total_tokens": sum(e.total_tokens for e in entries),
            "prompt_tokens": sum(e.prompt_tokens for e in entries),
            "completion_tokens": sum(e.completion_tokens for e in entries),
            "total_cost_usd": round(sum(e.cost_usd for e in entries), 6),
        }

    def get_run_summary(self) -> Dict[str, Any]:
        """Get cost summary for the entire run."""
        total_tokens = sum(e.total_tokens for e in self._entries)
        total_cost = sum(e.cost_usd for e in self._entries)
        unique_nodes = {e.node_id for e in self._entries}
        return {
            "run_id": self._run_id,
            "total_calls": len(self._entries),
            "total_tokens": total_tokens,
            "total_prompt_tokens": sum(e.prompt_tokens for e in self._entries),
            "total_completion_tokens": sum(e.completion_tokens for e in self._entries),
            "total_cost_usd": round(total_cost, 6),
            "nodes_with_llm_calls": len(unique_nodes),
            "provider_breakdown": self.get_provider_breakdown(),
        }


# ── Event Type Enum ───────────────────────────────────────────────────


class EventType(str, Enum):
    """Event types for the EventTracker."""
    RUN_CREATED = "run_created"
    PLAN_CREATED = "plan_created"
    GRAPH_COMPILED = "graph_compiled"
    SECURITY_CHECK = "security_check"
    TOOL_REQUESTED = "tool_requested"
    TOOL_ALLOWED = "tool_allowed"
    TOOL_DENIED = "tool_denied"
    TOOL_EXECUTED = "tool_executed"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    RETRY = "retry"
    REPLAN = "replan"
    RAG_SEARCH = "rag_search"
    SOURCE_RETRIEVED = "source_retrieved"
    CRITIC_STARTED = "critic_started"
    CRITIC_COMPLETED = "critic_completed"
    CRITIC_FAILED = "critic_failed"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_COMPLETED = "verification_completed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    REPORT_CREATED = "report_created"
    RUN_COMPLETED = "run_completed"


# ── Event Tracker ─────────────────────────────────────────────────────


class EventTracker:
    """
    Run-scoped event recording.

    Records timestamped events for a run, providing timeline access
    and event-type summaries.
    """

    def __init__(self) -> None:
        self._events: Dict[str, List[Dict[str, Any]]] = {}

    def record(
        self,
        run_id: str,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None,
        agent_role: str = "",
        task_id: str = "",
        node_id: str = "",
        duration_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Record a timestamped event."""
        event = {
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "event_type": event_type.value,
            "run_id": run_id,
            "timestamp": time.time(),
            "data": data or {},
            "agent_role": agent_role,
            "task_id": task_id,
            "node_id": node_id,
            "duration_ms": duration_ms,
        }
        self._events.setdefault(run_id, []).append(event)
        return event

    def get_events(self, run_id: str) -> List[Dict[str, Any]]:
        """Get all events for a run."""
        return list(self._events.get(run_id, []))

    def get_event_summary(self, run_id: str) -> Dict[str, int]:
        """Get event count by type for a run."""
        summary: Dict[str, int] = {}
        for evt in self._events.get(run_id, []):
            t = evt.get("event_type", "unknown")
            summary[t] = summary.get(t, 0) + 1
        return summary

    def get_all_run_ids(self) -> List[str]:
        """Get all run IDs with events."""
        return list(self._events.keys())

    def get_event_count(self, run_id: str) -> int:
        """Get total event count for a run."""
        return len(self._events.get(run_id, []))


    # ── Convenient Event Helper Methods ─────────────────────────────────

    def emit_run_created(self, run_id: str, goal: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Emit a RUN_CREATED event."""
        return self.record(
            run_id=run_id,
            event_type=EventType.RUN_CREATED,
            data={"goal": goal, "user_id": user_id},
        )

    def emit_run_completed(self, run_id: str, status: str = "completed", total_cost: float = 0.0) -> Dict[str, Any]:
        """Emit a RUN_COMPLETED event."""
        return self.record(
            run_id=run_id,
            event_type=EventType.RUN_COMPLETED,
            data={"status": status, "total_cost_usd": total_cost},
        )

    def emit_approved(self, run_id: str, approval_id: str) -> Dict[str, Any]:
        """Emit an APPROVED event."""
        return self.record(
            run_id=run_id,
            event_type=EventType.APPROVED,
            data={"approval_id": approval_id},
        )

    def emit_rejected(self, run_id: str, approval_id: str, reason: str = "") -> Dict[str, Any]:
        """Emit a REJECTED event."""
        return self.record(
            run_id=run_id,
            event_type=EventType.REJECTED,
            data={"approval_id": approval_id, "reason": reason},
        )

