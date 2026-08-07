"""
Module 5 Verification Script — Async Execution Engine & Retry/Compensation.

Tests:
  01. AgentScratchMemory basic put/get/has/delete
  02. AgentScratchMemory TTL eviction
  03. AgentScratchMemory max-entries LRU eviction
  04. AgentScratchMemory get_memory_stats()
  05. SharedProjectMemory CRUD
  06. ExecutionState node lifecycle
  07. ExecutionState message bus
  08. RetryPolicy — succeeds on first try
  09. RetryPolicy — succeeds on retry attempt
  10. RetryPolicy — exhausts retries → NodeExecutionError
  11. CompensationRouter — routes to compensation node
  12. CompensationRouter — escalates to HITL when no compensation
  13. build_retry_context — error-context injection
  14. topological_layers — correct layering
  15. topological_layers — cycle detection
  16. AsyncDAGExecutor — 2 parallel branches merge at verifier
  17. AsyncDAGExecutor — retry with error context injection
  18. AsyncDAGExecutor — sub-graph execution
"""

import asyncio
import sys
import time
import traceback

sys.path.insert(0, r"c:\hack")


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Tests ──────────────────────────────────────────────────────────────


def test_01_scratch_basic():
    """AgentScratchMemory basic put/get/has/delete."""
    from backend.engine.state_manager import AgentScratchMemory

    mem = AgentScratchMemory(default_ttl=60)
    mem.put("a", 1)
    mem.put("b", "hello")

    assert mem.get("a") == 1
    assert mem.get("b") == "hello"
    assert mem.get("missing") is None
    assert mem.get("missing", "default") == "default"
    assert mem.has("a") is True
    assert mem.has("missing") is False
    assert set(mem.keys()) == {"a", "b"}

    assert mem.delete("a") is True
    assert mem.delete("a") is False
    assert mem.has("a") is False

    mem.clear()
    assert len(mem.keys()) == 0

    print("  [PASS] 01  AgentScratchMemory basic CRUD")


def test_02_scratch_ttl_eviction():
    """AgentScratchMemory TTL eviction."""
    from backend.engine.state_manager import AgentScratchMemory

    mem = AgentScratchMemory(default_ttl=0.1)  # 100ms TTL
    mem.put("fast", "value")

    assert mem.has("fast") is True
    time.sleep(0.15)

    evicted = mem.evict_expired()
    assert evicted >= 1
    assert mem.has("fast") is False

    print("  [PASS] 02  TTL eviction (100ms)")


def test_03_scratch_lru_eviction():
    """AgentScratchMemory max-entries LRU eviction."""
    from backend.engine.state_manager import AgentScratchMemory

    mem = AgentScratchMemory(default_ttl=0, max_entries=5)  # TTL=0 → no time eviction
    for i in range(10):
        mem.put(f"k{i}", i)

    assert len(mem.keys()) == 5
    # Oldest keys (k0..k4) should be evicted; k5..k9 remain
    remaining = mem.keys()
    for i in range(5):
        assert f"k{i}" not in remaining
    for i in range(5, 10):
        assert f"k{i}" in remaining

    stats = mem.get_memory_stats()
    assert stats["eviction_count"] == 5

    print("  [PASS] 03  LRU eviction (max_entries=5)")


def test_04_scratch_memory_stats():
    """AgentScratchMemory get_memory_stats()."""
    from backend.engine.state_manager import AgentScratchMemory

    mem = AgentScratchMemory(default_ttl=300, max_entries=100)
    for i in range(10):
        mem.put(f"key{i}", f"value_{i}")

    stats = mem.get_memory_stats()
    assert stats["entry_count"] == 10
    assert stats["max_entries"] == 100
    assert stats["memory_estimate_bytes"] > 0
    assert stats["oldest_entry_age_seconds"] >= 0
    assert stats["eviction_count"] == 0
    assert stats["default_ttl"] == 300

    print("  [PASS] 04  get_memory_stats()")


def test_05_shared_project_memory():
    """SharedProjectMemory CRUD."""
    from backend.engine.state_manager import SharedProjectMemory

    mem = SharedProjectMemory()
    mem.put("goal", "Build an API")
    mem.put("provider", "openai")

    assert mem.get("goal") == "Build an API"
    assert mem.has("provider") is True
    assert mem.has("nope") is False
    assert set(mem.keys()) == {"goal", "provider"}

    d = mem.to_dict()
    assert d == {"goal": "Build an API", "provider": "openai"}

    mem.clear()
    assert len(mem.keys()) == 0

    print("  [PASS] 05  SharedProjectMemory CRUD")


