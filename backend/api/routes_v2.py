"""
AE-03 V2 REST & SSE API Endpoints (Directive V2).

New V2 API surface built on LangGraph WorkflowEngine:
  - POST /api/v2/run              — Start a new execution run (returns run_id)
  - GET  /api/v2/run/{run_id}/stream — SSE stream of execution events
  - GET  /api/v2/run/{run_id}/status — Get current run state
  - GET  /api/v2/run/{run_id}/report — Get final RunReport
  - POST /api/v2/run/{run_id}/approve — HITL approval resolution
  - GET  /api/v2/runs              — List all runs
  - GET  /api/v2/tools             — List registered tools
  - GET  /api/v2/agents            — List agent capabilities
  - GET  /api/v2/policy/audit      — Get audit log
  - GET  /api/v2/observability/replay/{run_id} — Get replay record

Integrates with:
  - WorkflowEngine (Module 5) for execution
  - EventTracker, CostTracker, AuditLog (Module 7) for observability
  - PolicyEngine (Module 6) for security
  - HITLGate (Module 6) for approvals
  - ToolRegistry (Module 3) for tool info
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.graph.agent_state import create_initial_state
from backend.graph.workflow import WorkflowEngine
from backend.observability.tracker import EventTracker, EventType
from backend.observability.tracer import AuditLog, CostTracker
from backend.schemas.artifacts import TraceEvent
from backend.observability.replay import ReplayEngine
from backend.safety.hitl_gate import HITLGate
from backend.safety.policy_engine import PolicyEngine
from backend.safety.agent_config import get_all_capabilities
from backend.schemas.contracts import ApprovalAction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2", tags=["v2-orchestrator"])


# ── Shared State (Process Singletons) ─────────────────────────────────

_event_tracker = EventTracker()
_cost_tracker = CostTracker()
_audit_log = AuditLog()
_hitl_gate = HITLGate()
_policy_engine = PolicyEngine()
_workflow_engine: Optional[WorkflowEngine] = None
_replay_engine: Optional[ReplayEngine] = None
_active_runs: Dict[str, Dict[str, Any]] = {}  # run_id -> final state


def _get_workflow_engine() -> WorkflowEngine:
    global _workflow_engine
    if _workflow_engine is None:
        _workflow_engine = WorkflowEngine()
    return _workflow_engine


def _get_replay_engine() -> ReplayEngine:
    global _replay_engine
    if _replay_engine is None:
        _replay_engine = ReplayEngine(_event_tracker, _cost_tracker, _audit_log)
    return _replay_engine


# ── Request/Response Models ───────────────────────────────────────────


class RunRequest(BaseModel):
    """Request body for POST /api/v2/run."""
    goal: str = Field(description="Natural-language goal to execute.")
    workspace_id: str = Field(default="default_workspace", description="Workspace scope.")
    user_id: str = Field(default="default_user", description="User identifier.")


class RunResponse(BaseModel):
    """Response for POST /api/v2/run."""
    run_id: str
    status: str
    message: str
    stream_url: str


class ApprovalRequest(BaseModel):
    """Request body for POST /api/v2/run/{run_id}/approve."""
    approval_id: str = Field(description="Approval request ID.")
    action: str = Field(description="Action: 'approve', 'reject', or 'request_changes'.")
    reason: str = Field(default="", description="Optional reviewer reason.")


class RunStatusResponse(BaseModel):
    """Response for GET /api/v2/run/{run_id}/status."""
    run_id: str
    status: str
    goal: str
    task_count: int
    tasks_completed: int
    tasks_failed: int
    current_task: Optional[str]
    errors: List[str]
    metrics: Dict[str, Any]
    updated_at: float


class RunReportResponse(BaseModel):
    """Response for GET /api/v2/run/{run_id}/report."""
    run_id: str
    status: str
    goal: str
    report_content: str
    artifacts: List[Dict[str, Any]]
    cost_summary: Dict[str, Any]
    event_count: int
    audit_entries: List[Dict[str, Any]]
    verification: Optional[Dict[str, Any]]
    metrics: Dict[str, Any]


# ── POST /api/v2/run — Start Execution ───────────────────────────────


@router.post("/run", response_model=RunResponse)
async def start_run(request: RunRequest):
    """
    Start a new LangGraph execution run.

    Accepts a natural-language goal and workspace_id, creates a run,
    and begins async execution. Returns the run_id and SSE stream URL.
    """
    import uuid

    run_id = f"run-{uuid.uuid4().hex[:8]}"

    logger.info("[API] POST /v2/run: goal='%s', run_id=%s", request.goal[:80], run_id)

    # Emit run created event
    _event_tracker.emit_run_created(run_id, request.goal, request.user_id)

    # Initialize active run entry immediately
    _active_runs[run_id] = {
        "run_id": run_id,
        "status": "running",
        "goal": request.goal,
        "tasks": [],
        "current_task": "initializing",
        "errors": [],
        "metrics": {},
        "updated_at": time.time(),
    }

    # Start execution in background
    engine = _get_workflow_engine()

    async def _execute():
        try:
            result = await engine.execute(
                goal=request.goal,
                user_id=request.user_id,
                workspace_id=request.workspace_id,
                run_id=run_id,
            )
            _active_runs[run_id] = dict(result)
            _event_tracker.emit_run_completed(
                run_id,
                status=result.get("status", "unknown"),
                total_cost=result.get("metrics", {}).get("total_cost_usd", 0.0),
            )
        except Exception as e:
            logger.error("[API] Run %s failed: %s", run_id, e)
            _active_runs[run_id] = {
                "run_id": run_id,
                "status": "failed",
                "errors": [str(e)],
                "goal": request.goal,
                "tasks": [],
                "updated_at": time.time(),
            }

    asyncio.create_task(_execute())


    return RunResponse(
        run_id=run_id,
        status="started",
        message=f"Run '{run_id}' started. Stream events at /api/v2/run/{run_id}/stream",
        stream_url=f"/api/v2/run/{run_id}/stream",
    )


# ── GET /api/v2/run/{run_id}/stream — SSE Event Stream ──────────────


@router.get("/run/{run_id}/stream")
async def stream_run_events(run_id: str):
    """
    SSE stream of execution events for a run.

    Streams TraceEvent objects as Server-Sent Events in real-time.
    The stream closes when the run completes or fails.
    """
    async def event_generator():
        # Register a queue-based listener
        queue: asyncio.Queue = asyncio.Queue()

        def on_event(event: TraceEvent):
            if event.run_id == run_id:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

        _event_tracker.add_listener(on_event)

        try:
            # Send any existing events first
            existing = _event_tracker.get_events(run_id)
            for evt in existing:
                data = json.dumps(evt, default=str)
                yield f"event: {evt.get('event_type', 'unknown')}\ndata: {data}\n\n"

            # Stream new events
            timeout_count = 0
            while timeout_count < 300:  # Max 5 minutes
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    data = event.to_json()
                    yield f"event: {event.event_type.value}\ndata: {data}\n\n"

                    # End stream on run completion
                    if event.event_type in (EventType.RUN_COMPLETED,):
                        yield f"event: done\ndata: {{}}\n\n"
                        break
                    timeout_count = 0
                except asyncio.TimeoutError:
                    timeout_count += 1
                    # Send keepalive
                    if timeout_count % 15 == 0:
                        yield f": keepalive\n\n"

            yield f"event: done\ndata: {{}}\n\n"

        finally:
            _event_tracker.remove_listener(on_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /api/v2/run/{run_id}/status — Run Status ────────────────────


@router.get("/run/{run_id}/status", response_model=RunStatusResponse)
async def get_run_status(run_id: str):
    """Get current status of a run."""
    state = _active_runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    tasks = state.get("tasks", [])
    completed = sum(1 for t in tasks if t.get("status") == "success")
    failed = sum(1 for t in tasks if t.get("status") == "failed")

    return RunStatusResponse(
        run_id=run_id,
        status=state.get("status", "unknown"),
        goal=state.get("goal", ""),
        task_count=len(tasks),
        tasks_completed=completed,
        tasks_failed=failed,
        current_task=state.get("current_task"),
        errors=state.get("errors", []),
        metrics=state.get("metrics", {}),
        updated_at=state.get("updated_at", 0.0),
    )


# ── GET /api/v2/run/{run_id}/report — Final Report ──────────────────


@router.get("/run/{run_id}/report", response_model=RunReportResponse)
async def get_run_report(run_id: str):
    """Get the final report for a completed run."""
    state = _active_runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    status = state.get("status", "unknown")
    if status not in ("success", "failed"):
        raise HTTPException(status_code=409, detail=f"Run '{run_id}' is still {status}.")

    # Extract report content from agent_outputs
    agent_outputs = state.get("agent_outputs", {})
    reporter_output = agent_outputs.get("reporter", {})
    report_content = reporter_output.get("report", "No report generated.")

    # Get cost summary
    cost_summary = _cost_tracker.get_run_summary(run_id)

    # Get audit entries
    audit_entries = _audit_log.get_entries(run_id)

    return RunReportResponse(
        run_id=run_id,
        status=status,
        goal=state.get("goal", ""),
        report_content=report_content,
        artifacts=state.get("artifacts", []),
        cost_summary=cost_summary,
        event_count=_event_tracker.get_event_count(run_id),
        audit_entries=audit_entries,
        verification=state.get("verification_state"),
        metrics=state.get("metrics", {}),
    )


# ── POST /api/v2/run/{run_id}/approve — HITL Approval ───────────────


@router.post("/run/{run_id}/approve")
async def resolve_approval(run_id: str, request: ApprovalRequest):
    """Resolve a pending HITL approval request."""
    action_map = {
        "approve": ApprovalAction.APPROVE,
        "reject": ApprovalAction.REJECT,
        "request_changes": ApprovalAction.REQUEST_CHANGES,
    }

    action = action_map.get(request.action)
    if action is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action '{request.action}'. Must be: approve, reject, request_changes.",
        )

    result = _hitl_gate.resolve(request.approval_id, action, request.reason)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Approval '{request.approval_id}' not found.")

    # Emit event
    if action == ApprovalAction.APPROVE:
        _event_tracker.emit_approved(run_id, request.approval_id)
    else:
        _event_tracker.emit_rejected(run_id, request.approval_id, request.reason)

    # Log to audit
    _audit_log.log_approval(
        run_id, request.approval_id, request.action,
        agent_role=result.agent_role.value,
        tool_name=result.tool_name,
        reason=request.reason,
    )

    return {
        "approval_id": request.approval_id,
        "action": request.action,
        "status": result.status,
        "message": f"Approval {request.action}d successfully.",
    }


# ── GET /api/v2/runs — List All Runs ─────────────────────────────────


@router.get("/runs")
async def list_runs():
    """List all tracked runs with basic status."""
    runs = []
    for run_id, state in _active_runs.items():
        runs.append({
            "run_id": run_id,
            "status": state.get("status", "unknown"),
            "goal": state.get("goal", "")[:100],
            "task_count": len(state.get("tasks", [])),
            "created_at": state.get("created_at", 0.0),
        })
    return {"runs": runs, "total": len(runs)}


# ── GET /api/v2/tools — List Registered Tools ────────────────────────


@router.get("/tools")
async def list_tools():
    """List all registered tools with their metadata."""
    from backend.tools.tool_registry import ToolRegistry
    registry = ToolRegistry()
    return registry.get_registry_info()


# ── GET /api/v2/agents — List Agent Capabilities ─────────────────────


@router.get("/agents")
async def list_agents():
    """List all agent roles with their capabilities."""
    return {"agents": get_all_capabilities()}


# ── GET /api/v2/policy/audit — Audit Log ─────────────────────────────


@router.get("/policy/audit")
async def get_audit_log(run_id: Optional[str] = Query(None)):
    """Get the security audit log, optionally filtered by run_id."""
    entries = _audit_log.get_entries(run_id)
    summary = _audit_log.get_summary()
    return {"entries": entries, "summary": summary}


# ── GET /api/v2/observability/replay/{run_id} — Replay ───────────────


@router.get("/observability/replay/{run_id}")
async def get_replay(run_id: str):
    """Get the full replay record for a completed run."""
    engine = _get_replay_engine()
    record = engine.replay(run_id)
    return record.to_dict()


# ── GET /api/v2/observability/events/{run_id} — Events ───────────────


@router.get("/observability/events/{run_id}")
async def get_events(run_id: str):
    """Get all events for a run."""
    events = _event_tracker.get_events(run_id)
    timeline = _event_tracker.get_timeline(run_id)
    summary = _event_tracker.get_event_summary(run_id)
    return {"events": events, "timeline": timeline, "summary": summary}


# ── GET /api/v2/observability/costs/{run_id} — Costs ─────────────────


@router.get("/observability/costs/{run_id}")
async def get_costs(run_id: str):
    """Get cost breakdown for a run."""
    return _cost_tracker.get_run_summary(run_id)


# ── GET /api/v2/hitl/pending — Pending Approvals ─────────────────────


@router.get("/hitl/pending")
async def get_pending_approvals():
    """Get all pending HITL approval requests."""
    pending = _hitl_gate.get_pending()
    return {
        "pending": [
            _hitl_gate.create_interrupt_payload(a)
            for a in pending
        ],
        "count": len(pending),
        "stats": _hitl_gate.get_stats(),
    }


# ── POST /api/v2/run/{run_id}/cancel — Cancel Run ───────────────────


@router.post("/run/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Cancel a running execution."""
    state = _active_runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    if state.get("status") not in ("running", "started", "pending"):
        raise HTTPException(status_code=409, detail=f"Run '{run_id}' is not cancellable (status={state.get('status')}).")

    state["status"] = "cancelled"
    _event_tracker.emit_run_completed(run_id, status="cancelled")
    _audit_log.log_workflow_event(run_id, "RUN_CANCELLED")

    return {"run_id": run_id, "status": "cancelled", "message": f"Run '{run_id}' cancelled."}


