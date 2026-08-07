"""Module 8 verification script -- exercises all evaluation components."""

import sys
import os
import json

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.evaluation.tasks import (
    load_benchmark_tasks,
    get_task_summary,
    get_tasks_by_difficulty,
    get_tasks_by_category,
)
from backend.evaluation.reporter import BenchmarkReporter, ModeAggregate
from backend.evaluation.benchmark import ExecutionMode
from backend.schemas.artifacts import BenchmarkResult, BenchmarkTask, DifficultyTier


def test_task_loading():
    """Verify tasks load correctly from DATA_PROVENANCE.md."""
    print("=== Task Loading Tests ===")

    tasks = load_benchmark_tasks()
    assert len(tasks) == 6, f"Expected 6 tasks, got {len(tasks)}"
    print(f"  Loaded {len(tasks)} tasks [OK]")

    # Verify task structure
    for t in tasks:
        assert t.task_id, f"Task missing task_id: {t}"
        assert t.source_dataset, f"Task {t.task_id} missing source_dataset"
        assert t.goal_text, f"Task {t.task_id} missing goal_text"
        assert t.difficulty_tier in DifficultyTier, f"Invalid tier: {t.difficulty_tier}"
    print(f"  All tasks structurally valid [OK]")

    # Verify specific tasks
    t1 = next(t for t in tasks if t.task_id == "TASK-001")
    assert t1.source_dataset == "AgentBench"
    assert t1.difficulty_tier == DifficultyTier.EASY
    assert t1.category == "code_gen"
    assert "function_name" in t1.expected_output_schema.get("properties", {})
    print(f"  TASK-001 details verified [OK]")

    t5 = next(t for t in tasks if t.task_id == "TASK-005")
    assert t5.source_dataset == "SWE-bench-Lite"
    assert t5.difficulty_tier == DifficultyTier.HARD
    assert t5.category == "multi_step_reasoning"
    print(f"  TASK-005 details verified [OK]")

    # Task with reference_answer
    assert t1.reference_answer is not None
    print(f"  TASK-001 has reference_answer: '{t1.reference_answer}' [OK]")

    # Task without reference_answer
    t2 = next(t for t in tasks if t.task_id == "TASK-002")
    assert t2.reference_answer is None
    print(f"  TASK-002 reference_answer is None [OK]")

    print("  All task loading tests passed!\n")


def test_task_filtering():
    """Verify task filtering by difficulty and category."""
    print("=== Task Filtering Tests ===")

    tasks = load_benchmark_tasks()

    easy = get_tasks_by_difficulty(tasks, DifficultyTier.EASY)
    assert len(easy) == 2, f"Expected 2 easy tasks, got {len(easy)}"
    print(f"  Easy tasks: {len(easy)} [OK]")

    medium = get_tasks_by_difficulty(tasks, DifficultyTier.MEDIUM)
    assert len(medium) == 2, f"Expected 2 medium tasks, got {len(medium)}"
    print(f"  Medium tasks: {len(medium)} [OK]")

    hard = get_tasks_by_difficulty(tasks, DifficultyTier.HARD)
    assert len(hard) == 2, f"Expected 2 hard tasks, got {len(hard)}"
    print(f"  Hard tasks: {len(hard)} [OK]")

    code_gen = get_tasks_by_category(tasks, "code_gen")
    assert len(code_gen) == 2, f"Expected 2 code_gen tasks, got {len(code_gen)}"
    print(f"  code_gen tasks: {len(code_gen)} [OK]")

    api_tasks = get_tasks_by_category(tasks, "api_integration")
    assert len(api_tasks) == 1, f"Expected 1 api_integration task, got {len(api_tasks)}"
    print(f"  api_integration tasks: {len(api_tasks)} [OK]")

    print("  All filtering tests passed!\n")


def test_task_summary():
    """Verify task summary generation."""
    print("=== Task Summary Tests ===")

    tasks = load_benchmark_tasks()
    summary = get_task_summary(tasks)

    assert summary["total_tasks"] == 6
    assert summary["by_difficulty"]["easy"] == 2
    assert summary["by_difficulty"]["medium"] == 2
    assert summary["by_difficulty"]["hard"] == 2
    assert "AgentBench" in summary["by_source"]
    assert "SWE-bench-Lite" in summary["by_source"]
    print(f"  Summary: {json.dumps(summary, indent=2)}")
    print("  All summary tests passed!\n")


def test_execution_modes():
    """Verify execution mode constants."""
    print("=== Execution Mode Tests ===")

    assert ExecutionMode.SINGLE_PROMPT == "single_prompt"
    assert ExecutionMode.STATIC_MULTI_AGENT == "static_multi_agent"
    assert ExecutionMode.AE03_DYNAMIC == "ae03_dynamic"
    assert len(ExecutionMode.ALL) == 3
    print(f"  All 3 modes defined [OK]")
    print("  All execution mode tests passed!\n")


