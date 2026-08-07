"""
AE-03 Agent Capability Matrix (Directive V2).

Defines the authoritative capability matrix mapping all 11 logical agent
roles to their permitted tools, allowed operations, resource limits,
and interaction constraints.

This is the single source of truth for what each agent CAN do.
The PolicyEngine uses this matrix to enforce deny-by-default access.

Roles per Directive V2 Section 9:
  ORCHESTRATOR, PLANNER, RESEARCHER, RAG, TOOL_EXECUTION,
  ANALYST, CRITIC, VERIFIER, SECURITY, REPORTER, VISUALIZATION
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Set

from backend.schemas.contracts import AgentRole, RiskLevel


# ── Agent Capability Entry ────────────────────────────────────────────


class AgentCapability:
    """Defines what a single agent role is allowed to do."""

    def __init__(
        self,
        role: AgentRole,
        allowed_tools: List[str],
        can_invoke_llm: bool = True,
        can_read_rag: bool = False,
        can_write_artifacts: bool = False,
        can_access_network: bool = False,
        can_execute_code: bool = False,
        max_retries: int = 2,
        timeout_seconds: int = 120,
        max_risk_level: RiskLevel = RiskLevel.LOW,
        description: str = "",
    ):
        self.role = role
        self.allowed_tools = frozenset(allowed_tools)
        self.can_invoke_llm = can_invoke_llm
        self.can_read_rag = can_read_rag
        self.can_write_artifacts = can_write_artifacts
        self.can_access_network = can_access_network
        self.can_execute_code = can_execute_code
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.max_risk_level = max_risk_level
        self.description = description

    def has_tool(self, tool_name: str) -> bool:
        """Check if this role can use a specific tool."""
        return tool_name in self.allowed_tools

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for observability."""
        return {
            "role": self.role.value,
            "allowed_tools": sorted(self.allowed_tools),
            "can_invoke_llm": self.can_invoke_llm,
            "can_read_rag": self.can_read_rag,
            "can_write_artifacts": self.can_write_artifacts,
            "can_access_network": self.can_access_network,
            "can_execute_code": self.can_execute_code,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "max_risk_level": self.max_risk_level.value,
        }


# ── Capability Matrix ────────────────────────────────────────────────

