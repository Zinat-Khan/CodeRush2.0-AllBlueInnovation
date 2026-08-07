"""AE-03: Safety sub-package — PolicyEngine, Agent Config, HITL Gate, Permissions."""

from backend.safety.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    DENIED_TOOLS,
    PermissionResult,
    PolicyRule,
    SafetyResult,
    ThreatSeverity,
)
from backend.safety.policy_engine import PolicyEngine
from backend.safety.interceptor import InterceptionResult, SafetyInterceptor
from backend.safety.approval_gate import ApprovalAction, ApprovalGate

__all__ = [
    "ApprovalAction",
    "ApprovalGate",
    "DEFAULT_ROLE_PERMISSIONS",
    "DENIED_TOOLS",
    "InterceptionResult",
    "PermissionResult",
    "PolicyEngine",
    "PolicyRule",
    "SafetyInterceptor",
    "SafetyResult",
    "ThreatSeverity",
]
