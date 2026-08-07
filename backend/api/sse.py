"""
AE-03 Server-Sent Events (SSE) — Real-Time Trace Streaming.

Provides:
  - GET /api/sse/runs/{run_id}  — Stream trace events for a live run
  - GET /api/sse/demo           — Stream a simulated execution demo

Uses ``sse-starlette`` for proper SSE with keep-alive support.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from backend.schemas.artifacts import TraceEventType

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sse"])


# ── SSE: Stream Run Trace ──────────────────────────────────────────────

@router.get("/sse/runs/{run_id}")
async def stream_run_events(run_id: str):
    """
    Stream trace events for a run in real-time via SSE.

    Redirects to the V2 SSE stream endpoint.
    For live streaming, use GET /api/v2/run/{run_id}/stream instead.
    """
    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        url=f"/api/v2/run/{run_id}/stream",
        status_code=307,
    )



# ── SSE: Demo Stream ──────────────────────────────────────────────────

@router.get("/sse/demo")
async def stream_demo():
    """
    Stream a simulated execution demo via SSE.

    This endpoint generates a realistic 5-node DAG execution
    sequence that the frontend can consume to demonstrate the
    real-time streaming capability without requiring LLM calls.
    """
    async def demo_generator() -> AsyncGenerator[Dict[str, str], None]:
        """Generate a complete demo execution trace."""
        run_id = f"demo-{uuid.uuid4().hex[:6]}"
        graph_id = f"graph-demo-{uuid.uuid4().hex[:4]}"

        nodes = [
            ("planner", "PLANNER", "Planner"),
            ("researcher", "RESEARCHER", "Researcher"),
            ("executor", "EXECUTOR", "Code Executor"),
            ("verifier", "VERIFIER", "Verifier"),
            ("reporter", "REPORTER", "Reporter"),
        ]

        # 1. Run start
        yield _sse_event("run_start", run_id, None, {
            "graph_id": graph_id,
            "node_count": len(nodes),
            "goal": "Demo execution trace",
        })
        await asyncio.sleep(0.3)

        # 2. Execute each node
        for i, (node_id, role, label) in enumerate(nodes):
            # Node start
            yield _sse_event("node_start", run_id, node_id, {
                "role": role,
                "label": label,
                "layer": i,
            })
            await asyncio.sleep(0.2)

            # LLM call
            yield _sse_event("llm_call", run_id, node_id, {
                "provider": "openai",
                "model": "gpt-4o",
                "tokens_prompt": 200 + i * 80,
            })
            await asyncio.sleep(0.5 + i * 0.2)

            # LLM result
            tokens_completion = 100 + i * 60
            cost = round((200 + i * 80 + tokens_completion) * 0.000008, 6)
            yield _sse_event("llm_result", run_id, node_id, {
                "tokens_completion": tokens_completion,
                "total_tokens": 200 + i * 80 + tokens_completion,
                "cost_usd": cost,
                "latency_ms": round(300 + i * 150 + asyncio.get_event_loop().time() % 100, 1),
            })
            await asyncio.sleep(0.1)

            # HITL approval for verifier
            if node_id == "verifier":
                yield _sse_event("approval_required", run_id, node_id, {
                    "tool": "validate_output",
                    "payload_summary": "Schema validation check",
                })
                await asyncio.sleep(1.5)
                yield _sse_event("approval_granted", run_id, node_id, {
                    "action": "approve",
                    "approver": "human_operator",
                })
                await asyncio.sleep(0.3)

            # Node end
            yield _sse_event("node_end", run_id, node_id, {
                "status": "success",
                "output_keys": ["result", "summary"],
            })
            await asyncio.sleep(0.2)

        # 3. Run end
        yield _sse_event("run_end", run_id, None, {
            "status": "success",
            "total_nodes": len(nodes),
            "nodes_succeeded": len(nodes),
            "nodes_failed": 0,
        })

        # 4. Stream end marker
        yield {
            "event": "stream_end",
            "id": f"end-{run_id}",
            "data": json.dumps({"run_id": run_id, "status": "demo_complete"}),
        }

    return EventSourceResponse(
        demo_generator(),
        media_type="text/event-stream",
        ping=15,
    )


# ── Helpers ────────────────────────────────────────────────────────────

def _sse_event(
    event_type: str,
    run_id: str,
    node_id: str | None,
    data: Dict[str, Any],
) -> Dict[str, str]:
    """Build a formatted SSE event dict."""
    return {
        "event": event_type,
        "id": f"evt-{uuid.uuid4().hex[:8]}",
        "data": json.dumps({
            "event_type": event_type,
            "run_id": run_id,
            "node_id": node_id,
            "timestamp": time.time(),
            "data": data,
        }, default=str),
    }
