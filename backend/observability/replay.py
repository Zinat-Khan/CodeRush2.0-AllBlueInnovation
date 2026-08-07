"""
AE-03 Execution Replay Engine — Re-Run Saved Graphs with Provider Hot-Swap.

Provides:
  - ReplayEngine: loads a saved RunRecord and re-executes the same
    ExecutionGraph with identical or overridden provider/model settings.
    Produces a side-by-side comparison of original vs replay metrics.
  - ReplayComparison: structured output comparing the two runs.
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional

from backend.schemas.artifacts import RunReport, TraceEvent, TraceEventType
from backend.schemas.contracts import (
    AgentConfig,
    ExecutionGraph,
    ExecutionResult,
    ExecutionStatus,
)
from backend.engine.executor import AsyncDAGExecutor, NodeHandler
from backend.engine.state_manager import ExecutionState
from backend.observability.tracker import CostTracker
from backend.observability.tracer import ExecutionTracer, RunRecord, RunStore

logger = logging.getLogger(__name__)


# ── Replay Comparison ──────────────────────────────────────────────────


class ReplayComparison:
    """
    Side-by-side comparison of original vs replay execution metrics.

    Populated by the ReplayEngine after a replay completes.
    """

    def __init__(
        self,
        original_run_id: str,
        replay_run_id: str,
        original_summary: Dict[str, Any],
        replay_summary: Dict[str, Any],
        original_cost: Dict[str, Any],
        replay_cost: Dict[str, Any],
        provider_override: Optional[str],
    ):
        self.original_run_id = original_run_id
        self.replay_run_id = replay_run_id
        self.original_summary = original_summary
        self.replay_summary = replay_summary
        self.original_cost = original_cost
        self.replay_cost = replay_cost
        self.provider_override = provider_override

    def to_dict(self) -> Dict[str, Any]:
        """Full comparison as a serialisable dictionary."""
        orig_cost = self.original_cost.get("total_cost_usd", 0.0)
        replay_cost = self.replay_cost.get("total_cost_usd", 0.0)
        cost_delta = round(replay_cost - orig_cost, 8)

        orig_tokens = self.original_cost.get("total_tokens", 0)
        replay_tokens = self.replay_cost.get("total_tokens", 0)
        token_delta = replay_tokens - orig_tokens

        orig_latency = self.original_summary.get("elapsed_ms", 0.0)
        replay_latency = self.replay_summary.get("elapsed_ms", 0.0)
        latency_delta = round(replay_latency - orig_latency, 1)

        return {
            "original_run_id": self.original_run_id,
            "replay_run_id": self.replay_run_id,
            "provider_override": self.provider_override,
            "comparison": {
                "cost_usd": {
                    "original": orig_cost,
                    "replay": replay_cost,
                    "delta": cost_delta,
                },
                "total_tokens": {
                    "original": orig_tokens,
                    "replay": replay_tokens,
                    "delta": token_delta,
                },
                "latency_ms": {
                    "original": orig_latency,
                    "replay": replay_latency,
                    "delta": latency_delta,
                },
                "nodes_succeeded": {
                    "original": self.original_summary.get("nodes_succeeded", 0),
                    "replay": self.replay_summary.get("nodes_succeeded", 0),
                },
                "nodes_failed": {
                    "original": self.original_summary.get("nodes_failed", 0),
                    "replay": self.replay_summary.get("nodes_failed", 0),
                },
            },
            "original_provider_breakdown": self.original_cost.get(
                "provider_breakdown", []
            ),
            "replay_provider_breakdown": self.replay_cost.get(
                "provider_breakdown", []
            ),
        }

    def summary_table(self) -> str:
        """
        Return a markdown-formatted comparison table for reports.
        """
        d = self.to_dict()["comparison"]

        lines = [
            "| Metric | Original | Replay | Delta |",
            "| :--- | ---: | ---: | ---: |",
        ]

        for metric, label in [
            ("cost_usd", "Cost (USD)"),
            ("total_tokens", "Total Tokens"),
            ("latency_ms", "Latency (ms)"),
        ]:
            orig = d[metric]["original"]
            replay = d[metric]["replay"]
            delta = d[metric]["delta"]
            if metric == "cost_usd":
                lines.append(
                    f"| {label} | ${orig:.6f} | ${replay:.6f} | ${delta:+.6f} |"
                )
            else:
                lines.append(
                    f"| {label} | {orig} | {replay} | {delta:+} |"
                )

        for metric, label in [
            ("nodes_succeeded", "Nodes Succeeded"),
            ("nodes_failed", "Nodes Failed"),
        ]:
            orig = d[metric]["original"]
            replay = d[metric]["replay"]
            delta = replay - orig
            lines.append(f"| {label} | {orig} | {replay} | {delta:+d} |")

        return "\n".join(lines)


# ── Replay Engine ──────────────────────────────────────────────────────


class ReplayEngine:
    """
    Re-executes a saved execution run with identical or hot-swapped
    provider configuration.

    Reads the original ExecutionGraph from the RunStore, creates a
    deep copy (optionally overriding model_provider on every node),
    executes it through the standard AsyncDAGExecutor, and produces
    a ReplayComparison with side-by-side metrics.

    Usage::

        engine = ReplayEngine(run_store=store, node_handler=handler_fn)
        comparison = await engine.replay(
            original_run_id="run-abc12345",
            override_provider="ollama",
        )
    """

    def __init__(
        self,
        run_store: RunStore,
        node_handler: NodeHandler,
    ):
        self._store = run_store
        self._handler = node_handler

    async def replay(
        self,
        original_run_id: str,
        override_provider: Optional[str] = None,
        override_model: Optional[str] = None,
        sub_graphs: Optional[Dict[str, ExecutionGraph]] = None,
    ) -> ReplayComparison:
        """
        Replay a previously executed run.

        Args:
            original_run_id: The run_id of the original execution to replay.
            override_provider: If set, swap every node's model_provider to this.
            override_model: If set, override the model_name on every node.
            sub_graphs: Optional sub-graph registry for nested execution.

        Returns:
            ReplayComparison with original vs replay metrics.

        Raises:
            ValueError: If the original run_id is not found in the store.
        """
        # 1. Load original run record
        record = self._store.get(original_run_id)
        if record is None:
            raise ValueError(
                f"Run '{original_run_id}' not found in the run store."
            )

        logger.info(
            "Replaying run '%s' (graph '%s') with provider_override=%s",
            original_run_id,
            record.graph.graph_id,
            override_provider,
        )

        # 2. Deep-copy the graph and optionally swap providers
        replay_graph = self._clone_graph_with_overrides(
            record.graph,
            override_provider=override_provider,
            override_model=override_model,
        )

        # 3. Create fresh tracing & cost tracking for the replay
        replay_run_id = f"replay-{uuid.uuid4().hex[:8]}"
        replay_tracer = ExecutionTracer(run_id=replay_run_id)
        replay_cost_tracker = CostTracker(run_id=replay_run_id)
        replay_trace_events: List[TraceEvent] = []

        # 4. Execute replay via standard executor
        replay_state = ExecutionState(
            run_id=replay_run_id,
            graph_id=replay_graph.graph_id,
        )

        executor = AsyncDAGExecutor(
            graph=replay_graph,
            node_handler=self._handler,
            sub_graphs=sub_graphs or {},
            state=replay_state,
            trace_events=replay_trace_events,
        )

        replay_state = await executor.run()

        # Ingest executor trace events into the tracer
        replay_tracer.ingest_events(replay_trace_events)

        # Record costs from execution results
        for node_id, result in replay_state.get_all_results().items():
            if result.tokens_used > 0:
                replay_cost_tracker.record(
                    node_id=node_id,
                    provider=result.provider_used or override_provider or "unknown",
                    model=override_model or "default",
                    tokens_prompt=result.tokens_prompt,
                    tokens_completion=result.tokens_completion,
                )

        # 5. Store the replay run
        replay_record = RunRecord(
            run_id=replay_run_id,
            tracer=replay_tracer,
            graph=replay_graph,
            goal_text=record.goal_text,
            cost_summary=replay_cost_tracker.get_run_summary(),
        )
        self._store.store(replay_record)

        # 6. Build comparison
        comparison = ReplayComparison(
            original_run_id=original_run_id,
            replay_run_id=replay_run_id,
            original_summary=_extract_run_summary(record),
            replay_summary=replay_state.summary(),
            original_cost=record.cost_summary,
            replay_cost=replay_cost_tracker.get_run_summary(),
            provider_override=override_provider,
        )

        logger.info(
            "Replay '%s' completed. Original cost=$%.6f, Replay cost=$%.6f",
            replay_run_id,
            record.cost_summary.get("total_cost_usd", 0.0),
            replay_cost_tracker.get_run_summary().get("total_cost_usd", 0.0),
        )

        return comparison

    # ── Internal Helpers ───────────────────────────────────────────────

    @staticmethod
    def _clone_graph_with_overrides(
        graph: ExecutionGraph,
        override_provider: Optional[str] = None,
        override_model: Optional[str] = None,
    ) -> ExecutionGraph:
        """
        Deep-copy an ExecutionGraph, optionally overriding model_provider
        and model_name on every node.
        """
        # Deep-copy via model serialisation to avoid shared references
        graph_data = graph.model_dump()
        graph_data["graph_id"] = f"replay-{graph.graph_id}"

        if override_provider or override_model:
            for node_id, node_data in graph_data.get("nodes", {}).items():
                if override_provider:
                    node_data["model_provider"] = override_provider
                if override_model:
                    node_data["model_name"] = override_model

        return ExecutionGraph.model_validate(graph_data)


# ── Helpers ────────────────────────────────────────────────────────────


def _extract_run_summary(record: RunRecord) -> Dict[str, Any]:
    """
    Extract a summary dict from a RunRecord.

    Tries to pull from the stored RunReport if available, otherwise
    returns basic info from the record itself.
    """
    if record.run_report:
        return {
            "run_id": record.run_report.run_id,
            "graph_id": record.run_report.graph_id,
            "elapsed_ms": record.run_report.total_latency_ms,
            "node_count": record.run_report.node_count,
            "nodes_succeeded": record.run_report.nodes_succeeded,
            "nodes_failed": record.run_report.nodes_failed,
            "nodes_retried": record.run_report.nodes_retried,
        }

    return {
        "run_id": record.run_id,
        "graph_id": record.graph.graph_id,
        "elapsed_ms": 0.0,
        "node_count": len(record.graph.nodes),
        "nodes_succeeded": 0,
        "nodes_failed": 0,
        "nodes_retried": 0,
    }
