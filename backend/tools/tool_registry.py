"""
AE-03 Tool Registry (Directive V2).

Centralised registry for all native ``@tool`` functions with:
  - Risk-level based access control
  - Agent role permission enforcement
  - HITL approval requirement checking
  - Tool discovery and listing
  - Resource limit enforcement

Integrates with the PolicyEngine (Module 6) for deny-by-default security.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from langchain_core.tools import BaseTool

from backend.schemas.contracts import AgentRole, RiskLevel, ToolConfig, ToolRequest, SecurityDecision, SecurityVerdict
from backend.tools.native_tools import (
    ALL_NATIVE_TOOLS,
    TOOL_METADATA,
    TOOL_NAME_MAP,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Centralised registry managing tool access, permissions, and discovery.

    All tool invocations pass through the registry which enforces:
      1. Tool existence check
      2. Agent role permission check
      3. Risk-level based approval routing
      4. Resource limit validation

    Usage::

        registry = ToolRegistry()
        tools = registry.get_tools_for_agent(AgentRole.RESEARCHER)
        decision = registry.check_permission(tool_request)
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}
        self._configs: Dict[str, ToolConfig] = {}
        self._register_native_tools()

    def _register_native_tools(self) -> None:
        """Register all native @tool functions with their metadata."""
        for tool_obj in ALL_NATIVE_TOOLS:
            name = tool_obj.name
            meta = TOOL_METADATA.get(name, {})

            config = ToolConfig(
                name=name,
                description=tool_obj.description or "",
                risk_level=meta.get("risk_level", RiskLevel.LOW),
                requires_approval=meta.get("requires_approval", False),
                allowed_agents=meta.get("allowed_agents", []),
                resource_limits=meta.get("resource_limits", {}),
            )

            self._tools[name] = tool_obj
            self._configs[name] = config

            logger.debug(
                "Registered tool '%s' (risk=%s, approval=%s, agents=%s)",
                name,
                config.risk_level.value,
                config.requires_approval,
                [a.value for a in config.allowed_agents],
            )

        logger.info("ToolRegistry: %d native tools registered.", len(self._tools))

    # ── Registration ──────────────────────────────────────────────────

    def register_tool(
        self,
        tool_obj: BaseTool,
        config: ToolConfig,
    ) -> None:
        """Register an additional tool with its configuration."""
        self._tools[config.name] = tool_obj
        self._configs[config.name] = config
        logger.info("Registered external tool '%s'", config.name)

    # ── Discovery ─────────────────────────────────────────────────────

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_tool_config(self, name: str) -> Optional[ToolConfig]:
        """Get a tool's configuration by name."""
        return self._configs.get(name)

    def get_all_tools(self) -> List[BaseTool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_all_tool_names(self) -> List[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def get_tools_for_agent(self, agent_role: AgentRole) -> List[BaseTool]:
        """
        Return tools permitted for a specific agent role.

        Enforces the agent capability matrix: an agent can only access
        tools whose ``allowed_agents`` list includes its role.
        """
        allowed = []
        for name, config in self._configs.items():
            if not config.allowed_agents or agent_role in config.allowed_agents:
                tool_obj = self._tools.get(name)
                if tool_obj:
                    allowed.append(tool_obj)

        logger.debug(
            "Agent '%s' has access to %d tools: %s",
            agent_role.value,
            len(allowed),
            [t.name for t in allowed],
        )
        return allowed

    def get_tool_names_for_agent(self, agent_role: AgentRole) -> List[str]:
        """Return tool names permitted for a specific agent role."""
        return [t.name for t in self.get_tools_for_agent(agent_role)]

    # ── Permission Checking ───────────────────────────────────────────

    def check_permission(self, request: ToolRequest) -> SecurityDecision:
        """
        Check if a tool request is permitted.

        Returns a SecurityDecision with:
          - ALLOW: Tool can proceed
          - DENY: Agent doesn't have permission
          - REQUIRE_APPROVAL: Tool needs HITL approval first

        This is the pre-PolicyEngine check. The PolicyEngine (Module 6)
        may impose additional restrictions.
        """
        tool_name = request.tool_name
        agent_role = request.agent_role

        # Check tool exists
        config = self._configs.get(tool_name)
        if config is None:
            return SecurityDecision(
                verdict=SecurityVerdict.DENY,
                tool_request=request,
                rule_matched="TOOL_NOT_FOUND",
                reason=f"Tool '{tool_name}' is not registered.",
                agent_role=agent_role,
            )

        # Check agent role permission
        if config.allowed_agents and agent_role not in config.allowed_agents:
            return SecurityDecision(
                verdict=SecurityVerdict.DENY,
                tool_request=request,
                rule_matched="AGENT_ROLE_DENIED",
                reason=(
                    f"Agent role '{agent_role.value}' is not permitted to use "
                    f"tool '{tool_name}'. Allowed: "
                    f"{[a.value for a in config.allowed_agents]}"
                ),
                agent_role=agent_role,
            )

        # Check if approval is required (risk-based or explicit)
        if config.requires_approval or request.requires_approval:
            return SecurityDecision(
                verdict=SecurityVerdict.REQUIRE_APPROVAL,
                tool_request=request,
                rule_matched="APPROVAL_REQUIRED",
                reason=(
                    f"Tool '{tool_name}' requires HITL approval "
                    f"(risk_level={config.risk_level.value})."
                ),
                agent_role=agent_role,
            )

        # Check risk level escalation
        if config.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return SecurityDecision(
                verdict=SecurityVerdict.REQUIRE_APPROVAL,
                tool_request=request,
                rule_matched="HIGH_RISK_TOOL",
                reason=(
                    f"Tool '{tool_name}' has risk_level={config.risk_level.value} "
                    f"and requires approval."
                ),
                agent_role=agent_role,
            )

        # All checks passed
        return SecurityDecision(
            verdict=SecurityVerdict.ALLOW,
            tool_request=request,
            rule_matched="PERMITTED",
            reason=f"Tool '{tool_name}' allowed for agent '{agent_role.value}'.",
            agent_role=agent_role,
        )

    # ── Observability ─────────────────────────────────────────────────

    def get_registry_info(self) -> Dict[str, Any]:
        """Return registry status for observability."""
        return {
            "total_tools": len(self._tools),
            "tools": [
                {
                    "name": name,
                    "risk_level": config.risk_level.value,
                    "requires_approval": config.requires_approval,
                    "allowed_agents": [a.value for a in config.allowed_agents],
                }
                for name, config in self._configs.items()
            ],
        }

    def get_risk_summary(self) -> Dict[str, int]:
        """Return count of tools by risk level."""
        summary: Dict[str, int] = {}
        for config in self._configs.values():
            level = config.risk_level.value
            summary[level] = summary.get(level, 0) + 1
        return summary
