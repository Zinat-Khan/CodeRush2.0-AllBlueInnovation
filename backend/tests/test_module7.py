"""Module 7 verification script — exercises all observability components."""

import sys
import os

# Ensure we can import from the project root (c:\hack)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.observability.tracker import CostTracker, calculate_cost, PROVIDER_PRICING
from backend.observability.tracer import ExecutionTracer, RunRecord, RunStore
from backend.observability.replay import ReplayComparison, ReplayEngine
from backend.schemas.artifacts import TraceEventType
from backend.schemas.contracts import (
    AgentConfig,
    AgentRole,
    ExecutionGraph,
    ExecutionResult,
    ExecutionStatus,
)


def test_cost_calculation():
    """Verify pricing table correctness across all providers."""
    print("=== Cost Calculation Tests ===")

    # OpenAI gpt-4o: $2.50/1M input, $10.00/1M output
    c1 = calculate_cost("openai", "gpt-4o", 1_000_000, 1_000_000)
    assert c1 == 12.5, f"Expected 12.5, got {c1}"
    print(f"  OpenAI gpt-4o 1M/1M: ${c1} ✓")

    # OpenAI gpt-4o-mini: $0.15/1M input, $0.60/1M output
    c2 = calculate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert c2 == 0.75, f"Expected 0.75, got {c2}"
    print(f"  OpenAI gpt-4o-mini 1M/1M: ${c2} ✓")

    # Gemini 1.5 Pro: $1.25/1M input, $5.00/1M output
    c3 = calculate_cost("gemini", "gemini-1.5-pro", 1_000_000, 1_000_000)
    assert c3 == 6.25, f"Expected 6.25, got {c3}"
    print(f"  Gemini 1.5 Pro 1M/1M: ${c3} ✓")

    # Ollama: $0.00 (local)
    c4 = calculate_cost("ollama", "llama3", 1_000_000, 1_000_000)
    assert c4 == 0.0, f"Expected 0.0, got {c4}"
    print(f"  Ollama llama3 1M/1M: ${c4} ✓")

    # Unknown provider: $0.00
    c5 = calculate_cost("unknown", "model", 500, 200)
    assert c5 == 0.0, f"Expected 0.0, got {c5}"
    print(f"  Unknown provider: ${c5} ✓")

    print("  All cost calculation tests passed!\n")


def test_cost_tracker():
    """Verify CostTracker aggregation across multiple nodes/providers."""
    print("=== CostTracker Tests ===")

    t = CostTracker("multi-node-run")
    t.record("researcher", "openai", "gpt-4o", 800, 300)
    t.record("executor", "gemini", "gemini-1.5-pro", 600, 400)
    t.record("verifier", "openai", "gpt-4o-mini", 400, 150)
    t.record("reporter", "ollama", "llama3", 500, 250)

    assert len(t) == 4, f"Expected 4 entries, got {len(t)}"
    print(f"  Total entries: {len(t)} ✓")

    # Per-provider breakdown
    bd = t.get_provider_breakdown()
    assert len(bd) == 4, f"Expected 4 groups, got {len(bd)}"
    print(f"  Provider breakdown: {len(bd)} groups ✓")
    for b in bd:
        print(f"    {b['provider']}/{b['model']}: {b['call_count']} calls, {b['total_tokens']} tokens, ${b['cost_usd']}")

    # Per-node summary
    ns = t.get_node_summary("researcher")
    assert ns["call_count"] == 1
    assert ns["total_tokens"] == 1100
    print(f"  Researcher node: {ns['total_tokens']} tokens, ${ns['total_cost_usd']} ✓")

    # Empty node
    empty = t.get_node_summary("nonexistent")
    assert empty["call_count"] == 0
    print(f"  Nonexistent node: {empty['call_count']} calls ✓")

    # Run summary
    summary = t.get_run_summary()
    assert summary["total_calls"] == 4
    assert summary["total_tokens"] > 0
    assert summary["nodes_with_llm_calls"] == 4
    print(f"  Run summary: {summary['total_calls']} calls, {summary['total_tokens']} tokens, ${summary['total_cost_usd']} ✓")

    print("  All CostTracker tests passed!\n")


def test_execution_tracer():
    """Verify ExecutionTracer event recording and export."""
    print("=== ExecutionTracer Tests ===")

    tr = ExecutionTracer("test-run-001")
    assert tr.run_id == "test-run-001"
    print(f"  Run ID: {tr.run_id} ✓")

    # Emit events
    e1 = tr.emit(TraceEventType.RUN_START, data={"graph_id": "g-test"})
    e2 = tr.emit(TraceEventType.NODE_START, node_id="n1", data={"role": "researcher"})
    e3 = tr.emit(TraceEventType.LLM_CALL, node_id="n1", data={"provider": "openai"})
    e4 = tr.emit(TraceEventType.LLM_RESULT, node_id="n1", data={"tokens": 450})
    e5 = tr.emit(TraceEventType.NODE_END, node_id="n1", data={"status": "success"})
    e6 = tr.emit(TraceEventType.NODE_START, node_id="n2", data={"role": "verifier"})
    e7 = tr.emit(TraceEventType.NODE_END, node_id="n2", data={"status": "success"})
    e8 = tr.emit(TraceEventType.RUN_END, data={"status": "success"})

    assert tr.event_count == 8
    print(f"  Event count: {tr.event_count} ✓")

    # Filter by type
    starts = tr.get_events(event_type=TraceEventType.NODE_START)
    assert len(starts) == 2
    print(f"  NODE_START events: {len(starts)} ✓")

    # Filter by node
    n1_events = tr.get_events(node_id="n1")
    assert len(n1_events) == 4
    print(f"  Node n1 events: {len(n1_events)} ✓")

    # Timeline
    timeline = tr.get_timeline()
    assert len(timeline) == 8
    assert "event_type" in timeline[0]
    assert "elapsed_s" in timeline[0]
    print(f"  Timeline entries: {len(timeline)} ✓")

    # Export
    export = tr.export_dict()
    assert export["run_id"] == "test-run-001"
    assert export["event_count"] == 8
    assert len(export["events"]) == 8
    print(f"  Export dict keys: {list(export.keys())} ✓")

    json_str = tr.export_json()
    assert len(json_str) > 0
    assert '"run_id"' in json_str
    print(f"  JSON export: {len(json_str)} chars ✓")

    # Ingest bulk events
    tr2 = ExecutionTracer("test-run-002")
    events = tr.get_all_events()
    tr2.ingest_events(events)
    assert tr2.event_count == 8
    # Verify run_id was overwritten
    for e in tr2.get_all_events():
        assert e.run_id == "test-run-002"
    print(f"  Bulk ingest: {tr2.event_count} events, run_id overwritten ✓")

    print("  All ExecutionTracer tests passed!\n")


