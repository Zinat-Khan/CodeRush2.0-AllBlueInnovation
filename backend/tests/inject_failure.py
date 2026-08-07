"""
AE-03 Failure Injection Utilities.

Provides test helpers for injecting controlled failures into the
execution pipeline to verify recovery, compensation, and fallback:

  - inject_schema_mutation(node_id)  — Corrupt a node's output schema
  - inject_provider_timeout(provider) — Simulate provider timeout
  - inject_permission_violation(agent_id, tool) — Trigger policy interception
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Dict, Optional

from backend.schemas.contracts import (
    AgentConfig,
    ExecutionResult,
    ExecutionStatus,
)
from backend.providers.base import ProviderError, RateLimitError

logger = logging.getLogger(__name__)


# ── Schema Mutation ────────────────────────────────────────────────────


class SchemaMutationInjector:
    """
    Injects schema mutations into node outputs to trigger Critic/Verifier
    detection and auto-retry.

    Usage::

        injector = SchemaMutationInjector(target_nodes=["executor"])
        # Wrap the normal handler
        wrapped = injector.wrap_handler(original_handler)
    """

    def __init__(
        self,
        target_nodes: list[str],
        mutation_type: str = "missing_key",
        trigger_count: int = 1,
    ):
        self.target_nodes = set(target_nodes)
        self.mutation_type = mutation_type
        self.trigger_count = trigger_count
        self._triggered: Dict[str, int] = {}

    def should_mutate(self, node_id: str) -> bool:
        """Check if this node should have its output mutated."""
        if node_id not in self.target_nodes:
            return False
        count = self._triggered.get(node_id, 0)
        if count >= self.trigger_count:
            return False
        return True

    def mutate_output(self, node_id: str, output: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mutate a node's output to trigger validation failure.

        Mutations:
          - ``missing_key``: Remove the first key from the output
          - ``wrong_type``: Replace first value with an invalid type
          - ``empty``: Return empty dict
          - ``corrupt_json``: Return a non-parseable payload marker
        """
        self._triggered[node_id] = self._triggered.get(node_id, 0) + 1
        logger.warning(
            "INJECTING schema mutation on node '%s' (type: %s, trigger #%d)",
            node_id, self.mutation_type, self._triggered[node_id],
        )

        if self.mutation_type == "missing_key" and output:
            mutated = dict(output)
            first_key = next(iter(mutated))
            del mutated[first_key]
            return mutated

        elif self.mutation_type == "wrong_type" and output:
            mutated = dict(output)
            first_key = next(iter(mutated))
            mutated[first_key] = {"__injected_error__": True}
            return mutated

        elif self.mutation_type == "empty":
            return {}

        elif self.mutation_type == "corrupt_json":
            return {"__corrupt__": "INVALID_PAYLOAD_MARKER"}

        return output

    def wrap_handler(
        self,
        handler: Callable[..., Coroutine[Any, Any, Dict[str, Any]]],
    ) -> Callable[..., Coroutine[Any, Any, Dict[str, Any]]]:
        """Wrap a node handler to inject schema mutations."""
        async def wrapped(
            node_id: str,
            config: AgentConfig,
            input_payload: Dict[str, Any],
            system_prompt: str,
        ) -> Dict[str, Any]:
            output = await handler(node_id, config, input_payload, system_prompt)
            if self.should_mutate(node_id):
                output = self.mutate_output(node_id, output)
            return output

        return wrapped


# ── Provider Timeout ───────────────────────────────────────────────────


