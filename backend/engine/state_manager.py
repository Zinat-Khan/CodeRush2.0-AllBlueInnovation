"""
AE-03 Engine State Manager — SharedProjectMemory, AgentScratchMemory & ExecutionState.

Provides:
  - SharedProjectMemory: Thread-safe, run-scoped key-value store shared
    across all agents in a single execution run.
  - AgentScratchMemory: Per-agent scratch pad with configurable TTL
    eviction and LRU max-entries cap.  [REV2 PATCH]
  - ExecutionState: Per-run bookkeeping — node statuses, outputs, errors,
    and inter-node message log.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from backend.schemas.contracts import (
    AgentMessage,
    ExecutionResult,
    ExecutionStatus,
)

logger = logging.getLogger(__name__)


# ── Agent Scratch Memory (TTL + LRU) ──────────────────────────────────


class _ScratchEntry:
    """Internal value wrapper that tracks creation time and TTL."""

    __slots__ = ("value", "created_at", "ttl_seconds")

    def __init__(self, value: Any, ttl_seconds: float):
        self.value = value
        self.created_at = time.time()
        self.ttl_seconds = ttl_seconds

    @property
    def is_expired(self) -> bool:
        if self.ttl_seconds <= 0:
            return False  # TTL of 0 means no expiry
        return (time.time() - self.created_at) > self.ttl_seconds

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class AgentScratchMemory:
    """
    Per-agent scratch pad with TTL eviction and max-entries cap.

    **REV2 PATCH** — Strict Time-To-Live eviction prevents OOM on local
    hardware.  Expired entries are evicted *before* each public access.

    Args:
        default_ttl: Default TTL in seconds (0 = no eviction).
        max_entries: Maximum entries before LRU eviction kicks in.
    """

    DEFAULT_TTL = 300       # 5 minutes
    DEFAULT_MAX_ENTRIES = 1000

    def __init__(
        self,
        default_ttl: float = DEFAULT_TTL,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        self._store: OrderedDict[str, _ScratchEntry] = OrderedDict()
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._eviction_count = 0
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────

    def put(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
    ) -> None:
        """Store a value with optional per-entry TTL override."""
        with self._lock:
            self._evict_expired_unsafe()
            entry = _ScratchEntry(value, ttl if ttl is not None else self._default_ttl)
            # If key already exists, remove it first to update position
            if key in self._store:
                del self._store[key]
            self._store[key] = entry
            # Enforce max-entries cap (LRU: evict oldest)
            while len(self._store) > self._max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                self._eviction_count += 1
                logger.debug("LRU eviction: key='%s'", evicted_key)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value, returning *default* if missing or expired."""
        with self._lock:
            self._evict_expired_unsafe()
            entry = self._store.get(key)
            if entry is None:
                return default
            # Move to end (most recently used)
            self._store.move_to_end(key)
            return entry.value

    def delete(self, key: str) -> bool:
        """Explicitly remove a key. Returns True if it existed."""
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def has(self, key: str) -> bool:
        """Check existence (evicts expired first)."""
        with self._lock:
            self._evict_expired_unsafe()
            return key in self._store

    def keys(self) -> List[str]:
        """Return all live keys."""
        with self._lock:
            self._evict_expired_unsafe()
            return list(self._store.keys())

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()

    def evict_expired(self) -> int:
        """
        Remove all entries whose TTL has elapsed.

        Call this *before* each execution cycle for deterministic cleanup.

        Returns:
            Number of entries evicted.
        """
        with self._lock:
            return self._evict_expired_unsafe()

    def get_memory_stats(self) -> dict:
        """Return monitoring statistics."""
        with self._lock:
            entries = list(self._store.values())
            oldest_age = max((e.age_seconds for e in entries), default=0.0)
            mem_estimate = sum(sys.getsizeof(e.value) for e in entries)
            return {
                "entry_count": len(self._store),
                "max_entries": self._max_entries,
                "memory_estimate_bytes": mem_estimate,
                "oldest_entry_age_seconds": round(oldest_age, 2),
                "eviction_count": self._eviction_count,
                "default_ttl": self._default_ttl,
            }

    # ── Internal ───────────────────────────────────────────────────────

    def _evict_expired_unsafe(self) -> int:
        """Remove expired entries (caller must hold self._lock)."""
        expired = [k for k, e in self._store.items() if e.is_expired]
        for k in expired:
            del self._store[k]
            self._eviction_count += 1
        if expired:
            logger.debug("TTL eviction: removed %d entries", len(expired))
        return len(expired)


# ── Shared Project Memory ──────────────────────────────────────────────