def test_06_execution_state_node_lifecycle():
    """ExecutionState node lifecycle tracking."""
    from backend.engine.state_manager import ExecutionState
    from backend.schemas.contracts import ExecutionResult, ExecutionStatus

    state = ExecutionState(run_id="test-run", graph_id="test-graph")
    state.init_node("n1")
    state.init_node("n2")

    assert state.get_node_status("n1") == ExecutionStatus.PENDING
    state.set_node_status("n1", ExecutionStatus.RUNNING)
    assert state.get_node_status("n1") == ExecutionStatus.RUNNING

    result = ExecutionResult(node_id="n1", status=ExecutionStatus.SUCCESS, output={"answer": 42})
    state.set_node_result("n1", result)
    assert state.get_node_result("n1").output == {"answer": 42}

    state.record_error("n2", "Some error")
    assert state.get_errors("n2") == ["Some error"]

    count = state.increment_retry("n2")
    assert count == 1
    assert state.get_retry_count("n2") == 1

    print("  [PASS] 06  ExecutionState node lifecycle")


def test_07_execution_state_message_bus():
    """ExecutionState typed message bus."""
    from backend.engine.state_manager import ExecutionState
    from backend.schemas.contracts import AgentMessage

    state = ExecutionState()
    msg1 = AgentMessage(sender_agent_id="a1", target_agent_id="a2", payload={"key": "val"})
    msg2 = AgentMessage(sender_agent_id="a2", target_agent_id="a3", payload={"data": 1})

    state.post_message(msg1)
    state.post_message(msg2)

    assert len(state.get_all_messages()) == 2
    for_a2 = state.get_messages_for("a2")
    assert len(for_a2) == 1
    assert for_a2[0].payload == {"key": "val"}

    print("  [PASS] 07  ExecutionState message bus")


def test_08_retry_policy_first_try_success():
    """RetryPolicy — succeeds on first try."""
    from backend.engine.recovery import RetryPolicy

    policy = RetryPolicy(max_retries=2)

    async def success_fn(**kwargs):
        return {"ok": True}

    async def _run():
        result = await policy.execute_with_retry("n1", success_fn)
        assert result == {"ok": True}

    run_async(_run())
    print("  [PASS] 08  RetryPolicy — success on first try")


