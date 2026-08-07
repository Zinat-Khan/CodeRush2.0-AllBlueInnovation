"""
AE-03 Permission Models & Default Permission Matrix.

Provides:
  - PermissionResult / SafetyResult: outcome models for policy checks.
  - PolicyRule: extensible rule schema loadable from JSON/YAML configs.
  - ThreatSeverity: severity classification for detected threats.
  - DEFAULT_ROLE_PERMISSIONS: maps each AgentRole to its default allowed
    tool set.
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


# ── Result Models ──────────────────────────────────────────────────────


class PermissionResult(BaseModel):
    """Outcome of a tool-permission check."""

    allowed: bool = Field(description="Whether the tool call is permitted.")
    reason: str = Field(
        default="",
        description="Human-readable explanation of the decision.",
    )
    agent_id: Optional[str] = Field(
        default=None,
        description="ID of the agent that requested the tool.",
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="Name of the tool that was evaluated.",
    )


class SafetyResult(BaseModel):
    """Outcome of a content-safety check."""

    safe: bool = Field(description="Whether the content is considered safe.")
    threat_type: Optional[str] = Field(
        default=None,
        description="Classification of the detected threat (if any).",
    )
    severity: ThreatSeverity = Field(
        default=ThreatSeverity.LOW,
        description="Severity level of the threat.",
    )
    matched_pattern: Optional[str] = Field(
        default=None,
        description="The pattern that triggered the detection (for audit).",
    )
    details: str = Field(
        default="",
        description="Human-readable explanation of the safety assessment.",
    )


# ── Policy Rule Model ─────────────────────────────────────────────────


class PolicyRule(BaseModel):
    """
    An extensible policy rule loaded from a JSON/YAML config file.

    Policy rules allow fine-grained control over which roles can access
    which tools, and which content patterns should be blocked.
    """

    rule_id: str = Field(description="Unique rule identifier.")
    description: str = Field(
        default="",
        description="Human-readable description of what this rule enforces.",
    )
    target_roles: List[AgentRole] = Field(
        default_factory=list,
        description="Agent roles this rule applies to. Empty = all roles.",
    )
    allowed_tools: List[str] = Field(
        default_factory=list,
        description="Tools explicitly allowed by this rule.",
    )
    denied_tools: List[str] = Field(
        default_factory=list,
        description="Tools explicitly denied by this rule.",
    )
    blocked_patterns: List[str] = Field(
        default_factory=list,
        description="Regex patterns for content-safety blocking.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this rule is active.",
    )
    priority: int = Field(
        default=0,
        description="Higher priority rules take precedence (evaluated first).",
    )


# ── Global Denied Tools ───────────────────────────────────────────────


DENIED_TOOLS: frozenset[str] = frozenset({
    "terminal_exec",
    "shell_exec",
    "file_delete",
    "file_write_system",
    "system_shutdown",
    "system_reboot",
    "process_kill",
    "registry_edit",
    "env_var_write",
    "network_config_modify",
    "disk_format",
    "user_create",
    "user_delete",
    "privilege_escalate",
})
"""
Hard-coded blocklist of system-critical tools.  No agent, regardless of
role or configuration, may invoke any tool in this set.
"""


# ── Default Role → Allowed-Tools Matrix ───────────────────────────────


DEFAULT_ROLE_PERMISSIONS: Dict[AgentRole, frozenset[str]] = {
    AgentRole.PLANNER: frozenset({
        "compile_graph",
        "validate_graph",
        "read_file",
        "web_search",
    }),
    AgentRole.RESEARCHER: frozenset({
        "web_search",
        "read_file",
        "read_url",
        "extract_entities",
        "summarize",
    }),
    AgentRole.EXECUTOR: frozenset({
        "code_execute",
        "code_generate",
        "api_call",
        "write_file",
        "read_file",
    }),
    AgentRole.ANALYST: frozenset({
        "data_analyze",
        "chart_generate",
        "read_file",
        "summarize",
        "web_search",
    }),
    AgentRole.CRITIC: frozenset({
        "validate_output",
        "schema_check",
        "read_file",
    }),
    AgentRole.VERIFIER: frozenset({
        "validate_output",
        "schema_check",
        "compare_outputs",
        "read_file",
    }),
    AgentRole.REPORTER: frozenset({
        "generate_report",
        "summarize",
        "read_file",
        "format_output",
    }),
    AgentRole.SUB_GRAPH: frozenset({
        "delegate_sub_graph",
    }),
}
"""
Default tool allowlist per role.  The PolicyEngine uses this matrix as
the baseline and overlays per-agent ``allowed_tools`` and any loaded
``PolicyRule`` configurations.
"""
