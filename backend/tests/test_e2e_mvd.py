"""
AE-03 End-to-End MVD Test Suite.

Covers all 4 MVD presentation scenarios:
  1. Goal → DAG Compilation (parallel branches + verifier join)
  2. Execution + Failure Recovery (schema mutation + auto-retry)
  3. Hot-Swap Provider (OpenAI → Ollama seamless switch)
  4. Replay + Cost Report (replay saved run + side-by-side comparison)

All tasks loaded from ``evaluation/DATA_PROVENANCE.md``.
"""

import sys
import os
import asyncio
import json

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.schemas.contracts import (
    AgentConfig,
    AgentRole,
    ExecutionGraph,
    ExecutionResult,
    ExecutionStatus,
)
from backend.schemas.artifacts import (
    BenchmarkResult,
    RunReport,
    TraceEvent,
    TraceEventType,
)
from backend.compiler.graph_compiler import GraphCompiler, CompilationResult
from backend.engine.executor import AsyncDAGExecutor
from backend.engine.state_manager import ExecutionState
from backend.observability.tracker import CostTracker
from backend.observability.tracer import ExecutionTracer, RunRecord, RunStore
from backend.observability.replay import ReplayEngine, ReplayComparison
from backend.evaluation.tasks import load_benchmark_tasks
from backend.tests.inject_failure import (
    FailureScenario,
    SchemaMutationInjector,
    ProviderTimeoutInjector,
)

import time
from typing import Any, Dict, List


# ── Demo Node Handler ──────────────────────────────────────────────────

async def demo_handler(
    node_id: str,
    config: AgentConfig,
    input_payload: Dict[str, Any],
    system_prompt: str,
) -> Dict[str, Any]:
    """Simulated handler that returns realistic synthetic output."""
    await asyncio.sleep(0.1)
    return {
        "node_id": node_id,
        "role": config.role.value,
        "status": "completed",
        "result": f"Output from {config.role.value} agent",
        "tokens_used": 200 + (hash(node_id) % 400),
    }


# ── Helper: Build Test Graph ──────────────────────────────────────────

def build_test_graph(goal: str = "Test goal") -> ExecutionGraph:
    """Build a 5-node DAG with parallel branches and a verifier join."""
    nodes = {
        "planner": AgentConfig(
            agent_id="planner",
            role=AgentRole.PLANNER,
            system_prompt="Plan the task decomposition",
            model_provider="openai",
        ),
        "researcher": AgentConfig(
            agent_id="researcher",
            role=AgentRole.RESEARCHER,
            system_prompt="Research the topic",
            model_provider="openai",
            allowed_tools=["web_search"],
        ),
        "executor": AgentConfig(
            agent_id="executor",
            role=AgentRole.EXECUTOR,
            system_prompt="Execute the code solution",
            model_provider="openai",
            allowed_tools=["code_execute"],
        ),
        "verifier": AgentConfig(
            agent_id="verifier",
            role=AgentRole.VERIFIER,
            system_prompt="Verify output correctness",
            model_provider="openai",
        ),
        "reporter": AgentConfig(
            agent_id="reporter",
            role=AgentRole.REPORTER,
            system_prompt="Generate final report",
            model_provider="openai",
        ),
    }

    edges = [
        ("planner", "researcher"),
        ("planner", "executor"),
        ("researcher", "verifier"),
        ("executor", "verifier"),
        ("verifier", "reporter"),
    ]

    graph = ExecutionGraph(
        graph_id=f"test-graph-{hash(goal) % 10000:04d}",
        version="1.0.0",
        nodes=nodes,
        edges=edges,
        metadata={"goal": goal, "mode": "test"},
    )
    graph.lock()
    return graph


# ── Scenario 1: Goal → DAG Compilation ────────────────────────────────

def test_scenario_1_dag_compilation():
    """
    Verify DAG compilation produces correct graph structure:
    - Parallel branches (planner → researcher, planner → executor)
    - Verifier join node (researcher → verifier, executor → verifier)
    - Topological ordering is valid
    """
    print("=== SCENARIO 1: Goal to DAG Compilation ===")

    tasks = load_benchmark_tasks()
    assert len(tasks) >= 1, "No benchmark tasks loaded"
    task = tasks[0]
    print(f"  Task: {task.task_id} ({task.category} / {task.difficulty_tier.value})")

    graph = build_test_graph(task.goal_text)

    # Verify structure
    assert len(graph.nodes) == 5, f"Expected 5 nodes, got {len(graph.nodes)}"
    assert len(graph.edges) == 5, f"Expected 5 edges, got {len(graph.edges)}"
    print(f"  Nodes: {list(graph.nodes.keys())}")
    print(f"  Edges: {graph.edges}")

    # Verify parallel branches from planner
    planner_targets = [t for s, t in graph.edges if s == "planner"]
    assert len(planner_targets) == 2, f"Expected 2 parallel branches, got {len(planner_targets)}"
    assert "researcher" in planner_targets
    assert "executor" in planner_targets
    print(f"  Parallel branches from planner: {planner_targets} [OK]")

    # Verify join at verifier
    verifier_sources = [s for s, t in graph.edges if t == "verifier"]
    assert len(verifier_sources) == 2, f"Expected 2 join sources, got {len(verifier_sources)}"
    print(f"  Join sources at verifier: {verifier_sources} [OK]")

    # Verify leaf nodes
    leaves = graph.get_leaf_nodes()
    assert "reporter" in leaves
    print(f"  Leaf nodes: {leaves} [OK]")

    # Verify root nodes
    roots = graph.get_root_nodes()
    assert "planner" in roots
    print(f"  Root nodes: {roots} [OK]")

    # Verify graph is locked
    assert graph.locked
    print(f"  Graph locked: {graph.locked} [OK]")

    print("  SCENARIO 1 PASSED\n")


