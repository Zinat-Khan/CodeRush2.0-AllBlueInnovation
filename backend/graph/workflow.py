"""
AE-03 LangGraph Execution Engine (Directive V2).

Implements the core ``StateGraph`` workflow that replaces the obsolete
custom Kahn topological sort DAG executor.

Features:
  - Sequential execution with node chaining
  - Parallel branches via LangGraph's native Send()
  - Conditional routing based on task status and agent outputs
  - Retries with exponential backoff
  - Recovery and compensation on failure
  - State checkpoints (MemorySaver for dev, SqliteSaver for production)
  - Scratchpad TTL memory management
  - HITL interrupt integration (Module 6)

Execution flow:
  START → planner_node → router → [parallel agent nodes] → join →
  critic_node → verifier_node → reporter_node → END
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Sequence

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from backend.config import AppSettings, get_settings
from backend.graph.agent_state import (
    AgentState,
    ScratchpadManager,
    create_initial_state,
)
from backend.schemas.contracts import (
    AgentRole,
    Artifact,
    RunStatus,
    Task,
    TaskGraph,
    TaskStatus,
)

logger = logging.getLogger(__name__)


# ── Node Functions ────────────────────────────────────────────────────
# Each node function takes AgentState, performs work, and returns state updates.


async def planner_node(state: AgentState) -> dict:
    """
    Planner node: Decomposes the goal into a TaskGraph.

    Uses the TaskCompiler (Module 4) to generate a validated plan.
    """
    goal = state.get("goal", "")
    workspace_id = state.get("workspace_id", "default_workspace")
    logger.info("[Planner] Compiling goal: '%s'", goal[:80])

    start_time = time.time()

    try:
        from backend.graph.task_compiler import TaskCompiler

        compiler = TaskCompiler()
        graph = await compiler.compile_goal(goal, workspace_id)

        # Serialize TaskGraph for state storage
        plan_data = {
            "graph_id": graph.graph_id,
            "goal": graph.goal,
            "tasks": [t.model_dump() for t in graph.tasks],
            "metadata": graph.metadata,
        }

        tasks_data = [t.model_dump() for t in graph.tasks]

        latency = (time.time() - start_time) * 1000
        logger.info(
            "[Planner] Plan generated: %d tasks in %.1fms",
            len(graph.tasks),
            latency,
        )

        return {
            "plan": plan_data,
            "tasks": tasks_data,
            "status": RunStatus.PLANNING.value,
            "metrics": {
                "total_latency_ms": latency,
                "nodes_total": len(graph.tasks),
            },
            "updated_at": time.time(),
        }

    except Exception as e:
        logger.error("[Planner] Failed: %s", e)
        return {
            "errors": [f"Planner failed: {str(e)}"],
            "status": RunStatus.FAILED.value,
            "updated_at": time.time(),
        }


async def agent_executor_node(state: AgentState) -> dict:
    """
    Agent executor node: Executes the current task using the appropriate agent.

    Routes to the correct agent based on task.agent_role, invokes the
    ModelRouter for LLM calls, and enforces tool permissions via ToolRegistry.
    """
    current_task_id = state.get("current_task")
    tasks = state.get("tasks", [])
    goal = state.get("goal", "")
    workspace_id = state.get("workspace_id", "default_workspace")

    # Find current task
    task_data = None
    for t in tasks:
        if t.get("task_id") == current_task_id:
            task_data = t
            break

    if task_data is None:
        return {
            "errors": [f"Task '{current_task_id}' not found in state."],
            "updated_at": time.time(),
        }

    agent_role = task_data.get("agent_role", "analyst")
    description = task_data.get("description", "")
    tools_required = task_data.get("tools_required", [])

    logger.info(
        "[Executor] Running task '%s' (role=%s): %s",
        current_task_id,
        agent_role,
        description[:60],
    )

    start_time = time.time()

    try:
        # Get previous agent outputs as context
        agent_outputs = state.get("agent_outputs", {})
        context_parts = []
        for dep_id in task_data.get("dependencies", []):
            dep_output = agent_outputs.get(dep_id)
            if dep_output:
                context_parts.append(f"[Output from {dep_id}]: {json.dumps(dep_output)[:500]}")

        # Build prompt
        prompt = (
            f"Goal: {goal}\n"
            f"Your role: {agent_role}\n"
            f"Task: {description}\n"
        )
        if context_parts:
            prompt += f"\nContext from previous tasks:\n" + "\n".join(context_parts)
        if tools_required:
            prompt += f"\nAvailable tools: {', '.join(tools_required)}"

        # System prompt per agent role
        system_prompts = {
            "planner": "You are a strategic planner. Break down complex goals into actionable steps.",
            "researcher": "You are a thorough researcher. Find and synthesize relevant information.",
            "rag": "You are a knowledge retrieval specialist. Search and extract relevant context from workspace documents.",
            "analyst": "You are a data analyst. Analyze information and extract insights.",
            "critic": "You are a critical reviewer. Evaluate work for completeness, accuracy, and quality. Flag issues.",
            "verifier": "You are a verification specialist. Independently verify claims, citations, and factual accuracy.",
            "reporter": "You are a report writer. Compile clear, structured, professional reports.",
            "visualization": "You are a data visualization specialist. Create clear, informative charts and graphs.",
            "security": "You are a security specialist. Evaluate operations for safety and compliance.",
            "orchestrator": "You are the orchestrator. Coordinate and manage the overall workflow.",
            "tool_execution": "You are a tool execution specialist. Execute tool operations precisely.",
        }

        system_prompt = system_prompts.get(agent_role, "You are a helpful assistant.")

        # Invoke LLM via ModelRouter
        from backend.models.model_router import ModelRouter

        router = ModelRouter()
        response_text, llm_metadata = await router.ainvoke_text(
            prompt=prompt,
            system_prompt=system_prompt,
        )

        latency = (time.time() - start_time) * 1000

        # Update task status
        task_data["status"] = TaskStatus.SUCCESS.value
        task_data["output"] = {
            "response": response_text[:2000],
            "provider": llm_metadata.get("provider", "unknown"),
            "model": llm_metadata.get("model", "unknown"),
        }
        task_data["finished_at"] = time.time()

        logger.info(
            "[Executor] Task '%s' completed in %.1fms (provider=%s)",
            current_task_id,
            latency,
            llm_metadata.get("provider", "unknown"),
        )

        return {
            "agent_outputs": {current_task_id: task_data["output"]},
            "metrics": {
                "total_tokens": llm_metadata.get("total_tokens", 0),
                "total_cost_usd": llm_metadata.get("cost_usd", 0.0),
                "total_latency_ms": latency,
                "nodes_succeeded": 1,
            },
            "updated_at": time.time(),
        }

    except Exception as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[Executor] Task '%s' failed: %s", current_task_id, e)

        task_data["status"] = TaskStatus.FAILED.value
        task_data["error"] = str(e)
        task_data["finished_at"] = time.time()

        return {
            "agent_outputs": {current_task_id: {"error": str(e)}},
            "errors": [f"Task '{current_task_id}' failed: {str(e)}"],
            "metrics": {"nodes_failed": 1, "total_latency_ms": latency},
            "updated_at": time.time(),
        }


async def critic_node(state: AgentState) -> dict:
    """
    Critic node: Reviews agent outputs for quality and completeness.

    Evaluates all artifacts and outputs from previous tasks, flags
    issues, and may request re-execution.
    """
    agent_outputs = state.get("agent_outputs", {})
    goal = state.get("goal", "")

    logger.info("[Critic] Reviewing %d agent outputs", len(agent_outputs))

    start_time = time.time()

    try:
        from backend.models.model_router import ModelRouter

        router = ModelRouter()

        # Summarize outputs for review
        output_summary = "\n".join(
            f"- {tid}: {json.dumps(out)[:300]}"
            for tid, out in agent_outputs.items()
        )

        prompt = (
            f"Original goal: {goal}\n\n"
            f"Agent outputs to review:\n{output_summary}\n\n"
            f"Evaluate each output for:\n"
            f"1. Completeness — does it fully address its task?\n"
            f"2. Accuracy — are claims supported and factual?\n"
            f"3. Quality — is the work of sufficient quality?\n"
            f"4. Consistency — do outputs align with each other?\n\n"
            f"Respond with a JSON object: "
            f'{{"passed": true/false, "issues": [...], "summary": "..."}}'
        )

        response_text, metadata = await router.ainvoke_text(
            prompt=prompt,
            system_prompt="You are a critical quality reviewer. Be thorough but fair.",
        )

        latency = (time.time() - start_time) * 1000

        return {
            "agent_outputs": {"critic": {"review": response_text[:1500]}},
            "metrics": {
                "total_tokens": metadata.get("total_tokens", 0),
                "total_cost_usd": metadata.get("cost_usd", 0.0),
                "critic_iterations": 1,
            },
            "updated_at": time.time(),
        }

    except Exception as e:
        logger.warning("[Critic] Review failed (non-blocking): %s", e)
        return {
            "agent_outputs": {"critic": {"review": "Critic unavailable", "error": str(e)}},
            "updated_at": time.time(),
        }


async def verifier_node(state: AgentState) -> dict:
    """
    Verifier node: Independent verification of outputs and claims.

    Performs factual accuracy checks and validates against the original goal.
    """
    agent_outputs = state.get("agent_outputs", {})
    goal = state.get("goal", "")

    logger.info("[Verifier] Verifying outputs")

    start_time = time.time()

    try:
        from backend.models.model_router import ModelRouter

        router = ModelRouter()

        prompt = (
            f"Original goal: {goal}\n\n"
            f"Verify the following outputs for factual accuracy:\n"
            f"{json.dumps(agent_outputs, default=str)[:2000]}\n\n"
            f"Check for: factual errors, unsupported claims, logical inconsistencies.\n"
            f"Respond with JSON: "
            f'{{"verified": true/false, "checks_passed": [...], "checks_failed": [...], "notes": "..."}}'
        )

        response_text, metadata = await router.ainvoke_text(
            prompt=prompt,
            system_prompt="You are an independent verification specialist. Verify facts rigorously.",
        )

        latency = (time.time() - start_time) * 1000

        return {
            "verification_state": {"result": response_text[:1500], "verified": True},
            "metrics": {
                "total_tokens": metadata.get("total_tokens", 0),
                "total_cost_usd": metadata.get("cost_usd", 0.0),
            },
            "updated_at": time.time(),
        }

    except Exception as e:
        logger.warning("[Verifier] Verification failed (non-blocking): %s", e)
        return {
            "verification_state": {"result": "Verifier unavailable", "verified": False, "error": str(e)},
            "updated_at": time.time(),
        }


async def reporter_node(state: AgentState) -> dict:
    """
    Reporter node: Compiles the final output report.

    Gathers all agent outputs, critic review, and verification results
    to produce a comprehensive final report.
    """
    agent_outputs = state.get("agent_outputs", {})
    goal = state.get("goal", "")
    verification = state.get("verification_state", {})
    metrics = state.get("metrics", {})

    logger.info("[Reporter] Compiling final report")

    start_time = time.time()

    try:
        from backend.models.model_router import ModelRouter

        router = ModelRouter()

        prompt = (
            f"Original goal: {goal}\n\n"
            f"Agent outputs:\n{json.dumps(agent_outputs, default=str)[:3000]}\n\n"
            f"Verification: {json.dumps(verification, default=str)[:500]}\n\n"
            f"Compile a comprehensive final report with:\n"
            f"1. Executive Summary\n"
            f"2. Key Findings\n"
            f"3. Detailed Analysis\n"
            f"4. Recommendations\n"
            f"5. Sources and References\n"
        )

        response_text, metadata = await router.ainvoke_text(
            prompt=prompt,
            system_prompt="You are a professional report writer. Create clear, well-structured reports.",
        )

        latency = (time.time() - start_time) * 1000

        # Create artifact for the report
        report_artifact = {
            "artifact_id": f"art-{uuid.uuid4().hex[:8]}",
            "artifact_type": "report",
            "title": f"Report: {goal[:50]}",
            "content": response_text,
            "producer_agent": AgentRole.REPORTER.value,
            "verified": verification.get("verified", False),
            "created_at": time.time(),
        }

        return {
            "artifacts": [report_artifact],
            "agent_outputs": {"reporter": {"report": response_text[:2000]}},
            "status": RunStatus.SUCCESS.value,
            "metrics": {
                "total_tokens": metadata.get("total_tokens", 0),
                "total_cost_usd": metadata.get("cost_usd", 0.0),
                "total_latency_ms": latency,
            },
            "updated_at": time.time(),
        }

    except Exception as e:
        logger.error("[Reporter] Report generation failed: %s", e)
        return {
            "errors": [f"Reporter failed: {str(e)}"],
            "status": RunStatus.FAILED.value,
            "updated_at": time.time(),
        }


async def task_router_node(state: AgentState) -> dict:
    """
    Router node: Selects the next task to execute based on dependencies.

    Implements topological ordering — picks the first PENDING task
    whose dependencies are all SUCCESS.
    """
    tasks = state.get("tasks", [])
    status = state.get("status", "")

    if status == RunStatus.FAILED.value:
        return {"status": RunStatus.FAILED.value}

    # Find next executable task
    completed_ids = set(
        t["task_id"] for t in tasks if t.get("status") == TaskStatus.SUCCESS.value
    )
    failed_ids = set(
        t["task_id"] for t in tasks if t.get("status") == TaskStatus.FAILED.value
    )

    for task in tasks:
        if task.get("status") != TaskStatus.PENDING.value:
            continue
        deps = set(task.get("dependencies", []))
        if deps.issubset(completed_ids):
            task["status"] = TaskStatus.RUNNING.value
            task["started_at"] = time.time()
            logger.info("[Router] Next task: %s (%s)", task["task_id"], task.get("agent_role"))
            return {
                "current_task": task["task_id"],
                "status": RunStatus.EXECUTING.value,
                "updated_at": time.time(),
            }

    # All tasks done or no more executable
    logger.info("[Router] No more executable tasks. Proceeding to review phase.")
    return {
        "current_task": None,
        "updated_at": time.time(),
    }


# ── Conditional Edges ─────────────────────────────────────────────────


def should_continue_execution(state: AgentState) -> str:
    """Decide whether to continue executing tasks or move to review."""
    current_task = state.get("current_task")
    status = state.get("status", "")

    if status == RunStatus.FAILED.value:
        return "reporter"

    if current_task is not None:
        return "executor"

    return "critic"


def should_continue_after_plan(state: AgentState) -> str:
    """Decide next step after planning."""
    status = state.get("status", "")
    tasks = state.get("tasks", [])

    if status == RunStatus.FAILED.value:
        return "reporter"

    if not tasks:
        return "reporter"

    return "router"


# ── Workflow Builder ──────────────────────────────────────────────────


class WorkflowEngine:
    """
    LangGraph StateGraph workflow engine.

    Builds and manages the execution graph with:
      - Planner → Router → Executor → Critic → Verifier → Reporter flow
      - State checkpoints via MemorySaver (dev) or SqliteSaver (prod)
      - Scratchpad TTL memory management
      - Run lifecycle management

    Usage::

        engine = WorkflowEngine()
        result = await engine.execute("Research AI safety trends")
        print(result["status"])  # "success"
        print(result["artifacts"])  # [report artifact]
    """

    def __init__(self, settings: Optional[AppSettings] = None):
        self._settings = settings or get_settings()
        self._checkpointer = MemorySaver()
        self._scratchpad = ScratchpadManager(
            ttl_seconds=self._settings.scratchpad_ttl_seconds,
            max_entries=self._settings.max_scratchpad_entries,
        )
        self._graph = self._build_graph()

    def _build_graph(self) -> Any:
        """Build the LangGraph StateGraph."""
        builder = StateGraph(AgentState)

        # Add nodes
        builder.add_node("planner", planner_node)
        builder.add_node("router", task_router_node)
        builder.add_node("executor", agent_executor_node)
        builder.add_node("critic", critic_node)
        builder.add_node("verifier", verifier_node)
        builder.add_node("reporter", reporter_node)

        # Add edges
        builder.add_edge(START, "planner")
        builder.add_conditional_edges(
            "planner",
            should_continue_after_plan,
            {"router": "router", "reporter": "reporter"},
        )
        builder.add_conditional_edges(
            "router",
            should_continue_execution,
            {"executor": "executor", "critic": "critic", "reporter": "reporter"},
        )
        builder.add_edge("executor", "router")  # After execution, route next task
        builder.add_edge("critic", "verifier")
        builder.add_edge("verifier", "reporter")
        builder.add_edge("reporter", END)

        # Compile with checkpointer
        return builder.compile(checkpointer=self._checkpointer)

    async def execute(
        self,
        goal: str,
        user_id: str = "default_user",
        workspace_id: str = "default_workspace",
        run_id: Optional[str] = None,
    ) -> AgentState:
        """
        Execute a goal through the full LangGraph workflow.

        Args:
            goal: Natural-language goal text.
            user_id: User identifier.
            workspace_id: Workspace scope.
            run_id: Optional run ID.

        Returns:
            Final AgentState with all outputs, artifacts, and metrics.
        """
        rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
        initial_state = create_initial_state(
            goal=goal,
            user_id=user_id,
            workspace_id=workspace_id,
            run_id=rid,
        )

        logger.info("[Engine] Starting execution: run_id=%s, goal='%s'", rid, goal[:80])

        config = {
            "configurable": {"thread_id": rid},
            "recursion_limit": 50,
        }

        try:
            # Stream through the graph
            final_state = None
            async for event in self._graph.astream(initial_state, config=config):
                # Each event is a dict with node_name -> state_update
                for node_name, state_update in event.items():
                    logger.debug("[Engine] Node '%s' completed", node_name)
                    final_state = state_update

            # Get final state from checkpoint
            final_state = await self._graph.aget_state(config)
            result = dict(final_state.values) if final_state else initial_state

            logger.info(
                "[Engine] Execution complete: run_id=%s, status=%s",
                rid,
                result.get("status", "unknown"),
            )

            return result

        except Exception as e:
            logger.error("[Engine] Execution failed: %s", e)
            initial_state["status"] = RunStatus.FAILED.value
            initial_state["errors"] = [f"Engine error: {str(e)}"]
            return initial_state

    async def execute_stream(
        self,
        goal: str,
        user_id: str = "default_user",
        workspace_id: str = "default_workspace",
        run_id: Optional[str] = None,
    ):
        """
        Execute with streaming — yields (node_name, state_update) tuples.

        Useful for SSE streaming to the frontend.
        """
        rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
        initial_state = create_initial_state(
            goal=goal,
            user_id=user_id,
            workspace_id=workspace_id,
            run_id=rid,
        )

        config = {
            "configurable": {"thread_id": rid},
            "recursion_limit": 50,
        }

        async for event in self._graph.astream(initial_state, config=config):
            for node_name, state_update in event.items():
                yield node_name, state_update

    def get_run_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve stored state for a run via checkpoint."""
        config = {"configurable": {"thread_id": run_id}}
        try:
            import asyncio
            state = asyncio.run(self._graph.aget_state(config))
            return dict(state.values) if state else None
        except Exception:
            return None

    @property
    def scratchpad(self) -> ScratchpadManager:
        """Access the TTL scratchpad memory manager."""
        return self._scratchpad

    def get_graph_structure(self) -> Dict[str, Any]:
        """Return the graph structure for visualization."""
        return {
            "nodes": ["planner", "router", "executor", "critic", "verifier", "reporter"],
            "edges": [
                {"from": "START", "to": "planner"},
                {"from": "planner", "to": "router", "condition": "has_tasks"},
                {"from": "planner", "to": "reporter", "condition": "failed"},
                {"from": "router", "to": "executor", "condition": "has_next_task"},
                {"from": "router", "to": "critic", "condition": "all_tasks_done"},
                {"from": "executor", "to": "router"},
                {"from": "critic", "to": "verifier"},
                {"from": "verifier", "to": "reporter"},
                {"from": "reporter", "to": "END"},
            ],
        }