class SharedProjectMemory:
    """
    Thread-safe key-value store shared across all agents in a single run.

    Unlike AgentScratchMemory, this has no TTL — values persist for the
    entire run lifecycle.  Used for shared context like the original goal
    text, compiled graph metadata, or aggregated intermediate results.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._store.get(key, default)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._store

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._store.keys())

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# ── Execution State ───────────────────────────────────────────────────


class ExecutionState:
    """
    Per-run execution state: tracks node statuses, results, error history,
    and the inter-agent message log.

    Thread-safe — designed for concurrent node execution via asyncio.
    """

    def __init__(self, run_id: Optional[str] = None, graph_id: str = ""):
        self.run_id: str = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self.graph_id: str = graph_id
        self.started_at: float = time.time()
        self.finished_at: Optional[float] = None

        # Node status tracking
        self._node_statuses: Dict[str, ExecutionStatus] = {}
        self._node_results: Dict[str, ExecutionResult] = {}
        self._node_errors: Dict[str, List[str]] = {}   # node_id → error history
        self._node_retry_counts: Dict[str, int] = {}

        # Inter-agent message log
        self._messages: List[AgentMessage] = []

        # Scratch memories (one per agent)
        self._scratch: Dict[str, AgentScratchMemory] = {}

        # Shared project memory (one per run)
        self.shared_memory = SharedProjectMemory()

        self._lock = threading.Lock()

    # ── Node lifecycle ─────────────────────────────────────────────────

    def init_node(self, node_id: str) -> None:
        """Register a node and set it to PENDING."""
        with self._lock:
            self._node_statuses[node_id] = ExecutionStatus.PENDING
            self._node_errors.setdefault(node_id, [])
            self._node_retry_counts.setdefault(node_id, 0)

    def set_node_status(self, node_id: str, status: ExecutionStatus) -> None:
        with self._lock:
            self._node_statuses[node_id] = status

    def get_node_status(self, node_id: str) -> ExecutionStatus:
        with self._lock:
            return self._node_statuses.get(node_id, ExecutionStatus.PENDING)

    def set_node_result(self, node_id: str, result: ExecutionResult) -> None:
        with self._lock:
            self._node_results[node_id] = result

    def get_node_result(self, node_id: str) -> Optional[ExecutionResult]:
        with self._lock:
            return self._node_results.get(node_id)

    def get_all_results(self) -> Dict[str, ExecutionResult]:
        with self._lock:
            return dict(self._node_results)

    # ── Error tracking ─────────────────────────────────────────────────

    def record_error(self, node_id: str, error: str) -> None:
        with self._lock:
            self._node_errors.setdefault(node_id, []).append(error)

    def get_errors(self, node_id: str) -> List[str]:
        with self._lock:
            return list(self._node_errors.get(node_id, []))

    def increment_retry(self, node_id: str) -> int:
        """Increment and return the new retry count."""
        with self._lock:
            self._node_retry_counts[node_id] = (
                self._node_retry_counts.get(node_id, 0) + 1
            )
            return self._node_retry_counts[node_id]

    def get_retry_count(self, node_id: str) -> int:
        with self._lock:
            return self._node_retry_counts.get(node_id, 0)

    # ── Message bus ────────────────────────────────────────────────────

    def post_message(self, msg: AgentMessage) -> None:
        with self._lock:
            self._messages.append(msg)

    def get_messages_for(self, target_agent_id: str) -> List[AgentMessage]:
        """Retrieve all messages addressed to a specific agent."""
        with self._lock:
            return [
                m for m in self._messages
                if m.target_agent_id == target_agent_id
            ]

    def get_all_messages(self) -> List[AgentMessage]:
        with self._lock:
            return list(self._messages)

    # ── Agent scratch memory ───────────────────────────────────────────

    def get_scratch(
        self,
        agent_id: str,
        default_ttl: float = AgentScratchMemory.DEFAULT_TTL,
        max_entries: int = AgentScratchMemory.DEFAULT_MAX_ENTRIES,
    ) -> AgentScratchMemory:
        """Get or create the scratch memory for an agent."""
        with self._lock:
            if agent_id not in self._scratch:
                self._scratch[agent_id] = AgentScratchMemory(
                    default_ttl=default_ttl,
                    max_entries=max_entries,
                )
            return self._scratch[agent_id]

    # ── Run lifecycle ──────────────────────────────────────────────────

    def mark_finished(self) -> None:
        self.finished_at = time.time()

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None

    @property
    def elapsed_ms(self) -> float:
        end = self.finished_at or time.time()
        return (end - self.started_at) * 1000

    def summary(self) -> Dict[str, Any]:
        """Aggregate summary suitable for a RunReport."""
        with self._lock:
            statuses = list(self._node_statuses.values())
            return {
                "run_id": self.run_id,
                "graph_id": self.graph_id,
                "elapsed_ms": round(self.elapsed_ms, 1),
                "node_count": len(self._node_statuses),
                "nodes_succeeded": statuses.count(ExecutionStatus.SUCCESS),
                "nodes_failed": statuses.count(ExecutionStatus.FAILED),
                "nodes_retried": sum(
                    1 for c in self._node_retry_counts.values() if c > 0
                ),
                "total_messages": len(self._messages),
                "is_finished": self.is_finished,
            }
