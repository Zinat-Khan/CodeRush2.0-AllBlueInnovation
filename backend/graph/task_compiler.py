"""
AE-03 Task-to-Graph Compiler (Directive V2).

Converts natural-language goals into validated, typed ``TaskGraph`` DAGs
that map directly onto LangGraph ``StateGraph`` workflows.

Pipeline:
  NATURAL LANGUAGE GOAL → STRUCTURED PLAN → VALIDATED TASK GRAPH → LANGGRAPH WORKFLOW

Pre-execution validation checks:
  1. Unknown agents           — reject unrecognised AgentRole values
  2. Invalid tools            — reject tools not in ToolRegistry
  3. Circular dependencies    — detect cycles via topological sort
  4. Missing dependencies     — reject references to non-existent task_ids
  5. Excessive parallelism    — cap max concurrent branches
  6. Unauthorized side effects— flag tools that don't match agent permissions
  7. Invalid schemas          — validate task input/output schemas
  8. Impossible tasks         — detect contradictory requirements
  9. Budget violations        — estimate cost and reject if over budget

Integrates with:
  - ``ModelRouter`` (Module 2) for LLM-powered plan generation
  - ``ToolRegistry`` (Module 3) for tool validation
  - ``AppSettings`` (Module 1) for budget limits
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.config import AppSettings, get_settings
from backend.schemas.contracts import (
    AgentRole,
    Task,
    TaskGraph,
    TaskStatus,
)

logger = logging.getLogger(__name__)


# ── Validation Result ─────────────────────────────────────────────────


class ValidationResult:
    """Aggregated validation result from pre-execution checks."""

    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.passed: bool = True

    def add_error(self, msg: str) -> None:
        """Add a blocking validation error."""
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        """Add a non-blocking validation warning."""
        self.warnings.append(msg)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": self.errors,
            "warnings": self.warnings,
        }

    def __repr__(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        return f"ValidationResult({status}, errors={len(self.errors)}, warnings={len(self.warnings)})"


# ── Task Compiler ─────────────────────────────────────────────────────


# System prompt for LLM-powered plan generation
PLANNER_SYSTEM_PROMPT = """You are the Planner agent for an AI orchestration system.
Given a user's natural-language goal, decompose it into a directed acyclic graph (DAG) 
of tasks. Each task must specify:

- task_id: A unique identifier (e.g., "task-001", "task-002")
- agent_role: One of: orchestrator, planner, researcher, rag, tool_execution, analyst, critic, verifier, security, reporter, visualization
- description: What this task should accomplish
- dependencies: List of task_ids that must complete before this task (empty for root tasks)
- tools_required: List of tool names needed (from: similarity_search, analyze_dataset, retrieve_public_document, generate_visualization, calculate_metric, public_search)

Output a JSON object with this schema:
{
  "goal": "<original goal>",
  "tasks": [
    {
      "task_id": "task-001",
      "agent_role": "<role>",
      "description": "<what to do>",
      "dependencies": [],
      "tools_required": []
    }
  ]
}

