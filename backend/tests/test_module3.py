"""
Module 3 Verification Script.

Tests the Task-to-Graph Compiler — prompt templates, graph validator,
graph compiler, and sub-graph compilation:
  1. Prompt templates are non-empty and contain required keywords
  2. GraphValidator — valid DAG passes all checks
  3. GraphValidator — cycle detection via Kahn's Algorithm
  4. GraphValidator — orphan node detection
  5. GraphValidator — parallel branch enforcement
  6. GraphValidator — critic/verifier join requirement
  7. GraphValidator — sub-graph reference integrity (dangling ref)
  8. GraphValidator — no recursive sub_graph in sub-graphs
  9. GraphValidator — validate_and_lock stamps version and locks
 10. GraphValidator — topological_sort returns correct ordering
 11. GraphCompiler — parse valid LLM JSON into ExecutionGraph
 12. GraphCompiler — parse JSON with markdown fences
 13. GraphCompiler — reject invalid JSON
 14. GraphCompiler — full compile_goal with mocked LLM (main graph)
 15. GraphCompiler — compile with nested sub-graph (mocked LLM)
 16. CompilationResult model creation
"""

import sys
import json
import asyncio
import traceback
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the project root is on the path
sys.path.insert(0, r"c:\hack")


# ── Helpers ────────────────────────────────────────────────────────────