# ── GET /api/v2/run/{run_id}/trace — Full Trace ─────────────────────


@router.get("/run/{run_id}/trace")
async def get_run_trace(run_id: str):
    """Get the full execution trace for a run."""
    events = _event_tracker.get_events(run_id)
    timeline = _event_tracker.get_timeline(run_id)
    costs = _cost_tracker.get_run_summary(run_id)
    audit = _audit_log.get_entries(run_id)

    return {
        "run_id": run_id,
        "events": events,
        "timeline": timeline,
        "cost_summary": costs,
        "audit_entries": audit,
        "event_count": len(events),
    }


# ── GET /api/v2/run/{run_id}/artifacts — Run Artifacts ───────────────


@router.get("/run/{run_id}/artifacts")
async def get_run_artifacts(run_id: str):
    """Get all artifacts produced by a run."""
    state = _active_runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    artifacts = state.get("artifacts", [])
    agent_outputs = state.get("agent_outputs", {})

    return {
        "run_id": run_id,
        "artifacts": artifacts,
        "agent_outputs": {
            k: str(v)[:1000] for k, v in agent_outputs.items()
        },
        "total": len(artifacts),
    }


# ── POST /api/v2/workflow/approve/{run_id} — Shortcut Approve ────────


@router.post("/workflow/approve/{run_id}")
async def workflow_approve(run_id: str):
    """Approve all pending HITL requests for a run."""
    pending = _hitl_gate.get_pending_for_run(run_id)
    if not pending:
        return {"run_id": run_id, "message": "No pending approvals.", "resolved": 0}

    resolved = 0
    for approval in pending:
        _hitl_gate.resolve(approval.approval_id, ApprovalAction.APPROVE, "Bulk approved")
        _event_tracker.emit_approved(run_id, approval.approval_id)
        _audit_log.log_approval(run_id, approval.approval_id, "approve", agent_role=approval.agent_role.value)
        resolved += 1

    return {"run_id": run_id, "message": f"Approved {resolved} request(s).", "resolved": resolved}