class ProviderTimeoutInjector:
    """
    Simulates provider timeouts/failures to test fallback chain behaviour.

    Usage::

        injector = ProviderTimeoutInjector(target_provider="openai")
        # The provider router will catch the error and fallback
    """

    def __init__(
        self,
        target_provider: str,
        failure_type: str = "timeout",
        trigger_count: int = 1,
        delay_seconds: float = 5.0,
    ):
        self.target_provider = target_provider
        self.failure_type = failure_type
        self.trigger_count = trigger_count
        self.delay_seconds = delay_seconds
        self._triggered_count = 0

    def should_trigger(self, provider: str) -> bool:
        """Check if this provider call should be intercepted."""
        if provider != self.target_provider:
            return False
        if self._triggered_count >= self.trigger_count:
            return False
        return True

    async def inject(self, provider: str) -> None:
        """
        Inject the configured failure.

        Raises the appropriate exception based on failure_type.
        """
        if not self.should_trigger(provider):
            return

        self._triggered_count += 1
        logger.warning(
            "INJECTING %s on provider '%s' (trigger #%d/%d)",
            self.failure_type, provider,
            self._triggered_count, self.trigger_count,
        )

        if self.failure_type == "timeout":
            await asyncio.sleep(self.delay_seconds)
            raise ProviderError(
                f"Injected timeout after {self.delay_seconds}s",
                provider=provider,
                retryable=True,
            )

        elif self.failure_type == "rate_limit":
            raise RateLimitError(
                "Injected rate limit (429)",
                provider=provider,
            )

        elif self.failure_type == "crash":
            raise ProviderError(
                "Injected provider crash (500)",
                provider=provider,
                status_code=500,
                retryable=False,
            )

        elif self.failure_type == "auth_error":
            raise ProviderError(
                "Injected authentication error (401)",
                provider=provider,
                status_code=401,
                retryable=False,
            )


# ── Permission Violation ───────────────────────────────────────────────


class PermissionViolationInjector:
    """
    Injects tool usage into a node handler that violates the agent's
    allowed_tools list, triggering PolicyEngine interception.

    Usage::

        injector = PermissionViolationInjector(
            target_node="researcher",
            tool="code_execute",
        )
        wrapped = injector.wrap_handler(original_handler)
    """

    def __init__(
        self,
        target_node: str,
        tool: str,
        trigger_count: int = 1,
    ):
        self.target_node = target_node
        self.tool = tool
        self.trigger_count = trigger_count
        self._triggered_count = 0

    def wrap_handler(
        self,
        handler: Callable[..., Coroutine[Any, Any, Dict[str, Any]]],
    ) -> Callable[..., Coroutine[Any, Any, Dict[str, Any]]]:
        """Wrap handler to inject tool violation in the output."""
        async def wrapped(
            node_id: str,
            config: AgentConfig,
            input_payload: Dict[str, Any],
            system_prompt: str,
        ) -> Dict[str, Any]:
            output = await handler(node_id, config, input_payload, system_prompt)

            if (
                node_id == self.target_node
                and self._triggered_count < self.trigger_count
            ):
                self._triggered_count += 1
                logger.warning(
                    "INJECTING permission violation: node '%s' requesting tool '%s'",
                    node_id, self.tool,
                )
                output["__tool_request__"] = {
                    "tool": self.tool,
                    "args": {"injected": True},
                    "node_id": node_id,
                }

            return output

        return wrapped


# ── Combined Failure Scenario ──────────────────────────────────────────


class FailureScenario:
    """
    Pre-configured failure scenarios for the 4 MVD demo situations.

    Usage::

        scenario = FailureScenario.schema_corruption("executor")
        wrapped_handler = scenario.schema_injector.wrap_handler(handler)
    """

    def __init__(self):
        self.schema_injector: Optional[SchemaMutationInjector] = None
        self.timeout_injector: Optional[ProviderTimeoutInjector] = None
        self.permission_injector: Optional[PermissionViolationInjector] = None

    @classmethod
    def schema_corruption(cls, target_node: str = "executor") -> "FailureScenario":
        """MVD Scenario 2: Schema corruption → auto-retry."""
        scenario = cls()
        scenario.schema_injector = SchemaMutationInjector(
            target_nodes=[target_node],
            mutation_type="missing_key",
            trigger_count=1,
        )
        return scenario

    @classmethod
    def provider_failover(
        cls, target_provider: str = "openai"
    ) -> "FailureScenario":
        """MVD Scenario 3: Provider timeout → fallback chain."""
        scenario = cls()
        scenario.timeout_injector = ProviderTimeoutInjector(
            target_provider=target_provider,
            failure_type="timeout",
            trigger_count=1,
            delay_seconds=3.0,
        )
        return scenario

    @classmethod
    def permission_escalation(
        cls, target_node: str = "researcher", tool: str = "code_execute"
    ) -> "FailureScenario":
        """Unauthorized tool usage → PolicyEngine block."""
        scenario = cls()
        scenario.permission_injector = PermissionViolationInjector(
            target_node=target_node,
            tool=tool,
            trigger_count=1,
        )
        return scenario
