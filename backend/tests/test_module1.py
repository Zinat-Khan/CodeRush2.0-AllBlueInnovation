"""
Module 1 Verification Script.

Tests all Pydantic v2 contracts, schemas, and environment vault:
1. AppSettings instantiation
2. AgentConfig with all roles including sub_graph
3. ExecutionGraph with utility methods
4. AgentMessage creation
5. ExecutionResult
6. TraceEvent, Artifact, RunReport
7. BenchmarkTask / BenchmarkResult
8. Validation rejection on malformed data
"""

import sys
import traceback

# Ensure the project root is on the path
sys.path.insert(0, r"c:\hack")


def test_app_settings():
    """Test environment vault configuration loading."""
    from backend.config import AppSettings

    # Instantiate with defaults (no .env file needed for test)
    settings = AppSettings(
        openai_api_key="sk-test",
        gemini_api_key="gem-test",
    )
    assert settings.openai_api_key == "sk-test"
    assert settings.gemini_api_key == "gem-test"
    assert settings.ollama_host == "http://localhost:11434"
    assert settings.default_provider == "openai"
    assert settings.get_model_for_provider("openai") == "gpt-4o"
    assert settings.get_model_for_provider("gemini") == "gemini-1.5-pro"
    assert settings.get_model_for_provider("ollama") == "llama3"
    assert settings.has_provider_key("openai") is True
    assert settings.has_provider_key("ollama") is True
    assert settings.scratch_memory_ttl_seconds == 300
    assert settings.scratch_memory_max_entries == 1000
    print("  [PASS] AppSettings")


def test_agent_config_all_roles():
    """Test AgentConfig creation with every role."""
    from backend.schemas.contracts import AgentConfig, AgentRole

    for role in AgentRole:
        if role == AgentRole.SUB_GRAPH:
            cfg = AgentConfig(
                role=role,
                sub_graph_id="sub-graph-001",
                system_prompt="Delegate to sub-graph",
            )
            assert cfg.sub_graph_id == "sub-graph-001"
        else:
            cfg = AgentConfig(
                role=role,
                system_prompt=f"I am a {role.value} agent.",
                allowed_tools=["web_search", "code_exec"],
            )
            assert cfg.sub_graph_id is None
        assert cfg.role == role
        assert cfg.agent_id.startswith("agent-")
    print("  [PASS] AgentConfig — all 8 roles including sub_graph")


def test_agent_config_sub_graph_validation():
    """Test that sub_graph role enforces sub_graph_id presence."""
    from pydantic import ValidationError
    from backend.schemas.contracts import AgentConfig, AgentRole

    # Missing sub_graph_id on SUB_GRAPH role should fail
    try:
        AgentConfig(role=AgentRole.SUB_GRAPH)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    # Non-sub_graph role with sub_graph_id should fail
    try:
        AgentConfig(role=AgentRole.RESEARCHER, sub_graph_id="invalid")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    print("  [PASS] AgentConfig — sub_graph_id validation")


