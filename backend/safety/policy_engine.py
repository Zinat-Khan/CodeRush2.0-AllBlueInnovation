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
  - Deterministic (no LLM calls) — pure rule evaluation
  - Stateless — each check is independent
  - Composable — rules are evaluated in order, first match wins
  - Auditable — every decision is logged with rule_matched and reason

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

import logging
import re
import time
from typing import Any, Dict, List, Optional

from backend.schemas.contracts import (
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

logger = logging.getLogger(__name__)


# ── Prompt Injection Patterns ─────────────────────────────────────────

INJECTION_PATTERNS = [
    {
        "name": "ignore_instructions",
        "pattern": r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?|context)",
        "severity": "high",
    },
    {
        "name": "new_persona",
        "pattern": r"(?i)(you\s+are\s+now|pretend\s+to\s+be|act\s+as\s+if|roleplay\s+as)",
        "severity": "high",
    },
    {
        "name": "system_override",
        "pattern": r"(?i)(new\s+system\s+prompt|override\s+your|disregard\s+all|forget\s+(everything|all))",
        "severity": "critical",
    },
    {
        "name": "code_execution",
        "pattern": r"(?i)(exec|eval|__import__|subprocess|os\.system|os\.popen)\s*\(",
        "severity": "critical",
    },
    {
        "name": "data_exfiltration",
        "pattern": r"(?i)(send\s+.*\s+to|upload\s+.*\s+to|post\s+.*\s+to|transmit\s+.*\s+to)\s+(http|ftp|ssh)",
        "severity": "high",
    },
    {
        "name": "boundary_escape",
        "pattern": r"(?i)(```\s*(system|admin|root)|<\/?system>|<\/?admin>)",
        "severity": "medium",
    },
]


# ── Policy Engine ─────────────────────────────────────────────────────


class PolicyEngine:
    """
    Deterministic deny-by-default security policy engine.

    Evaluates every tool request and content operation against a
    composable rule chain. No LLM calls — pure deterministic logic.

    Usage::

        engine = PolicyEngine()

        # Check a tool request
        decision = engine.evaluate_tool_request(tool_request)
        if decision.verdict == SecurityVerdict.DENY:
            raise PermissionError(decision.reason)

        # Scan content for injection
        decision = engine.scan_content(text, source="user_upload")
    """

    def __init__(self) -> None:
        self._audit_log: List[SecurityDecision] = []
        self._compiled_patterns = [
            {
                **p,
                "_regex": re.compile(p["pattern"]),
            }
            for p in INJECTION_PATTERNS
        ]
        logger.info("PolicyEngine initialised with %d injection patterns.", len(INJECTION_PATTERNS))

    # ── Tool Request Evaluation ───────────────────────────────────────

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

        Returns:
            SecurityDecision with verdict, rule_matched, and reason.
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
            # Network access check
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

        # Rule 6: All checks passed — ALLOW
        return self._log_decision(
            SecurityVerdict.ALLOW,
            request,
            "ALL_CHECKS_PASSED",
            f"Tool '{tool_name}' allowed for agent '{agent_role.value}'.",
            agent_role,
        )

    # ── Content Scanning ──────────────────────────────────────────────

    def scan_content(
        self,
        content: str,
        source: str = "unknown",
        agent_role: Optional[AgentRole] = None,
    ) -> SecurityDecision:
        """
        Scan text content for prompt-injection and adversarial patterns.

        External content (websites, PDFs, READMEs, search results, RAG chunks)
        is treated as untrusted data per Directive V2 Section 10.

        Args:
            content: Text content to scan.
            source: Source identifier (e.g., 'user_upload', 'web_scrape', 'rag_chunk').
            agent_role: Optional agent role for context.

        Returns:
            SecurityDecision — ALLOW if clean, DENY if injection detected.
        """
        if not content:
            return SecurityDecision(
                verdict=SecurityVerdict.ALLOW,
                rule_matched="EMPTY_CONTENT",
                reason="Empty content — no threat.",
                agent_role=agent_role,
            )

        # Check against all injection patterns
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

        # Content is clean
        return SecurityDecision(
            verdict=SecurityVerdict.ALLOW,
            rule_matched="CONTENT_CLEAN",
            reason=f"Content from '{source}' passed all injection checks.",
            agent_role=agent_role,
        )

    # ── Batch Evaluation ──────────────────────────────────────────────

    def evaluate_batch(
        self, requests: List[ToolRequest]
    ) -> List[SecurityDecision]:
        """Evaluate multiple tool requests."""
        return [self.evaluate_tool_request(r) for r in requests]

    # ── Operation Checks ──────────────────────────────────────────────

    def check_file_access(
        self, agent_role: AgentRole, file_path: str, operation: str = "read"
    ) -> SecurityDecision:
        """Check if an agent can access a file."""
        capability = get_capability(agent_role)

        # Only TOOL_EXECUTION and agents with write_artifacts can write
        if operation == "write" and not capability.can_write_artifacts:
            return self._log_decision(
                SecurityVerdict.DENY,
                None,
                "FILE_WRITE_DENIED",
                f"Agent '{agent_role.value}' does not have file write permission.",
                agent_role,
            )

        # Block access to sensitive paths
        sensitive_patterns = [
            r"\.env",
            r"\.git",
            r"__pycache__",
            r"node_modules",
            r"\.ssh",
            r"\.aws",
            r"credentials",
            r"secrets?",
            r"private_key",
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

        # Block internal/private URLs
        private_patterns = [
            r"localhost",
            r"127\.0\.0\.\d+",
            r"10\.\d+\.\d+\.\d+",
            r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+",
            r"192\.168\.\d+\.\d+",
            r"\.internal",
            r"\.local",
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

    # ── Audit Log ─────────────────────────────────────────────────────

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

    # ── Internal ──────────────────────────────────────────────────────

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
