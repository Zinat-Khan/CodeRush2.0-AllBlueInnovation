"""
AE-03 Benchmark Reporter — Marginal Value Comparison Report.

Aggregates BenchmarkResult data from all three execution modes into
a structured comparison report showing the marginal value of the
AE-03 orchestrator over simpler approaches.

Output formats:
  - Structured JSON
  - Markdown table
  - Per-task detail breakdown
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from backend.schemas.artifacts import BenchmarkResult, BenchmarkTask

logger = logging.getLogger(__name__)


# ── Mode-Level Aggregate ───────────────────────────────────────────────


class ModeAggregate:
    """Aggregated metrics for a single execution mode across all tasks."""

    def __init__(self, mode: str, results: List[BenchmarkResult]):
        self.mode = mode
        self.results = results
        self.total_tasks = len(results)
        self.succeeded = sum(1 for r in results if r.success)
        self.failed = self.total_tasks - self.succeeded

    @property
    def success_rate(self) -> float:
        return (self.succeeded / self.total_tasks * 100) if self.total_tasks else 0.0

    @property
    def avg_handoff_validity(self) -> float:
        vals = [r.handoff_validity_pct for r in self.results if r.success]
        return (sum(vals) / len(vals)) if vals else 0.0

    @property
    def avg_recovery_rate(self) -> float:
        vals = [r.recovery_rate_pct for r in self.results if r.success]
        return (sum(vals) / len(vals)) if vals else 0.0

    @property
    def total_cost(self) -> float:
        return sum(r.total_cost_usd for r in self.results)

    @property
    def avg_cost(self) -> float:
        return self.total_cost / self.total_tasks if self.total_tasks else 0.0

    @property
    def total_latency_ms(self) -> float:
        return sum(r.latency_ms for r in self.results)

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total_tasks if self.total_tasks else 0.0

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.results)

    @property
    def avg_tokens(self) -> float:
        return self.total_tokens / self.total_tasks if self.total_tasks else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "total_tasks": self.total_tasks,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "success_rate_pct": round(self.success_rate, 1),
            "avg_handoff_validity_pct": round(self.avg_handoff_validity, 1),
            "avg_recovery_rate_pct": round(self.avg_recovery_rate, 1),
            "total_cost_usd": round(self.total_cost, 6),
            "avg_cost_usd": round(self.avg_cost, 6),
            "total_latency_ms": round(self.total_latency_ms, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "total_tokens": self.total_tokens,
            "avg_tokens": round(self.avg_tokens, 0),
        }


# ── Marginal Value Report ─────────────────────────────────────────────


class BenchmarkReporter:
    """
    Generates marginal value comparison reports from benchmark results.

    Usage::

        reporter = BenchmarkReporter(results)
        json_report = reporter.to_json()
        md_report = reporter.to_markdown()
    """

    MODE_LABELS = {
        "single_prompt": "Single Prompt",
        "static_multi_agent": "Static Multi-Agent",
        "ae03_dynamic": "AE-03 Dynamic",
    }

    def __init__(
        self,
        results: List[BenchmarkResult],
        tasks: Optional[List[BenchmarkTask]] = None,
    ):
        self._results = results
        self._tasks = tasks or []
        self._task_map = {t.task_id: t for t in self._tasks}
        self._aggregates: Dict[str, ModeAggregate] = {}
        self._build_aggregates()

    def _build_aggregates(self) -> None:
        """Group results by mode and compute aggregates."""
        by_mode: Dict[str, List[BenchmarkResult]] = {}
        for r in self._results:
            by_mode.setdefault(r.mode, []).append(r)

        for mode, mode_results in by_mode.items():
            self._aggregates[mode] = ModeAggregate(mode, mode_results)

    # ── JSON Report ────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Return the full comparison report as a dictionary."""
        return {
            "benchmark_summary": {
                "total_results": len(self._results),
                "modes_evaluated": list(self._aggregates.keys()),
                "total_tasks": len(set(r.task_id for r in self._results)),
            },
            "mode_comparison": {
                mode: agg.to_dict()
                for mode, agg in self._aggregates.items()
            },
            "marginal_value": self._compute_marginal_value(),
            "per_task_breakdown": self._per_task_breakdown(),
        }

    def to_json(self, indent: int = 2) -> str:
        """Return the full report as formatted JSON."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    # ── Markdown Report ────────────────────────────────────────────────

    def to_markdown(self) -> str:
        """
        Generate a markdown-formatted comparison report.

        Includes:
          - Summary table comparing all modes
          - Marginal value analysis
          - Per-task breakdown
        """
        lines = [
            "# AE-03 Benchmark Evaluation Report",
            "",
            "## Mode Comparison Summary",
            "",
        ]

        # Summary table
        lines.append("| Metric | " + " | ".join(
            self.MODE_LABELS.get(m, m) for m in self._aggregates
        ) + " |")
        lines.append("| :--- | " + " | ".join(
            "---:" for _ in self._aggregates
        ) + " |")

        metrics = [
            ("Task Success Rate (%)", "success_rate_pct"),
            ("Handoff Validity (%)", "avg_handoff_validity_pct"),
            ("Recovery Rate (%)", "avg_recovery_rate_pct"),
            ("Total Cost (USD)", "total_cost_usd"),
            ("Avg Cost/Task (USD)", "avg_cost_usd"),
            ("Avg Latency (ms)", "avg_latency_ms"),
            ("Total Tokens", "total_tokens"),
        ]

        for label, key in metrics:
            row = f"| {label} |"
            for mode, agg in self._aggregates.items():
                val = agg.to_dict()[key]
                if "cost" in key.lower() or "usd" in key.lower():
                    row += f" ${val:.6f} |"
                elif isinstance(val, float):
                    row += f" {val:.1f} |"
                else:
                    row += f" {val} |"
            lines.append(row)

        # Marginal value section
        mv = self._compute_marginal_value()
        if mv:
            lines.extend([
                "",
                "## Marginal Value Analysis",
                "",
                "Comparing AE-03 Dynamic mode against baselines:",
                "",
            ])

            for comparison in mv:
                lines.extend([
                    f"### vs {self.MODE_LABELS.get(comparison['baseline'], comparison['baseline'])}",
                    "",
                    f"- **Success Rate Delta**: {comparison['success_rate_delta']:+.1f}%",
                    f"- **Cost Delta**: ${comparison['cost_delta']:+.6f}",
                    f"- **Latency Delta**: {comparison['latency_delta']:+.1f}ms",
                    f"- **Token Delta**: {comparison['token_delta']:+d}",
                    "",
                ])

        # Per-task breakdown
        lines.extend([
            "## Per-Task Breakdown",
            "",
        ])

        task_ids = sorted(set(r.task_id for r in self._results))
        for task_id in task_ids:
            task = self._task_map.get(task_id)
            tier = task.difficulty_tier.value if task else "unknown"
            category = task.category if task else "unknown"

            lines.append(f"### {task_id} ({tier} / {category})")
            lines.append("")
            lines.append("| Mode | Success | Cost | Latency | Tokens | Error |")
            lines.append("| :--- | :---: | ---: | ---: | ---: | :--- |")

            task_results = [r for r in self._results if r.task_id == task_id]
            for r in task_results:
                mode_label = self.MODE_LABELS.get(r.mode, r.mode)
                success_icon = "PASS" if r.success else "FAIL"
                error_str = (r.error[:50] + "...") if r.error and len(r.error) > 50 else (r.error or "-")
                lines.append(
                    f"| {mode_label} | {success_icon} | "
                    f"${r.total_cost_usd:.6f} | {r.latency_ms:.0f}ms | "
                    f"{r.total_tokens} | {error_str} |"
                )
            lines.append("")

        return "\n".join(lines)

    # ── Marginal Value Computation ─────────────────────────────────────

    def _compute_marginal_value(self) -> List[Dict[str, Any]]:
        """
        Compute the marginal value of AE-03 Dynamic vs each baseline mode.

        Returns a list of comparison dicts, one per baseline.
        """
        ae03 = self._aggregates.get("ae03_dynamic")
        if not ae03:
            return []

        comparisons = []
        for mode, agg in self._aggregates.items():
            if mode == "ae03_dynamic":
                continue
            comparisons.append({
                "baseline": mode,
                "ae03_mode": "ae03_dynamic",
                "success_rate_delta": round(ae03.success_rate - agg.success_rate, 1),
                "cost_delta": round(ae03.total_cost - agg.total_cost, 6),
                "latency_delta": round(ae03.avg_latency_ms - agg.avg_latency_ms, 1),
                "token_delta": ae03.total_tokens - agg.total_tokens,
                "handoff_validity_delta": round(
                    ae03.avg_handoff_validity - agg.avg_handoff_validity, 1
                ),
                "recovery_rate_delta": round(
                    ae03.avg_recovery_rate - agg.avg_recovery_rate, 1
                ),
            })

        return comparisons

    # ── Per-Task Breakdown ─────────────────────────────────────────────

    def _per_task_breakdown(self) -> List[Dict[str, Any]]:
        """Return per-task results grouped by task_id."""
        by_task: Dict[str, List[BenchmarkResult]] = {}
        for r in self._results:
            by_task.setdefault(r.task_id, []).append(r)

        breakdown = []
        for task_id, task_results in sorted(by_task.items()):
            task = self._task_map.get(task_id)
            entry = {
                "task_id": task_id,
                "category": task.category if task else "unknown",
                "difficulty": task.difficulty_tier.value if task else "unknown",
                "results": [r.model_dump() for r in task_results],
            }
            breakdown.append(entry)

        return breakdown
