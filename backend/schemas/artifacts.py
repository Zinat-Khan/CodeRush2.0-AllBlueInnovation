"""
AE-03 Artifact, Trace Event & Run Report Models.

Defines models for observability, traceability, and evaluation:
  - TraceEventType enum
  - TraceEvent: timestamped execution events
  - Artifact: intermediate or final output artifact
  - RunReport: aggregated run summary with cost breakdown
  - BenchmarkTask: evaluation task definition (loaded from DATA_PROVENANCE.md)
  - BenchmarkResult: per-task evaluation result
"""

from __future__ import annotations

import uuid
from enum import Enum
from time import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Trace Event Types ──────────────────────────────────────────────────


class TraceEventType(str, Enum):
    """All observable events in the execution lifecycle."""

    NODE_START = "node_start"
    NODE_END = "node_end"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    LLM_CALL = "llm_call"
    LLM_RESULT = "llm_result"
    VERIFICATION_PASS = "verification_pass"
    VERIFICATION_FAIL = "verification_fail"
    NODE_FAIL = "node_fail"
    RETRY = "retry"
    COMPENSATION = "compensation"
    HUMAN_APPROVAL_REQUESTED = "human_approval_requested"
    HUMAN_APPROVAL_GRANTED = "human_approval_granted"
    HUMAN_APPROVAL_REJECTED = "human_approval_rejected"
    MEMORY_EVICTION = "memory_eviction"
    PROVIDER_FALLBACK = "provider_fallback"
    GRAPH_COMPILE_START = "graph_compile_start"
    GRAPH_COMPILE_END = "graph_compile_end"
    SUB_GRAPH_START = "sub_graph_start"
    SUB_GRAPH_END = "sub_graph_end"
    SECURITY_ALERT = "security_alert"
    RUN_START = "run_start"
    RUN_END = "run_end"


# ── Trace Event ────────────────────────────────────────────────────────


class TraceEvent(BaseModel):
    """A single timestamped event in the execution trace."""

    event_id: str = Field(
        default_factory=lambda: f"evt-{uuid.uuid4().hex[:8]}",
        description="Unique event identifier.",
    )
    event_type: TraceEventType = Field(
        description="Category of the event.",
    )
    timestamp: float = Field(
        default_factory=time,
        description="Unix timestamp when the event occurred.",
    )
    run_id: str = Field(
        default="",
        description="ID of the execution run this event belongs to.",
    )
    node_id: Optional[str] = Field(
        default=None,
        description="ID of the agent node associated with this event.",
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific payload data.",
    )


# ── Artifact ───────────────────────────────────────────────────────────


class ArtifactType(str, Enum):
    """Classification of produced artifacts."""

    TEXT = "text"
    CODE = "code"
    JSON_DATA = "json_data"
    REPORT = "report"
    SCHEMA = "schema"
    GRAPH = "graph"
    LOG = "log"


class Artifact(BaseModel):
    """An intermediate or final output artifact produced during execution."""

    artifact_id: str = Field(
        default_factory=lambda: f"art-{uuid.uuid4().hex[:8]}",
        description="Unique artifact identifier.",
    )
    artifact_type: ArtifactType = Field(
        description="Classification of the artifact content.",
    )
    name: str = Field(
        default="",
        description="Human-readable name for the artifact.",
    )
    content: Any = Field(
        default=None,
        description="The artifact payload (text, dict, code string, etc.).",
    )
    producer_node_id: Optional[str] = Field(
        default=None,
        description="ID of the agent node that produced this artifact.",
    )
    run_id: Optional[str] = Field(
        default=None,
        description="ID of the execution run.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata (mime type, size, etc.).",
    )
    created_at: float = Field(
        default_factory=time,
        description="Unix timestamp of artifact creation.",
    )


# ── Run Report ─────────────────────────────────────────────────────────


