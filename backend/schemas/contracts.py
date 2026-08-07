"""
AE-03 Pydantic v2 Data Contracts (Directive V2).

Defines all typed data models that flow through the LangGraph execution
engine, PolicyEngine, RAG pipeline, observability layer, and API surface.

Models per Directive V2 Section 13:
  AgentConfig, ToolConfig, Task, TaskGraph, AgentMessage, Artifact,
  ToolRequest, ToolResult, RunState, ApprovalRequest, SecurityDecision,
  ResearchSource, RAGDocument, RAGChunk, VerificationResult, RunMetrics.
"""

from __future__ import annotations

import uuid
from enum import Enum
from time import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enumerations ──────────────────────────────────────────────────────


class AgentRole(str, Enum):
    """Logical agent roles per Directive V2 Section 9."""
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    RESEARCHER = "researcher"
    RAG = "rag"
    TOOL_EXECUTION = "tool_execution"
    ANALYST = "analyst"
    CRITIC = "critic"
    VERIFIER = "verifier"
    SECURITY = "security"
    REPORTER = "reporter"
    VISUALIZATION = "visualization"


class TaskStatus(str, Enum):
    """Status of a task within a workflow run."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    WAITING_APPROVAL = "waiting_approval"


class RiskLevel(str, Enum):
    """Risk level for tool operations."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalAction(str, Enum):
    """HITL approval actions."""
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class SecurityVerdict(str, Enum):
    """PolicyEngine security decision verdicts."""
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class RunStatus(str, Enum):
    """Overall status of an execution run."""
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DifficultyTier(str, Enum):
    """Benchmark task difficulty."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


# ── Tool Configuration ───────────────────────────────────────────────


class ToolConfig(BaseModel):
    """Configuration for a native @tool function."""
    name: str = Field(description="Unique tool identifier.")
    description: str = Field(default="", description="Human-readable tool description.")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, description="Risk classification.")
    requires_approval: bool = Field(default=False, description="Whether HITL approval is needed.")
    allowed_agents: List[AgentRole] = Field(
        default_factory=list,
        description="Agent roles permitted to invoke this tool.",
    )
    resource_limits: Dict[str, Any] = Field(
        default_factory=dict,
        description="Resource constraints (max_tokens, timeout_seconds, etc.).",
    )
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for tool input validation.",
    )
    output_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for tool output validation.",
    )


# ── Agent Configuration ──────────────────────────────────────────────


class AgentConfig(BaseModel):
    """Configuration for a logical agent within the orchestration graph."""
    agent_id: str = Field(
        default_factory=lambda: f"agent-{uuid.uuid4().hex[:8]}",
        description="Unique agent identifier.",
    )
    role: AgentRole = Field(description="Logical agent role.")
    system_prompt: str = Field(default="", description="System prompt for the agent LLM.")
    model_provider: str = Field(default="google", description="LLM provider for this agent.")
    model_name: Optional[str] = Field(default=None, description="Override model name.")
    allowed_tools: List[str] = Field(
        default_factory=list,
        description="Tool names this agent is permitted to invoke.",
    )
    max_retries: int = Field(default=2, description="Max retry attempts on failure.")
    timeout_seconds: int = Field(default=120, description="Per-node execution timeout.")
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Task & Task Graph ────────────────────────────────────────────────


class Task(BaseModel):
    """A single task node within a workflow execution graph."""
    task_id: str = Field(
        default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}",
        description="Unique task identifier.",
    )
    agent_role: AgentRole = Field(description="Agent role assigned to this task.")
    description: str = Field(default="", description="What this task should accomplish.")
    dependencies: List[str] = Field(
        default_factory=list,
        description="task_ids that must complete before this task runs.",
    )
    tools_required: List[str] = Field(
        default_factory=list,
        description="Tool names needed for this task.",
    )
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    output: Dict[str, Any] = Field(default_factory=dict, description="Task output data.")
    error: Optional[str] = Field(default=None, description="Error message if failed.")
    started_at: Optional[float] = Field(default=None)
    finished_at: Optional[float] = Field(default=None)


class TaskGraph(BaseModel):
    """A directed acyclic graph of tasks representing a workflow plan."""
    graph_id: str = Field(
        default_factory=lambda: f"graph-{uuid.uuid4().hex[:8]}",
        description="Unique graph identifier.",
    )
    goal: str = Field(default="", description="Original user goal text.")
    tasks: List[Task] = Field(default_factory=list, description="Ordered task nodes.")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time)

    def get_task(self, task_id: str) -> Optional[Task]:
        """Look up a task by ID."""
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def get_root_tasks(self) -> List[Task]:
        """Return tasks with no dependencies."""
        return [t for t in self.tasks if not t.dependencies]

    def get_leaf_tasks(self) -> List[Task]:
        """Return tasks that no other task depends on."""
        depended_on = set()
        for t in self.tasks:
            depended_on.update(t.dependencies)
        return [t for t in self.tasks if t.task_id not in depended_on]


# ── Agent Messages ───────────────────────────────────────────────────


class AgentMessage(BaseModel):
    """A message exchanged between agents in the execution graph."""
    message_id: str = Field(
        default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}",
    )
    sender: AgentRole = Field(description="Sending agent role.")
    receiver: AgentRole = Field(description="Receiving agent role.")
    content: str = Field(default="", description="Message text content.")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Structured data.")
    timestamp: float = Field(default_factory=time)


# ── Artifacts ────────────────────────────────────────────────────────


class Artifact(BaseModel):
    """An output artifact produced by an agent during execution."""
    artifact_id: str = Field(
        default_factory=lambda: f"art-{uuid.uuid4().hex[:8]}",
    )
    artifact_type: str = Field(
        default="text",
        description="Type (text | code | table | chart | report | json).",
    )
    title: str = Field(default="", description="Human-readable title.")
    content: str = Field(default="", description="Artifact content body.")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    producer_agent: AgentRole = Field(default=AgentRole.REPORTER)
    verified: bool = Field(default=False, description="Whether Verifier has validated this.")
    created_at: float = Field(default_factory=time)


# ── Tool Request / Result ────────────────────────────────────────────


class ToolRequest(BaseModel):
    """A request from an agent to invoke a tool."""
    request_id: str = Field(
        default_factory=lambda: f"treq-{uuid.uuid4().hex[:8]}",
    )
    tool_name: str = Field(description="Name of the tool to invoke.")
    agent_role: AgentRole = Field(description="Agent requesting the tool.")
    agent_id: str = Field(default="", description="Specific agent instance ID.")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Tool input arguments.")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW)
    requires_approval: bool = Field(default=False)
    timestamp: float = Field(default_factory=time)


class ToolResult(BaseModel):
    """Result returned from a tool invocation."""
    request_id: str = Field(description="Matching ToolRequest ID.")
    tool_name: str = Field(description="Name of the tool invoked.")
    success: bool = Field(default=True)
    output: Dict[str, Any] = Field(default_factory=dict, description="Tool output data.")
    error: Optional[str] = Field(default=None, description="Error message if failed.")
    tokens_used: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    latency_ms: float = Field(default=0.0)
    timestamp: float = Field(default_factory=time)


# ── Run State ────────────────────────────────────────────────────────


class RunState(BaseModel):
    """Overall state of an execution run (maps to LangGraph AgentState)."""
    run_id: str = Field(
        default_factory=lambda: f"run-{uuid.uuid4().hex[:8]}",
    )
    user_id: str = Field(default="default_user")
    workspace_id: str = Field(default="default_workspace")
    goal: str = Field(default="")
    plan: Optional[TaskGraph] = Field(default=None)
    status: RunStatus = Field(default=RunStatus.PENDING)
    artifacts: List[Artifact] = Field(default_factory=list)
    messages: List[AgentMessage] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    metrics: Optional["RunMetrics"] = Field(default=None)
    created_at: float = Field(default_factory=time)
    updated_at: float = Field(default_factory=time)


# ── Approval ─────────────────────────────────────────────────────────


class ApprovalRequest(BaseModel):
    """HITL approval request generated by LangGraph interrupt()."""
    approval_id: str = Field(
        default_factory=lambda: f"apr-{uuid.uuid4().hex[:8]}",
    )
    run_id: str = Field(description="Associated run ID.")
    agent_role: AgentRole = Field(description="Agent requesting approval.")
    tool_name: str = Field(default="", description="Tool that triggered the approval.")
    risk_level: RiskLevel = Field(default=RiskLevel.HIGH)
    context_summary: str = Field(default="", description="Human-readable context.")
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="pending", description="pending | approved | rejected | changes_requested")
    action: Optional[ApprovalAction] = Field(default=None)
    reason: Optional[str] = Field(default=None, description="Human reviewer's reason.")
    timestamp: float = Field(default_factory=time)
    resolved_at: Optional[float] = Field(default=None)


# ── Security Decision ────────────────────────────────────────────────


class SecurityDecision(BaseModel):
    """Decision record from the deterministic PolicyEngine."""
    decision_id: str = Field(
        default_factory=lambda: f"sec-{uuid.uuid4().hex[:8]}",
    )
    verdict: SecurityVerdict = Field(description="Allow, deny, or require approval.")
    tool_request: Optional[ToolRequest] = Field(default=None)
    rule_matched: str = Field(default="", description="PolicyEngine rule that triggered.")
    reason: str = Field(default="", description="Human-readable explanation.")
    agent_role: Optional[AgentRole] = Field(default=None)
    timestamp: float = Field(default_factory=time)


# ── Research Sources ──────────────────────────────────────────────────


class ResearchSource(BaseModel):
    """A research source collected by the Researcher agent."""
    source_id: str = Field(
        default_factory=lambda: f"src-{uuid.uuid4().hex[:8]}",
    )
    url: str = Field(default="", description="Source URL.")
    title: str = Field(default="", description="Source title.")
    content_hash: str = Field(default="", description="SHA-256 hash of content.")
    snippet: str = Field(default="", description="Extracted text snippet.")
    relevance_score: float = Field(default=0.0, description="Relevance to query (0-1).")
    source_quality: str = Field(default="unknown", description="Quality assessment.")
    retrieved_at: float = Field(default_factory=time)


# ── RAG Documents & Chunks ───────────────────────────────────────────


class RAGDocument(BaseModel):
    """A document ingested into the RAG pipeline."""
    document_id: str = Field(
        default_factory=lambda: f"doc-{uuid.uuid4().hex[:8]}",
    )
    filename: str = Field(default="")
    content_type: str = Field(default="text/plain")
    workspace_id: str = Field(default="default_workspace")
    chunk_count: int = Field(default=0)
    total_tokens: int = Field(default=0)
    ingested_at: float = Field(default_factory=time)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RAGChunk(BaseModel):
    """A chunk produced by RecursiveCharacterTextSplitter."""
    chunk_id: str = Field(
        default_factory=lambda: f"chk-{uuid.uuid4().hex[:8]}",
    )
    document_id: str = Field(description="Parent RAGDocument ID.")
    content: str = Field(default="", description="Chunk text content.")
    chunk_index: int = Field(default=0, description="Position within the document.")
    embedding_vector: Optional[List[float]] = Field(
        default=None,
        description="Embedding vector (populated after embedding step).",
    )
    workspace_id: str = Field(default="default_workspace")
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Verification ─────────────────────────────────────────────────────


class VerificationResult(BaseModel):
    """Result from the Verifier agent's independent validation."""
    verification_id: str = Field(
        default_factory=lambda: f"ver-{uuid.uuid4().hex[:8]}",
    )
    artifact_id: str = Field(description="Artifact being verified.")
    passed: bool = Field(default=False, description="Whether verification passed.")
    checks_run: List[str] = Field(default_factory=list, description="List of checks performed.")
    checks_passed: List[str] = Field(default_factory=list)
    checks_failed: List[str] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list, description="Issues found.")
    verifier_notes: str = Field(default="", description="Verifier's commentary.")
    timestamp: float = Field(default_factory=time)