def test_execution_graph():
    """Test ExecutionGraph construction and utility methods."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph

    planner = AgentConfig(agent_id="planner-1", role=AgentRole.PLANNER)
    researcher = AgentConfig(agent_id="researcher-1", role=AgentRole.RESEARCHER)
    executor = AgentConfig(agent_id="executor-1", role=AgentRole.EXECUTOR)
    critic = AgentConfig(agent_id="critic-1", role=AgentRole.CRITIC)

    graph = ExecutionGraph(
        graph_id="test-graph",
        nodes={
            "planner-1": planner,
            "researcher-1": researcher,
            "executor-1": executor,
            "critic-1": critic,
        },
        edges=[
            ("planner-1", "researcher-1"),
            ("planner-1", "executor-1"),
            ("researcher-1", "critic-1"),
            ("executor-1", "critic-1"),
        ],
        metadata={"goal": "Test workflow"},
    )

    assert graph.get_root_nodes() == ["planner-1"]
    assert graph.get_leaf_nodes() == ["critic-1"]
    assert set(graph.get_successors("planner-1")) == {"researcher-1", "executor-1"}
    assert set(graph.get_predecessors("critic-1")) == {"researcher-1", "executor-1"}
    assert len(graph.get_node_ids()) == 4
    assert graph.locked is False

    graph.lock()
    assert graph.locked is True

    print("  [PASS] ExecutionGraph — construction, utilities, locking")


def test_execution_graph_nested():
    """Test ExecutionGraph with nested sub-graph references."""
    from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph

    sub_graph = ExecutionGraph(
        graph_id="sub-graph-001",
        parent_graph_id="main-graph",
        nodes={
            "sub-worker": AgentConfig(agent_id="sub-worker", role=AgentRole.EXECUTOR),
        },
        edges=[],
    )
    assert sub_graph.parent_graph_id == "main-graph"

    main_graph = ExecutionGraph(
        graph_id="main-graph",
        nodes={
            "sub-node": AgentConfig(
                agent_id="sub-node",
                role=AgentRole.SUB_GRAPH,
                sub_graph_id="sub-graph-001",
            ),
        },
        edges=[],
    )
    assert main_graph.nodes["sub-node"].sub_graph_id == "sub-graph-001"

    print("  [PASS] ExecutionGraph — nested sub-graph support")


def test_agent_message():
    """Test AgentMessage creation."""
    from backend.schemas.contracts import AgentMessage

    msg = AgentMessage(
        sender_agent_id="researcher-1",
        target_agent_id="critic-1",
        payload={"entities": ["API", "Schema"]},
        provenance_trace_id="run-abc",
    )
    assert msg.sender_agent_id == "researcher-1"
    assert msg.message_id.startswith("msg-")
    assert msg.timestamp > 0
    print("  [PASS] AgentMessage")


def test_execution_result():
    """Test ExecutionResult creation with token/cost tracking."""
    from backend.schemas.contracts import ExecutionResult, ExecutionStatus

    result = ExecutionResult(
        node_id="researcher-1",
        status=ExecutionStatus.SUCCESS,
        output={"entities": ["API"]},
        tokens_used=150,
        tokens_prompt=100,
        tokens_completion=50,
        latency_ms=234.5,
        cost_usd=0.0015,
        provider_used="openai",
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.tokens_used == 150
    assert result.error is None
    print("  [PASS] ExecutionResult")


def test_trace_event():
    """Test TraceEvent creation."""
    from backend.schemas.artifacts import TraceEvent, TraceEventType

    evt = TraceEvent(
        event_type=TraceEventType.NODE_START,
        run_id="run-001",
        node_id="researcher-1",
        data={"message": "Starting researcher node"},
    )
    assert evt.event_type == TraceEventType.NODE_START
    assert evt.event_id.startswith("evt-")
    print("  [PASS] TraceEvent")


def test_artifact():
    """Test Artifact creation."""
    from backend.schemas.artifacts import Artifact, ArtifactType

    art = Artifact(
        artifact_type=ArtifactType.CODE,
        name="generated_schema.py",
        content="class UserSchema(BaseModel): ...",
        producer_node_id="executor-1",
    )
    assert art.artifact_type == ArtifactType.CODE
    assert art.artifact_id.startswith("art-")
    print("  [PASS] Artifact")


def test_run_report():
    """Test RunReport creation with provider breakdown."""
    from backend.schemas.artifacts import ProviderCostBreakdown, RunReport

    report = RunReport(
        run_id="run-001",
        graph_id="graph-001",
        status="success",
        total_tokens=5000,
        total_cost_usd=0.05,
        total_latency_ms=3200.0,
        node_count=4,
        nodes_succeeded=4,
        provider_breakdown=[
            ProviderCostBreakdown(
                provider="openai",
                model="gpt-4o",
                tokens_prompt=3000,
                tokens_completion=2000,
                total_tokens=5000,
                cost_usd=0.05,
                call_count=3,
            ),
        ],
        goal_text="Audit API security",
    )
    assert report.total_tokens == 5000
    assert len(report.provider_breakdown) == 1
    assert report.provider_breakdown[0].provider == "openai"
    print("  [PASS] RunReport")


def test_benchmark_models():
    """Test BenchmarkTask and BenchmarkResult."""
    from backend.schemas.artifacts import (
        BenchmarkResult,
        BenchmarkTask,
        DifficultyTier,
    )

    task = BenchmarkTask(
        task_id="bench-001",
        source_dataset="AgentBench",
        goal_text="Generate a REST API schema for user management.",
        difficulty_tier=DifficultyTier.MEDIUM,
        category="code_gen",
    )
    assert task.source_dataset == "AgentBench"

    result = BenchmarkResult(
        task_id="bench-001",
        mode="ae03_dynamic",
        success=True,
        handoff_validity_pct=95.0,
        recovery_rate_pct=100.0,
        total_cost_usd=0.03,
        latency_ms=2100.0,
        total_tokens=3500,
    )
    assert result.success is True
    print("  [PASS] BenchmarkTask & BenchmarkResult")


def test_malformed_data_rejection():
    """Test that Pydantic rejects malformed data correctly."""
    from pydantic import ValidationError
    from backend.schemas.contracts import AgentConfig, ExecutionResult

    # AgentConfig: invalid token_budget (negative)
    try:
        AgentConfig(role="researcher", token_budget=-1)
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    # ExecutionResult: invalid status enum
    try:
        ExecutionResult(node_id="x", status="invalid_status")
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass

    print("  [PASS] Malformed data rejection")


# ── Run All Tests ──────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_app_settings,
        test_agent_config_all_roles,
        test_agent_config_sub_graph_validation,
        test_execution_graph,
        test_execution_graph_nested,
        test_agent_message,
        test_execution_result,
        test_trace_event,
        test_artifact,
        test_run_report,
        test_benchmark_models,
        test_malformed_data_rejection,
    ]

    print("=" * 60)
    print("MODULE 1 VERIFICATION — Contracts & Environment Vault")
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
        print("[ANTIGRAVITY STEP GATE 1]: Module 1 complete.")
        print("All Pydantic contracts, schemas, and environment vault configs")
        print("are initialized and verified. Please confirm with 'APPROVED'")
        print("to begin Module 2.")