class ProviderCostBreakdown(BaseModel):
    """Cost breakdown for a single LLM provider within a run."""

    provider: str = Field(description="Provider name.")
    model: str = Field(default="", description="Model identifier used.")
    tokens_prompt: int = Field(default=0, description="Total prompt tokens.")
    tokens_completion: int = Field(default=0, description="Total completion tokens.")
    total_tokens: int = Field(default=0, description="Sum of prompt + completion.")
    cost_usd: float = Field(default=0.0, description="Estimated USD cost.")
    call_count: int = Field(default=0, description="Number of LLM calls made.")


class RunReport(BaseModel):
    """Aggregated summary of a completed execution run."""

    run_id: str = Field(
        default_factory=lambda: f"run-{uuid.uuid4().hex[:8]}",
        description="Unique run identifier.",
    )
    graph_id: str = Field(
        default="",
        description="ID of the ExecutionGraph that was executed.",
    )
    status: str = Field(
        default="pending",
        description="Overall run status (pending | running | success | failed).",
    )
    started_at: float = Field(
        default_factory=time,
        description="Unix timestamp when the run started.",
    )
    finished_at: Optional[float] = Field(
        default=None,
        description="Unix timestamp when the run completed.",
    )
    total_tokens: int = Field(
        default=0,
        description="Total tokens consumed across all nodes.",
    )
    total_cost_usd: float = Field(
        default=0.0,
        description="Total estimated USD cost.",
    )
    total_latency_ms: float = Field(
        default=0.0,
        description="Total wall-clock execution time in milliseconds.",
    )
    node_count: int = Field(
        default=0,
        description="Number of nodes in the executed graph.",
    )
    nodes_succeeded: int = Field(default=0)
    nodes_failed: int = Field(default=0)
    nodes_retried: int = Field(default=0)
    provider_breakdown: List[ProviderCostBreakdown] = Field(
        default_factory=list,
        description="Per-provider cost breakdown.",
    )
    events: List[TraceEvent] = Field(
        default_factory=list,
        description="Full ordered trace event log.",
    )
    final_output: Dict[str, Any] = Field(
        default_factory=dict,
        description="Final synthesized output from the Reporter node.",
    )
    goal_text: str = Field(
        default="",
        description="Original natural language goal that triggered this run.",
    )


# ── Benchmark Models ───────────────────────────────────────────────────


class DifficultyTier(str, Enum):
    """Benchmark task difficulty classification."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class BenchmarkTask(BaseModel):
    """A single evaluation benchmark task loaded from DATA_PROVENANCE.md."""

    task_id: str = Field(description="Unique task identifier.")
    source_dataset: str = Field(
        description="Origin dataset (e.g., 'AgentBench', 'SWE-bench-Lite').",
    )
    goal_text: str = Field(
        description="Natural language goal to feed into the orchestrator.",
    )
    expected_output_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for expected output structure.",
    )
    difficulty_tier: DifficultyTier = Field(
        description="Difficulty classification of the task.",
    )
    category: str = Field(
        default="general",
        description="Task category (code_gen, data_analysis, api_integration, etc.).",
    )
    reference_answer: Optional[str] = Field(
        default=None,
        description="Reference answer or expected output for validation.",
    )


class BenchmarkResult(BaseModel):
    """Result of running a single benchmark task in a specific mode."""

    task_id: str = Field(description="ID of the benchmark task.")
    mode: str = Field(
        description="Execution mode (single_prompt | static_multi_agent | ae03_dynamic).",
    )
    success: bool = Field(description="Whether the task completed successfully.")
    handoff_validity_pct: float = Field(
        default=0.0,
        description="Percentage of inter-agent messages passing schema validation.",
    )
    recovery_rate_pct: float = Field(
        default=0.0,
        description="Percentage of failures auto-retried successfully.",
    )
    total_cost_usd: float = Field(default=0.0)
    latency_ms: float = Field(default=0.0)
    total_tokens: int = Field(default=0)
    output: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = Field(default=None)
