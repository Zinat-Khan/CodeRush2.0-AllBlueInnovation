"""
AE-03 REST API Endpoints — Compile, Execute, Runs, Replay, Approve.

Provides all JSON endpoints consumed by the Next.js frontend:
  - POST /api/compile     — Compile a goal into an ExecutionGraph
  - POST /api/execute     — Execute a compiled graph
  - GET  /api/runs        — List all stored runs
  - GET  /api/runs/{id}   — Get a specific run's trace and metrics
  - POST /api/replay      — Replay a stored run with provider hot-swap
  - POST /api/approve     — Approve/reject a HITL approval request
  - GET  /api/providers   — List available providers
  - GET  /api/benchmark   — Get benchmark task summary
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.schemas.contracts import (
    AgentConfig,
    AgentRole,
    ExecutionGraph,
    ExecutionResult,
    ExecutionStatus,
)
from backend.schemas.artifacts import (
    BenchmarkResult,
    RunReport,
    TraceEvent,
    TraceEventType,
)
from backend.providers.router import ProviderRouter
from backend.compiler.graph_compiler import GraphCompiler, CompilationResult, CompilationError
from backend.engine.executor import AsyncDAGExecutor
from backend.engine.state_manager import ExecutionState
from backend.observability.tracker import CostTracker, calculate_cost
from backend.observability.tracer import ExecutionTracer, RunRecord, RunStore
from backend.observability.replay import ReplayEngine, ReplayComparison
from backend.evaluation.tasks import load_benchmark_tasks, get_task_summary

logger = logging.getLogger(__name__)

router = APIRouter(tags=["orchestrator"])


# ── Shared State ───────────────────────────────────────────────────────
# Singleton instances for the duration of the process.

_provider_router = ProviderRouter()
_run_store = RunStore()
_pending_approvals: Dict[str, asyncio.Event] = {}
_pending_approval_data: Dict[str, Dict[str, Any]] = {}


def get_run_store() -> RunStore:
    """Get the singleton RunStore instance."""
    return _run_store


# ── Request/Response Models ────────────────────────────────────────────


class CompileRequest(BaseModel):
    goal: str = Field(description="Natural-language goal to compile into a DAG.")
    provider: str = Field(default="openai", description="LLM provider for compilation.")
    model: Optional[str] = Field(default=None, description="Override model name.")


class CompileResponse(BaseModel):
    graph_id: str
    node_count: int
    edge_count: int
    nodes: Dict[str, Any]
    edges: List[Any]
    sub_graphs: Dict[str, Any]
    compilation_tokens: int
    compilation_cost_usd: float


class ExecuteRequest(BaseModel):
    goal: str = Field(description="Goal text for compilation + execution.")
    provider: str = Field(default="openai")
    model: Optional[str] = Field(default=None)


class ExecuteResponse(BaseModel):
    run_id: str
    graph_id: str
    status: str
    node_count: int
    nodes_succeeded: int
    nodes_failed: int
    total_tokens: int
    total_cost_usd: float
    elapsed_ms: float
    final_output: Dict[str, Any]


class ReplayRequest(BaseModel):
    original_run_id: str = Field(description="Run ID to replay.")
    override_provider: Optional[str] = Field(default=None)
    override_model: Optional[str] = Field(default=None)


class ApproveRequest(BaseModel):
    approval_id: str = Field(description="Approval request ID.")
    action: str = Field(description="'approve' or 'reject'.")
    reason: Optional[str] = Field(default=None, description="Optional reason for the decision.")


# ── Demo Node Handler ──────────────────────────────────────────────────

async def _demo_node_handler(
    node_id: str,
    config: AgentConfig,
    input_payload: Dict[str, Any],
    system_prompt: str,
) -> Dict[str, Any]:
    """
    Simulated node handler for demo/MVD purposes.

    In production, this would dispatch to actual LLM providers
    via the ProviderRouter. For the MVD, it returns synthetic
    outputs after a realistic delay.
    """
    await asyncio.sleep(0.5 + (hash(node_id) % 10) * 0.1)

    return {
        "node_id": node_id,
        "role": config.role.value,
        "status": "completed",
        "output_summary": f"Processed by {config.role.value} agent",
        "input_keys": list(input_payload.keys()),
        "timestamp": time.time(),
    }


# ── POST /api/compile ──────────────────────────────────────────────────

@router.post("/compile", response_model=CompileResponse)
async def compile_goal(request: CompileRequest):
    """
    Compile a natural-language goal into an ExecutionGraph DAG.

    Returns the graph structure with nodes, edges, and any sub-graphs.
    """
    if not request.goal.strip():
        raise HTTPException(status_code=400, detail="Goal text cannot be empty.")

    try:
        compiler = GraphCompiler(
            provider_router=_provider_router,
            default_provider=request.provider,
            default_model=request.model,
        )
        result: CompilationResult = await compiler.compile_goal(
            goal=request.goal,
            provider=request.provider,
            model=request.model,
        )

        return CompileResponse(
            graph_id=result.main_graph.graph_id,
            node_count=len(result.main_graph.nodes),
            edge_count=len(result.main_graph.edges),
            nodes={
                nid: n.model_dump(mode="json")
                for nid, n in result.main_graph.nodes.items()
            },
            edges=[list(e) for e in result.main_graph.edges],
            sub_graphs={
                sgid: sg.model_dump(mode="json")
                for sgid, sg in result.sub_graphs.items()
            },
            compilation_tokens=result.compilation_tokens,
            compilation_cost_usd=result.compilation_cost_usd,
        )

    except CompilationError as e:
        raise HTTPException(status_code=422, detail=f"Compilation failed: {e}")
    except Exception as e:
        logger.error("Compile error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")


# ── POST /api/execute ──────────────────────────────────────────────────

@router.post("/execute", response_model=ExecuteResponse)
async def execute_goal(request: ExecuteRequest):
    """
    Compile and execute a goal end-to-end.

    Returns execution metrics including run_id, token usage,
    cost, and the final synthesized output.
    """
    if not request.goal.strip():
        raise HTTPException(status_code=400, detail="Goal text cannot be empty.")

    start_time = time.time()
    run_id = f"run-{uuid.uuid4().hex[:8]}"

    try:
        # Phase 1: Compile
        compiler = GraphCompiler(
            provider_router=_provider_router,
            default_provider=request.provider,
            default_model=request.model,
        )
        compilation = await compiler.compile_goal(
            goal=request.goal,
            provider=request.provider,
            model=request.model,
        )

        # Phase 2: Execute
        tracer = ExecutionTracer(run_id=run_id)
        cost_tracker = CostTracker(run_id=run_id)
        trace_events: List[TraceEvent] = []

        state = ExecutionState(
            run_id=run_id,
            graph_id=compilation.main_graph.graph_id,
        )
        state.shared_memory.put("goal_text", request.goal)

        executor = AsyncDAGExecutor(
            graph=compilation.main_graph,
            node_handler=_demo_node_handler,
            sub_graphs=compilation.sub_graphs,
            state=state,
            trace_events=trace_events,
        )

        final_state = await executor.run()
        elapsed_ms = (time.time() - start_time) * 1000

        # Collect metrics
        all_results = final_state.get_all_results()
        total_tokens = sum(r.tokens_used for r in all_results.values())
        total_cost = sum(r.cost_usd for r in all_results.values())
        succeeded = sum(1 for r in all_results.values() if r.status == ExecutionStatus.SUCCESS)
        failed = sum(1 for r in all_results.values() if r.status == ExecutionStatus.FAILED)

        # Get final output from leaf nodes
        leaf_ids = compilation.main_graph.get_leaf_nodes()
        final_output = {}
        for lid in leaf_ids:
            lr = all_results.get(lid)
            if lr:
                final_output[lid] = lr.output

        # Ingest trace events
        tracer.ingest_events(trace_events)

        # Store run record
        record = RunRecord(
            run_id=run_id,
            tracer=tracer,
            graph=compilation.main_graph,
            goal_text=request.goal,
            cost_summary=cost_tracker.get_run_summary(),
        )
        _run_store.store(record)

        return ExecuteResponse(
            run_id=run_id,
            graph_id=compilation.main_graph.graph_id,
            status="success" if failed == 0 else "partial_failure",
            node_count=len(compilation.main_graph.nodes),
            nodes_succeeded=succeeded,
            nodes_failed=failed,
            total_tokens=total_tokens + compilation.compilation_tokens,
            total_cost_usd=total_cost + compilation.compilation_cost_usd,
            elapsed_ms=round(elapsed_ms, 1),
            final_output=final_output,
        )

    except CompilationError as e:
        raise HTTPException(status_code=422, detail=f"Compilation failed: {e}")
    except Exception as e:
        logger.error("Execute error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")


# ── GET /api/runs ──────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs():
    """List all stored execution runs (newest first)."""
    return {"runs": _run_store.list_runs(), "total": len(_run_store)}


# ── GET /api/runs/{run_id} ─────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """
    Get detailed information about a specific run.

    Returns full trace events, cost summary, and graph structure.
    """
    record = _run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    return {
        "run_id": record.run_id,
        "graph_id": record.graph.graph_id,
        "goal_text": record.goal_text,
        "stored_at": record.stored_at,
        "event_count": record.tracer.event_count,
        "cost_summary": record.cost_summary,
        "graph": record.graph.model_dump(mode="json"),
        "timeline": record.tracer.get_timeline(),
    }


# ── GET /api/runs/{run_id}/export ──────────────────────────────────────

@router.get("/runs/{run_id}/export")
async def export_run(run_id: str):
    """Export a run's full trace as a JSON document."""
    record = _run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    return record.tracer.export_dict()


