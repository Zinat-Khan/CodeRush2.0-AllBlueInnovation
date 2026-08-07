"""
AE-03 Policy Engine — Standalone, Stateless Policy Evaluator.

[REV2 PATCH] — Decoupled from the Verifier agent.  The Verifier handles
schema/output validation; the Policy Engine handles access control and
content-safety screening.

Provides:
  - PolicyEngine: evaluates tool-permission and content-safety checks.
  - load_policy_rules(): loads extensible policy rules from a JSON config.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from backend.schemas.contracts import AgentConfig, AgentRole
from backend.safety.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    DENIED_TOOLS,
    PermissionResult,
    PolicyRule,
    SafetyResult,
    ThreatSeverity,
)

logger = logging.getLogger(__name__)


# ── Adversarial Content Detection Patterns ─────────────────────────────

_ADVERSARIAL_PATTERNS: List[dict] = [
    {
        "name": "prompt_injection_ignore",
        "pattern": r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?|context)",
        "threat_type": "prompt_injection",
        "severity": ThreatSeverity.CRITICAL,
    },
    {
        "name": "prompt_injection_new_role",
        "pattern": r"(?i)you\s+are\s+now\s+(a\s+)?(new|different|unrestricted|jailbroken)",
        "threat_type": "prompt_injection",
        "severity": ThreatSeverity.CRITICAL,
    },
    {
        "name": "system_prompt_extraction",
        "pattern": r"(?i)(show|reveal|print|output|repeat|display)\s+(your\s+)?(system\s+prompt|instructions|initial\s+prompt|system\s+message)",
        "threat_type": "system_prompt_extraction",
        "severity": ThreatSeverity.HIGH,
    },
    {
        "name": "role_override",
        "pattern": r"(?i)(act|behave|respond)\s+as\s+(if\s+)?(you\s+)?(are|were)\s+(a\s+)?(different|unrestricted|admin|root|sudo)",
        "threat_type": "role_override",
        "severity": ThreatSeverity.HIGH,
    },
    {
        "name": "delimiter_injection",
        "pattern": r"(?i)(```|<\|im_end\|>|<\|im_start\|>|<\|system\|>|<\|user\|>|<\|assistant\|>|\[INST\]|\[/INST\])",
        "threat_type": "delimiter_injection",
        "severity": ThreatSeverity.MEDIUM,
    },
    {
        "name": "api_key_exfiltration",
        "pattern": r"(?i)(send|post|transmit|exfiltrate|leak|share|output)\s+.{0,40}(api[_\s]?key|secret|token|password|credential)",
        "threat_type": "data_exfiltration",
        "severity": ThreatSeverity.CRITICAL,
    },
    {
        "name": "encoded_injection",
        "pattern": r"(?i)(base64|hex|rot13|url)[_\s]?(encode|decode|convert)\s+.{0,30}(prompt|instruction|system|ignore)",
        "threat_type": "encoded_injection",
        "severity": ThreatSeverity.MEDIUM,
    },
    {
        "name": "tool_escalation_request",
        "pattern": r"(?i)(grant|give|enable|allow|unlock)\s+(me\s+|yourself\s+)?(access|permission)\s+to\s+.{0,40}(terminal|shell|system|admin|root|sudo|exec)",
        "threat_type": "privilege_escalation",
        "severity": ThreatSeverity.HIGH,
    },
]

_COMPILED_PATTERNS: List[dict] = []
for _p in _ADVERSARIAL_PATTERNS:
    _COMPILED_PATTERNS.append({
        **_p,
        "_compiled": re.compile(_p["pattern"]),
    })


# ── Policy Rule Loading ───────────────────────────────────────────────


def load_policy_rules(path: str) -> List[PolicyRule]:
    """
    Load policy rules from a JSON configuration file.

    Expected format::

        {
          "rules": [
            {
              "rule_id": "restrict-researcher-tools",
              "description": "Researchers may not call code_execute",
              "target_roles": ["researcher"],
              "denied_tools": ["code_execute"],
              "enabled": true,
              "priority": 10
            }
          ]
        }

    Args:
        path: Filesystem path to the JSON config file.

    Returns:
        List of validated PolicyRule objects (sorted by priority descending).

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        json.JSONDecodeError: If the config file contains invalid JSON.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Policy config not found: {path}")

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    rules_data = raw.get("rules", [])
    rules = [PolicyRule(**r) for r in rules_data]
    # Sort by priority descending (highest priority evaluated first)
    rules.sort(key=lambda r: r.priority, reverse=True)
    logger.info("Loaded %d policy rules from '%s'", len(rules), path)
    return rules