# ── POST /api/v2/workflow/reject/{run_id} — Shortcut Reject ──────────


@router.post("/workflow/reject/{run_id}")
async def workflow_reject(run_id: str, reason: str = "Rejected via API"):
    """Reject all pending HITL requests for a run."""
    pending = _hitl_gate.get_pending_for_run(run_id)
    if not pending:
        return {"run_id": run_id, "message": "No pending approvals.", "resolved": 0}

    resolved = 0
    for approval in pending:
        _hitl_gate.resolve(approval.approval_id, ApprovalAction.REJECT, reason)
        _event_tracker.emit_rejected(run_id, approval.approval_id, reason)
        _audit_log.log_approval(run_id, approval.approval_id, "reject", agent_role=approval.agent_role.value, reason=reason)
        resolved += 1

    return {"run_id": run_id, "message": f"Rejected {resolved} request(s).", "resolved": resolved}


# ── POST /api/v2/workflow/request-changes/{run_id} — Request Changes ─


@router.post("/workflow/request-changes/{run_id}")
async def workflow_request_changes(run_id: str, reason: str = "Changes requested"):
    """Request changes for all pending HITL requests for a run."""
    pending = _hitl_gate.get_pending_for_run(run_id)
    if not pending:
        return {"run_id": run_id, "message": "No pending approvals.", "resolved": 0}

    resolved = 0
    for approval in pending:
        _hitl_gate.resolve(approval.approval_id, ApprovalAction.REQUEST_CHANGES, reason)
        _audit_log.log_approval(run_id, approval.approval_id, "request_changes", agent_role=approval.agent_role.value, reason=reason)
        resolved += 1

    return {"run_id": run_id, "message": f"Requested changes for {resolved} request(s).", "resolved": resolved}