# ── POST /api/replay ──────────────────────────────────────────────────

@router.post("/replay")
async def replay_run(request: ReplayRequest):
    """
    Replay a previously executed run with optional provider hot-swap.

    Returns a side-by-side comparison of original vs replay metrics.
    """
    try:
        engine = ReplayEngine(
            run_store=_run_store,
            node_handler=_demo_node_handler,
        )
        comparison: ReplayComparison = await engine.replay(
            original_run_id=request.original_run_id,
            override_provider=request.override_provider,
            override_model=request.override_model,
        )
        return comparison.to_dict()

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Replay error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Replay failed: {e}")


# ── POST /api/approve ─────────────────────────────────────────────────

@router.post("/approve")
async def handle_approval(request: ApproveRequest):
    """
    Handle a HITL approval decision.

    Resolves the async event that the executor is waiting on.
    """
    event = _pending_approvals.get(request.approval_id)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail=f"Approval request '{request.approval_id}' not found or already resolved.",
        )

    _pending_approval_data[request.approval_id] = {
        "action": request.action,
        "reason": request.reason,
        "resolved_at": time.time(),
    }
    event.set()

    return {
        "approval_id": request.approval_id,
        "action": request.action,
        "status": "resolved",
    }


# ── GET /api/approvals/pending ─────────────────────────────────────────

@router.get("/approvals/pending")
async def list_pending_approvals():
    """List all pending approval requests."""
    pending = [
        {
            "approval_id": aid,
            "status": "pending" if not event.is_set() else "resolved",
        }
        for aid, event in _pending_approvals.items()
        if not event.is_set()
    ]
    return {"pending_approvals": pending, "count": len(pending)}


# ── GET /api/providers ─────────────────────────────────────────────────

@router.get("/providers")
async def list_providers():
    """List available LLM providers and their status."""
    try:
        available = _provider_router.get_available_providers()
    except Exception:
        available = []

    return {
        "providers": available,
        "stats": _provider_router.get_stats() if available else {},
    }


# ── GET /api/benchmark/summary ─────────────────────────────────────────

@router.get("/benchmark/summary")
async def benchmark_summary():
    """Return a summary of available benchmark tasks."""
    try:
        tasks = load_benchmark_tasks()
        summary = get_task_summary(tasks)
        return {
            "summary": summary,
            "tasks": [t.model_dump(mode="json") for t in tasks],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load tasks: {e}")