def test_reporter():
    """Verify reporter aggregation and output formatting."""
    print("=== Reporter Tests ===")

    # Create mock results simulating all 3 modes on 2 tasks
    results = [
        # Task-001 results
        BenchmarkResult(
            task_id="TASK-001", mode="single_prompt",
            success=True, handoff_validity_pct=100.0,
            total_cost_usd=0.005, latency_ms=1200, total_tokens=600,
            output={"function_name": "top_frequent", "code": "..."},
        ),
        BenchmarkResult(
            task_id="TASK-001", mode="static_multi_agent",
            success=True, handoff_validity_pct=92.0, recovery_rate_pct=100.0,
            total_cost_usd=0.012, latency_ms=3400, total_tokens=1800,
            output={"function_name": "top_frequent", "code": "..."},
        ),
        BenchmarkResult(
            task_id="TASK-001", mode="ae03_dynamic",
            success=True, handoff_validity_pct=95.5, recovery_rate_pct=100.0,
            total_cost_usd=0.018, latency_ms=5200, total_tokens=2400,
            output={"function_name": "top_frequent", "code": "..."},
        ),
        # Task-005 results
        BenchmarkResult(
            task_id="TASK-005", mode="single_prompt",
            success=False, total_cost_usd=0.008, latency_ms=2000,
            total_tokens=900, error="Output missing required keys",
        ),
        BenchmarkResult(
            task_id="TASK-005", mode="static_multi_agent",
            success=False, handoff_validity_pct=85.0, recovery_rate_pct=50.0,
            total_cost_usd=0.015, latency_ms=4100, total_tokens=2100,
            error="Verifier rejected output",
        ),
        BenchmarkResult(
            task_id="TASK-005", mode="ae03_dynamic",
            success=True, handoff_validity_pct=98.0, recovery_rate_pct=100.0,
            total_cost_usd=0.025, latency_ms=7800, total_tokens=3500,
            output={"cycles_found": ["auth<->db"], "refactoring_plan": "..."},
        ),
    ]

    tasks = load_benchmark_tasks()
    reporter = BenchmarkReporter(results, tasks)

    # JSON report
    report_dict = reporter.to_dict()
    assert "benchmark_summary" in report_dict
    assert "mode_comparison" in report_dict
    assert "marginal_value" in report_dict
    assert "per_task_breakdown" in report_dict
    assert report_dict["benchmark_summary"]["total_results"] == 6
    assert report_dict["benchmark_summary"]["total_tasks"] == 2
    print(f"  Report dict has all sections [OK]")

    # Mode comparison
    mc = report_dict["mode_comparison"]
    assert "single_prompt" in mc
    assert "static_multi_agent" in mc
    assert "ae03_dynamic" in mc

    # AE-03 should have 100% success (2/2 tasks)
    ae03 = mc["ae03_dynamic"]
    assert ae03["success_rate_pct"] == 100.0
    print(f"  AE-03 success rate: {ae03['success_rate_pct']}% [OK]")

    # Single prompt should have 50% success (1/2)
    sp = mc["single_prompt"]
    assert sp["success_rate_pct"] == 50.0
    print(f"  Single prompt success rate: {sp['success_rate_pct']}% [OK]")

    # Marginal value
    mv = report_dict["marginal_value"]
    assert len(mv) == 2  # vs single_prompt and vs static_multi_agent
    for comp in mv:
        assert "success_rate_delta" in comp
        assert "cost_delta" in comp
    print(f"  Marginal value comparisons: {len(mv)} [OK]")

    # JSON export
    json_str = reporter.to_json()
    parsed = json.loads(json_str)
    assert parsed["benchmark_summary"]["total_results"] == 6
    print(f"  JSON export: {len(json_str)} chars [OK]")

    # Markdown export
    md = reporter.to_markdown()
    assert "# AE-03 Benchmark Evaluation Report" in md
    assert "Mode Comparison Summary" in md
    assert "Marginal Value Analysis" in md
    assert "Per-Task Breakdown" in md
    assert "TASK-001" in md
    assert "TASK-005" in md
    print(f"  Markdown export: {len(md)} chars [OK]")

    # Print a sample of the markdown
    print("\n  --- Sample Markdown Output ---")
    for line in md.split("\n")[:25]:
        print(f"  {line}")
    print("  --- End Sample ---\n")

    print("  All reporter tests passed!\n")


def test_mode_aggregate():
    """Verify ModeAggregate calculations."""
    print("=== ModeAggregate Tests ===")

    results = [
        BenchmarkResult(
            task_id="T1", mode="test", success=True,
            handoff_validity_pct=90.0, recovery_rate_pct=80.0,
            total_cost_usd=0.01, latency_ms=1000, total_tokens=500,
        ),
        BenchmarkResult(
            task_id="T2", mode="test", success=True,
            handoff_validity_pct=95.0, recovery_rate_pct=100.0,
            total_cost_usd=0.02, latency_ms=2000, total_tokens=800,
        ),
        BenchmarkResult(
            task_id="T3", mode="test", success=False,
            total_cost_usd=0.005, latency_ms=500, total_tokens=200,
            error="Failed",
        ),
    ]

    agg = ModeAggregate("test", results)
    assert agg.total_tasks == 3
    assert agg.succeeded == 2
    assert agg.failed == 1
    assert round(agg.success_rate, 1) == 66.7
    assert agg.total_cost == 0.035
    assert agg.total_tokens == 1500
    print(f"  Aggregate: {agg.to_dict()}")
    print("  All aggregate tests passed!\n")


def main():
    print("=" * 60)
    print("MODULE 8: EVALUATION HARNESS VERIFICATION")
    print("=" * 60)
    print()

    test_task_loading()
    test_task_filtering()
    test_task_summary()
    test_execution_modes()
    test_mode_aggregate()
    test_reporter()

    print("=" * 60)
    print("ALL MODULE 8 TESTS PASSED [OK]")
    print("=" * 60)


if __name__ == "__main__":
    main()
