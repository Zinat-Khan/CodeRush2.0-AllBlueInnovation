"""
AE-03 Benchmark Runner — 3-Mode Evaluation Harness.

Executes benchmark tasks in three modes and collects metrics for
marginal value comparison:

  1. **Single Monolithic Prompt** — One LLM call with the full task.
  2. **Static Manual Multi-Agent Graph** — Hardcoded DAG with fixed
     agent assignments (researcher → executor → verifier → reporter).
  3. **AE-03 Dynamic Generated Graph** — Planner-compiled DAG from
     the natural-language goal text.

All tasks are loaded from ``evaluation/DATA_PROVENANCE.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional

from backend.schemas.artifacts import (
    BenchmarkResult,
    BenchmarkTask,
    DifficultyTier,
    TraceEventType,
)
from backend.schemas.contracts import (
    AgentConfig,
    AgentRole,
    ExecutionGraph,
    ExecutionResult,
    ExecutionStatus,
)
from backend.providers.router import ProviderRouter
from backend.compiler.graph_compiler import GraphCompiler, CompilationResult
from backend.engine.executor import AsyncDAGExecutor, NodeHandler
from backend.engine.state_manager import ExecutionState
from backend.observability.tracker import CostTracker, calculate_cost
from backend.observability.tracer import ExecutionTracer
from backend.evaluation.tasks import load_benchmark_tasks, get_task_summary

logger = logging.getLogger(__name__)


# ── Execution Modes ────────────────────────────────────────────────────


class ExecutionMode:
    """Constants for the three benchmark execution modes."""
    SINGLE_PROMPT = "single_prompt"
    STATIC_MULTI_AGENT = "static_multi_agent"
    AE03_DYNAMIC = "ae03_dynamic"

    ALL = [SINGLE_PROMPT, STATIC_MULTI_AGENT, AE03_DYNAMIC]


# ── Benchmark Runner ───────────────────────────────────────────────────


class BenchmarkRunner:
    """
    Runs benchmark tasks in all three modes and collects metrics.

    Usage::

        runner = BenchmarkRunner(
            provider_router=router,
            node_handler=handler_fn,
        )
        results = await runner.run_all()
        # results is a list of BenchmarkResult for every (task, mode) combo
    """

    def __init__(
        self,
        provider_router: ProviderRouter,
        node_handler: NodeHandler,
        *,
        provenance_path: Optional[str] = None,
        default_provider: str = "openai",
        default_model: Optional[str] = None,
    ):
        self._router = provider_router
        self._handler = node_handler
        self._provenance_path = provenance_path
        self._default_provider = default_provider
        self._default_model = default_model
        self._results: List[BenchmarkResult] = []

    async def run_all(
        self,
        tasks: Optional[List[BenchmarkTask]] = None,
        modes: Optional[List[str]] = None,
    ) -> List[BenchmarkResult]:
        """
        Run all benchmark tasks in the specified modes.

        Args:
            tasks: Override task list. If None, loads from DATA_PROVENANCE.md.
            modes: Override mode list. If None, runs all three modes.

        Returns:
            List of BenchmarkResult for each (task, mode) pair.
        """
        if tasks is None:
            tasks = load_benchmark_tasks(self._provenance_path)

        if modes is None:
            modes = ExecutionMode.ALL

        summary = get_task_summary(tasks)
        logger.info(
            "Starting benchmark run: %d tasks x %d modes = %d executions",
            summary["total_tasks"],
            len(modes),
            summary["total_tasks"] * len(modes),
        )

        self._results.clear()

        for task in tasks:
            for mode in modes:
                logger.info(
                    "Running task '%s' (%s) in mode '%s'",
                    task.task_id, task.difficulty_tier.value, mode,
                )
                try:
                    result = await self._run_single(task, mode)
                except Exception as e:
                    logger.error(
                        "Benchmark error for %s/%s: %s",
                        task.task_id, mode, e,
                    )
                    result = BenchmarkResult(
                        task_id=task.task_id,
                        mode=mode,
                        success=False,
                        error=str(e),
                    )
                self._results.append(result)

        logger.info("Benchmark run complete: %d results", len(self._results))
        return self._results

    async def run_single_task(
        self,
        task: BenchmarkTask,
        mode: str,
    ) -> BenchmarkResult:
        """Run a single task in a specific mode."""
        result = await self._run_single(task, mode)
        self._results.append(result)
        return result

    @property
    def results(self) -> List[BenchmarkResult]:
        """All collected results from this runner."""
        return list(self._results)

    # ── Mode Dispatchers ───────────────────────────────────────────────

    async def _run_single(
        self, task: BenchmarkTask, mode: str
    ) -> BenchmarkResult:
        """Dispatch to the appropriate mode handler."""
        if mode == ExecutionMode.SINGLE_PROMPT:
            return await self._run_single_prompt(task)
        elif mode == ExecutionMode.STATIC_MULTI_AGENT:
            return await self._run_static_multi_agent(task)
        elif mode == ExecutionMode.AE03_DYNAMIC:
            return await self._run_ae03_dynamic(task)
        else:
            raise ValueError(f"Unknown execution mode: {mode}")

    # ── Mode 1: Single Monolithic Prompt ───────────────────────────────

    async def _run_single_prompt(self, task: BenchmarkTask) -> BenchmarkResult:
        """
        Execute a task with a single LLM call containing the full
        task description and expected output schema.
        """
        start = time.time()

        system_prompt = (
            "You are an expert assistant. Complete the following task and "
            "return your answer as a valid JSON object matching the provided schema.\n\n"
            f"Expected output schema:\n{json.dumps(task.expected_output_schema, indent=2)}"
        )

        try:
            response = await self._router.call(
                prompt=task.goal_text,
                system_prompt=system_prompt,
                provider=self._default_provider,
                model=self._default_model,
                json_mode=True,
                temperature=0.3,
                max_tokens=4096,
            )

            latency_ms = (time.time() - start) * 1000
            cost = calculate_cost(
                response.provider,
                response.model,
                response.tokens_prompt,
                response.tokens_completion,
            )

            output = response.parsed_json or {}
            success = _validate_output(output, task.expected_output_schema)

            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.SINGLE_PROMPT,
                success=success,
                handoff_validity_pct=100.0,  # N/A for single prompt
                recovery_rate_pct=0.0,       # N/A for single prompt
                total_cost_usd=cost,
                latency_ms=latency_ms,
                total_tokens=response.total_tokens,
                output=output,
            )

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.SINGLE_PROMPT,
                success=False,
                latency_ms=latency_ms,
                error=str(e),
            )

    # ── Mode 2: Static Manual Multi-Agent Graph ────────────────────────

    async def _run_static_multi_agent(self, task: BenchmarkTask) -> BenchmarkResult:
        """
        Execute a task using a hardcoded 4-node DAG:
            researcher → executor → verifier → reporter
        """
        start = time.time()

        graph = _build_static_graph(task, self._default_provider)
        state = ExecutionState(graph_id=graph.graph_id)
        state.shared_memory.put("goal_text", task.goal_text)
        trace_events = []

        executor = AsyncDAGExecutor(
            graph=graph,
            node_handler=self._handler,
            state=state,
            trace_events=trace_events,
        )

        try:
            final_state = await executor.run()
            latency_ms = (time.time() - start) * 1000

            # Collect metrics
            all_results = final_state.get_all_results()
            total_tokens = sum(r.tokens_used for r in all_results.values())
            total_cost = sum(r.cost_usd for r in all_results.values())
            msgs = final_state.get_all_messages()
            valid_msgs = sum(1 for m in msgs if m.payload)
            handoff_pct = (valid_msgs / len(msgs) * 100) if msgs else 100.0
            retries = sum(1 for r in all_results.values() if r.retry_count > 0)
            failed = sum(1 for r in all_results.values() if r.status == ExecutionStatus.FAILED)
            recovery_pct = (retries / (retries + failed) * 100) if (retries + failed) > 0 else 100.0

            # Get final output from reporter node
            reporter_result = all_results.get("reporter")
            output = reporter_result.output if reporter_result else {}
            success = reporter_result is not None and reporter_result.status == ExecutionStatus.SUCCESS

            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.STATIC_MULTI_AGENT,
                success=success,
                handoff_validity_pct=round(handoff_pct, 1),
                recovery_rate_pct=round(recovery_pct, 1),
                total_cost_usd=total_cost,
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                output=output,
            )

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.STATIC_MULTI_AGENT,
                success=False,
                latency_ms=latency_ms,
                error=str(e),
            )

    # ── Mode 3: AE-03 Dynamic Generated Graph ─────────────────────────

    async def _run_ae03_dynamic(self, task: BenchmarkTask) -> BenchmarkResult:
        """
        Execute a task by first compiling a DAG via the Planner LLM,
        then executing the compiled graph through the standard engine.
        """
        start = time.time()

        compiler = GraphCompiler(
            provider_router=self._router,
            default_provider=self._default_provider,
            default_model=self._default_model,
        )

        try:
            # Phase 1: Compile
            compilation = await compiler.compile_goal(
                goal=task.goal_text,
                provider=self._default_provider,
                model=self._default_model,
                validate=True,
                lock=True,
            )

            # Phase 2: Execute
            state = ExecutionState(graph_id=compilation.main_graph.graph_id)
            state.shared_memory.put("goal_text", task.goal_text)
            trace_events = []

            executor = AsyncDAGExecutor(
                graph=compilation.main_graph,
                node_handler=self._handler,
                sub_graphs=compilation.sub_graphs,
                state=state,
                trace_events=trace_events,
            )

            final_state = await executor.run()
            latency_ms = (time.time() - start) * 1000

            # Collect metrics
            all_results = final_state.get_all_results()
            total_tokens = (
                sum(r.tokens_used for r in all_results.values())
                + compilation.compilation_tokens
            )
            total_cost = (
                sum(r.cost_usd for r in all_results.values())
                + compilation.compilation_cost_usd
            )

            msgs = final_state.get_all_messages()
            valid_msgs = sum(1 for m in msgs if m.payload)
            handoff_pct = (valid_msgs / len(msgs) * 100) if msgs else 100.0

            retries = sum(1 for r in all_results.values() if r.retry_count > 0)
            failed = sum(1 for r in all_results.values() if r.status == ExecutionStatus.FAILED)
            recovery_pct = (retries / (retries + failed) * 100) if (retries + failed) > 0 else 100.0

            # Get output from leaf nodes
            leaf_ids = compilation.main_graph.get_leaf_nodes()
            output = {}
            success = True
            for lid in leaf_ids:
                lr = all_results.get(lid)
                if lr:
                    output[lid] = lr.output
                    if lr.status != ExecutionStatus.SUCCESS:
                        success = False
                else:
                    success = False

            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.AE03_DYNAMIC,
                success=success,
                handoff_validity_pct=round(handoff_pct, 1),
                recovery_rate_pct=round(recovery_pct, 1),
                total_cost_usd=total_cost,
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                output=output,
            )

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.AE03_DYNAMIC,
                success=False,
                latency_ms=latency_ms,
                error=str(e),
            )


# ── Static Graph Builder ──────────────────────────────────────────────


def _build_static_graph(
    task: BenchmarkTask, provider: str
) -> ExecutionGraph:
    """
    Build a hardcoded 4-node DAG for the static multi-agent mode:

        researcher → executor → verifier → reporter

    Each node has a fixed role and system prompt tailored to the task.
    """
    schema_str = json.dumps(task.expected_output_schema, indent=2)

    nodes = {
        "researcher": AgentConfig(
            agent_id="researcher",
            role=AgentRole.RESEARCHER,
            system_prompt=(
                "You are a research analyst. Analyze the following task and "
                "produce a structured research brief with key findings, "
                "relevant data points, and a recommended approach.\n\n"
                f"Task category: {task.category}\n"
                f"Difficulty: {task.difficulty_tier.value}"
            ),
            model_provider=provider,
            allowed_tools=["web_search", "data_fetch"],
        ),
        "executor": AgentConfig(
            agent_id="executor",
            role=AgentRole.EXECUTOR,
            system_prompt=(
                "You are a task executor. Using the research brief provided, "
                "implement the solution. Return your output as valid JSON "
                f"matching this schema:\n{schema_str}"
            ),
            model_provider=provider,
            allowed_tools=["code_execute", "api_call"],
        ),
        "verifier": AgentConfig(
            agent_id="verifier",
            role=AgentRole.VERIFIER,
            system_prompt=(
                "You are a quality verifier. Review the executor's output "
                "and validate it against the expected schema. Check for "
                "completeness, correctness, and edge cases. Return a "
                "verification report with pass/fail status."
            ),
            model_provider=provider,
            allowed_tools=[],
        ),
        "reporter": AgentConfig(
            agent_id="reporter",
            role=AgentRole.REPORTER,
            system_prompt=(
                "You are a report generator. Compile the verified results "
                "into a final output JSON matching the expected schema. "
                "Include any corrections noted by the verifier.\n\n"
                f"Expected output schema:\n{schema_str}"
            ),
            model_provider=provider,
            allowed_tools=[],
        ),
    }

    edges = [
        ("researcher", "executor"),
        ("executor", "verifier"),
        ("verifier", "reporter"),
    ]

    graph = ExecutionGraph(
        graph_id=f"static-{task.task_id}-{uuid.uuid4().hex[:6]}",
        version="1.0.0",
        nodes=nodes,
        edges=edges,
        metadata={
            "task_id": task.task_id,
            "mode": "static_multi_agent",
            "goal": task.goal_text,
        },
    )
    graph.lock()
    return graph


# ── Output Validation ──────────────────────────────────────────────────


def _validate_output(
    output: Dict[str, Any],
    expected_schema: Dict[str, Any],
) -> bool:
    """
    Basic structural validation of output against expected schema.

    Checks that all required keys from the schema are present in the
    output. Does NOT perform full JSON Schema validation (to avoid
    adding a heavy dependency); uses a lightweight key-check approach.
    """
    if not expected_schema:
        return bool(output)  # Any non-empty output is valid

    required = expected_schema.get("required", [])
    properties = expected_schema.get("properties", {})

    if not required and not properties:
        return bool(output)

    # Check required keys are present
    for key in required:
        if key not in output:
            logger.debug("Missing required key '%s' in output", key)
            return False

    return True