AGENT_CAPABILITIES: Dict[AgentRole, AgentCapability] = {
    AgentRole.ORCHESTRATOR: AgentCapability(
        role=AgentRole.ORCHESTRATOR,
        allowed_tools=["similarity_search"],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=False,
        can_access_network=False,
        can_execute_code=False,
        max_retries=3,
        timeout_seconds=180,
        max_risk_level=RiskLevel.MEDIUM,
        description="Coordinates workflow, delegates tasks, monitors progress.",
    ),
    AgentRole.PLANNER: AgentCapability(
        role=AgentRole.PLANNER,
        allowed_tools=["calculate_metric"],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=False,
        can_access_network=False,
        can_execute_code=False,
        max_retries=2,
        timeout_seconds=120,
        max_risk_level=RiskLevel.LOW,
        description="Decomposes goals into task DAGs. No tool execution.",
    ),
    AgentRole.RESEARCHER: AgentCapability(
        role=AgentRole.RESEARCHER,
        allowed_tools=[
            "public_search",
            "retrieve_public_document",
            "similarity_search",
            "analyze_dataset",
            "calculate_metric",
        ],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=False,
        can_access_network=True,
        can_execute_code=False,
        max_retries=2,
        timeout_seconds=120,
        max_risk_level=RiskLevel.MEDIUM,
        description="Searches public sources and workspace knowledge.",
    ),
    AgentRole.RAG: AgentCapability(
        role=AgentRole.RAG,
        allowed_tools=["similarity_search"],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=False,
        can_access_network=False,
        can_execute_code=False,
        max_retries=2,
        timeout_seconds=60,
        max_risk_level=RiskLevel.LOW,
        description="Retrieves workspace context via vector search.",
    ),
    AgentRole.TOOL_EXECUTION: AgentCapability(
        role=AgentRole.TOOL_EXECUTION,
        allowed_tools=[
            "similarity_search",
            "analyze_dataset",
            "retrieve_public_document",
            "generate_visualization",
            "calculate_metric",
            "public_search",
        ],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=True,
        can_access_network=True,
        can_execute_code=True,
        max_retries=2,
        timeout_seconds=120,
        max_risk_level=RiskLevel.HIGH,
        description="Executes tool operations. Broadest tool access.",
    ),
    AgentRole.ANALYST: AgentCapability(
        role=AgentRole.ANALYST,
        allowed_tools=[
            "similarity_search",
            "analyze_dataset",
            "calculate_metric",
            "retrieve_public_document",
            "generate_visualization",
            "public_search",
        ],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=True,
        can_access_network=True,
        can_execute_code=False,
        max_retries=2,
        timeout_seconds=120,
        max_risk_level=RiskLevel.MEDIUM,
        description="Analyzes data, computes metrics, generates insights.",
    ),
    AgentRole.CRITIC: AgentCapability(
        role=AgentRole.CRITIC,
        allowed_tools=[],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=False,
        can_access_network=False,
        can_execute_code=False,
        max_retries=1,
        timeout_seconds=90,
        max_risk_level=RiskLevel.LOW,
        description="Reviews work quality. No tool access — LLM-only evaluation.",
    ),
    AgentRole.VERIFIER: AgentCapability(
        role=AgentRole.VERIFIER,
        allowed_tools=[],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=False,
        can_access_network=False,
        can_execute_code=False,
        max_retries=1,
        timeout_seconds=90,
        max_risk_level=RiskLevel.LOW,
        description="Verifies factual accuracy. No tool access — LLM-only verification.",
    ),
    AgentRole.SECURITY: AgentCapability(
        role=AgentRole.SECURITY,
        allowed_tools=[],
        can_invoke_llm=False,
        can_read_rag=False,
        can_write_artifacts=False,
        can_access_network=False,
        can_execute_code=False,
        max_retries=0,
        timeout_seconds=10,
        max_risk_level=RiskLevel.LOW,
        description="Deterministic policy evaluation. No LLM, no tools.",
    ),
    AgentRole.REPORTER: AgentCapability(
        role=AgentRole.REPORTER,
        allowed_tools=[],
        can_invoke_llm=True,
        can_read_rag=True,
        can_write_artifacts=True,
        can_access_network=False,
        can_execute_code=False,
        max_retries=2,
        timeout_seconds=120,
        max_risk_level=RiskLevel.LOW,
        description="Compiles final reports. LLM access for writing, artifact creation.",
    ),
    AgentRole.VISUALIZATION: AgentCapability(
        role=AgentRole.VISUALIZATION,
        allowed_tools=["generate_visualization"],
        can_invoke_llm=True,
        can_read_rag=False,
        can_write_artifacts=True,
        can_access_network=False,
        can_execute_code=False,
        max_retries=2,
        timeout_seconds=60,
        max_risk_level=RiskLevel.LOW,
        description="Creates charts and visualizations.",
    ),
}


# ── Lookup Helpers ────────────────────────────────────────────────────


def get_capability(role: AgentRole) -> AgentCapability:
    """Get capability entry for an agent role."""
    cap = AGENT_CAPABILITIES.get(role)
    if cap is None:
        raise ValueError(f"Unknown agent role: {role.value}")
    return cap


def get_allowed_tools(role: AgentRole) -> List[str]:
    """Return sorted list of tool names permitted for an agent role."""
    return sorted(get_capability(role).allowed_tools)


def is_tool_allowed(role: AgentRole, tool_name: str) -> bool:
    """Check if a specific tool is allowed for an agent role."""
    return get_capability(role).has_tool(tool_name)


def get_all_capabilities() -> Dict[str, Dict[str, Any]]:
    """Return the full capability matrix as a serializable dict."""
    return {
        role.value: cap.to_dict()
        for role, cap in AGENT_CAPABILITIES.items()
    }