def test_run_store():
    """Verify RunStore CRUD operations."""
    print("=== RunStore Tests ===")

    store = RunStore()

    # Create a minimal graph for storage
    graph = ExecutionGraph(
        graph_id="g-test",
        nodes={
            "n1": AgentConfig(agent_id="a1", role=AgentRole.RESEARCHER),
            "n2": AgentConfig(agent_id="a2", role=AgentRole.VERIFIER),
        },
        edges=[("n1", "n2")],
    )

    tracer = ExecutionTracer("run-test-001")
    tracer.emit(TraceEventType.RUN_START, data={"graph_id": "g-test"})
    tracer.emit(TraceEventType.RUN_END, data={"status": "success"})

    record = RunRecord(
        run_id="run-test-001",
        tracer=tracer,
        graph=graph,
        goal_text="Test goal for verification",
        cost_summary={"total_cost_usd": 0.005, "total_tokens": 700},
    )

    # Store
    store.store(record)
    assert len(store) == 1
    assert "run-test-001" in store
    print(f"  Stored run: {record.run_id} ✓")

    # Retrieve
    retrieved = store.get("run-test-001")
    assert retrieved is not None
    assert retrieved.goal_text == "Test goal for verification"
    print(f"  Retrieved run: {retrieved.run_id} ✓")

    # List
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-test-001"
    assert runs[0]["goal_text"].startswith("Test goal")
    print(f"  Listed runs: {len(runs)} ✓")

    # Summary dict
    summary = record.to_summary_dict()
    assert "run_id" in summary
    assert "total_cost_usd" in summary
    print(f"  Summary keys: {list(summary.keys())} ✓")

    # Delete
    deleted = store.delete("run-test-001")
    assert deleted is True
    assert len(store) == 0
    print(f"  Deleted run: True ✓")

    # Not found
    assert store.get("nonexistent") is None
    assert store.delete("nonexistent") is False
    print(f"  Not found handled: ✓")

    print("  All RunStore tests passed!\n")


def test_replay_comparison():
    """Verify ReplayComparison output formatting."""
    print("=== ReplayComparison Tests ===")

    comparison = ReplayComparison(
        original_run_id="run-orig",
        replay_run_id="replay-001",
        original_summary={
            "run_id": "run-orig",
            "elapsed_ms": 5200.0,
            "nodes_succeeded": 4,
            "nodes_failed": 0,
            "nodes_retried": 1,
        },
        replay_summary={
            "run_id": "replay-001",
            "elapsed_ms": 8100.0,
            "nodes_succeeded": 3,
            "nodes_failed": 1,
            "nodes_retried": 2,
        },
        original_cost={
            "total_cost_usd": 0.0125,
            "total_tokens": 2800,
            "provider_breakdown": [
                {"provider": "openai", "model": "gpt-4o", "cost_usd": 0.0125},
            ],
        },
        replay_cost={
            "total_cost_usd": 0.0,
            "total_tokens": 3200,
            "provider_breakdown": [
                {"provider": "ollama", "model": "llama3", "cost_usd": 0.0},
            ],
        },
        provider_override="ollama",
    )

    d = comparison.to_dict()
    assert d["original_run_id"] == "run-orig"
    assert d["replay_run_id"] == "replay-001"
    assert d["provider_override"] == "ollama"
    assert d["comparison"]["cost_usd"]["delta"] < 0  # Ollama is cheaper
    print(f"  Comparison dict: cost delta = ${d['comparison']['cost_usd']['delta']} ✓")

    table = comparison.summary_table()
    assert "| Metric |" in table
    assert "Cost (USD)" in table
    assert "Latency (ms)" in table
    print(f"  Summary table:\n{table}")
    print(f"  ReplayComparison formatted ✓")

    print("  All ReplayComparison tests passed!\n")


def main():
    print("=" * 60)
    print("MODULE 7: OBSERVABILITY VERIFICATION")
    print("=" * 60)
    print()

    test_cost_calculation()
    test_cost_tracker()
    test_execution_tracer()
    test_run_store()
    test_replay_comparison()

    print("=" * 60)
    print("ALL MODULE 7 TESTS PASSED ✓")
    print("=" * 60)


if __name__ == "__main__":
    main()
