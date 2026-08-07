"""
AE-03 Async DAG Executor — Topological Traversal with Parallel Fan-Out.

Provides:
  - AsyncDAGExecutor: walks a compiled ExecutionGraph in topological
    order, runs independent nodes in parallel via asyncio.gather(),
    feeds validated AgentMessage objects between nodes, and handles
    sub-graph delegation via recursive child executors.

Execution lifecycle per node:
    PENDING → RUNNING → SUCCESS / FAILED / RETRYING / WAITING_FOR_APPROVAL
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Callable, Coroutine, Dict, List, Optional

from backend.schemas.contracts import (
    AgentConfig,
    AgentMessage,
    AgentRole,
    ExecutionGraph,
    ExecutionResult,
    ExecutionStatus,
)
from backend.schemas.artifacts import TraceEvent, TraceEventType
from backend.engine.state_manager import ExecutionState
from backend.engine.recovery import (
    CompensationRouter,
    NodeExecutionError,
    RetryPolicy,
    build_retry_context,
)

logger = logging.getLogger(__name__)


# ── Node Handler Type ──────────────────────────────────────────────────

NodeHandler = Callable[
    [str, AgentConfig, Dict[str, Any], str],
    Coroutine[Any, Any, Dict[str, Any]],
]
"""
Signature:
    async def handler(
        node_id: str,
        config: AgentConfig,
        input_payload: dict,
        system_prompt: str,
    ) -> dict   # output payload