def run_async(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_valid_graph():
    """Create a valid ExecutionGraph with parallel branches and a critic join."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph

    return ExecutionGraph(
        graph_id="test-graph",
        nodes={
            "planner-1": AgentConfig(agent_id="planner-1", role=AgentRole.PLANNER),
            "researcher-1": AgentConfig(agent_id="researcher-1", role=AgentRole.RESEARCHER),
            "executor-1": AgentConfig(agent_id="executor-1", role=AgentRole.EXECUTOR),
            "critic-1": AgentConfig(agent_id="critic-1", role=AgentRole.CRITIC),
            "reporter-1": AgentConfig(agent_id="reporter-1", role=AgentRole.REPORTER),
        },
        edges=[
            ("planner-1", "researcher-1"),
            ("planner-1", "executor-1"),
            ("researcher-1", "critic-1"),
            ("executor-1", "critic-1"),
            ("critic-1", "reporter-1"),
        ],
    )


# The JSON the mocked LLM will return for main graph compilation
MOCK_MAIN_GRAPH_JSON = {
    "graph_id": "graph-mock-main",
    "version": "1.0.0",
    "nodes": {
        "planner-1": {
            "agent_id": "planner-1",
            "role": "planner",
            "system_prompt": "Decompose the task.",
            "allowed_tools": [],
            "token_budget": 4096,
            "model_provider": "openai",
            "timeout_seconds": 120,
            "max_retries": 2,
            "sub_graph_id": None,
        },
        "researcher-1": {
            "agent_id": "researcher-1",
            "role": "researcher",
            "system_prompt": "Research the topic.",
            "allowed_tools": ["web_search"],
            "token_budget": 4096,
            "model_provider": "openai",
            "timeout_seconds": 120,
            "max_retries": 2,
            "sub_graph_id": None,
        },
        "executor-1": {
            "agent_id": "executor-1",
            "role": "executor",
            "system_prompt": "Execute the plan.",
            "allowed_tools": ["code_exec"],
            "token_budget": 4096,
            "model_provider": "openai",
            "timeout_seconds": 180,
            "max_retries": 2,
            "sub_graph_id": None,
        },
        "critic-1": {
            "agent_id": "critic-1",
            "role": "critic",
            "system_prompt": "Review the outputs.",
            "allowed_tools": [],
            "token_budget": 4096,
            "model_provider": "gemini",
            "timeout_seconds": 120,
            "max_retries": 2,
            "sub_graph_id": None,
        },
        "reporter-1": {
            "agent_id": "reporter-1",
            "role": "reporter",
            "system_prompt": "Write the final report.",
            "allowed_tools": [],
            "token_budget": 4096,
            "model_provider": "openai",
            "timeout_seconds": 120,
            "max_retries": 1,
            "sub_graph_id": None,
        },
    },
    "edges": [
        ["planner-1", "researcher-1"],
        ["planner-1", "executor-1"],
        ["researcher-1", "critic-1"],
        ["executor-1", "critic-1"],
        ["critic-1", "reporter-1"],
    ],
    "metadata": {
        "goal": "Test goal",
        "compiled_by": "planner",
    },
}

# The JSON for a main graph that contains a sub_graph node
MOCK_MAIN_WITH_SUBGRAPH_JSON = {
    "graph_id": "graph-with-sub",
    "version": "1.0.0",
    "nodes": {
        "planner-1": {
            "agent_id": "planner-1",
            "role": "planner",
            "system_prompt": "Plan the task.",
            "sub_graph_id": None,
        },
        "sub-node-1": {
            "agent_id": "sub-node-1",
            "role": "sub_graph",
            "system_prompt": "Handle data pipeline sub-task.",
            "sub_graph_id": "sub-graph-data",
        },
        "executor-1": {
            "agent_id": "executor-1",
            "role": "executor",
            "system_prompt": "Execute using sub-graph results.",
            "sub_graph_id": None,
        },
        "critic-1": {
            "agent_id": "critic-1",
            "role": "critic",
            "system_prompt": "Review all outputs.",
            "sub_graph_id": None,
        },
        "reporter-1": {
            "agent_id": "reporter-1",
            "role": "reporter",
            "system_prompt": "Write the report.",
            "sub_graph_id": None,
        },
    },
    "edges": [
        ["planner-1", "sub-node-1"],
        ["planner-1", "executor-1"],
        ["sub-node-1", "critic-1"],
        ["executor-1", "critic-1"],
        ["critic-1", "reporter-1"],
    ],
    "metadata": {"goal": "Complex task", "compiled_by": "planner"},
}

# The JSON for the compiled sub-graph
MOCK_SUB_GRAPH_JSON = {
    "graph_id": "sub-graph-data",
    "version": "1.0.0",
    "nodes": {
        "researcher-sub": {
            "agent_id": "researcher-sub",
            "role": "researcher",
            "system_prompt": "Fetch data for the pipeline.",
            "sub_graph_id": None,
        },
        "verifier-sub": {
            "agent_id": "verifier-sub",
            "role": "verifier",
            "system_prompt": "Verify data integrity.",
            "sub_graph_id": None,
        },
    },
    "edges": [
        ["researcher-sub", "verifier-sub"],
    ],
    "metadata": {"goal": "Sub-task", "compiled_by": "planner"},
}


# ── Tests ──────────────────────────────────────────────────────────────


def test_prompt_templates():
    """Test that prompt templates contain required content."""
    from backend.compiler.prompt_templates import (
        PLANNER_SYSTEM_PROMPT,
        SUB_GRAPH_SYSTEM_PROMPT,
    )

    # Non-empty
    assert len(PLANNER_SYSTEM_PROMPT) > 200
    assert len(SUB_GRAPH_SYSTEM_PROMPT) > 100

    # Contains all 8 roles
    for role in ["planner", "researcher", "executor", "analyst",
                 "critic", "verifier", "reporter", "sub_graph"]:
        assert role in PLANNER_SYSTEM_PROMPT, f"Missing role '{role}' in planner prompt"

    # Contains key instructions
    assert "DAG" in PLANNER_SYSTEM_PROMPT
    assert "parallel" in PLANNER_SYSTEM_PROMPT.lower()
    assert "JSON" in PLANNER_SYSTEM_PROMPT
    assert "cycle" in SUB_GRAPH_SYSTEM_PROMPT.lower() or "DAG" in SUB_GRAPH_SYSTEM_PROMPT

    print("  [PASS] Prompt templates — non-empty, all roles present")


def test_validator_valid_dag():
    """Test that a well-formed DAG passes validation."""
    from backend.compiler.validator import GraphValidator

    graph = _make_valid_graph()
    # Should not raise
    GraphValidator.validate(graph)
    print("  [PASS] GraphValidator — valid DAG passes all checks")


def test_validator_cycle_detection():
    """Test Kahn's Algorithm detects cycles."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph
    from backend.compiler.validator import GraphValidator, ValidationError

    graph = ExecutionGraph(
        graph_id="cyclic",
        nodes={
            "a": AgentConfig(agent_id="a", role=AgentRole.EXECUTOR),
            "b": AgentConfig(agent_id="b", role=AgentRole.EXECUTOR),
            "c": AgentConfig(agent_id="c", role=AgentRole.CRITIC),
        },
        edges=[("a", "b"), ("b", "c"), ("c", "a")],  # Cycle: a→b→c→a
    )

    try:
        GraphValidator.validate(graph)
        assert False, "Should have raised ValidationError for cycle"
    except ValidationError as e:
        assert any("cycle" in err.lower() or "Cycle" in err for err in e.errors), \
            f"Expected cycle error, got: {e.errors}"

    print("  [PASS] GraphValidator — cycle detection via Kahn's Algorithm")


def test_validator_orphan_detection():
    """Test that orphan nodes (no edges) are detected in multi-node graphs."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph
    from backend.compiler.validator import GraphValidator, ValidationError

    graph = ExecutionGraph(
        graph_id="orphan-test",
        nodes={
            "a": AgentConfig(agent_id="a", role=AgentRole.PLANNER),
            "b": AgentConfig(agent_id="b", role=AgentRole.RESEARCHER),
            "c": AgentConfig(agent_id="c", role=AgentRole.EXECUTOR),
            "orphan": AgentConfig(agent_id="orphan", role=AgentRole.ANALYST),
            "critic-1": AgentConfig(agent_id="critic-1", role=AgentRole.CRITIC),
        },
        edges=[
            ("a", "b"),
            ("a", "c"),
            ("b", "critic-1"),
            ("c", "critic-1"),
        ],
        # "orphan" has no edges at all
    )

    try:
        GraphValidator.validate(graph)
        assert False, "Should have raised ValidationError for orphan"
    except ValidationError as e:
        assert any("orphan" in err.lower() for err in e.errors), \
            f"Expected orphan error, got: {e.errors}"

    print("  [PASS] GraphValidator — orphan node detection")


def test_validator_parallel_branch_required():
    """Test that main graphs require at least one parallel branch."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph
    from backend.compiler.validator import GraphValidator, ValidationError

    # Linear chain: a → b → c (no parallel branch)
    graph = ExecutionGraph(
        graph_id="linear",
        nodes={
            "a": AgentConfig(agent_id="a", role=AgentRole.PLANNER),
            "b": AgentConfig(agent_id="b", role=AgentRole.EXECUTOR),
            "c": AgentConfig(agent_id="c", role=AgentRole.CRITIC),
        },
        edges=[("a", "b"), ("b", "c")],
    )

    try:
        GraphValidator.validate(graph, is_sub_graph=False)
        assert False, "Should have raised ValidationError for no parallel branch"
    except ValidationError as e:
        assert any("parallel" in err.lower() for err in e.errors), \
            f"Expected parallel branch error, got: {e.errors}"

    print("  [PASS] GraphValidator — parallel branch enforcement")


def test_validator_critic_verifier_required():
    """Test that main graphs require at least one critic or verifier."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph
    from backend.compiler.validator import GraphValidator, ValidationError

    # No critic or verifier node at all
    graph = ExecutionGraph(
        graph_id="no-critic",
        nodes={
            "a": AgentConfig(agent_id="a", role=AgentRole.PLANNER),
            "b": AgentConfig(agent_id="b", role=AgentRole.RESEARCHER),
            "c": AgentConfig(agent_id="c", role=AgentRole.EXECUTOR),
            "d": AgentConfig(agent_id="d", role=AgentRole.REPORTER),
        },
        edges=[
            ("a", "b"),
            ("a", "c"),
            ("b", "d"),
            ("c", "d"),
        ],
    )

    try:
        GraphValidator.validate(graph, is_sub_graph=False)
        assert False, "Should have raised ValidationError for missing critic/verifier"
    except ValidationError as e:
        assert any("critic" in err.lower() or "verifier" in err.lower() for err in e.errors), \
            f"Expected critic/verifier error, got: {e.errors}"

    print("  [PASS] GraphValidator — critic/verifier join requirement")


def test_validator_sub_graph_dangling_reference():
    """Test that a dangling sub_graph_id (no matching sub-graph) is caught."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph
    from backend.compiler.validator import GraphValidator, ValidationError

    graph = ExecutionGraph(
        graph_id="main",
        nodes={
            "a": AgentConfig(agent_id="a", role=AgentRole.PLANNER),
            "sub": AgentConfig(
                agent_id="sub",
                role=AgentRole.SUB_GRAPH,
                sub_graph_id="nonexistent-sub",
            ),
            "b": AgentConfig(agent_id="b", role=AgentRole.EXECUTOR),
            "c": AgentConfig(agent_id="c", role=AgentRole.CRITIC),
        },
        edges=[
            ("a", "sub"),
            ("a", "b"),
            ("sub", "c"),
            ("b", "c"),
        ],
    )

    # Provide an empty sub_graphs map → "nonexistent-sub" won't be found
    try:
        GraphValidator.validate(graph, sub_graphs={"other-sub": ExecutionGraph(graph_id="other-sub")})
        assert False, "Should have raised ValidationError for dangling sub_graph_id"
    except ValidationError as e:
        assert any("nonexistent-sub" in err for err in e.errors), \
            f"Expected dangling reference error, got: {e.errors}"

    print("  [PASS] GraphValidator — sub-graph dangling reference detected")


def test_validator_no_recursive_sub_graph():
    """Test that sub-graphs cannot contain sub_graph role nodes."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph
    from backend.compiler.validator import GraphValidator, ValidationError

    sub = ExecutionGraph(
        graph_id="sub-1",
        parent_graph_id="main",
        nodes={
            "worker": AgentConfig(agent_id="worker", role=AgentRole.EXECUTOR),
            "nested-sub": AgentConfig(
                agent_id="nested-sub",
                role=AgentRole.SUB_GRAPH,
                sub_graph_id="sub-2",
            ),
        },
        edges=[("worker", "nested-sub")],
    )

    try:
        GraphValidator.validate(sub, is_sub_graph=True)
        assert False, "Should have raised ValidationError for recursive sub_graph"
    except ValidationError as e:
        assert any("recursive" in err.lower() or "sub_graph" in err for err in e.errors), \
            f"Expected recursive nesting error, got: {e.errors}"

    print("  [PASS] GraphValidator — no recursive sub_graph in sub-graphs")


def test_validator_validate_and_lock():
    """Test validate_and_lock stamps version and locks the graph."""
    from backend.compiler.validator import GraphValidator

    graph = _make_valid_graph()
    assert graph.locked is False

    GraphValidator.validate_and_lock(graph, version="2.1.0")

    assert graph.locked is True
    assert graph.version == "2.1.0"

    print("  [PASS] GraphValidator — validate_and_lock stamps version & locks")


def test_topological_sort():
    """Test topological_sort returns a valid ordering."""
    from backend.compiler.validator import GraphValidator, ValidationError

    graph = _make_valid_graph()
    order = GraphValidator.topological_sort(graph)

    assert len(order) == 5
    # planner-1 must come before researcher-1 and executor-1
    assert order.index("planner-1") < order.index("researcher-1")
    assert order.index("planner-1") < order.index("executor-1")
    # critic-1 must come after researcher-1 and executor-1
    assert order.index("critic-1") > order.index("researcher-1")
    assert order.index("critic-1") > order.index("executor-1")
    # reporter-1 must come last
    assert order.index("reporter-1") > order.index("critic-1")

    # Cyclic graph should raise
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph
    cyclic = ExecutionGraph(
        graph_id="cyclic",
        nodes={
            "a": AgentConfig(agent_id="a", role=AgentRole.EXECUTOR),
            "b": AgentConfig(agent_id="b", role=AgentRole.EXECUTOR),
        },
        edges=[("a", "b"), ("b", "a")],
    )
    try:
        GraphValidator.topological_sort(cyclic)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    print("  [PASS] GraphValidator — topological_sort correct ordering")


def test_compiler_parse_valid_json():
    """Test that GraphCompiler can parse valid LLM JSON into an ExecutionGraph."""
    from backend.providers.base import LLMResponse
    from backend.compiler.graph_compiler import GraphCompiler

    mock_router = MagicMock()
    compiler = GraphCompiler(mock_router)

    response = LLMResponse(
        content=json.dumps(MOCK_MAIN_GRAPH_JSON),
        parsed_json=MOCK_MAIN_GRAPH_JSON,
        tokens_prompt=100,
        tokens_completion=200,
        total_tokens=300,
        model="gpt-4o",
        provider="openai",
    )

    graph = compiler._parse_graph_response(response, "Test goal")

    assert graph.graph_id == "graph-mock-main"
    assert len(graph.nodes) == 5
    assert len(graph.edges) == 5
    assert "planner-1" in graph.nodes
    assert graph.nodes["researcher-1"].role.value == "researcher"

    print("  [PASS] GraphCompiler — parse valid LLM JSON into ExecutionGraph")


def test_compiler_parse_with_markdown_fences():
    """Test parsing JSON wrapped in markdown code fences."""
    from backend.providers.base import LLMResponse
    from backend.compiler.graph_compiler import GraphCompiler

    # Wrap in markdown fences (common LLM behavior)
    raw = "```json\n" + json.dumps(MOCK_MAIN_GRAPH_JSON) + "\n```"

    mock_router = MagicMock()
    compiler = GraphCompiler(mock_router)

    response = LLMResponse(
        content=raw,
        parsed_json=None,  # Not pre-parsed
        model="gpt-4o",
        provider="openai",
    )

    graph = compiler._parse_graph_response(response, "Test")
    assert graph.graph_id == "graph-mock-main"
    assert len(graph.nodes) == 5

    print("  [PASS] GraphCompiler — parse JSON with markdown fences")


def test_compiler_reject_invalid_json():
    """Test that invalid JSON raises CompilationError."""
    from backend.providers.base import LLMResponse
    from backend.compiler.graph_compiler import GraphCompiler, CompilationError

    mock_router = MagicMock()
    compiler = GraphCompiler(mock_router)

    response = LLMResponse(
        content="This is not JSON at all.",
        parsed_json=None,
        model="gpt-4o",
        provider="openai",
    )

    try:
        compiler._parse_graph_response(response, "Test")
        assert False, "Should have raised CompilationError"
    except CompilationError as e:
        assert "JSON" in str(e)

    print("  [PASS] GraphCompiler -- reject invalid JSON -> CompilationError")


def test_compiler_full_compile_goal():
    """Test full compile_goal flow with mocked LLM returning a valid graph."""
    from backend.providers.base import LLMResponse
    from backend.compiler.graph_compiler import GraphCompiler

    mock_response = LLMResponse(
        content=json.dumps(MOCK_MAIN_GRAPH_JSON),
        parsed_json=MOCK_MAIN_GRAPH_JSON,
        tokens_prompt=200,
        tokens_completion=300,
        total_tokens=500,
        model="gpt-4o",
        provider="openai",
    )

    mock_router = MagicMock()
    mock_router.call = AsyncMock(return_value=mock_response)

    compiler = GraphCompiler(mock_router)

    async def _run():
        result = await compiler.compile_goal("Audit the API security")
        assert result.main_graph.graph_id == "graph-mock-main"
        assert len(result.main_graph.nodes) == 5
        assert result.main_graph.locked is True
        assert result.main_graph.metadata["goal"] == "Audit the API security"
        assert len(result.sub_graphs) == 0

    run_async(_run())
    print("  [PASS] GraphCompiler — full compile_goal with mocked LLM")


def test_compiler_compile_with_sub_graph():
    """Test compile_goal with a sub_graph node triggers sub-graph compilation."""
    from backend.providers.base import LLMResponse
    from backend.compiler.graph_compiler import GraphCompiler

    # First call returns main graph with sub_graph node
    main_response = LLMResponse(
        content=json.dumps(MOCK_MAIN_WITH_SUBGRAPH_JSON),
        parsed_json=MOCK_MAIN_WITH_SUBGRAPH_JSON,
        tokens_prompt=200,
        tokens_completion=400,
        total_tokens=600,
        model="gpt-4o",
        provider="openai",
    )

    # Second call returns the sub-graph
    sub_response = LLMResponse(
        content=json.dumps(MOCK_SUB_GRAPH_JSON),
        parsed_json=MOCK_SUB_GRAPH_JSON,
        tokens_prompt=100,
        tokens_completion=150,
        total_tokens=250,
        model="gpt-4o",
        provider="openai",
    )

    mock_router = MagicMock()
    mock_router.call = AsyncMock(side_effect=[main_response, sub_response])

    compiler = GraphCompiler(mock_router)

    async def _run():
        result = await compiler.compile_goal("Complex task with sub-workflow")

        # Main graph should exist and be locked
        assert result.main_graph.graph_id == "graph-with-sub"
        assert result.main_graph.locked is True

        # Sub-graph should be compiled
        assert "sub-graph-data" in result.sub_graphs
        sg = result.sub_graphs["sub-graph-data"]
        assert sg.parent_graph_id == "graph-with-sub"
        assert sg.locked is True
        assert "researcher-sub" in sg.nodes
        assert "verifier-sub" in sg.nodes

    run_async(_run())
    print("  [PASS] GraphCompiler — compile with nested sub-graph")


def test_compilation_result_model():
    """Test CompilationResult Pydantic model."""
    from backend.compiler.graph_compiler import CompilationResult
    from backend.schemas.contracts import ExecutionGraph

    result = CompilationResult(
        main_graph=ExecutionGraph(graph_id="test"),
        sub_graphs={},
        compilation_tokens=500,
        compilation_cost_usd=0.005,
    )

    assert result.main_graph.graph_id == "test"
    assert result.compilation_tokens == 500
    assert result.compilation_cost_usd == 0.005
    assert len(result.sub_graphs) == 0

    print("  [PASS] CompilationResult model creation")


# ── Run All Tests ──────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_prompt_templates,
        test_validator_valid_dag,
        test_validator_cycle_detection,
        test_validator_orphan_detection,
        test_validator_parallel_branch_required,
        test_validator_critic_verifier_required,
        test_validator_sub_graph_dangling_reference,
        test_validator_no_recursive_sub_graph,
        test_validator_validate_and_lock,
        test_topological_sort,
        test_compiler_parse_valid_json,
        test_compiler_parse_with_markdown_fences,
        test_compiler_reject_invalid_json,
        test_compiler_full_compile_goal,
        test_compiler_compile_with_sub_graph,
        test_compilation_result_model,
    ]

    print("=" * 60)
    print("MODULE 3 VERIFICATION — Task-to-Graph Compiler")
    print("=" * 60)

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {test_fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
    else:
        print()
        print("[ANTIGRAVITY STEP GATE 3]: Module 3 complete.")
        print("Prompt templates, graph validator (Kahn's cycle detection,")
        print("structural rules), and graph compiler (with sub-graph support)")
        print("are verified. Please confirm with 'APPROVED' to begin Module 4.")
