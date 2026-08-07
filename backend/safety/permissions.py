"""
AE-03 Permission Models & Default Permission Matrix (Directive V2).

Provides:
  - PermissionResult / SafetyResult: outcome models for policy checks.
  - PolicyRule: extensible rule schema loadable from JSON/YAML configs.
  - ThreatSeverity: severity classification for detected threats.
  - DEFAULT_ROLE_PERMISSIONS: maps each AgentRole to its default allowed
    tool set (Directive V2 aligned).
  - DENIED_TOOLS: global blocklist of system-critical tools that no agent
    may invoke.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.schemas.contracts import AgentRole


# ── Enums ──────────────────────────────────────────────────────────────


class ThreatSeverity(str, Enum):
    """Severity level for a detected content safety threat."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Outcome Models ─────────────────────────────────────────────────────


class PermissionResult(BaseModel):
    """Outcome of a tool-permission check."""

    allowed: bool = False
    reason: str = ""
    rule_matched: Optional[str] = None
    agent_role: Optional[str] = None
    tool_name: Optional[str] = None


class SafetyResult(BaseModel):
    """Outcome of a content-safety screening pass."""

    safe: bool = True
    threats: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    severity: ThreatSeverity = ThreatSeverity.LOW


# ── Policy Rule ────────────────────────────────────────────────────────


class PolicyRule(BaseModel):
    """
    An extensible policy rule that the PolicyEngine evaluates.

    Rules can be loaded from JSON/YAML config files to extend or
    override default behaviour.
    """

    rule_id: str = Field(description="Unique rule identifier.")
    name: str = Field(default="", description="Human-readable name.")
    description: str = Field(default="")
    action: str = Field(
        default="deny",
        description="Action when rule matches: 'allow', 'deny', 'require_approval'.",
    )
    applies_to_roles: List[str] = Field(
        default_factory=list,
        description="Agent roles this rule applies to (empty = all).",
    )
    applies_to_tools: List[str] = Field(
        default_factory=list,
        description="Tool names this rule applies to (empty = all).",
    )
    conditions: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional conditions for the rule.",
    )
    priority: int = Field(
        default=100,
        description="Priority (lower = evaluated first).",
    )
    enabled: bool = Field(default=True)


# ── Global Blocklist ───────────────────────────────────────────────────

DENIED_TOOLS: frozenset = frozenset({
    "os_exec",
    "shell_command",
    "rm_rf",
    "format_disk",
    "delete_database",
    "drop_table",
    "send_email",
    "transfer_funds",
    "modify_credentials",
    "escalate_privileges",
})
"""
Hard-coded blocklist of system-critical tools.  No agent, regardless of
role or configuration, may invoke any tool in this set.
"""


# ── Default Role → Allowed-Tools Matrix (Directive V2) ────────────────

DEFAULT_ROLE_PERMISSIONS: Dict[AgentRole, frozenset] = {
    AgentRole.ORCHESTRATOR: frozenset({
        "similarity_search",
    }),
    AgentRole.PLANNER: frozenset({
        "calculate_metric",
    }),
    AgentRole.RESEARCHER: frozenset({
        "public_search",
        "retrieve_public_document",
        "similarity_search",
        "analyze_dataset",
        "calculate_metric",
    }),
    AgentRole.RAG: frozenset({
        "similarity_search",
    }),
    AgentRole.TOOL_EXECUTION: frozenset({
        "similarity_search",
        "analyze_dataset",
        "retrieve_public_document",
        "generate_visualization",
        "calculate_metric",
        "public_search",
    }),
    AgentRole.ANALYST: frozenset({
        "similarity_search",
        "analyze_dataset",
        "calculate_metric",
        "retrieve_public_document",
        "generate_visualization",
        "public_search",
    }),
    AgentRole.CRITIC: frozenset(),
    AgentRole.VERIFIER: frozenset(),
    AgentRole.SECURITY: frozenset(),
    AgentRole.REPORTER: frozenset(),
    AgentRole.VISUALIZATION: frozenset({
        "generate_visualization",
    }),
}
"""
Default tool allowlist per role (Directive V2 aligned).  The PolicyEngine
uses this matrix as the baseline and overlays per-agent ``allowed_tools``
and any loaded ``PolicyRule`` configurations.
"""
