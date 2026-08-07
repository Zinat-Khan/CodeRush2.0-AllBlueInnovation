"""
AE-03 Core Pydantic v2 Typed Data Contracts.

Defines the canonical data models used across the entire orchestrator:
  - AgentRole enum (including sub_graph for nested workflows)
  - AgentConfig: per-agent specification
  - ExecutionGraph: validated DAG structure
  - AgentMessage: typed inter-agent communication
  - ExecutionStatus enum & ExecutionResult
"""

from __future__ import annotations

import uuid
from enum import Enum
from time import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────


class AgentRole(str, Enum):
    """All supported agent roles in the orchestrator."""

    PLANNER = "planner"
    RESEARCHER = "researcher"
    EXECUTOR = "executor"
    ANALYST = "analyst"
    CRITIC = "critic"
    VERIFIER = "verifier"
    REPORTER = "reporter"
    SUB_GRAPH = "sub_graph"  # REV2: Supports nested workflow delegation


class ExecutionStatus(str, Enum):
    """Lifecycle status of a single node execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPENSATING = "compensating"
    SKIPPED = "skipped"


class ModelProvider(str, Enum):
    """Supported LLM provider identifiers."""

    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"


# ── Agent Configuration ────────────────────────────────────────────────


class AgentConfig(BaseModel):
    """
    Specification for a single agent node in the execution DAG.

    When role is SUB_GRAPH, the agent delegates execution to a nested
    ExecutionGraph identified by sub_graph_id.
    """

    agent_id: str = Field(
        default_factory=lambda: f"agent-{uuid.uuid4().hex[:8]}",
        description="Unique identifier for this agent instance.",
    )
    role: AgentRole = Field(
        description="Functional role determining the agent's behaviour.",
    )
    system_prompt: str = Field(
        default="",
        description="System-level instruction prompt for the LLM.",
    )
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema describing expected input payload.",
    )
    output_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema describing expected output payload.",
    )
    allowed_tools: List[str] = Field(
        default_factory=list,
        description="Explicit tool allowlist enforced by PolicyEngine.",
    )
    token_budget: int = Field(
        default=4096,
        ge=1,
        description="Maximum tokens this agent may consume per invocation.",
    )
    model_provider: str = Field(
        default="openai",
        description="LLM provider to use (openai | gemini | ollama).",
    )
    model_name: Optional[str] = Field(
        default=None,
        description="Override model name. If None, uses provider default.",
    )
    timeout_seconds: int = Field(
        default=120,
        ge=1,
        description="Maximum wall-clock seconds before the node is timed out.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        description="Maximum retry attempts on failure.",
    )
    requires_human_approval: bool = Field(
        default=False,
        description="If True, pause execution and wait for human sign-off.",
    )
    scratch_memory_ttl: int = Field(
        default=300,
        ge=0,
        description="TTL in seconds for scratch memory entries (0 = no eviction).",
    )

    # ── REV2: Nested workflow support ──────────────────────────────────
    sub_graph_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the nested ExecutionGraph to delegate to. "
            "Required when role is SUB_GRAPH."
        ),
    )

    @model_validator(mode="after")
    def validate_sub_graph_consistency(self) -> "AgentConfig":
        """Ensure sub_graph_id is set iff role is SUB_GRAPH."""
        if self.role == AgentRole.SUB_GRAPH and not self.sub_graph_id:
            raise ValueError(
                "sub_graph_id is required when role is 'sub_graph'."
            )
        if self.role != AgentRole.SUB_GRAPH and self.sub_graph_id is not None:
            raise ValueError(
                "sub_graph_id must be None when role is not 'sub_graph'."
            )
        return self


# ── Execution Graph ───────────────────────────────────────────────────


class ExecutionGraph(BaseModel):
    """
    A validated Directed Acyclic Graph of agent nodes.

    Represents a compiled execution plan produced by the Planner.
    When parent_graph_id is set, this is a nested sub-graph.
    """

    graph_id: str = Field(
        default_factory=lambda: f"graph-{uuid.uuid4().hex[:8]}",
        description="Unique identifier for this graph.",
    )
    version: str = Field(
        default="1.0.0",
        description="Semantic version of the graph definition.",
    )
    nodes: Dict[str, AgentConfig] = Field(
        default_factory=dict,
        description="Mapping of node_id → AgentConfig.",
    )
    edges: List[Tuple[str, str]] = Field(
        default_factory=list,
        description="Directed edges as (source_node_id, target_node_id) tuples.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary metadata (goal text, compile timestamp, etc.).",
    )
    locked: bool = Field(
        default=False,
        description="If True, graph structure is frozen for execution.",
    )

    # ── REV2: Nested graph support ─────────────────────────────────────
    parent_graph_id: Optional[str] = Field(
        default=None,
        description="ID of the parent graph if this is a nested sub-graph.",
    )

    def lock(self) -> None:
        """Freeze the graph for execution. No further structural edits."""
        self.locked = True

    def get_node_ids(self) -> List[str]:
        """Return all node IDs in the graph."""
        return list(self.nodes.keys())

    def get_predecessors(self, node_id: str) -> List[str]:
        """Return IDs of all nodes that feed into the given node."""
        return [src for src, tgt in self.edges if tgt == node_id]

    def get_successors(self, node_id: str) -> List[str]:
        """Return IDs of all nodes that the given node feeds into."""
        return [tgt for src, tgt in self.edges if src == node_id]

    def get_root_nodes(self) -> List[str]:
        """Return node IDs with no incoming edges (entry points)."""
        targets = {tgt for _, tgt in self.edges}
        return [nid for nid in self.nodes if nid not in targets]

    def get_leaf_nodes(self) -> List[str]:
        """Return node IDs with no outgoing edges (exit points)."""
        sources = {src for src, _ in self.edges}
        return [nid for nid in self.nodes if nid not in sources]


# ── Inter-Agent Communication ──────────────────────────────────────────


class AgentMessage(BaseModel):
    """Typed message passed between agent nodes during execution."""

    message_id: str = Field(
        default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}",
        description="Unique message identifier.",
    )
    sender_agent_id: str = Field(
        description="ID of the agent that produced this message.",
    )
    target_agent_id: str = Field(
        description="ID of the agent that should consume this message.",
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="The typed data payload (validated against schemas).",
    )
    timestamp: float = Field(
        default_factory=time,
        description="Unix timestamp when the message was created.",
    )
    provenance_trace_id: str = Field(
        default="",
        description="Run-level trace ID for end-to-end provenance tracking.",
    )


# ── Execution Result ──────────────────────────────────────────────────


class ExecutionResult(BaseModel):
    """Outcome of executing a single agent node."""

    node_id: str = Field(description="ID of the executed node.")
    status: ExecutionStatus = Field(description="Final execution status.")
    output: Dict[str, Any] = Field(
        default_factory=dict,
        description="Output data produced by the node.",
    )
    tokens_used: int = Field(
        default=0,
        ge=0,
        description="Total tokens consumed (prompt + completion).",
    )
    tokens_prompt: int = Field(
        default=0,
        ge=0,
        description="Prompt/input tokens consumed.",
    )
    tokens_completion: int = Field(
        default=0,
        ge=0,
        description="Completion/output tokens consumed.",
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock execution time in milliseconds.",
    )
    cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Estimated cost in USD for this node's LLM usage.",
    )
    provider_used: str = Field(
        default="",
        description="Provider that actually served the request (after fallback).",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of retry attempts before this result.",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if status is FAILED.",
    )
    error_trace: Optional[str] = Field(
        default=None,
        description="Full traceback string for debugging.",
    )