# ── POST /api/v2/documents/upload — Document Upload ──────────────────


class DocumentUploadRequest(BaseModel):
    """Request body for document upload."""
    content: str = Field(description="Document text content.")
    filename: str = Field(default="uploaded.txt", description="Filename.")
    workspace_id: str = Field(default="default_workspace")


@router.post("/documents/upload")
async def upload_document(request: DocumentUploadRequest):
    """Upload a document for RAG indexing."""
    # Scan content for injection
    decision = _policy_engine.scan_content(request.content, source=f"upload:{request.filename}")
    if decision.verdict.value == "deny":
        _audit_log.log_injection_detected(
            "", decision.rule_matched, source=request.filename,
            matched_text=decision.reason[:100],
        )
        raise HTTPException(status_code=403, detail=f"Content rejected: {decision.reason}")

    # Index into RAG
    try:
        from backend.rag.pipeline import RAGPipeline
        pipeline = RAGPipeline()
        doc_id = pipeline.add_document(request.content, metadata={
            "filename": request.filename,
            "workspace_id": request.workspace_id,
            "source": "upload",
        })
        return {"status": "indexed", "doc_id": doc_id, "filename": request.filename}
    except Exception as e:
        return {"status": "accepted", "message": f"Document accepted (RAG indexing deferred): {e}"}


# ── POST /api/v2/rag/query — RAG Query ──────────────────────────────


class RAGQueryRequest(BaseModel):
    """Request body for RAG query."""
    query: str = Field(description="Search query.")
    workspace_id: str = Field(default="default_workspace")
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/rag/query")
async def rag_query(request: RAGQueryRequest):
    """Query the RAG pipeline for relevant documents."""
    # Scan query for injection
    decision = _policy_engine.scan_content(request.query, source="rag_query")
    if decision.verdict.value == "deny":
        raise HTTPException(status_code=403, detail=f"Query rejected: {decision.reason}")

    try:
        from backend.rag.pipeline import RAGPipeline
        pipeline = RAGPipeline()
        results = pipeline.query(request.query, top_k=request.top_k)
        return {"query": request.query, "results": results, "count": len(results)}
    except Exception as e:
        return {"query": request.query, "results": [], "count": 0, "error": str(e)}

