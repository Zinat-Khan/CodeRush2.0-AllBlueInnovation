"""AE-03: Safety sub-package — Policy Engine, Interceptor & HITL Approval Gate."""

from backend.safety.permissions import (
    DENIED_TOOLS,
    DEFAULT_ROLE_PERMISSIONS,
    PermissionResult,
    PolicyRule,
    SafetyResult,
    ThreatSeverity,
)
from backend.safety.policy_engine import PolicyEngine
from backend.safety.interceptor import SafetyInterceptor, InterceptionResult
from backend.safety.approval_gate import ApprovalGate, ApprovalAction

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