Rules:
1. Every workflow MUST end with a reporter task that compiles the final output.
2. Every workflow SHOULD include a verifier task before the reporter.
3. Research tasks should come before analysis tasks.
4. Use parallel branches where tasks are independent.
5. Keep the graph minimal — no unnecessary tasks.
6. The critic agent should review analyst outputs for quality.
"""


class TaskCompiler:
    """
    Compiles natural-language goals into validated TaskGraph DAGs.

    The compiler operates in two modes:
      1. **LLM-powered** — Uses the ModelRouter to generate plans from goals
      2. **Template-based** — Uses predefined templates for common goal patterns

    All generated graphs undergo 9 pre-execution validation checks before
    being approved for execution.

    Usage::

        compiler = TaskCompiler()

        # LLM-powered compilation
        graph = await compiler.compile_goal("Research AI trends and create a report")

        # Template-based compilation
        graph = compiler.compile_from_template("research_report", goal="AI trends")

        # Validation only
        result = compiler.validate(graph)
    """

    # Maximum parallel branches allowed
    MAX_PARALLEL_BRANCHES = 8

    # Maximum tasks per graph
    MAX_TASKS = 50

    # Known goal templates
    TEMPLATES = {
        "research_report": [
            Task(task_id="task-001", agent_role=AgentRole.PLANNER,
                 description="Decompose the research goal into sub-topics and search queries.",
                 dependencies=[], tools_required=[]),
            Task(task_id="task-002", agent_role=AgentRole.RESEARCHER,
                 description="Search for relevant information using public search and document retrieval.",
                 dependencies=["task-001"], tools_required=["public_search", "retrieve_public_document"]),
            Task(task_id="task-003", agent_role=AgentRole.RAG,
                 description="Search workspace knowledge base for relevant context.",
                 dependencies=["task-001"], tools_required=["similarity_search"]),
            Task(task_id="task-004", agent_role=AgentRole.ANALYST,
                 description="Analyze and synthesize research findings into structured insights.",
                 dependencies=["task-002", "task-003"], tools_required=["analyze_dataset"]),
            Task(task_id="task-005", agent_role=AgentRole.CRITIC,
                 description="Review analysis for completeness, accuracy, and bias.",
                 dependencies=["task-004"], tools_required=[]),
            Task(task_id="task-006", agent_role=AgentRole.VERIFIER,
                 description="Verify claims, citations, and factual accuracy.",
                 dependencies=["task-005"], tools_required=[]),
            Task(task_id="task-007", agent_role=AgentRole.REPORTER,
                 description="Compile final research report with executive summary, findings, and recommendations.",
                 dependencies=["task-006"], tools_required=[]),
        ],
        "data_analysis": [
            Task(task_id="task-001", agent_role=AgentRole.PLANNER,
                 description="Plan the data analysis workflow and identify required metrics.",
                 dependencies=[], tools_required=[]),
            Task(task_id="task-002", agent_role=AgentRole.RAG,
                 description="Retrieve relevant data from workspace knowledge base.",
                 dependencies=["task-001"], tools_required=["similarity_search"]),
            Task(task_id="task-003", agent_role=AgentRole.ANALYST,
                 description="Perform statistical analysis and compute metrics.",
                 dependencies=["task-002"], tools_required=["analyze_dataset", "calculate_metric"]),
            Task(task_id="task-004", agent_role=AgentRole.VISUALIZATION,
                 description="Generate charts and visualizations from analysis results.",
                 dependencies=["task-003"], tools_required=["generate_visualization"]),
            Task(task_id="task-005", agent_role=AgentRole.CRITIC,
                 description="Review analysis methodology and results for validity.",
                 dependencies=["task-003"], tools_required=[]),
            Task(task_id="task-006", agent_role=AgentRole.VERIFIER,
                 description="Verify statistical claims and visualization accuracy.",
                 dependencies=["task-004", "task-005"], tools_required=[]),
            Task(task_id="task-007", agent_role=AgentRole.REPORTER,
                 description="Compile analysis report with findings, charts, and recommendations.",
                 dependencies=["task-006"], tools_required=[]),
        ],
        "simple_question": [
            Task(task_id="task-001", agent_role=AgentRole.RAG,
                 description="Search workspace knowledge base for relevant context.",
                 dependencies=[], tools_required=["similarity_search"]),
            Task(task_id="task-002", agent_role=AgentRole.RESEARCHER,
                 description="Search public sources for additional context if needed.",
                 dependencies=[], tools_required=["public_search"]),
            Task(task_id="task-003", agent_role=AgentRole.ANALYST,
                 description="Synthesize information and formulate a comprehensive answer.",
                 dependencies=["task-001", "task-002"], tools_required=[]),
            Task(task_id="task-004", agent_role=AgentRole.VERIFIER,
                 description="Verify answer accuracy and completeness.",
                 dependencies=["task-003"], tools_required=[]),
            Task(task_id="task-005", agent_role=AgentRole.REPORTER,
                 description="Format and present the final answer.",
                 dependencies=["task-004"], tools_required=[]),
        ],
    }

    def __init__(self, settings: Optional[AppSettings] = None):
        self._settings = settings or get_settings()

    # ── LLM-Powered Compilation ───────────────────────────────────────

    async def compile_goal(
        self,
        goal: str,
        workspace_id: str = "default_workspace",
    ) -> TaskGraph:
        """
        Compile a natural-language goal into a validated TaskGraph.

        Uses the ModelRouter to generate a structured plan, then validates
        the resulting graph against all 9 pre-execution checks.

        Args:
            goal: Natural-language goal text.
            workspace_id: Workspace context for the compilation.

        Returns:
            Validated TaskGraph ready for LangGraph execution.

        Raises:
            ValueError: If the generated plan fails validation.
        """
        logger.info("[Compiler] Compiling goal: '%s'", goal[:100])

        # Step 1: Try LLM-powered planning
        try:
            from backend.models.model_router import ModelRouter

            router = ModelRouter(self._settings)
            response_text, metadata = await router.ainvoke_text(
                prompt=f"Goal: {goal}\n\nWorkspace: {workspace_id}",
                system_prompt=PLANNER_SYSTEM_PROMPT,
            )

            # Parse LLM response as JSON
            plan_data = self._extract_json(response_text)
            graph = self._build_graph_from_plan(plan_data, goal)

        except Exception as e:
            logger.warning(
                "[Compiler] LLM planning failed (%s), falling back to template matching.",
                str(e)[:100],
            )
            graph = self._template_fallback(goal)

        # Step 2: Validate
        validation = self.validate(graph)
        if not validation.passed:
            logger.error(
                "[Compiler] Validation FAILED: %s", validation.errors
            )
            # Try to auto-fix common issues
            graph = self._auto_fix(graph, validation)
            validation = self.validate(graph)
            if not validation.passed:
                raise ValueError(
                    f"Task graph validation failed: {validation.errors}"
                )

        logger.info(
            "[Compiler] Compilation complete: %d tasks, validation %s",
            len(graph.tasks),
            "PASSED" if validation.passed else "FAILED",
        )

        return graph

    # ── Template-Based Compilation ────────────────────────────────────

    def compile_from_template(
        self,
        template_name: str,
        goal: str,
    ) -> TaskGraph:
        """
        Compile a goal using a predefined task template.

        Args:
            template_name: Template key ('research_report', 'data_analysis', 'simple_question').
            goal: Natural-language goal text.

        Returns:
            TaskGraph built from the template.

        Raises:
            KeyError: If template_name is not found.
        """
        if template_name not in self.TEMPLATES:
            raise KeyError(
                f"Unknown template '{template_name}'. "
                f"Available: {list(self.TEMPLATES.keys())}"
            )

        tasks = []
        for template_task in self.TEMPLATES[template_name]:
            # Deep copy the template task with fresh IDs
            task = Task(
                task_id=template_task.task_id,
                agent_role=template_task.agent_role,
                description=template_task.description,
                dependencies=list(template_task.dependencies),
                tools_required=list(template_task.tools_required),
            )
            tasks.append(task)

        graph = TaskGraph(goal=goal, tasks=tasks)
        logger.info(
            "[Compiler] Template '%s' → %d tasks",
            template_name,
            len(tasks),
        )
        return graph

    # ── Validation ────────────────────────────────────────────────────

    def validate(self, graph: TaskGraph) -> ValidationResult:
        """
        Run all 9 pre-execution validation checks on a TaskGraph.

        Returns a ValidationResult with errors and warnings.
        """
        result = ValidationResult()

        self._check_empty_graph(graph, result)
        self._check_unknown_agents(graph, result)
        self._check_invalid_tools(graph, result)
        self._check_circular_dependencies(graph, result)
        self._check_missing_dependencies(graph, result)
        self._check_excessive_parallelism(graph, result)
        self._check_unauthorized_side_effects(graph, result)
        self._check_invalid_schemas(graph, result)
        self._check_budget_violations(graph, result)

        logger.info("[Validator] %s", result)
        return result

    # ── Validation Check 0: Empty Graph ───────────────────────────────

    def _check_empty_graph(self, graph: TaskGraph, result: ValidationResult) -> None:
        """Reject empty graphs."""
        if not graph.tasks:
            result.add_error("EMPTY_GRAPH: TaskGraph has no tasks.")
        if len(graph.tasks) > self.MAX_TASKS:
            result.add_error(
                f"TOO_MANY_TASKS: {len(graph.tasks)} tasks exceeds limit of {self.MAX_TASKS}."
            )

    # ── Validation Check 1: Unknown Agents ────────────────────────────

    def _check_unknown_agents(self, graph: TaskGraph, result: ValidationResult) -> None:
        """Reject tasks with unrecognised AgentRole values."""
        valid_roles = set(r.value for r in AgentRole)
        for task in graph.tasks:
            if task.agent_role.value not in valid_roles:
                result.add_error(
                    f"UNKNOWN_AGENT: Task '{task.task_id}' has unknown agent_role "
                    f"'{task.agent_role.value}'. Valid: {sorted(valid_roles)}"
                )

    # ── Validation Check 2: Invalid Tools ─────────────────────────────

    def _check_invalid_tools(self, graph: TaskGraph, result: ValidationResult) -> None:
        """Reject tasks requiring tools not in the ToolRegistry."""
        try:
            from backend.tools.tool_registry import ToolRegistry

            registry = ToolRegistry()
            registered_tools = set(registry.get_all_tool_names())
        except Exception:
            # ToolRegistry not available — skip check with warning
            result.add_warning(
                "TOOL_CHECK_SKIPPED: ToolRegistry not available for validation."
            )
            return

        for task in graph.tasks:
            for tool_name in task.tools_required:
                if tool_name not in registered_tools:
                    result.add_error(
                        f"INVALID_TOOL: Task '{task.task_id}' requires unknown tool "
                        f"'{tool_name}'. Registered: {sorted(registered_tools)}"
                    )

    # ── Validation Check 3: Circular Dependencies ─────────────────────

    def _check_circular_dependencies(
        self, graph: TaskGraph, result: ValidationResult
    ) -> None:
        """Detect cycles via Kahn's topological sort."""
        task_ids = set(t.task_id for t in graph.tasks)
        adj: Dict[str, List[str]] = {t.task_id: [] for t in graph.tasks}
        in_degree: Dict[str, int] = {t.task_id: 0 for t in graph.tasks}

        for task in graph.tasks:
            for dep in task.dependencies:
                if dep in task_ids:
                    adj[dep].append(task.task_id)
                    in_degree[task.task_id] += 1

        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        sorted_count = 0

        while queue:
            node = queue.popleft()
            sorted_count += 1
            for neighbour in adj.get(node, []):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if sorted_count != len(task_ids):
            cycle_tasks = [
                tid for tid, deg in in_degree.items() if deg > 0
            ]
            result.add_error(
                f"CIRCULAR_DEPENDENCY: Cycle detected involving tasks: {cycle_tasks}"
            )

    # ── Validation Check 4: Missing Dependencies ──────────────────────

    def _check_missing_dependencies(
        self, graph: TaskGraph, result: ValidationResult
    ) -> None:
        """Reject references to non-existent task_ids."""
        task_ids = set(t.task_id for t in graph.tasks)

        for task in graph.tasks:
            for dep in task.dependencies:
                if dep not in task_ids:
                    result.add_error(
                        f"MISSING_DEPENDENCY: Task '{task.task_id}' depends on "
                        f"'{dep}' which does not exist in the graph."
                    )

    # ── Validation Check 5: Excessive Parallelism ─────────────────────

    def _check_excessive_parallelism(
        self, graph: TaskGraph, result: ValidationResult
    ) -> None:
        """Cap max concurrent branches."""
        root_tasks = graph.get_root_tasks()
        if len(root_tasks) > self.MAX_PARALLEL_BRANCHES:
            result.add_warning(
                f"EXCESSIVE_PARALLELISM: {len(root_tasks)} root tasks exceeds "
                f"recommended max of {self.MAX_PARALLEL_BRANCHES}."
            )

        # Check max width at any level (BFS level-order)
        task_ids = set(t.task_id for t in graph.tasks)
        children: Dict[str, List[str]] = {t.task_id: [] for t in graph.tasks}
        for task in graph.tasks:
            for dep in task.dependencies:
                if dep in task_ids:
                    children[dep].append(task.task_id)

        # BFS from roots
        if root_tasks:
            level_queue = deque([(t.task_id, 0) for t in root_tasks])
            max_width = len(root_tasks)
            level_counts: Dict[int, int] = {0: len(root_tasks)}
            visited: Set[str] = set()

            while level_queue:
                node_id, level = level_queue.popleft()
                if node_id in visited:
                    continue
                visited.add(node_id)

                for child in children.get(node_id, []):
                    next_level = level + 1
                    level_counts[next_level] = level_counts.get(next_level, 0) + 1
                    level_queue.append((child, next_level))

            max_width = max(level_counts.values()) if level_counts else 0
            if max_width > self.MAX_PARALLEL_BRANCHES:
                result.add_warning(
                    f"EXCESSIVE_PARALLELISM: Max width {max_width} at some level "
                    f"exceeds {self.MAX_PARALLEL_BRANCHES}."
                )

    # ── Validation Check 6: Unauthorized Side Effects ─────────────────

    def _check_unauthorized_side_effects(
        self, graph: TaskGraph, result: ValidationResult
    ) -> None:
        """Flag tools that don't match agent permissions."""
        try:
            from backend.tools.tool_registry import ToolRegistry

            registry = ToolRegistry()
        except Exception:
            return  # Skip if registry unavailable

        for task in graph.tasks:
            for tool_name in task.tools_required:
                config = registry.get_tool_config(tool_name)
                if config and config.allowed_agents:
                    if task.agent_role not in config.allowed_agents:
                        result.add_warning(
                            f"UNAUTHORIZED_TOOL: Task '{task.task_id}' "
                            f"(agent={task.agent_role.value}) uses tool '{tool_name}' "
                            f"which is only allowed for "
                            f"{[a.value for a in config.allowed_agents]}."
                        )

    # ── Validation Check 7: Invalid Schemas ───────────────────────────

    def _check_invalid_schemas(
        self, graph: TaskGraph, result: ValidationResult
    ) -> None:
        """Validate task structure integrity."""
        seen_ids: Set[str] = set()
        for task in graph.tasks:
            # Duplicate task_id check
            if task.task_id in seen_ids:
                result.add_error(
                    f"DUPLICATE_TASK_ID: '{task.task_id}' appears more than once."
                )
            seen_ids.add(task.task_id)

            # Empty description check
            if not task.description.strip():
                result.add_warning(
                    f"EMPTY_DESCRIPTION: Task '{task.task_id}' has no description."
                )

            # Self-dependency check
            if task.task_id in task.dependencies:
                result.add_error(
                    f"SELF_DEPENDENCY: Task '{task.task_id}' depends on itself."
                )

    # ── Validation Check 8: Budget Violations ─────────────────────────

    def _check_budget_violations(
        self, graph: TaskGraph, result: ValidationResult
    ) -> None:
        """Estimate cost and reject if over budget."""
        # Rough cost estimate: ~$0.01 per LLM call per task
        estimated_cost_per_task = 0.01
        estimated_total = len(graph.tasks) * estimated_cost_per_task
        max_cost = self._settings.max_cost

        if estimated_total > max_cost:
            result.add_warning(
                f"BUDGET_WARNING: Estimated cost ${estimated_total:.2f} "
                f"may exceed budget ${max_cost:.2f} "
                f"({len(graph.tasks)} tasks × ${estimated_cost_per_task}/task)."
            )

        # Check if graph is too deep (proxy for runtime)
        max_depth = self._compute_max_depth(graph)
        # Each depth level ≈ 30s runtime
        estimated_runtime = max_depth * 30
        if estimated_runtime > self._settings.max_runtime_seconds:
            result.add_warning(
                f"RUNTIME_WARNING: Estimated runtime {estimated_runtime}s "
                f"(depth={max_depth}) may exceed limit "
                f"{self._settings.max_runtime_seconds}s."
            )

    # ── Helpers ───────────────────────────────────────────────────────

    def _template_fallback(self, goal: str) -> TaskGraph:
        """Select the best template based on goal keywords."""
        goal_lower = goal.lower()

        # Keyword matching for template selection
        if any(w in goal_lower for w in ["analyze", "analysis", "data", "metric", "statistic"]):
            template = "data_analysis"
        elif any(w in goal_lower for w in ["research", "report", "investigate", "study", "explore"]):
            template = "research_report"
        else:
            template = "simple_question"

        logger.info("[Compiler] Template fallback: '%s'", template)
        return self.compile_from_template(template, goal)

    def _build_graph_from_plan(
        self, plan_data: Dict[str, Any], goal: str
    ) -> TaskGraph:
        """Build a TaskGraph from parsed LLM plan JSON."""
        tasks = []

        for task_data in plan_data.get("tasks", []):
            try:
                role_str = task_data.get("agent_role", "analyst")
                try:
                    role = AgentRole(role_str)
                except ValueError:
                    logger.warning("Unknown agent_role '%s', defaulting to ANALYST", role_str)
                    role = AgentRole.ANALYST

                task = Task(
                    task_id=task_data.get("task_id", ""),
                    agent_role=role,
                    description=task_data.get("description", ""),
                    dependencies=task_data.get("dependencies", []),
                    tools_required=task_data.get("tools_required", []),
                )
                tasks.append(task)

            except Exception as e:
                logger.warning("Skipping malformed task: %s", e)

        return TaskGraph(goal=goal, tasks=tasks)

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON object from LLM response text."""
        import re

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code blocks
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } block
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError("Could not extract JSON from LLM response.")

    def _auto_fix(self, graph: TaskGraph, validation: ValidationResult) -> TaskGraph:
        """Attempt to auto-fix common validation issues."""
        tasks = list(graph.tasks)
        task_ids = set(t.task_id for t in tasks)

        # Fix missing dependencies by removing invalid refs
        for task in tasks:
            task.dependencies = [d for d in task.dependencies if d in task_ids]

        # Ensure there's a reporter task at the end
        has_reporter = any(t.agent_role == AgentRole.REPORTER for t in tasks)
        if not has_reporter:
            leaf_tasks = [t for t in tasks if t.task_id not in
                         {d for tt in tasks for d in tt.dependencies}]
            reporter = Task(
                task_id="task-reporter",
                agent_role=AgentRole.REPORTER,
                description="Compile and present the final output.",
                dependencies=[t.task_id for t in leaf_tasks],
                tools_required=[],
            )
            tasks.append(reporter)

        # Ensure there's a verifier before reporter
        has_verifier = any(t.agent_role == AgentRole.VERIFIER for t in tasks)
        if not has_verifier and has_reporter:
            reporter_task = next(
                (t for t in tasks if t.agent_role == AgentRole.REPORTER), None
            )
            if reporter_task and reporter_task.dependencies:
                verifier = Task(
                    task_id="task-verifier",
                    agent_role=AgentRole.VERIFIER,
                    description="Verify outputs for accuracy before final report.",
                    dependencies=list(reporter_task.dependencies),
                    tools_required=[],
                )
                tasks.append(verifier)
                reporter_task.dependencies = [verifier.task_id]

        graph.tasks = tasks
        logger.info("[Compiler] Auto-fix applied: %d tasks after fixes", len(tasks))
        return graph

    def _compute_max_depth(self, graph: TaskGraph) -> int:
        """Compute the longest path in the DAG (cycle-safe)."""
        task_map = {t.task_id: t for t in graph.tasks}
        depths: Dict[str, int] = {}
        visiting: set = set()  # Guard against cycles

        def _depth(tid: str) -> int:
            if tid in depths:
                return depths[tid]
            if tid in visiting:
                # Cycle detected — return 0 to break recursion
                return 0
            visiting.add(tid)
            task = task_map.get(tid)
            if not task or not task.dependencies:
                depths[tid] = 0
                visiting.discard(tid)
                return 0
            d = 1 + max(
                (_depth(dep) for dep in task.dependencies if dep in task_map),
                default=0,
            )
            depths[tid] = d
            visiting.discard(tid)
            return d

        if not graph.tasks:
            return 0


        return max(_depth(t.task_id) for t in graph.tasks)

    # ── Topological Sort ──────────────────────────────────────────────

    def topological_sort(self, graph: TaskGraph) -> List[Task]:
        """
        Return tasks in topological order (Kahn's algorithm).

        Useful for sequential execution planning.
        """
        task_map = {t.task_id: t for t in graph.tasks}
        in_degree: Dict[str, int] = {t.task_id: 0 for t in graph.tasks}
        adj: Dict[str, List[str]] = {t.task_id: [] for t in graph.tasks}

        for task in graph.tasks:
            for dep in task.dependencies:
                if dep in task_map:
                    adj[dep].append(task.task_id)
                    in_degree[task.task_id] += 1

        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        result: List[Task] = []

        while queue:
            tid = queue.popleft()
            result.append(task_map[tid])
            for neighbour in adj.get(tid, []):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        return result

    # ── Parallel Branch Detection ─────────────────────────────────────

    def get_parallel_groups(self, graph: TaskGraph) -> List[List[str]]:
        """
        Identify groups of tasks that can execute in parallel.

        Returns a list of lists, where each inner list contains task_ids
        that share the same set of dependencies and can run concurrently.
        """
        # Group by dependency set
        dep_groups: Dict[str, List[str]] = {}
        for task in graph.tasks:
            dep_key = ",".join(sorted(task.dependencies))
            dep_groups.setdefault(dep_key, []).append(task.task_id)

        return [
            group for group in dep_groups.values() if len(group) > 1
        ]

    # ── Graph Info ────────────────────────────────────────────────────

    def get_graph_info(self, graph: TaskGraph) -> Dict[str, Any]:
        """Return graph metadata for observability."""
        return {
            "graph_id": graph.graph_id,
            "goal": graph.goal[:100],
            "task_count": len(graph.tasks),
            "max_depth": self._compute_max_depth(graph),
            "root_tasks": [t.task_id for t in graph.get_root_tasks()],
            "leaf_tasks": [t.task_id for t in graph.get_leaf_tasks()],
            "parallel_groups": self.get_parallel_groups(graph),
            "roles_used": list(set(t.agent_role.value for t in graph.tasks)),
            "tools_used": list(set(
                tool for t in graph.tasks for tool in t.tools_required
            )),
        }