# ── Run Metrics ──────────────────────────────────────────────────────


class RunMetrics(BaseModel):
    """Aggregated metrics for a completed execution run."""
    total_tokens: int = Field(default=0)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_cost_usd: float = Field(default=0.0)
    total_latency_ms: float = Field(default=0.0)
    nodes_total: int = Field(default=0)
    nodes_succeeded: int = Field(default=0)
    nodes_failed: int = Field(default=0)
    nodes_retried: int = Field(default=0)
    tools_invoked: int = Field(default=0)
    tools_denied: int = Field(default=0)
    approvals_requested: int = Field(default=0)
    approvals_granted: int = Field(default=0)
    approvals_rejected: int = Field(default=0)
    security_violations: int = Field(default=0)
    rag_queries: int = Field(default=0)
    research_sources_collected: int = Field(default=0)
    critic_iterations: int = Field(default=0)
    verification_pass_rate: float = Field(default=0.0)
    provider_breakdown: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-provider token/cost breakdown.",
    )


# ── Benchmark Task ───────────────────────────────────────────────────


class BenchmarkTask(BaseModel):
    """A benchmark evaluation task loaded from DATA_PROVENANCE.md."""
    task_id: str = Field(description="Unique task identifier (TASK-001, etc.).")
    source_dataset: str = Field(description="Source dataset (AgentBench, SWE-bench Lite).")
    category: str = Field(default="general")
    difficulty_tier: DifficultyTier = Field(default=DifficultyTier.MEDIUM)
    goal_text: str = Field(description="Natural-language goal for the task.")
    expected_output_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for expected output validation.",
    )
    sha256_hash: str = Field(default="", description="Integrity hash of source data.")


# ── Forward reference resolution ─────────────────────────────────────
RunState.model_rebuild()
