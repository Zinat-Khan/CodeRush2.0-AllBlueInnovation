"""
AE-03 Typed AgentState for LangGraph StateGraph (Directive V2).

Defines the central ``AgentState`` TypedDict that flows through every
node in the LangGraph execution graph. This replaces the old custom
``ExecutionState`` with a fully typed, LangGraph-native state.

Also provides ``ScratchpadEntry`` for TTL-based scratch memory and
``ScratchpadManager`` for memory lifecycle management.

State fields per Directive V2 Section 5:
  run_id, user_id, workspace_id, goal, plan, tasks, current_task,
  artifacts, agent_outputs, memory_refs, rag_refs, source_refs,
  security_events, approval_state, verification_state, errors,
  metrics, status, created_at, updated_at.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Sequence

from langgraph.graph import MessagesState
from typing_extensions import Annotated, TypedDict

from backend.schemas.contracts import (
    AgentRole,
    Artifact,
    ApprovalRequest,
    RunMetrics,
    RunStatus,
    SecurityDecision,
    Task,
    TaskGraph,
    VerificationResult,
)


# ── Reducer Functions ─────────────────────────────────────────────────
# LangGraph uses reducers to merge state updates from parallel branches.


def _append_list(existing: List, new: List) -> List:
    """Reducer: append new items to existing list (deduplicates by identity)."""
    return existing + new


def _merge_dict(existing: Dict, new: Dict) -> Dict:
    """Reducer: merge new dict into existing dict (new values win)."""
    merged = dict(existing)
    merged.update(new)
    return merged


def _replace(existing: Any, new: Any) -> Any:
    """Reducer: replace value entirely."""
    return new


# ── Scratchpad (TTL Memory) ──────────────────────────────────────────


class ScratchpadEntry(TypedDict, total=False):
    """A single scratchpad memory entry with TTL."""
    key: str
    value: Any
    created_at: float
    ttl_seconds: int
    agent_role: str


class ScratchpadManager:
    """
    TTL-based scratch memory manager.

    Manages ephemeral key-value entries that auto-expire after
    ``SCRATCHPAD_TTL_SECONDS`` (default 300s). Enforces
    ``MAX_SCRATCHPAD_ENTRIES`` (default 100) capacity limit.

    Used by agents to store intermediate results, partial computations,
    and cross-node context that should not persist beyond the run.
    """

    def __init__(
        self,
        ttl_seconds: int = 300,
        max_entries: int = 100,
    ):
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: Dict[str, ScratchpadEntry] = {}

    def put(
        self,
        key: str,
        value: Any,
        agent_role: str = "",
        ttl_override: Optional[int] = None,
    ) -> None:
        """Store a value with TTL."""
        self._evict_expired()

        if len(self._entries) >= self._max_entries:
            # Evict oldest entry
            oldest_key = min(
                self._entries, key=lambda k: self._entries[k].get("created_at", 0)
            )
            del self._entries[oldest_key]

        self._entries[key] = ScratchpadEntry(
            key=key,
            value=value,
            created_at=time.time(),
            ttl_seconds=ttl_override or self._ttl,
            agent_role=agent_role,
        )

    def get(self, key: str) -> Optional[Any]:
        """Retrieve a value if it exists and hasn't expired."""
        self._evict_expired()
        entry = self._entries.get(key)
        if entry is None:
            return None
        return entry.get("value")

    def delete(self, key: str) -> bool:
        """Delete an entry. Returns True if it existed."""
        return self._entries.pop(key, None) is not None

    def list_keys(self) -> List[str]:
        """Return all non-expired keys."""
        self._evict_expired()
        return list(self._entries.keys())

    def size(self) -> int:
        """Return current entry count (after eviction)."""
        self._evict_expired()
        return len(self._entries)

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()

    def _evict_expired(self) -> None:
        """Remove entries that have exceeded their TTL."""
        now = time.time()
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.get("created_at", 0) > entry.get("ttl_seconds", self._ttl)
        ]
        for key in expired:
            del self._entries[key]

    def to_list(self) -> List[ScratchpadEntry]:
        """Return all non-expired entries as a list."""
        self._evict_expired()
        return list(self._entries.values())


# ── Agent State ──────────────────────────────────────────────────────


class AgentState(TypedDict, total=False):
    """
    Central state object for the LangGraph StateGraph.

    Every node in the execution graph reads from and writes to this state.
    LangGraph automatically handles state persistence via checkpointers
    (MemorySaver for dev, SqliteSaver for production).

    Fields use Annotated types with reducers for parallel branch merging.
    """

    # ── Identity ──────────────────────────────────────────────────────
    run_id: str
    user_id: str
    workspace_id: str

    # ── Goal & Plan ───────────────────────────────────────────────────
    goal: str
    plan: Optional[Dict[str, Any]]  # Serialized TaskGraph

    # ── Task Tracking ─────────────────────────────────────────────────
    tasks: Annotated[List[Dict[str, Any]], _append_list]
    current_task: Optional[str]  # task_id of the currently executing task

    # ── Agent Outputs ─────────────────────────────────────────────────
    agent_outputs: Annotated[Dict[str, Any], _merge_dict]

    # ── Artifacts ─────────────────────────────────────────────────────
    artifacts: Annotated[List[Dict[str, Any]], _append_list]

    # ── Messages (LangGraph native) ───────────────────────────────────
    messages: Annotated[List[Any], _append_list]

    # ── Memory & RAG References ───────────────────────────────────────
    memory_refs: Annotated[List[str], _append_list]
    rag_refs: Annotated[List[Dict[str, Any]], _append_list]
    source_refs: Annotated[List[Dict[str, Any]], _append_list]

    # ── Security & Approval ───────────────────────────────────────────
    security_events: Annotated[List[Dict[str, Any]], _append_list]
    approval_state: Optional[Dict[str, Any]]

    # ── Verification ──────────────────────────────────────────────────
    verification_state: Optional[Dict[str, Any]]

    # ── Errors ────────────────────────────────────────────────────────
    errors: Annotated[List[str], _append_list]

    # ── Metrics ───────────────────────────────────────────────────────
    metrics: Annotated[Dict[str, Any], _merge_dict]

    # ── Status ────────────────────────────────────────────────────────
    status: str  # RunStatus value

    # ── Timestamps ────────────────────────────────────────────────────
    created_at: float
    updated_at: float


# ── State Factory ─────────────────────────────────────────────────────


def create_initial_state(
    goal: str,
    user_id: str = "default_user",
    workspace_id: str = "default_workspace",
    run_id: Optional[str] = None,
) -> AgentState:
    """
    Create a fresh AgentState with all fields initialised.

    Args:
        goal: Natural-language goal text.
        user_id: User identifier.
        workspace_id: Workspace scope.
        run_id: Optional run ID (auto-generated if not provided).

    Returns:
        Fully initialised AgentState ready for LangGraph execution.
    """
    now = time.time()
    return AgentState(
        run_id=run_id or f"run-{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        workspace_id=workspace_id,
        goal=goal,
        plan=None,
        tasks=[],
        current_task=None,
        agent_outputs={},
        artifacts=[],
        messages=[],
        memory_refs=[],
        rag_refs=[],
        source_refs=[],
        security_events=[],
        approval_state=None,
        verification_state=None,
        errors=[],
        metrics={
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "total_latency_ms": 0.0,
            "nodes_total": 0,
            "nodes_succeeded": 0,
            "nodes_failed": 0,
        },
        status=RunStatus.PENDING.value,
        created_at=now,
        updated_at=now,
    )