# ── Policy Engine ──────────────────────────────────────────────────────


class PolicyEngine:
    """
    Standalone, stateless policy evaluator.

    [REV2 PATCH] — Separated from the Verifier agent.  The PolicyEngine
    handles access control (tool permissions) and content-safety screening.
    The Verifier handles output schema validation.

    Usage::

        engine = PolicyEngine()
        perm = engine.check_permission(agent_config, "terminal_exec")
        safety = engine.check_content_safety(user_input)
    """

    def __init__(
        self,
        *,
        custom_rules: Optional[List[PolicyRule]] = None,
        additional_denied_tools: Optional[set[str]] = None,
    ):
        self._custom_rules: List[PolicyRule] = custom_rules or []
        self._additional_denied: set[str] = additional_denied_tools or set()

    # ── Tool Permission Checking ───────────────────────────────────────

    def check_permission(
        self,
        agent_config: AgentConfig,
        tool_name: str,
    ) -> PermissionResult:
        """
        Evaluate whether an agent is allowed to invoke a specific tool.

        Decision logic (evaluated in order):
          1. DENY if ``tool_name`` is in the global ``DENIED_TOOLS`` blocklist.
          2. DENY if ``tool_name`` is in any custom PolicyRule's ``denied_tools``
             that targets this agent's role.
          3. ALLOW if ``tool_name`` is in the agent's explicit ``allowed_tools``.
          4. ALLOW if ``tool_name`` is in the ``DEFAULT_ROLE_PERMISSIONS``
             for this agent's role.
          5. ALLOW if a matching custom PolicyRule grants the tool.
          6. DENY by default (principle of least privilege).

        Args:
            agent_config: Configuration of the requesting agent.
            tool_name: Name of the tool being requested.

        Returns:
            PermissionResult indicating allowed/denied with reason.
        """
        agent_id = agent_config.agent_id
        role = agent_config.role

        # 1. Global blocklist — absolute deny
        if tool_name in DENIED_TOOLS or tool_name in self._additional_denied:
            reason = (
                f"Tool '{tool_name}' is globally blocked (DENIED_TOOLS). "
                f"No agent may invoke this tool."
            )
            logger.warning(
                "SECURITY ALERT: Agent '%s' (role=%s) attempted blocked "
                "tool '%s'",
                agent_id, role.value, tool_name,
            )
            return PermissionResult(
                allowed=False,
                reason=reason,
                agent_id=agent_id,
                tool_name=tool_name,
            )

        # 2. Custom rules — explicit denials (highest priority first)
        for rule in self._custom_rules:
            if not rule.enabled:
                continue
            if rule.target_roles and role not in rule.target_roles:
                continue
            if tool_name in rule.denied_tools:
                reason = (
                    f"Tool '{tool_name}' denied by policy rule "
                    f"'{rule.rule_id}': {rule.description}"
                )
                logger.info(
                    "Policy rule '%s' denied tool '%s' for agent '%s'",
                    rule.rule_id, tool_name, agent_id,
                )
                return PermissionResult(
                    allowed=False,
                    reason=reason,
                    agent_id=agent_id,
                    tool_name=tool_name,
                )

        # 3. Agent-level explicit allowlist
        if agent_config.allowed_tools and tool_name in agent_config.allowed_tools:
            return PermissionResult(
                allowed=True,
                reason=f"Tool '{tool_name}' is in agent's explicit allowed_tools.",
                agent_id=agent_id,
                tool_name=tool_name,
            )

        # 4. Default role permissions
        role_defaults = DEFAULT_ROLE_PERMISSIONS.get(role, frozenset())
        if tool_name in role_defaults:
            return PermissionResult(
                allowed=True,
                reason=(
                    f"Tool '{tool_name}' is permitted by default for "
                    f"role '{role.value}'."
                ),
                agent_id=agent_id,
                tool_name=tool_name,
            )

        # 5. Custom rules — explicit grants
        for rule in self._custom_rules:
            if not rule.enabled:
                continue
            if rule.target_roles and role not in rule.target_roles:
                continue
            if tool_name in rule.allowed_tools:
                return PermissionResult(
                    allowed=True,
                    reason=(
                        f"Tool '{tool_name}' granted by policy rule "
                        f"'{rule.rule_id}': {rule.description}"
                    ),
                    agent_id=agent_id,
                    tool_name=tool_name,
                )

        # 6. Default deny
        reason = (
            f"Tool '{tool_name}' is not in the allowed set for agent "
            f"'{agent_id}' (role='{role.value}'). Denied by default."
        )
        logger.info(
            "Default deny: agent '%s' (role=%s) requested tool '%s'",
            agent_id, role.value, tool_name,
        )
        return PermissionResult(
            allowed=False,
            reason=reason,
            agent_id=agent_id,
            tool_name=tool_name,
        )

    # ── Content Safety Checking ────────────────────────────────────────

    def check_content_safety(self, content: str) -> SafetyResult:
        """
        Scan content for adversarial patterns (prompt injection, data
        exfiltration, privilege escalation, etc.).

        Uses a curated set of compiled regex patterns plus any
        ``blocked_patterns`` from loaded custom PolicyRules.

        Args:
            content: The text content to evaluate.

        Returns:
            SafetyResult indicating safe/unsafe with threat details.
        """
        if not content or not content.strip():
            return SafetyResult(safe=True, details="Empty content is safe.")

        # Check built-in adversarial patterns
        for pattern_def in _COMPILED_PATTERNS:
            match = pattern_def["_compiled"].search(content)
            if match:
                matched_text = match.group(0)
                logger.warning(
                    "CONTENT SAFETY ALERT: Pattern '%s' matched: '%s'",
                    pattern_def["name"],
                    matched_text[:80],
                )
                return SafetyResult(
                    safe=False,
                    threat_type=pattern_def["threat_type"],
                    severity=pattern_def["severity"],
                    matched_pattern=pattern_def["name"],
                    details=(
                        f"Adversarial pattern '{pattern_def['name']}' "
                        f"detected: '{matched_text[:60]}'"
                    ),
                )

        # Check custom rule blocked patterns
        for rule in self._custom_rules:
            if not rule.enabled:
                continue
            for pattern_str in rule.blocked_patterns:
                try:
                    regex = re.compile(pattern_str, re.IGNORECASE)
                    match = regex.search(content)
                    if match:
                        matched_text = match.group(0)
                        logger.warning(
                            "Custom rule '%s' blocked content: '%s'",
                            rule.rule_id,
                            matched_text[:80],
                        )
                        return SafetyResult(
                            safe=False,
                            threat_type="custom_rule_violation",
                            severity=ThreatSeverity.HIGH,
                            matched_pattern=f"rule:{rule.rule_id}",
                            details=(
                                f"Custom policy rule '{rule.rule_id}' "
                                f"blocked pattern matched: "
                                f"'{matched_text[:60]}'"
                            ),
                        )
                except re.error as exc:
                    logger.error(
                        "Invalid regex in rule '%s': %s",
                        rule.rule_id, exc,
                    )

        return SafetyResult(
            safe=True,
            details="No adversarial patterns detected.",
        )

    # ── Bulk / Convenience ─────────────────────────────────────────────

    def evaluate_tool_call(
        self,
        agent_config: AgentConfig,
        tool_name: str,
        tool_input: str = "",
    ) -> tuple[PermissionResult, SafetyResult]:
        """
        Combined permission + content-safety check for a tool invocation.

        Returns:
            Tuple of (PermissionResult, SafetyResult).
        """
        perm = self.check_permission(agent_config, tool_name)
        safety = self.check_content_safety(tool_input)
        return perm, safety
