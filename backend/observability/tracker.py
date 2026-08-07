"""
AE-03 Token & Cost Tracker — Per-Node and Per-Run Aggregation.

Provides:
  - PROVIDER_PRICING: canonical pricing table (USD per 1M tokens)
  - CostEntry: immutable record of a single LLM call's cost
  - CostTracker: accumulates token usage and USD cost across an
    entire execution run, with per-node and per-provider breakdown
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Provider Pricing Table (USD per 1M tokens) ────────────────────────
# Canonical source of truth — also referenced by providers/router.py.

PROVIDER_PRICING: Dict[str, Dict[str, Tuple[float, float]]] = {
    # provider: { model: (input_cost_per_1M, output_cost_per_1M) }
    "openai": {
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
    },
    "gemini": {
        "gemini-1.5-pro": (1.25, 5.00),
    },
    "ollama": {
        # Local inference — zero cost
        "_default": (0.0, 0.0),
    },
}


def calculate_cost(
    provider: str,
    model: str,
    tokens_prompt: int,
    tokens_completion: int,
) -> float:
    """
    Calculate USD cost for a single LLM call.

    Falls back to ``_default`` pricing within a provider if the exact
    model is not in the table, and to zero cost if the provider itself
    is unknown.

    Returns:
        Estimated cost in USD, rounded to 8 decimal places.
    """
    provider_prices = PROVIDER_PRICING.get(provider, {})
    input_price, output_price = provider_prices.get(
        model, provider_prices.get("_default", (0.0, 0.0))
    )
    cost = (tokens_prompt / 1_000_000 * input_price) + (
        tokens_completion / 1_000_000 * output_price
    )
    return round(cost, 8)


# ── Cost Entry ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CostEntry:
    """Immutable record of a single LLM call's token usage and cost."""

    node_id: str
    provider: str
    model: str
    tokens_prompt: int
    tokens_completion: int
    total_tokens: int
    cost_usd: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "provider": self.provider,
            "model": self.model,
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


# ── Cost Tracker ───────────────────────────────────────────────────────


class CostTracker:
    """
    Accumulates token usage and USD cost across an entire execution run.

    Thread-safe — designed for concurrent node execution via asyncio.

    Usage::

        tracker = CostTracker(run_id="run-abc12345")
        tracker.record(
            node_id="researcher-1",
            provider="openai",
            model="gpt-4o",
            tokens_prompt=450,
            tokens_completion=120,
        )
        report = tracker.get_run_summary()
    """

    def __init__(self, run_id: str = ""):
        self._run_id = run_id
        self._entries: List[CostEntry] = []
        self._lock = threading.Lock()

    @property
    def run_id(self) -> str:
        return self._run_id

    # ── Recording ──────────────────────────────────────────────────────

    def record(
        self,
        node_id: str,
        provider: str,
        model: str,
        tokens_prompt: int,
        tokens_completion: int,
    ) -> CostEntry:
        """
        Record a single LLM call's token usage.

        Calculates cost from the pricing table and appends the entry.

        Returns:
            The created CostEntry.
        """
        cost = calculate_cost(provider, model, tokens_prompt, tokens_completion)
        total = tokens_prompt + tokens_completion

        entry = CostEntry(
            node_id=node_id,
            provider=provider,
            model=model,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            total_tokens=total,
            cost_usd=cost,
        )

        with self._lock:
            self._entries.append(entry)

        logger.debug(
            "Cost recorded: node=%s provider=%s model=%s tokens=%d cost=$%.6f",
            node_id, provider, model, total, cost,
        )
        return entry

    # ── Per-Node Aggregation ───────────────────────────────────────────

    def get_node_summary(self, node_id: str) -> Dict[str, Any]:
        """Return aggregated token usage and cost for a specific node."""
        with self._lock:
            node_entries = [e for e in self._entries if e.node_id == node_id]

        if not node_entries:
            return {
                "node_id": node_id,
                "call_count": 0,
                "tokens_prompt": 0,
                "tokens_completion": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
            }

        return {
            "node_id": node_id,
            "call_count": len(node_entries),
            "tokens_prompt": sum(e.tokens_prompt for e in node_entries),
            "tokens_completion": sum(e.tokens_completion for e in node_entries),
            "total_tokens": sum(e.total_tokens for e in node_entries),
            "total_cost_usd": round(sum(e.cost_usd for e in node_entries), 8),
        }

    # ── Per-Provider Aggregation ───────────────────────────────────────

    def get_provider_breakdown(self) -> List[Dict[str, Any]]:
        """
        Return per-provider aggregated cost breakdown.

        Returns a list of dicts, one per provider+model combination,
        compatible with the ``ProviderCostBreakdown`` schema.
        """
        with self._lock:
            entries = list(self._entries)

        # Group by (provider, model)
        groups: Dict[Tuple[str, str], List[CostEntry]] = {}
        for e in entries:
            key = (e.provider, e.model)
            groups.setdefault(key, []).append(e)

        breakdown = []
        for (provider, model), group in sorted(groups.items()):
            breakdown.append({
                "provider": provider,
                "model": model,
                "tokens_prompt": sum(e.tokens_prompt for e in group),
                "tokens_completion": sum(e.tokens_completion for e in group),
                "total_tokens": sum(e.total_tokens for e in group),
                "cost_usd": round(sum(e.cost_usd for e in group), 8),
                "call_count": len(group),
            })

        return breakdown

    # ── Run-Level Summary ──────────────────────────────────────────────

    def get_run_summary(self) -> Dict[str, Any]:
        """
        Return complete run-level summary with aggregate totals and
        per-provider breakdown.

        Compatible with the ``RunReport`` schema fields.
        """
        with self._lock:
            entries = list(self._entries)

        total_prompt = sum(e.tokens_prompt for e in entries)
        total_completion = sum(e.tokens_completion for e in entries)
        total_tokens = sum(e.total_tokens for e in entries)
        total_cost = round(sum(e.cost_usd for e in entries), 8)

        # Unique nodes that had at least one call
        unique_nodes = {e.node_id for e in entries}

        return {
            "run_id": self._run_id,
            "total_calls": len(entries),
            "total_tokens_prompt": total_prompt,
            "total_tokens_completion": total_completion,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "nodes_with_llm_calls": len(unique_nodes),
            "provider_breakdown": self.get_provider_breakdown(),
        }

    # ── All Entries ────────────────────────────────────────────────────

    def get_all_entries(self) -> List[Dict[str, Any]]:
        """Return all recorded cost entries as dicts."""
        with self._lock:
            return [e.to_dict() for e in self._entries]

    def clear(self) -> None:
        """Reset all tracked entries."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