def test_09_retry_policy_success_on_retry():
    """RetryPolicy — succeeds on second attempt."""
    from backend.engine.recovery import RetryPolicy

    policy = RetryPolicy(max_retries=2, delays=[0.01, 0.01])
    call_count = 0

    async def flaky_fn(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ValueError("transient failure")
        return {"recovered": True}

    async def _run():
        result = await policy.execute_with_retry("n1", flaky_fn)
        assert result == {"recovered": True}
        assert call_count == 2

    run_async(_run())
    print("  [PASS] 09  RetryPolicy — success on retry")


def test_10_retry_policy_exhausted():
    """RetryPolicy — exhausts retries → NodeExecutionError."""
    from backend.engine.recovery import RetryPolicy, NodeExecutionError

    policy = RetryPolicy(max_retries=2, delays=[0.01, 0.01])

    async def always_fail(**kwargs):
        raise RuntimeError("permanent failure")

    exhausted_called = False

    async def on_exhausted(node_id, errors):
        nonlocal exhausted_called
        exhausted_called = True

    async def _run():
        try:
            await policy.execute_with_retry(
                "n1", always_fail, on_exhausted=on_exhausted
            )
            assert False, "Expected NodeExecutionError"
        except NodeExecutionError as exc:
            assert exc.node_id == "n1"
            assert len(exc.error_history) == 3  # 1 initial + 2 retries
            assert exhausted_called

    run_async(_run())
    print("  [PASS] 10  RetryPolicy -- exhausts retries -> NodeExecutionError")


def test_11_compensation_routes():
    """CompensationRouter — routes to compensation node."""
    from backend.engine.recovery import CompensationRouter

    router = CompensationRouter({"n1": "n1_comp"})

    assert router.has_compensation("n1") is True
    assert router.get_compensation_target("n1") == "n1_comp"

    comp_called = False

    async def on_comp(nid, comp_nid, errors):
        nonlocal comp_called
        comp_called = True
        assert comp_nid == "n1_comp"

    async def _run():
        result = await router.route("n1", ["err1"], on_compensate=on_comp)
        assert result == "compensating"
        assert comp_called

    run_async(_run())
    print("  [PASS] 11  CompensationRouter — routes to compensation")


def test_12_compensation_escalates():
    """CompensationRouter — escalates to HITL when no compensation."""
    from backend.engine.recovery import CompensationRouter

    router = CompensationRouter()

    escalated = False

    async def on_esc(nid, errors):
        nonlocal escalated
        escalated = True

    async def _run():
        result = await router.route("n1", ["err1"], on_escalate=on_esc)
        assert result == "waiting_for_approval"
        assert escalated

    run_async(_run())
    print("  [PASS] 12  CompensationRouter — escalates to HITL")


def test_13_build_retry_context():
    """build_retry_context — error-context injection."""
    from backend.engine.recovery import build_retry_context

    prompt = "You are a researcher."
    ctx = build_retry_context(prompt, ["err1", "err2"])
    assert "[RETRY CONTEXT" in ctx
    assert "err1" in ctx
    assert "err2" in ctx
    assert ctx.startswith("You are a researcher.")

    # No errors → unchanged
    assert build_retry_context(prompt, []) == prompt

    print("  [PASS] 13  build_retry_context — error injection")


def test_14_topological_layers():
    """topological_layers — correct layering with parallel branches."""
    from backend.engine.executor import topological_layers
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph

    # Diamond: A → B, A → C, B → D, C → D
    graph = ExecutionGraph(
        nodes={
            "A": AgentConfig(role=AgentRole.PLANNER),
            "B": AgentConfig(role=AgentRole.RESEARCHER),
            "C": AgentConfig(role=AgentRole.EXECUTOR),
            "D": AgentConfig(role=AgentRole.VERIFIER),
        },
        edges=[("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")],
    )

    layers = topological_layers(graph)
    assert len(layers) == 3
    assert layers[0] == ["A"]
    assert set(layers[1]) == {"B", "C"}  # Parallel
    assert layers[2] == ["D"]

    print("  [PASS] 14  topological_layers — diamond graph")


def test_15_topological_cycle_detection():
    """topological_layers — cycle detection raises ValueError."""
    from backend.engine.executor import topological_layers
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph

    graph = ExecutionGraph(
        nodes={
            "A": AgentConfig(role=AgentRole.PLANNER),
            "B": AgentConfig(role=AgentRole.RESEARCHER),
        },
        edges=[("A", "B"), ("B", "A")],
    )

    try:
        topological_layers(graph)
        assert False, "Expected ValueError for cycle"
    except ValueError as e:
        assert "Cycle detected" in str(e)

    print("  [PASS] 15  topological_layers — cycle detection")


def test_16_executor_parallel_branches():
    """AsyncDAGExecutor — 2 parallel branches merge at verifier."""
    from backend.engine.executor import AsyncDAGExecutor
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph

    execution_order = []

    async def mock_handler(node_id, config, input_payload, system_prompt):
        execution_order.append(node_id)
        await asyncio.sleep(0.01)
        return {"node": node_id, "result": f"{node_id}_output"}

    graph = ExecutionGraph(
        nodes={
            "planner": AgentConfig(role=AgentRole.PLANNER),
            "researcher": AgentConfig(role=AgentRole.RESEARCHER),
            "executor": AgentConfig(role=AgentRole.EXECUTOR),
            "verifier": AgentConfig(role=AgentRole.VERIFIER),
        },
        edges=[
            ("planner", "researcher"),
            ("planner", "executor"),
            ("researcher", "verifier"),
            ("executor", "verifier"),
        ],
    )

    async def _run():
        executor = AsyncDAGExecutor(graph, mock_handler)
        state = await executor.run()

        summary = state.summary()
        assert summary["nodes_succeeded"] == 4
        assert summary["nodes_failed"] == 0
        assert summary["is_finished"] is True

        # Verify planner ran first
        assert execution_order[0] == "planner"
        # researcher and executor ran in parallel (both before verifier)
        assert "verifier" == execution_order[-1]
        assert set(execution_order[1:3]) == {"researcher", "executor"}

    run_async(_run())
    print("  [PASS] 16  AsyncDAGExecutor — parallel branches merge at verifier")


def test_17_executor_retry_with_context():
    """AsyncDAGExecutor — retry with error context injection."""
    from backend.engine.executor import AsyncDAGExecutor
    from backend.engine.recovery import RetryPolicy
    from backend.schemas.contracts import (
        AgentConfig,
        AgentRole,
        ExecutionGraph,
        ExecutionStatus,
    )

    call_count = 0
    received_prompts = []

    async def flaky_handler(node_id, config, input_payload, system_prompt):
        nonlocal call_count
        call_count += 1
        received_prompts.append(system_prompt)
        if call_count < 2:
            raise ValueError("simulated failure")
        return {"recovered": True}

    graph = ExecutionGraph(
        nodes={
            "n1": AgentConfig(
                role=AgentRole.EXECUTOR,
                system_prompt="You are an executor.",
            ),
        },
        edges=[],
    )

    async def _run():
        executor = AsyncDAGExecutor(
            graph,
            flaky_handler,
            retry_policy=RetryPolicy(max_retries=2, delays=[0.01, 0.01]),
        )
        state = await executor.run()

        assert state.get_node_status("n1") == ExecutionStatus.SUCCESS
        assert call_count == 2
        # Second call should have retry context
        assert "[RETRY CONTEXT" in received_prompts[1]

    run_async(_run())
    print("  [PASS] 17  AsyncDAGExecutor — retry with error context")


def test_18_executor_sub_graph():
    """AsyncDAGExecutor — sub-graph execution."""
    from backend.engine.executor import AsyncDAGExecutor
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph, ExecutionStatus

    async def simple_handler(node_id, config, input_payload, system_prompt):
        return {"done": True, "node": node_id}

    sub_graph = ExecutionGraph(
        graph_id="sub-1",
        nodes={
            "s1": AgentConfig(role=AgentRole.RESEARCHER),
            "s2": AgentConfig(role=AgentRole.ANALYST),
        },
        edges=[("s1", "s2")],
        parent_graph_id="main",
    )

    main_graph = ExecutionGraph(
        graph_id="main",
        nodes={
            "entry": AgentConfig(role=AgentRole.PLANNER),
            "delegate": AgentConfig(
                role=AgentRole.SUB_GRAPH,
                sub_graph_id="sub-1",
            ),
            "final": AgentConfig(role=AgentRole.REPORTER),
        },
        edges=[("entry", "delegate"), ("delegate", "final")],
    )

    async def _run():
        executor = AsyncDAGExecutor(
            main_graph,
            simple_handler,
            sub_graphs={"sub-1": sub_graph},
        )
        state = await executor.run()

        assert state.get_node_status("entry") == ExecutionStatus.SUCCESS
        assert state.get_node_status("delegate") == ExecutionStatus.SUCCESS
        assert state.get_node_status("final") == ExecutionStatus.SUCCESS

        # Sub-graph result should contain summary
        delegate_result = state.get_node_result("delegate")
        assert "sub_graph_summary" in delegate_result.output

    run_async(_run())
    print("  [PASS] 18  AsyncDAGExecutor — sub-graph execution")


# ── Run All ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_01_scratch_basic,
        test_02_scratch_ttl_eviction,
        test_03_scratch_lru_eviction,
        test_04_scratch_memory_stats,
        test_05_shared_project_memory,
        test_06_execution_state_node_lifecycle,
        test_07_execution_state_message_bus,
        test_08_retry_policy_first_try_success,
        test_09_retry_policy_success_on_retry,
        test_10_retry_policy_exhausted,
        test_11_compensation_routes,
        test_12_compensation_escalates,
        test_13_build_retry_context,
        test_14_topological_layers,
        test_15_topological_cycle_detection,
        test_16_executor_parallel_branches,
        test_17_executor_retry_with_context,
        test_18_executor_sub_graph,
    ]

    print("=" * 64)
    print("MODULE 5 VERIFICATION — Async Execution Engine & Recovery")
    print("=" * 64)

    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("=" * 64)
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 64)

    if failed > 0:
        sys.exit(1)
    else:
        print()
        print("[ANTIGRAVITY STEP GATE 5]: Module 5 complete.")
        print("AsyncDAGExecutor (topological traversal, parallel fan-out,")
        print("sub-graph delegation), StateManager (SharedProjectMemory,")
        print("AgentScratchMemory with TTL+LRU), and Recovery (RetryPolicy,")
        print("CompensationRouter, error-context injection) are verified.")
        print("Please confirm with 'APPROVED' to begin Module 6.")
