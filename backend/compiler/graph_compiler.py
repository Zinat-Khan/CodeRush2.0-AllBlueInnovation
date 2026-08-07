"""
AE-03 Graph Compiler — Goal-to-DAG Compilation Engine.

Accepts a natural-language goal, calls the Planner LLM to produce a
validated ExecutionGraph DAG, and recursively compiles any nested
sub-graph nodes.

Returns a CompilationResult containing the main graph plus all
compiled sub-graphs keyed by their sub_graph_id.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.compiler.prompt_templates import (
    PLANNER_SYSTEM_PROMPT,
    SUB_GRAPH_SYSTEM_PROMPT,
)
from backend.compiler.validator import GraphValidator, ValidationError
from backend.providers.base import LLMResponse
from backend.providers.router import ProviderRouter
from backend.schemas.contracts import AgentConfig, AgentRole, ExecutionGraph

logger = logging.getLogger(__name__)


# ── Compilation Result ─────────────────────────────────────────────────


class CompilationResult(BaseModel):
    """Result of compiling a natural-language goal into execution graphs."""

    main_graph: ExecutionGraph = Field(
        description="The top-level execution graph.",
    )
    sub_graphs: Dict[str, ExecutionGraph] = Field(
        default_factory=dict,
        description="Map of sub_graph_id → compiled sub-graph.",
    )
    compilation_tokens: int = Field(
        default=0,
        description="Total tokens consumed during compilation.",
    )
    compilation_cost_usd: float = Field(
        default=0.0,
        description="Estimated cost of the compilation LLM calls.",
    )


# ── Graph Compiler ─────────────────────────────────────────────────────


class GraphCompiler:
    """
    Compiles natural-language goals into validated ExecutionGraph DAGs.

    Usage:
        compiler = GraphCompiler(provider_router)
        result = await compiler.compile_goal("Audit the REST API security")
    """

    MAX_SUB_GRAPH_DEPTH = 3  # Prevent infinite recursion

    def __init__(
        self,
        provider_router: ProviderRouter,
        default_provider: str = "openai",
        default_model: Optional[str] = None,
    ):
        self._router = provider_router
        self._default_provider = default_provider
        self._default_model = default_model

    async def compile_goal(
        self,
        goal: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        validate: bool = True,
        lock: bool = True,
    ) -> CompilationResult:
        """
        Compile a natural-language goal into an ExecutionGraph.

        Args:
            goal: The user's natural-language goal.
            provider: LLM provider to use for compilation.
            model: Override model name.
            validate: Run structural validation checks.
            lock: Lock the graph after validation.

        Returns:
            CompilationResult with main_graph and any sub_graphs.

        Raises:
            CompilationError: If the LLM output cannot be parsed.
            ValidationError: If the graph fails structural validation.
        """
        all_sub_graphs: Dict[str, ExecutionGraph] = {}
        total_tokens = 0
        total_cost = 0.0

        # Compile the main graph
        main_graph, tokens, cost = await self._compile_single(
            goal=goal,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            provider=provider or self._default_provider,
            model=model or self._default_model,
        )
        total_tokens += tokens
        total_cost += cost

        # Set metadata
        main_graph.metadata["goal"] = goal
        main_graph.metadata["compiled_by"] = "planner"

        # Recursively compile sub-graphs
        await self._compile_sub_graphs(
            graph=main_graph,
            sub_graphs=all_sub_graphs,
            depth=0,
            provider=provider or self._default_provider,
            model=model or self._default_model,
            token_accumulator={"tokens": total_tokens, "cost": total_cost},
        )
        total_tokens = token_accumulator_final = all_sub_graphs  # handled below

        # Validate
        if validate:
            GraphValidator.validate(
                main_graph,
                sub_graphs=all_sub_graphs,
                is_sub_graph=False,
            )
            for sg_id, sg in all_sub_graphs.items():
                GraphValidator.validate(
                    sg,
                    sub_graphs={},
                    is_sub_graph=True,
                )

        # Lock
        if lock:
            main_graph.lock()
            for sg in all_sub_graphs.values():
                sg.lock()

        return CompilationResult(
            main_graph=main_graph,
            sub_graphs=all_sub_graphs,
            compilation_tokens=total_tokens if isinstance(total_tokens, int) else 0,
            compilation_cost_usd=total_cost,
        )

    async def _compile_single(
        self,
        goal: str,
        system_prompt: str,
        provider: str,
        model: Optional[str],
    ) -> tuple[ExecutionGraph, int, float]:
        """
        Make a single LLM call to compile a goal into an ExecutionGraph.

        Returns:
            Tuple of (ExecutionGraph, tokens_used, estimated_cost).
        """
        response: LLMResponse = await self._router.call(
            prompt=goal,
            system_prompt=system_prompt,
            provider=provider,
            model=model,
            json_mode=True,
            temperature=0.4,  # Lower temperature for structured output
            max_tokens=4096,
        )

        graph = self._parse_graph_response(response, goal)

        from backend.providers.router import estimate_cost
        cost = estimate_cost(
            response.provider,
            response.model,
            response.tokens_prompt,
            response.tokens_completion,
        )

        return graph, response.total_tokens, cost

    async def _compile_sub_graphs(
        self,
        graph: ExecutionGraph,
        sub_graphs: Dict[str, ExecutionGraph],
        depth: int,
        provider: str,
        model: Optional[str],
        token_accumulator: Dict[str, float],
    ) -> None:
        """Recursively compile any sub_graph nodes found in the graph."""
        if depth >= self.MAX_SUB_GRAPH_DEPTH:
            logger.warning(
                "Max sub-graph depth (%d) reached; skipping further nesting.",
                self.MAX_SUB_GRAPH_DEPTH,
            )
            return

        for node_id, node in graph.nodes.items():
            if node.role == AgentRole.SUB_GRAPH and node.sub_graph_id:
                if node.sub_graph_id in sub_graphs:
                    continue  # Already compiled

                sub_goal = (
                    node.system_prompt
                    or f"Implement sub-workflow: {node.sub_graph_id}"
                )

                sub_graph, tokens, cost = await self._compile_single(
                    goal=sub_goal,
                    system_prompt=SUB_GRAPH_SYSTEM_PROMPT,
                    provider=provider,
                    model=model,
                )
                sub_graph.graph_id = node.sub_graph_id
                sub_graph.parent_graph_id = graph.graph_id
                sub_graphs[node.sub_graph_id] = sub_graph

                token_accumulator["tokens"] = (
                    token_accumulator.get("tokens", 0) + tokens
                )
                token_accumulator["cost"] = (
                    token_accumulator.get("cost", 0.0) + cost
                )

                # Recurse into the sub-graph (though SUB_GRAPH_SYSTEM_PROMPT
                # instructs the LLM not to use sub_graph role, we check anyway)
                await self._compile_sub_graphs(
                    graph=sub_graph,
                    sub_graphs=sub_graphs,
                    depth=depth + 1,
                    provider=provider,
                    model=model,
                    token_accumulator=token_accumulator,
                )

    def _parse_graph_response(
        self, response: LLMResponse, goal: str
    ) -> ExecutionGraph:
        """
        Parse LLM response content into an ExecutionGraph.

        Handles both pre-parsed JSON (from json_mode) and raw text that
        may include markdown code fences.
        """
        # Try pre-parsed JSON first
        data = response.parsed_json
        if not data:
            raw = response.content.strip()
            # Strip markdown code fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise CompilationError(
                    f"Failed to parse LLM response as JSON: {e}\n"
                    f"Raw content: {raw[:500]}"
                ) from e

        if not isinstance(data, dict):
            raise CompilationError(
                f"Expected JSON object, got {type(data).__name__}."
            )

        # Build nodes
        nodes: Dict[str, AgentConfig] = {}
        raw_nodes = data.get("nodes", {})
        for node_id, node_data in raw_nodes.items():
            try:
                # Ensure agent_id matches the key
                node_data["agent_id"] = node_id
                nodes[node_id] = AgentConfig(**node_data)
            except Exception as e:
                raise CompilationError(
                    f"Failed to parse node '{node_id}': {e}"
                ) from e

        # Build edges
        edges: List[tuple[str, str]] = []
        raw_edges = data.get("edges", [])
        for edge in raw_edges:
            if isinstance(edge, (list, tuple)) and len(edge) == 2:
                edges.append((str(edge[0]), str(edge[1])))
            else:
                raise CompilationError(
                    f"Invalid edge format: {edge}. Expected [source, target]."
                )

        return ExecutionGraph(
            graph_id=data.get("graph_id", ""),
            version=data.get("version", "1.0.0"),
            nodes=nodes,
            edges=edges,
            metadata=data.get("metadata", {"goal": goal}),
        )


# ── Compilation Error ──────────────────────────────────────────────────


class CompilationError(Exception):
    """Raised when the compiler fails to produce a valid graph."""
    pass