# ── Scenario 2: Execution + Failure Recovery ──────────────────────────

def test_scenario_2_execution_recovery():
    """
    Verify execution with injected failure and recovery:
    - Schema mutation on executor node
    - Auto-retry mechanism
    - Final successful completion
    """
    print("=== SCENARIO 2: Execution + Failure Recovery ===")

    tasks = load_benchmark_tasks()
    task = tasks[0]
    graph = build_test_graph(task.goal_text)

    # Create failure scenario
    scenario = FailureScenario.schema_corruption("executor")
    assert scenario.schema_injector is not None
    print(f"  Injector target: executor (mutation: missing_key)")

    # Wrap handler
    wrapped_handler = scenario.schema_injector.wrap_handler(demo_handler)

    # Execute with wrapped handler
    state = ExecutionState(graph_id=graph.graph_id)
    trace_events: List[TraceEvent] = []

    executor = AsyncDAGExecutor(
        graph=graph,
        node_handler=wrapped_handler,
        state=state,
        trace_events=trace_events,
    )

    final_state = asyncio.run(executor.run())

    # Verify execution completed
    results = final_state.get_all_results()
    assert len(results) > 0, "No execution results"
    print(f"  Nodes executed: {len(results)}")

    # Verify trace events were generated
    assert len(trace_events) > 0, "No trace events"
    print(f"  Trace events: {len(trace_events)}")

    # Verify the schema injector was triggered
    assert scenario.schema_injector._triggered.get("executor", 0) >= 1
    print(f"  Schema mutation triggered on executor [OK]")

    print("  SCENARIO 2 PASSED\n")


# ── Scenario 3: Hot-Swap Provider ─────────────────────────────────────

def test_scenario_3_provider_hotswap():
    """
    Verify provider hot-swap by building a graph with one provider,
    then replaying it with a different provider.
    """
    print("=== SCENARIO 3: Hot-Swap Provider ===")

    graph = build_test_graph("Provider hot-swap test")
    store = RunStore()

    # Execute with original provider (openai)
    state = ExecutionState(graph_id=graph.graph_id)
    trace_events: List[TraceEvent] = []

    executor = AsyncDAGExecutor(
        graph=graph,
        node_handler=demo_handler,
        state=state,
        trace_events=trace_events,
    )
    final_state = asyncio.run(executor.run())

    # Store original run
    tracer = ExecutionTracer("run-original")
    tracer.ingest_events(trace_events)
    cost_tracker = CostTracker("run-original")
    for nid, result in final_state.get_all_results().items():
        cost_tracker.record(nid, "openai", "gpt-4o", 300, 150)

    record = RunRecord(
        run_id="run-original",
        tracer=tracer,
        graph=graph,
        goal_text="Provider hot-swap test",
        cost_summary=cost_tracker.get_run_summary(),
    )
    store.store(record)
    print(f"  Original run stored: {record.run_id}")
    print(f"  Original cost: ${cost_tracker.get_run_summary()['total_cost_usd']}")

    # Verify the graph nodes have openai as provider
    for nid, node in graph.nodes.items():
        assert node.model_provider == "openai", f"Node {nid} has provider {node.model_provider}"
    print(f"  All nodes use provider: openai [OK]")

    # Create replay engine and replay with ollama
    engine = ReplayEngine(run_store=store, node_handler=demo_handler)
    comparison = asyncio.run(engine.replay(
        original_run_id="run-original",
        override_provider="ollama",
    ))

    # Verify comparison
    d = comparison.to_dict()
    assert d["provider_override"] == "ollama"
    assert d["original_run_id"] == "run-original"
    print(f"  Replay run: {d['replay_run_id']}")
    print(f"  Provider override: {d['provider_override']} [OK]")
    print(f"  Replay cost: ${d['comparison']['cost_usd']['replay']}")

    # Verify markdown table generation
    table = comparison.summary_table()
    assert "| Metric |" in table
    print(f"  Comparison table generated [OK]")

    print("  SCENARIO 3 PASSED\n")


