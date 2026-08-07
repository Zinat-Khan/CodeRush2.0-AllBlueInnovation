"""
AE-03 Evaluation Harness — 3-Mode Benchmark Runner (Directive V2).

Executes benchmark tasks in three modes and collects comparative metrics:

  1. **Single Monolithic Prompt** — One LLM call with the full task.
  2. **Static Manual Multi-Agent Graph** — Hardcoded template-based DAG.
  3. **AE-03 Dynamic Generated Graph** — TaskCompiler-generated DAG
     from the natural-language goal text via LangGraph WorkflowEngine.

Comparison metrics per Directive V2:
  - Success rate
  - Answer quality (LLM-judged)
  - Evidence coverage
  - Handoff correctness
  - Verification pass rate
  - Security violations
  - Token usage
  - Cost (USD)
  - Latency (ms)
  - Recovery rate
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.schemas.artifacts import BenchmarkResult, BenchmarkTask
from backend.schemas.contracts import AgentRole
from backend.observability.tracker import EventTracker
from backend.observability.tracer import CostTracker, AuditLog

logger = logging.getLogger(__name__)


# ── Execution Modes ──────────────────────────────────────────────────


class ExecutionMode:
    """Constants for the three benchmark execution modes."""
    SINGLE_PROMPT = "single_prompt"
    STATIC_MULTI_AGENT = "static_multi_agent"
    AE03_DYNAMIC = "ae03_dynamic"
    ALL = [SINGLE_PROMPT, STATIC_MULTI_AGENT, AE03_DYNAMIC]


# ── Benchmark Runner ─────────────────────────────────────────────────


class BenchmarkRunner:
    """
    3-mode evaluation harness.

    Runs each benchmark task through all three execution modes and
    collects metrics for comparative analysis.

    Usage::

        runner = BenchmarkRunner()
        results = await runner.run_all(tasks)
        comparison = runner.compare_results(results)
    """

    def __init__(self) -> None:
        self._event_tracker = EventTracker()
        self._cost_tracker = CostTracker()
        self._audit_log = AuditLog()

    # ── Mode 1: Single Monolithic Prompt ──────────────────────────────

    async def run_single_prompt(self, task: BenchmarkTask) -> BenchmarkResult:
        """
        Execute a task with a single LLM call (no orchestration).

        This is the baseline — one prompt, one response.
        """
        run_id = f"bench-sp-{uuid.uuid4().hex[:6]}"
        start = time.time()

        try:
            from backend.models.model_router import ModelRouter

            router = ModelRouter()
            prompt = (
                f"Complete the following task thoroughly:\n\n"
                f"Task: {task.goal_text}\n\n"
                f"Provide a comprehensive, well-structured response."
            )

            response_text, metadata = await router.ainvoke_text(
                prompt=prompt,
                system_prompt="You are a helpful, thorough assistant.",
            )

            latency = (time.time() - start) * 1000
            tokens = metadata.get("total_tokens", 0)
            cost = metadata.get("cost_usd", 0.0)

            self._cost_tracker.record(
                run_id, metadata.get("provider", ""), metadata.get("model", ""),
                "single", task.task_id, metadata.get("prompt_tokens", 0),
                metadata.get("completion_tokens", 0), cost, latency,
            )

            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.SINGLE_PROMPT,
                success=True,
                total_cost_usd=cost,
                latency_ms=latency,
                total_tokens=tokens,
                output={"response": response_text[:2000]},
                handoff_validity_pct=100.0,  # N/A for single prompt
                recovery_rate_pct=100.0,  # N/A
            )

        except Exception as e:
            latency = (time.time() - start) * 1000
            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.SINGLE_PROMPT,
                success=False,
                latency_ms=latency,
                error=str(e),
            )

    # ── Mode 2: Static Multi-Agent Graph ──────────────────────────────

    async def run_static_multi_agent(self, task: BenchmarkTask) -> BenchmarkResult:
        """
        Execute a task with a static template-based multi-agent graph.

        Uses the TaskCompiler's research_report template (fixed DAG).
        """
        run_id = f"bench-sm-{uuid.uuid4().hex[:6]}"
        start = time.time()

        try:
            from backend.graph.task_compiler import TaskCompiler
            from backend.models.model_router import ModelRouter

            compiler = TaskCompiler()
            router = ModelRouter()

            # Use static template
            graph = compiler.compile_from_template("research_report", task.goal_text)
            topo = compiler.topological_sort(graph)

            outputs: Dict[str, Any] = {}
            total_tokens = 0
            total_cost = 0.0
            handoff_valid = 0
            handoff_total = 0

            for t in topo:
                # Build context from dependencies
                context = "\n".join(
                    f"[{dep}]: {json.dumps(outputs.get(dep, {}))[:400]}"
                    for dep in t.dependencies
                )

                prompt = (
                    f"Goal: {task.goal_text}\n"
                    f"Your role: {t.agent_role.value}\n"
                    f"Task: {t.description}\n"
                )
                if context:
                    prompt += f"\nPrevious outputs:\n{context}"

                response, meta = await router.ainvoke_text(
                    prompt=prompt,
                    system_prompt=f"You are a {t.agent_role.value} agent.",
                )

                outputs[t.task_id] = {"response": response[:1000]}
                total_tokens += meta.get("total_tokens", 0)
                total_cost += meta.get("cost_usd", 0.0)

                # Validate handoff (check output is non-empty)
                handoff_total += 1
                if response and len(response) > 10:
                    handoff_valid += 1

                self._cost_tracker.record(
                    run_id, meta.get("provider", ""), meta.get("model", ""),
                    t.agent_role.value, t.task_id,
                    meta.get("prompt_tokens", 0), meta.get("completion_tokens", 0),
                    meta.get("cost_usd", 0.0), 0,
                )

            latency = (time.time() - start) * 1000
            handoff_pct = (handoff_valid / handoff_total * 100) if handoff_total > 0 else 0.0

            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.STATIC_MULTI_AGENT,
                success=True,
                total_cost_usd=total_cost,
                latency_ms=latency,
                total_tokens=total_tokens,
                output=outputs,
                handoff_validity_pct=round(handoff_pct, 1),
                recovery_rate_pct=100.0,
            )

        except Exception as e:
            latency = (time.time() - start) * 1000
            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.STATIC_MULTI_AGENT,
                success=False,
                latency_ms=latency,
                error=str(e),
            )

    # ── Mode 3: AE-03 Dynamic Generated Graph ────────────────────────

    async def run_ae03_dynamic(self, task: BenchmarkTask) -> BenchmarkResult:
        """
        Execute a task using the full LangGraph WorkflowEngine.

        This is the full AE-03 pipeline: TaskCompiler → WorkflowEngine.
        """
        run_id = f"bench-dyn-{uuid.uuid4().hex[:6]}"
        start = time.time()

        try:
            from backend.graph.workflow import WorkflowEngine

            engine = WorkflowEngine()
            result = await engine.execute(
                goal=task.goal_text,
                user_id="benchmark",
                workspace_id="benchmark_ws",
                run_id=run_id,
            )

            latency = (time.time() - start) * 1000
            metrics = result.get("metrics", {})
            total_tokens = metrics.get("total_tokens", 0)
            total_cost = metrics.get("total_cost_usd", 0.0)
            status = result.get("status", "unknown")

            # Calculate handoff validity
            tasks = result.get("tasks", [])
            agent_outputs = result.get("agent_outputs", {})
            handoff_valid = sum(
                1 for tid, out in agent_outputs.items()
                if out and not out.get("error")
            )
            handoff_total = len(tasks)
            handoff_pct = (handoff_valid / handoff_total * 100) if handoff_total > 0 else 0.0

            # Calculate recovery rate
            failed = metrics.get("nodes_failed", 0)
            succeeded = metrics.get("nodes_succeeded", 0)
            total_nodes = failed + succeeded
            recovery_pct = (succeeded / total_nodes * 100) if total_nodes > 0 else 100.0

            # Security violations
            security_violations = len(self._audit_log.get_violations(run_id))

            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.AE03_DYNAMIC,
                success=status == "success",
                total_cost_usd=total_cost,
                latency_ms=latency,
                total_tokens=total_tokens,
                output={
                    "agent_outputs": {
                        k: str(v)[:500] for k, v in agent_outputs.items()
                    },
                    "artifacts": result.get("artifacts", []),
                    "security_violations": security_violations,
                    "verification": result.get("verification_state", {}),
                },
                handoff_validity_pct=round(handoff_pct, 1),
                recovery_rate_pct=round(recovery_pct, 1),
            )

        except Exception as e:
            latency = (time.time() - start) * 1000
            return BenchmarkResult(
                task_id=task.task_id,
                mode=ExecutionMode.AE03_DYNAMIC,
                success=False,
                latency_ms=latency,
                error=str(e),
            )

    # ── Run All Modes ─────────────────────────────────────────────────

    async def run_task(
        self,
        task: BenchmarkTask,
        modes: Optional[List[str]] = None,
    ) -> List[BenchmarkResult]:
        """Run a single task across specified modes."""
        modes = modes or ExecutionMode.ALL
        results = []

        for mode in modes:
            logger.info("[Benchmark] Running task '%s' in mode '%s'", task.task_id, mode)
            if mode == ExecutionMode.SINGLE_PROMPT:
                result = await self.run_single_prompt(task)
            elif mode == ExecutionMode.STATIC_MULTI_AGENT:
                result = await self.run_static_multi_agent(task)
            elif mode == ExecutionMode.AE03_DYNAMIC:
                result = await self.run_ae03_dynamic(task)
            else:
                continue
            results.append(result)
            logger.info(
                "[Benchmark] Task '%s' mode '%s': success=%s, cost=$%.4f, latency=%.0fms",
                task.task_id, mode, result.success, result.total_cost_usd, result.latency_ms,
            )

        return results

    async def run_all(
        self,
        tasks: List[BenchmarkTask],
        modes: Optional[List[str]] = None,
    ) -> List[BenchmarkResult]:
        """Run all tasks across all modes sequentially."""
        all_results = []
        for task in tasks:
            results = await self.run_task(task, modes)
            all_results.extend(results)
        return all_results

    # ── Comparison ────────────────────────────────────────────────────

    def compare_results(self, results: List[BenchmarkResult]) -> Dict[str, Any]:
        """
        Compare results across execution modes.

        Returns comparative metrics per Directive V2:
          success_rate, avg_cost, avg_latency, avg_tokens,
          avg_handoff_validity, avg_recovery_rate
        """
        by_mode: Dict[str, List[BenchmarkResult]] = {}
        for r in results:
            by_mode.setdefault(r.mode, []).append(r)

        comparison = {}
        for mode, mode_results in by_mode.items():
            n = len(mode_results)
            successes = sum(1 for r in mode_results if r.success)
            comparison[mode] = {
                "total_tasks": n,
                "success_rate": round(successes / n * 100, 1) if n > 0 else 0.0,
                "avg_cost_usd": round(sum(r.total_cost_usd for r in mode_results) / n, 6) if n > 0 else 0.0,
                "total_cost_usd": round(sum(r.total_cost_usd for r in mode_results), 6),
                "avg_latency_ms": round(sum(r.latency_ms for r in mode_results) / n, 1) if n > 0 else 0.0,
                "avg_tokens": round(sum(r.total_tokens for r in mode_results) / n) if n > 0 else 0,
                "total_tokens": sum(r.total_tokens for r in mode_results),
                "avg_handoff_validity_pct": round(
                    sum(r.handoff_validity_pct for r in mode_results) / n, 1
                ) if n > 0 else 0.0,
                "avg_recovery_rate_pct": round(
                    sum(r.recovery_rate_pct for r in mode_results) / n, 1
                ) if n > 0 else 0.0,
            }

        return {
            "modes": comparison,
            "total_results": len(results),
            "total_tasks": len(set(r.task_id for r in results)),
        }

    def format_comparison_table(self, comparison: Dict[str, Any]) -> str:
        """Format comparison as a readable table string."""
        modes = comparison.get("modes", {})
        lines = [
            "| Metric                  | Single Prompt | Static Multi-Agent | AE-03 Dynamic |",
            "|:------------------------|:--------------|:-------------------|:--------------|",
        ]

        metrics = [
            ("Success Rate (%)", "success_rate"),
            ("Avg Cost ($)", "avg_cost_usd"),
            ("Avg Latency (ms)", "avg_latency_ms"),
            ("Avg Tokens", "avg_tokens"),
            ("Handoff Validity (%)", "avg_handoff_validity_pct"),
            ("Recovery Rate (%)", "avg_recovery_rate_pct"),
        ]

        for label, key in metrics:
            sp = modes.get("single_prompt", {}).get(key, "-")
            sm = modes.get("static_multi_agent", {}).get(key, "-")
            dy = modes.get("ae03_dynamic", {}).get(key, "-")
            lines.append(f"| {label:<23s} | {str(sp):>13s} | {str(sm):>18s} | {str(dy):>13s} |")

        return "\n".join(lines)