"""


# ── Topological Sort ───────────────────────────────────────────────────


def topological_layers(graph: ExecutionGraph) -> List[List[str]]:
    """
    Return nodes grouped into topological layers (Kahn's algorithm).

    Nodes within the same layer are independent and can execute in
    parallel.  Layers are returned in dependency order.

    Raises:
        ValueError: If the graph contains a cycle.
    """
    in_degree: Dict[str, int] = defaultdict(int)
    adj: Dict[str, List[str]] = defaultdict(list)

    for node_id in graph.nodes:
        in_degree.setdefault(node_id, 0)

    for src, tgt in graph.edges:
        adj[src].append(tgt)
        in_degree[tgt] += 1

    queue: deque[str] = deque(
        nid for nid, deg in in_degree.items() if deg == 0
    )
    layers: List[List[str]] = []
    visited = 0

    while queue:
        layer = list(queue)
        layers.append(layer)
        next_queue: deque[str] = deque()
        for nid in layer:
            visited += 1
            for successor in adj[nid]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    next_queue.append(successor)
        queue = next_queue

    if visited != len(graph.nodes):
        raise ValueError(
            f"Cycle detected in graph '{graph.graph_id}': "
            f"visited {visited}/{len(graph.nodes)} nodes."
        )

    return layers


# ── Async DAG Executor ─────────────────────────────────────────────────


class AsyncDAGExecutor:
    """
    Execute a compiled ExecutionGraph asynchronously.

    Features:
      - Topological-layer traversal with ``asyncio.gather()`` fan-out.
      - Typed ``AgentMessage`` bus between nodes.
      - Per-node retry via ``RetryPolicy`` + error-context injection.
      - Compensation routing when retries are exhausted.
      - Recursive sub-graph execution for ``sub_graph`` role nodes.
      - TTL scratch-memory eviction before each execution cycle.

    Usage::

        executor = AsyncDAGExecutor(graph, handler_fn)
        state = await executor.run()
    """

    def __init__(
        self,
        graph: ExecutionGraph,
        node_handler: NodeHandler,
        *,
        sub_graphs: Optional[Dict[str, ExecutionGraph]] = None,
        retry_policy: Optional[RetryPolicy] = None,
        compensation_map: Optional[Dict[str, str]] = None,
        state: Optional[ExecutionState] = None,
        trace_events: Optional[List[TraceEvent]] = None,
    ):
        self._graph = graph
        self._handler = node_handler
        self._sub_graphs = sub_graphs or {}
        self._retry = retry_policy or RetryPolicy()
        self._compensation = CompensationRouter(compensation_map)
        self._state = state or ExecutionState(graph_id=graph.graph_id)
        self._trace: List[TraceEvent] = trace_events if trace_events is not None else []

    @property
    def state(self) -> ExecutionState:
        return self._state

    @property
    def trace_events(self) -> List[TraceEvent]:
        return self._trace

    # ── Main Run Loop ──────────────────────────────────────────────────

    async def run(self) -> ExecutionState:
        """
        Execute the full DAG and return the completed state.
        """
        run_id = self._state.run_id
        self._emit(TraceEventType.RUN_START, data={"graph_id": self._graph.graph_id})
        logger.info("Run '%s' started (graph '%s')", run_id, self._graph.graph_id)

        # Initialise all nodes
        for node_id in self._graph.nodes:
            self._state.init_node(node_id)

        try:
            layers = topological_layers(self._graph)
        except ValueError as exc:
            logger.error("Graph validation failed: %s", exc)
            self._emit(TraceEventType.RUN_END, data={"error": str(exc)})
            self._state.mark_finished()
            return self._state

        for layer_idx, layer in enumerate(layers):
            logger.info(
                "Executing layer %d/%d: %s",
                layer_idx + 1,
                len(layers),
                layer,
            )
            # Evict expired scratch memory entries before each layer
            for node_id in layer:
                config = self._graph.nodes[node_id]
                scratch = self._state.get_scratch(
                    config.agent_id,
                    default_ttl=config.scratch_memory_ttl,
                )
                scratch.evict_expired()

            # Fan-out: execute all nodes in this layer concurrently
            tasks = [
                self._execute_node(node_id)
                for node_id in layer
            ]
            await asyncio.gather(*tasks)

            # Abort early if any critical node failed without compensation
            for node_id in layer:
                status = self._state.get_node_status(node_id)
                if status == ExecutionStatus.FAILED:
                    logger.error(
                        "Node '%s' FAILED — aborting remaining layers.",
                        node_id,
                    )
                    self._emit(
                        TraceEventType.RUN_END,
                        data={"aborted_at_layer": layer_idx, "failed_node": node_id},
                    )
                    self._state.mark_finished()
                    return self._state

        self._emit(TraceEventType.RUN_END, data={"status": "success"})
        self._state.mark_finished()
        logger.info(
            "Run '%s' completed in %.0fms",
            run_id,
            self._state.elapsed_ms,
        )
        return self._state

    # ── Single Node Execution ──────────────────────────────────────────

    async def _execute_node(self, node_id: str) -> None:
        """Execute a single node with retry and compensation."""
        config = self._graph.nodes[node_id]
        self._state.set_node_status(node_id, ExecutionStatus.RUNNING)
        self._emit(
            TraceEventType.NODE_START,
            node_id=node_id,
            data={"role": config.role.value, "provider": config.model_provider},
        )

        start_time = time.time()

        # Collect inputs from predecessors
        input_payload = self._collect_inputs(node_id, config)

        # Handle sub_graph nodes specially
        if config.role == AgentRole.SUB_GRAPH:
            await self._execute_sub_graph(node_id, config, input_payload, start_time)
            return

        try:
            output = await self._retry.execute_with_retry(
                node_id=node_id,
                fn=self._run_handler,
                on_retry=self._on_retry_callback,
                on_exhausted=self._on_exhausted_callback,
                node_id_arg=node_id,
                config=config,
                input_payload=input_payload,
            )

            latency_ms = (time.time() - start_time) * 1000
            result = ExecutionResult(
                node_id=node_id,
                status=ExecutionStatus.SUCCESS,
                output=output,
                latency_ms=latency_ms,
                retry_count=self._state.get_retry_count(node_id),
                provider_used=config.model_provider,
            )
            self._state.set_node_result(node_id, result)
            self._state.set_node_status(node_id, ExecutionStatus.SUCCESS)
            self._emit(
                TraceEventType.NODE_END,
                node_id=node_id,
                data={"status": "success", "latency_ms": round(latency_ms, 1)},
            )

            # Post output as AgentMessages to successors
            self._post_outputs(node_id, config, output)

        except NodeExecutionError as exc:
            latency_ms = (time.time() - start_time) * 1000
            error_str = str(exc)
            self._state.record_error(node_id, error_str)

            # Try compensation
            route_result = await self._compensation.route(
                node_id=node_id,
                error_history=exc.error_history,
                on_compensate=self._on_compensate,
                on_escalate=self._on_escalate,
            )

            final_status = ExecutionStatus(route_result)
            # If no compensation exists and we escalated, mark as FAILED
            if final_status == ExecutionStatus.WAITING_FOR_APPROVAL:
                final_status = ExecutionStatus.FAILED

            result = ExecutionResult(
                node_id=node_id,
                status=final_status,
                output={},
                latency_ms=latency_ms,
                retry_count=self._state.get_retry_count(node_id),
                provider_used=config.model_provider,
                error=error_str,
            )
            self._state.set_node_result(node_id, result)
            self._state.set_node_status(node_id, final_status)
            self._emit(
                TraceEventType.NODE_FAIL,
                node_id=node_id,
                data={"error": error_str, "status": final_status.value},
            )

    async def _run_handler(
        self,
        *,
        node_id_arg: str,
        config: AgentConfig,
        input_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Wrap the user-supplied handler with error-context injection."""
        errors = self._state.get_errors(node_id_arg)
        prompt = build_retry_context(config.system_prompt, errors)
        return await self._handler(node_id_arg, config, input_payload, prompt)

    # ── Sub-Graph Execution ────────────────────────────────────────────

    async def _execute_sub_graph(
        self,
        node_id: str,
        config: AgentConfig,
        input_payload: Dict[str, Any],
        start_time: float,
    ) -> None:
        """Recursively execute a nested sub-graph."""
        sub_graph_id = config.sub_graph_id
        assert sub_graph_id is not None

        if sub_graph_id not in self._sub_graphs:
            error = f"Sub-graph '{sub_graph_id}' not found."
            logger.error(error)
            self._state.set_node_status(node_id, ExecutionStatus.FAILED)
            self._state.record_error(node_id, error)
            result = ExecutionResult(
                node_id=node_id,
                status=ExecutionStatus.FAILED,
                error=error,
                latency_ms=(time.time() - start_time) * 1000,
            )
            self._state.set_node_result(node_id, result)
            return

        self._emit(TraceEventType.SUB_GRAPH_START, node_id=node_id, data={"sub_graph_id": sub_graph_id})

        sub_graph = self._sub_graphs[sub_graph_id]
        child_executor = AsyncDAGExecutor(
            graph=sub_graph,
            node_handler=self._handler,
            sub_graphs=self._sub_graphs,
            retry_policy=self._retry,
            state=ExecutionState(graph_id=sub_graph.graph_id),
            trace_events=self._trace,  # share trace log
        )

        child_state = await child_executor.run()

        latency_ms = (time.time() - start_time) * 1000

        # Aggregate child results into parent
        child_summary = child_state.summary()
        success = child_summary["nodes_failed"] == 0

        result = ExecutionResult(
            node_id=node_id,
            status=ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILED,
            output={"sub_graph_summary": child_summary},
            latency_ms=latency_ms,
        )
        self._state.set_node_result(node_id, result)
        self._state.set_node_status(
            node_id,
            ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILED,
        )

        self._emit(
            TraceEventType.SUB_GRAPH_END,
            node_id=node_id,
            data={"sub_graph_id": sub_graph_id, "success": success},
        )

        if success:
            self._post_outputs(node_id, config, result.output)

    # ── Input Collection ───────────────────────────────────────────────

    def _collect_inputs(
        self,
        node_id: str,
        config: AgentConfig,
    ) -> Dict[str, Any]:
        """Gather outputs from all predecessor nodes as input payload."""
        predecessors = self._graph.get_predecessors(node_id)
        if not predecessors:
            # Root node — pull from shared memory if available
            goal = self._state.shared_memory.get("goal_text", "")
            return {"goal_text": goal} if goal else {}

        combined: Dict[str, Any] = {}
        for pred_id in predecessors:
            result = self._state.get_node_result(pred_id)
            if result and result.status == ExecutionStatus.SUCCESS:
                combined[pred_id] = result.output
        return combined

    # ── Output Posting ─────────────────────────────────────────────────

    def _post_outputs(
        self,
        node_id: str,
        config: AgentConfig,
        output: Dict[str, Any],
    ) -> None:
        """Post the node's output as AgentMessages to its successors."""
        successors = self._graph.get_successors(node_id)
        for succ_id in successors:
            succ_config = self._graph.nodes.get(succ_id)
            msg = AgentMessage(
                sender_agent_id=config.agent_id,
                target_agent_id=succ_config.agent_id if succ_config else succ_id,
                payload=output,
                provenance_trace_id=self._state.run_id,
            )
            self._state.post_message(msg)

    # ── Callbacks ──────────────────────────────────────────────────────

    async def _on_retry_callback(
        self, node_id: str, attempt: int, error_msg: str
    ) -> None:
        self._state.increment_retry(node_id)
        self._state.record_error(node_id, error_msg)
        self._state.set_node_status(node_id, ExecutionStatus.RETRYING)
        self._emit(
            TraceEventType.RETRY,
            node_id=node_id,
            data={"attempt": attempt, "error": error_msg},
        )

    async def _on_exhausted_callback(
        self, node_id: str, error_history: list[str]
    ) -> None:
        logger.error(
            "Node '%s' exhausted all %d retries.",
            node_id,
            len(error_history),
        )

    async def _on_compensate(
        self,
        node_id: str,
        comp_node_id: str,
        error_history: list[str],
    ) -> None:
        self._emit(
            TraceEventType.COMPENSATION,
            node_id=node_id,
            data={"compensation_node": comp_node_id, "errors": error_history},
        )

    async def _on_escalate(
        self, node_id: str, error_history: list[str]
    ) -> None:
        self._emit(
            TraceEventType.HUMAN_APPROVAL_REQUESTED,
            node_id=node_id,
            data={"reason": "retries_exhausted", "errors": error_history},
        )

    # ── Trace Emission ─────────────────────────────────────────────────

    def _emit(
        self,
        event_type: TraceEventType,
        *,
        node_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = TraceEvent(
            event_type=event_type,
            run_id=self._state.run_id,
            node_id=node_id,
            data=data or {},
        )
        self._trace.append(event)