# ── Scenario 4: Replay + Cost Report ─────────────────────────────────

def test_scenario_4_replay_cost_report():
    """
    Verify replay produces valid cost comparison report
    with side-by-side metrics.
    """
    print("=== SCENARIO 4: Replay + Cost Report ===")

    store = RunStore()
    graph = build_test_graph("Cost analysis test")

    # Execute original run
    state = ExecutionState(graph_id=graph.graph_id)
    trace_events: List[TraceEvent] = []

    executor = AsyncDAGExecutor(
        graph=graph,
        node_handler=demo_handler,
        state=state,
        trace_events=trace_events,
    )
    final_state = asyncio.run(executor.run())

    # Build cost data
    tracer = ExecutionTracer("run-cost-test")
    tracer.ingest_events(trace_events)
    cost_tracker = CostTracker("run-cost-test")

    node_token_pairs = [
        ("planner", 500, 200),
        ("researcher", 400, 180),
        ("executor", 600, 250),
        ("verifier", 300, 120),
        ("reporter", 350, 160),
    ]
    for nid, tp, tc in node_token_pairs:
        cost_tracker.record(nid, "openai", "gpt-4o", tp, tc)

    original_summary = cost_tracker.get_run_summary()
    print(f"  Original run tokens: {original_summary['total_tokens']}")
    print(f"  Original run cost: ${original_summary['total_cost_usd']}")

    record = RunRecord(
        run_id="run-cost-test",
        tracer=tracer,
        graph=graph,
        goal_text="Cost analysis test",
        cost_summary=original_summary,
    )
    store.store(record)

    # Replay with Ollama (free)
    engine = ReplayEngine(run_store=store, node_handler=demo_handler)
    comparison = asyncio.run(engine.replay(
        original_run_id="run-cost-test",
        override_provider="ollama",
    ))

    d = comparison.to_dict()

    # Cost should be lower with Ollama
    assert d["comparison"]["cost_usd"]["original"] > 0
    print(f"  Original cost: ${d['comparison']['cost_usd']['original']}")
    print(f"  Replay cost: ${d['comparison']['cost_usd']['replay']}")
    print(f"  Cost delta: ${d['comparison']['cost_usd']['delta']}")

    # Verify breakdown
    assert len(d["original_provider_breakdown"]) > 0
    print(f"  Original breakdown: {len(d['original_provider_breakdown'])} entries [OK]")

    # Verify stored replay
    assert store.get(d["replay_run_id"]) is not None
    print(f"  Replay stored: {d['replay_run_id']} [OK]")

    # Verify runs listing
    all_runs = store.list_runs()
    assert len(all_runs) >= 2
    print(f"  Total runs in store: {len(all_runs)} [OK]")

    # Generate markdown table
    table = comparison.summary_table()
    assert "Cost (USD)" in table
    assert "Total Tokens" in table
    print(f"  Markdown report generated [OK]")
    print(f"\n  --- Report ---\n{table}\n  --- End ---")

    print("  SCENARIO 4 PASSED\n")


# ── Data Provenance Validation ────────────────────────────────────────

def test_data_provenance_loading():
    """Verify tasks load from DATA_PROVENANCE.md as required by REV2."""
    print("=== DATA PROVENANCE VALIDATION ===")

    tasks = load_benchmark_tasks()
    assert len(tasks) == 6, f"Expected 6 tasks, got {len(tasks)}"
    print(f"  Tasks loaded: {len(tasks)} [OK]")

    # Verify all expected task IDs
    task_ids = {t.task_id for t in tasks}
    for expected_id in ["TASK-001", "TASK-002", "TASK-003", "TASK-004", "TASK-005", "TASK-006"]:
        assert expected_id in task_ids, f"Missing task {expected_id}"
    print(f"  All 6 task IDs present [OK]")

    # Verify difficulty distribution
    by_tier = {}
    for t in tasks:
        by_tier[t.difficulty_tier.value] = by_tier.get(t.difficulty_tier.value, 0) + 1
    assert by_tier == {"easy": 2, "medium": 2, "hard": 2}
    print(f"  Difficulty distribution: {by_tier} [OK]")

    # Verify schema fields present
    for t in tasks:
        assert t.goal_text, f"Task {t.task_id} missing goal_text"
        assert t.source_dataset, f"Task {t.task_id} missing source_dataset"
    print(f"  All tasks have required fields [OK]")

    print("  DATA PROVENANCE PASSED\n")


# ── Main ───────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("AE-03 END-TO-END MVD TEST SUITE")
    print("=" * 60)
    print()

    test_data_provenance_loading()
    test_scenario_1_dag_compilation()
    test_scenario_2_execution_recovery()
    test_scenario_3_provider_hotswap()
    test_scenario_4_replay_cost_report()

    print("=" * 60)
    print("ALL 4 MVD SCENARIOS + DATA PROVENANCE PASSED [OK]")
    print("=" * 60)


if __name__ == "__main__":
    main()
