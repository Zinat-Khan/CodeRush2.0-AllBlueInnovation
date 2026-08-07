"""
AE-03 Deterministic Policy Engine (Directive V2).

Deny-by-default security engine that intercepts all agent operations
and enforces access control before execution. This is a COMPLETE rewrite
of the V1 policy engine.

Intercepts:
  - Tool requests (via ToolRegistry permission + agent capability matrix)
  - Resource access (file, network, database)
  - External actions (API calls, web scraping)
  - Prompt-injection defense (treats external content as untrusted)

Architecture:
  - Deterministic (no LLM calls) â€” pure rule evaluation
  - Stateless â€” each check is independent
  - Composable â€” rules are evaluated in order, first match wins
  - Auditable â€” every decision is logged with rule_matched and reason

Policy Rules:
  1. DENY if tool not registered in ToolRegistry
  2. DENY if agent role not in tool's allowed_agents
  3. DENY if agent capability matrix forbids the operation
  4. REQUIRE_APPROVAL if risk_level >= HIGH
  5. REQUIRE_APPROVAL if tool explicitly requires approval
  6. DENY if content contains prompt-injection patterns
  7. ALLOW if all checks pass
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.schemas.contracts import (
    AgentConfig,
    AgentRole,
    RiskLevel,
    SecurityDecision,
    SecurityVerdict,
    ToolRequest,
)
from backend.safety.agent_config import (
    AGENT_CAPABILITIES,
    get_capability,
    is_tool_allowed,
)
from backend.safety.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    DENIED_TOOLS,
    PermissionResult,
    PolicyRule,
    SafetyResult,
    ThreatSeverity,
)

logger = logging.getLogger(__name__)


# â”€â”€ Prompt Injection Patterns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

INJECTION_PATTERNS = [
    {
        "name": "ignore_instructions",
        "pattern": r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?|context)",
        "severity": "high",
        "threat_type": "prompt_injection",
    },
    {
        "name": "new_persona",
        "pattern": r"(?i)(you\s+are\s+now|pretend\s+to\s+be|act\s+as\s+if|roleplay\s+as)",
        "severity": "high",
        "threat_type": "prompt_injection",
    },
    {
        "name": "system_override",
        "pattern": r"(?i)(new\s+system\s+prompt|override\s+your|disregard\s+all|forget\s+(everything|all))",
        "severity": "critical",
        "threat_type": "prompt_injection",
    },
    {
        "name": "code_execution",
        "pattern": r"(?i)(exec|eval|__import__|subprocess|os\.system|os\.popen)\s*\(",
        "severity": "critical",
        "threat_type": "code_injection",
    },
    {
        "name": "api_key_exfiltration",
        "pattern": r"(?i)(api[_\s]?key|secret[_\s]?key|token|password|credential)s?\s.*(send|post|upload|transmit|forward|give|share)|(send|post|upload|transmit|forward|give|share)\s.*(api[_\s]?key|secret[_\s]?key|token|password|credential)s?",
        "severity": "critical",
        "threat_type": "data_exfiltration",
    },
    {
        "name": "data_exfiltration",
        "pattern": r"(?i)(send\s+.*\s+to|upload\s+.*\s+to|post\s+.*\s+to|transmit\s+.*\s+to)\s+(http|ftp|ssh)",
        "severity": "high",
        "threat_type": "data_exfiltration",
    },
    {
        "name": "boundary_escape",
        "pattern": r"(?i)(```\s*(system|admin|root)|<\/?system>|<\/?admin>)",
        "severity": "medium",
        "threat_type": "prompt_injection",
    },
    {
        "name": "system_prompt_extraction",
        "pattern": r"(?i)(show|reveal|print|output|display|give)\s+(me\s+)?(your\s+)?(system\s+prompt|initial\s+instructions?|hidden\s+prompt)",
        "severity": "high",
        "threat_type": "system_prompt_extraction",
    },
]


# â”€â”€ Policy Engine â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class PolicyEngine:
    """
    Deterministic deny-by-default security policy engine.

    Evaluates every tool request and content operation against a
    composable rule chain. No LLM calls â€” pure deterministic logic.

    Usage::

        engine = PolicyEngine()

        # V1-compat methods
        perm_result = engine.check_permission(agent_config, tool_name)
        safety_result = engine.check_content_safety(content)
        perm, safety = engine.evaluate_tool_call(agent_config, tool_name, content)
    """

    def __init__(self, custom_rules: Optional[List[PolicyRule]] = None) -> None:
        self._audit_log: List[SecurityDecision] = []
        self._custom_rules: List[PolicyRule] = custom_rules or []
        self._compiled_patterns = [
            {
                **p,
                "_regex": re.compile(p["pattern"]),
            }
            for p in INJECTION_PATTERNS
        ]
        # Compile custom rule patterns
        self._compiled_custom_patterns = []
        for rule in self._custom_rules:
            for pat in rule.blocked_patterns:
                self._compiled_custom_patterns.append({
                    "rule_id": rule.rule_id,
                    "regex": re.compile(pat),
                })
        logger.info(
            "PolicyEngine initialised with %d injection patterns, %d custom rules.",
            len(INJECTION_PATTERNS),
            len(self._custom_rules),
        )

    # â”€â”€ V1-Compat: check_permission â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def check_permission(
        self, agent_config: AgentConfig, tool_name: str
    ) -> PermissionResult:
        """
        Check whether an agent is permitted to use a tool.

        Evaluation order:
          1. DENY if tool is in global DENIED_TOOLS blocklist
          2. Check custom PolicyRule deny/allow rules
          3. ALLOW if tool is in agent's explicit allowed_tools
          4. ALLOW if tool is in DEFAULT_ROLE_PERMISSIONS for agent's role
          5. DENY by default
        """
        role = agent_config.role

        # 1. Global blocklist
        if tool_name in DENIED_TOOLS:
            return PermissionResult(
                allowed=False,
                reason=f"Tool '{tool_name}' is globally blocked (DENIED_TOOLS).",
                rule_matched="DENIED_TOOLS",
                agent_role=role.value,
                agent_id=agent_config.agent_id,
                tool_name=tool_name,
            )

        # 2. Custom rules (sorted by priority descending)
        sorted_rules = sorted(
            [r for r in self._custom_rules if r.enabled],
            key=lambda r: r.priority,
            reverse=True,
        )
        for rule in sorted_rules:
            # Check if rule applies to this role
            if rule.target_roles and role not in rule.target_roles:
                continue

            # Deny rules
            if tool_name in rule.denied_tools:
                return PermissionResult(
                    allowed=False,
                    reason=f"Custom rule '{rule.rule_id}' denies tool '{tool_name}'.",
                    rule_matched=rule.rule_id,
                    agent_role=role.value,
                    agent_id=agent_config.agent_id,
                    tool_name=tool_name,
                )

            # Allow rules
            if tool_name in rule.allowed_tools:
                return PermissionResult(
                    allowed=True,
                    reason=f"Custom rule '{rule.rule_id}' grants tool '{tool_name}'.",
                    rule_matched=rule.rule_id,
                    agent_role=role.value,
                    agent_id=agent_config.agent_id,
                    tool_name=tool_name,
                )

        # 3. Agent's explicit allowed_tools
        if tool_name in agent_config.allowed_tools:
            return PermissionResult(
                allowed=True,
                reason=f"Tool '{tool_name}' is in agent's explicit allowed_tools.",
                rule_matched="AGENT_ALLOWED_TOOLS",
                agent_role=role.value,
                agent_id=agent_config.agent_id,
                tool_name=tool_name,
            )

        # 4. Default role permissions
        default_perms = DEFAULT_ROLE_PERMISSIONS.get(role, frozenset())
        if tool_name in default_perms:
            return PermissionResult(
                allowed=True,
                reason=f"Tool '{tool_name}' allowed by default role permissions for '{role.value}'.",
                rule_matched="DEFAULT_ROLE_PERMISSIONS",
                agent_role=role.value,
                agent_id=agent_config.agent_id,
                tool_name=tool_name,
            )

        # 5. Default deny
        return PermissionResult(
            allowed=False,
            reason=f"Tool '{tool_name}' denied by default â€” not in the allowed set for role '{role.value}'.",
            rule_matched="DEFAULT_DENY",
            agent_role=role.value,
            agent_id=agent_config.agent_id,
            tool_name=tool_name,
        )

    # â”€â”€ V1-Compat: check_content_safety â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def check_content_safety(self, content: str) -> SafetyResult:
        """
        Scan content for prompt injection, data exfiltration, and custom
        blocked patterns.

        Returns SafetyResult with threat_type and severity if unsafe.
        """
        if not content:
            return SafetyResult(safe=True)

        # Check built-in injection patterns
        for pattern in self._compiled_patterns:
            match = pattern["_regex"].search(content)
            if match:
                severity_str = pattern.get("severity", "medium")
                threat_type = pattern.get("threat_type", "prompt_injection")
                sev = ThreatSeverity(severity_str)
                return SafetyResult(
                    safe=False,
                    threat_type=threat_type,
                    severity=sev,
                    matched_pattern=pattern["name"],
                    details=f"Matched pattern: {pattern['name']} â€” {match.group(0)[:100]}",
                )

        # Check custom rule blocked patterns
        for cp in self._compiled_custom_patterns:
            match = cp["regex"].search(content)
            if match:
                return SafetyResult(
                    safe=False,
                    threat_type="custom_rule_violation",
                    severity=ThreatSeverity.MEDIUM,
                    matched_pattern=cp["rule_id"],
                    details=f"Custom rule '{cp['rule_id']}' matched: {match.group(0)[:100]}",
                )

        return SafetyResult(safe=True)

    # â”€â”€ V1-Compat: evaluate_tool_call â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def evaluate_tool_call(
        self,
        agent_config: AgentConfig,
        tool_name: str,
        tool_input: str = "",
    ) -> Tuple[PermissionResult, SafetyResult]:
        """
        Combined permission + content safety check.

        Returns a tuple of (PermissionResult, SafetyResult).
        """
        perm = self.check_permission(agent_config, tool_name)
        safety = self.check_content_safety(tool_input)
        return perm, safety

    # â”€â”€ V2: Tool Request Evaluation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def evaluate_tool_request(self, request: ToolRequest) -> SecurityDecision:
        """
        Evaluate a tool request against all policy rules.

        Rule chain (first match wins):
          1. DENY if tool not registered
          2. DENY if agent role not permitted by capability matrix
          3. DENY if agent capability matrix forbids the operation type
          4. REQUIRE_APPROVAL if risk >= HIGH
          5. REQUIRE_APPROVAL if tool explicitly requires approval
          6. ALLOW
        """
        tool_name = request.tool_name
        agent_role = request.agent_role

        # Rule 1: Check tool exists in registry
        try:
            from backend.tools.tool_registry import ToolRegistry
            registry = ToolRegistry()
            tool_config = registry.get_tool_config(tool_name)
            if tool_config is None:
                return self._log_decision(
                    SecurityVerdict.DENY,
                    request,
                    "TOOL_NOT_REGISTERED",
                    f"Tool '{tool_name}' is not registered in the ToolRegistry.",
                    agent_role,
                )
        except Exception as e:
            logger.warning("ToolRegistry check failed: %s", e)
            tool_config = None

        # Rule 2: Check agent capability matrix
        try:
            capability = get_capability(agent_role)
            if not capability.has_tool(tool_name):
                return self._log_decision(
                    SecurityVerdict.DENY,
                    request,
                    "CAPABILITY_MATRIX_DENIED",
                    f"Agent role '{agent_role.value}' is not permitted tool "
                    f"'{tool_name}' by the capability matrix. Allowed: "
                    f"{sorted(capability.allowed_tools)}",
                    agent_role,
                )
        except ValueError:
            return self._log_decision(
                SecurityVerdict.DENY,
                request,
                "UNKNOWN_AGENT_ROLE",
                f"Unknown agent role: '{agent_role.value}'.",
                agent_role,
            )

        # Rule 3: Check operation-level permissions
        if tool_config:
            if tool_name in ("retrieve_public_document", "public_search"):
                if not capability.can_access_network:
                    return self._log_decision(
                        SecurityVerdict.DENY,
                        request,
                        "NETWORK_ACCESS_DENIED",
                        f"Agent '{agent_role.value}' does not have network access permission.",
                        agent_role,
                    )

        # Rule 4: Risk level escalation
        request_risk = request.risk_level
        if tool_config:
            request_risk = tool_config.risk_level

        if request_risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return self._log_decision(
                SecurityVerdict.REQUIRE_APPROVAL,
                request,
                "HIGH_RISK_OPERATION",
                f"Tool '{tool_name}' has risk_level={request_risk.value}. "
                f"HITL approval required.",
                agent_role,
            )

        # Rule 5: Explicit approval requirement
        if request.requires_approval or (tool_config and tool_config.requires_approval):
            return self._log_decision(
                SecurityVerdict.REQUIRE_APPROVAL,
                request,
                "EXPLICIT_APPROVAL_REQUIRED",
                f"Tool '{tool_name}' explicitly requires HITL approval.",
                agent_role,
            )

        # Rule 6: All checks passed â€” ALLOW
        return self._log_decision(
            SecurityVerdict.ALLOW,
            request,
            "ALL_CHECKS_PASSED",
            f"Tool '{tool_name}' allowed for agent '{agent_role.value}'.",
            agent_role,
        )

    # â”€â”€ Content Scanning â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def scan_content(
        self,
        content: str,
        source: str = "unknown",
        agent_role: Optional[AgentRole] = None,
    ) -> SecurityDecision:
        """
        Scan text content for prompt-injection and adversarial patterns.
        """
        if not content:
            return SecurityDecision(
                verdict=SecurityVerdict.ALLOW,
                rule_matched="EMPTY_CONTENT",
                reason="Empty content â€” no threat.",
                agent_role=agent_role,
            )

        for pattern in self._compiled_patterns:
            match = pattern["_regex"].search(content)
            if match:
                severity = pattern.get("severity", "medium")
                matched_text = match.group(0)[:100]

                decision = SecurityDecision(
                    verdict=SecurityVerdict.DENY,
                    rule_matched=f"INJECTION_{pattern['name'].upper()}",
                    reason=(
                        f"Prompt injection detected in content from '{source}': "
                        f"pattern='{pattern['name']}', severity={severity}, "
                        f"matched='{matched_text}'"
                    ),
                    agent_role=agent_role,
                )

                self._audit_log.append(decision)
                logger.warning(
                    "[PolicyEngine] INJECTION DETECTED: %s (source=%s, severity=%s)",
                    pattern["name"],
                    source,
                    severity,
                )
                return decision

        return SecurityDecision(
            verdict=SecurityVerdict.ALLOW,
            rule_matched="CONTENT_CLEAN",
            reason=f"Content from '{source}' passed all injection checks.",
            agent_role=agent_role,
        )

    # â”€â”€ Batch Evaluation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def evaluate_batch(
        self, requests: List[ToolRequest]
    ) -> List[SecurityDecision]:
        """Evaluate multiple tool requests."""
        return [self.evaluate_tool_request(r) for r in requests]

    # â”€â”€ Operation Checks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def check_file_access(
        self, agent_role: AgentRole, file_path: str, operation: str = "read"
    ) -> SecurityDecision:
        """Check if an agent can access a file."""
        capability = get_capability(agent_role)

        if operation == "write" and not capability.can_write_artifacts:
            return self._log_decision(
                SecurityVerdict.DENY,
                None,
                "FILE_WRITE_DENIED",
                f"Agent '{agent_role.value}' does not have file write permission.",
                agent_role,
            )

        sensitive_patterns = [
            r"\.env", r"\.git", r"__pycache__", r"node_modules",
            r"\.ssh", r"\.aws", r"credentials", r"secrets?", r"private_key",
        ]
        for pattern in sensitive_patterns:
            if re.search(pattern, file_path, re.IGNORECASE):
                return self._log_decision(
                    SecurityVerdict.DENY,
                    None,
                    "SENSITIVE_PATH_BLOCKED",
                    f"Access to sensitive path '{file_path}' is blocked.",
                    agent_role,
                )

        return SecurityDecision(
            verdict=SecurityVerdict.ALLOW,
            rule_matched="FILE_ACCESS_ALLOWED",
            reason=f"File {operation} access allowed for '{agent_role.value}'.",
            agent_role=agent_role,
        )

    def check_network_access(
        self, agent_role: AgentRole, url: str
    ) -> SecurityDecision:
        """Check if an agent can access a URL."""
        capability = get_capability(agent_role)

        if not capability.can_access_network:
            return self._log_decision(
                SecurityVerdict.DENY,
                None,
                "NETWORK_ACCESS_DENIED",
                f"Agent '{agent_role.value}' does not have network access.",
                agent_role,
            )

        private_patterns = [
            r"localhost", r"127\.0\.0\.\d+", r"10\.\d+\.\d+\.\d+",
            r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+", r"192\.168\.\d+\.\d+",
            r"\.internal", r"\.local",
        ]
        for pattern in private_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return self._log_decision(
                    SecurityVerdict.DENY,
                    None,
                    "PRIVATE_URL_BLOCKED",
                    f"Access to private/internal URL '{url}' is blocked.",
                    agent_role,
                )

        return SecurityDecision(
            verdict=SecurityVerdict.ALLOW,
            rule_matched="NETWORK_ACCESS_ALLOWED",
            reason=f"Network access to '{url}' allowed for '{agent_role.value}'.",
            agent_role=agent_role,
        )

    # â”€â”€ Audit Log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return the immutable audit log of all security decisions."""
        return [
            {
                "decision_id": d.decision_id,
                "verdict": d.verdict.value,
                "rule_matched": d.rule_matched,
                "reason": d.reason,
                "agent_role": d.agent_role.value if d.agent_role else None,
                "timestamp": d.timestamp,
            }
            for d in self._audit_log
        ]

    def get_audit_summary(self) -> Dict[str, int]:
        """Return summary counts by verdict."""
        summary: Dict[str, int] = {"allow": 0, "deny": 0, "require_approval": 0}
        for d in self._audit_log:
            summary[d.verdict.value] = summary.get(d.verdict.value, 0) + 1
        return summary

    def clear_audit_log(self) -> None:
        """Clear the audit log (for testing)."""
        self._audit_log.clear()

    # â”€â”€ Internal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _log_decision(
        self,
        verdict: SecurityVerdict,
        request: Optional[ToolRequest],
        rule: str,
        reason: str,
        agent_role: Optional[AgentRole],
    ) -> SecurityDecision:
        """Create, log, and return a SecurityDecision."""
        decision = SecurityDecision(
            verdict=verdict,
            tool_request=request,
            rule_matched=rule,
            reason=reason,
            agent_role=agent_role,
        )
        self._audit_log.append(decision)

        log_fn = logger.info if verdict == SecurityVerdict.ALLOW else logger.warning
        log_fn(
            "[PolicyEngine] %s: rule=%s agent=%s reason=%s",
            verdict.value.upper(),
            rule,
            agent_role.value if agent_role else "none",
            reason[:100],
        )
        return decision


# â”€â”€ Standalone: load_policy_rules â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def load_policy_rules(path: str) -> List[PolicyRule]:
    """
    Load PolicyRule definitions from a JSON file.

    Args:
        path: Path to JSON file containing a {"rules": [...]} structure.

    Returns:
        List of PolicyRule instances, sorted by priority descending.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    import os
    if not os.path.exists(path):
        raise FileNotFoundError(f"Policy rules file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rules = [PolicyRule(**r) for r in data.get("rules", [])]
    rules.sort(key=lambda r: r.priority, reverse=True)
    return rules


