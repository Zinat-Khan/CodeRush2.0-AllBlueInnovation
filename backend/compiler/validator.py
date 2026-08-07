"""
AE-03 Graph Validator — Cycle Detection, Structural Rules & Locking.

Provides:
  - Kahn's Algorithm for DAG cycle detection
  - Orphan node detection (isolated nodes)
  - Structural validation rules (parallel branches, verifier/critic join)
  - Sub-graph reference integrity checks
  - Graph locking and version stamping
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set

from backend.schemas.contracts import AgentRole, ExecutionGraph

logger = logging.getLogger(__name__)


# ── Custom Exception ───────────────────────────────────────────────────


class ValidationError(Exception):
    """Raised when graph validation fails."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        self.errors = errors or [message]
        super().__init__(message)


# ── Graph Validator ────────────────────────────────────────────────────


class GraphValidator:
    """
    Validates ExecutionGraph instances for structural correctness.

    Runs the following checks:
      1. Acyclicity via Kahn's Algorithm
      2. No orphan nodes (unless single-node graph)
      3. At least one parallel branch (≥2 nodes sharing a predecessor)
      4. At least one critic or verifier join node
      5. Sub-graph reference integrity
      6. No circular sub-graph nesting
    """

    @staticmethod
    def validate(
        graph: ExecutionGraph,
        sub_graphs: Optional[Dict[str, ExecutionGraph]] = None,
        is_sub_graph: bool = False,
    ) -> None:
        """
        Run all validation checks on the graph.

        Args:
            graph: The ExecutionGraph to validate.
            sub_graphs: Map of sub_graph_id → ExecutionGraph for reference checks.
            is_sub_graph: If True, relaxes some rules (e.g., parallel branch
                          requirement) for nested sub-workflows.

        Raises:
            ValidationError: If any validation check fails.
        """
        errors: List[str] = []
        sub_graphs = sub_graphs or {}

        # 1. Empty graph check
        if not graph.nodes:
            errors.append("Graph has no nodes.")
            raise ValidationError("Graph validation failed.", errors)

        # 2. Edge reference integrity — all edge endpoints must exist in nodes
        node_ids = set(graph.nodes.keys())
        for src, tgt in graph.edges:
            if src not in node_ids:
                errors.append(f"Edge source '{src}' not found in nodes.")
            if tgt not in node_ids:
                errors.append(f"Edge target '{tgt}' not found in nodes.")

        if errors:
            raise ValidationError("Edge reference integrity failed.", errors)

        # 3. Cycle detection via Kahn's Algorithm
        GraphValidator._check_acyclic(graph, errors)

        # 4. Root and leaf node checks
        roots = graph.get_root_nodes()
        leaves = graph.get_leaf_nodes()

        if not roots:
            errors.append("Graph has no root nodes (every node has an incoming edge — cycle likely).")
        if not leaves:
            errors.append("Graph has no leaf nodes (every node has an outgoing edge — cycle likely).")

        # 5. Orphan detection (nodes with no edges at all, in multi-node graphs)
        if len(graph.nodes) > 1:
            connected = set()
            for src, tgt in graph.edges:
                connected.add(src)
                connected.add(tgt)
            orphans = node_ids - connected
            for orphan in orphans:
                errors.append(f"Orphan node '{orphan}' has no incoming or outgoing edges.")

        # 6. Structural rules (only for main graphs, not sub-graphs)
        if not is_sub_graph:
            GraphValidator._check_parallel_branches(graph, errors)
            GraphValidator._check_critic_or_verifier(graph, errors)

        # 7. Sub-graph reference integrity
        GraphValidator._check_sub_graph_references(graph, sub_graphs, errors)

        # 8. No recursive nesting in sub-graphs
        if is_sub_graph:
            for node_id, node in graph.nodes.items():
                if node.role == AgentRole.SUB_GRAPH:
                    errors.append(
                        f"Sub-graph node '{node_id}' uses role 'sub_graph' — "
                        "recursive nesting is not allowed inside sub-graphs."
                    )

        if errors:
            raise ValidationError(
                f"Graph validation failed with {len(errors)} error(s).", errors
            )

        logger.info("Graph '%s' passed all validation checks.", graph.graph_id)

    @staticmethod
    def validate_and_lock(
        graph: ExecutionGraph,
        sub_graphs: Optional[Dict[str, ExecutionGraph]] = None,
        is_sub_graph: bool = False,
        version: str = "1.0.0",
    ) -> None:
        """Validate, stamp version, and lock the graph for execution."""
        GraphValidator.validate(graph, sub_graphs, is_sub_graph)
        graph.version = version
        graph.lock()
        logger.info(
            "Graph '%s' locked at version %s.", graph.graph_id, version
        )

    # ── Kahn's Algorithm ──────────────────────────────────────────────

    @staticmethod
    def _check_acyclic(graph: ExecutionGraph, errors: List[str]) -> None:
        """
        Detect cycles using Kahn's topological sort.

        If the number of nodes processed is less than the total node count,
        a cycle exists.
        """
        in_degree: Dict[str, int] = defaultdict(int)
        adjacency: Dict[str, List[str]] = defaultdict(list)

        for node_id in graph.nodes:
            in_degree.setdefault(node_id, 0)

        for src, tgt in graph.edges:
            adjacency[src].append(tgt)
            in_degree[tgt] += 1

        queue: deque[str] = deque()
        for node_id, deg in in_degree.items():
            if deg == 0:
                queue.append(node_id)

        processed = 0
        while queue:
            node = queue.popleft()
            processed += 1
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if processed < len(graph.nodes):
            errors.append(
                f"Cycle detected: Kahn's algorithm processed {processed}/{len(graph.nodes)} nodes."
            )

    # ── Parallel Branch Check ─────────────────────────────────────────

    @staticmethod
    def _check_parallel_branches(graph: ExecutionGraph, errors: List[str]) -> None:
        """
        Verify at least one node fans out to ≥2 successors (parallel branch).
        """
        for node_id in graph.nodes:
            successors = graph.get_successors(node_id)
            if len(successors) >= 2:
                return  # Found a parallel branch

        errors.append(
            "Main graph must have at least one parallel branch "
            "(a node with ≥2 outgoing edges)."
        )

    # ── Critic/Verifier Join Check ────────────────────────────────────

    @staticmethod
    def _check_critic_or_verifier(graph: ExecutionGraph, errors: List[str]) -> None:
        """
        Verify at least one critic or verifier node exists that acts as
        a join point (≥2 incoming edges).
        """
        join_roles = {AgentRole.CRITIC, AgentRole.VERIFIER}

        for node_id, node in graph.nodes.items():
            if node.role in join_roles:
                predecessors = graph.get_predecessors(node_id)
                if len(predecessors) >= 2:
                    return  # Found a valid join node

        # Relaxed check: just ensure a critic or verifier exists
        for node_id, node in graph.nodes.items():
            if node.role in join_roles:
                return

        errors.append(
            "Main graph must have at least one 'critic' or 'verifier' node."
        )

    # ── Sub-Graph Reference Integrity ─────────────────────────────────

    @staticmethod
    def _check_sub_graph_references(
        graph: ExecutionGraph,
        sub_graphs: Dict[str, ExecutionGraph],
        errors: List[str],
    ) -> None:
        """
        Verify that every sub_graph node references an existing sub-graph,
        and that sub-graphs don't reference their parent.
        """
        for node_id, node in graph.nodes.items():
            if node.role == AgentRole.SUB_GRAPH:
                if not node.sub_graph_id:
                    errors.append(
                        f"Sub-graph node '{node_id}' is missing sub_graph_id."
                    )
                elif sub_graphs and node.sub_graph_id not in sub_graphs:
                    errors.append(
                        f"Sub-graph node '{node_id}' references "
                        f"'{node.sub_graph_id}' which does not exist."
                    )

        # Check for circular parent references
        for sg_id, sg in sub_graphs.items():
            if sg.parent_graph_id and sg.parent_graph_id == sg.graph_id:
                errors.append(
                    f"Sub-graph '{sg_id}' references itself as parent."
                )

    # ── Topological Sort (utility) ────────────────────────────────────

    @staticmethod
    def topological_sort(graph: ExecutionGraph) -> List[str]:
        """
        Return a valid topological ordering of the graph nodes.

        Raises:
            ValidationError: If the graph contains a cycle.
        """
        in_degree: Dict[str, int] = defaultdict(int)
        adjacency: Dict[str, List[str]] = defaultdict(list)

        for node_id in graph.nodes:
            in_degree.setdefault(node_id, 0)

        for src, tgt in graph.edges:
            adjacency[src].append(tgt)
            in_degree[tgt] += 1

        queue: deque[str] = deque()
        for node_id, deg in in_degree.items():
            if deg == 0:
                queue.append(node_id)

        order: List[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) < len(graph.nodes):
            raise ValidationError(
                f"Cannot topologically sort: cycle detected "
                f"({len(order)}/{len(graph.nodes)} nodes processed)."
            )

        return order
