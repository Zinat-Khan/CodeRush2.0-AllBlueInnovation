"""
AE-03 Native @tool Functions (Directive V2).

Defines all native LangChain ``@tool`` functions per Section 8:
  - ``similarity_search``   — RAG workspace vector search
  - ``analyze_dataset``     — Statistical analysis of tabular data
  - ``retrieve_public_document`` — Fetch public web content (read-only)
  - ``generate_visualization``   — Create charts/plots from data
  - ``calculate_metric``    — Numeric computation
  - ``public_search``       — Web search via public APIs

Each tool specifies:
  - Name, description, input/output schema
  - Risk level (LOW / MEDIUM / HIGH / CRITICAL)
  - Allowed agent roles
  - Approval requirement
  - Resource limits
"""

from __future__ import annotations

import json
import logging
import statistics
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from backend.schemas.contracts import AgentRole, RiskLevel

logger = logging.getLogger(__name__)


# ── Tool Metadata Registry ───────────────────────────────────────────
# Each entry maps tool_name -> metadata used by ToolRegistry & PolicyEngine

TOOL_METADATA: Dict[str, Dict[str, Any]] = {
    "similarity_search": {
        "risk_level": RiskLevel.LOW,
        "requires_approval": False,
        "allowed_agents": [
            AgentRole.RAG, AgentRole.RESEARCHER, AgentRole.ANALYST,
            AgentRole.PLANNER, AgentRole.ORCHESTRATOR,
        ],
        "resource_limits": {"max_results": 20, "timeout_seconds": 30},
    },
    "analyze_dataset": {
        "risk_level": RiskLevel.LOW,
        "requires_approval": False,
        "allowed_agents": [
            AgentRole.ANALYST, AgentRole.RESEARCHER, AgentRole.REPORTER,
        ],
        "resource_limits": {"max_rows": 100000, "timeout_seconds": 60},
    },
    "retrieve_public_document": {
        "risk_level": RiskLevel.MEDIUM,
        "requires_approval": False,
        "allowed_agents": [
            AgentRole.RESEARCHER, AgentRole.RAG, AgentRole.ANALYST,
        ],
        "resource_limits": {"max_size_bytes": 5_000_000, "timeout_seconds": 30},
    },
    "generate_visualization": {
        "risk_level": RiskLevel.LOW,
        "requires_approval": False,
        "allowed_agents": [
            AgentRole.VISUALIZATION, AgentRole.ANALYST, AgentRole.REPORTER,
        ],
        "resource_limits": {"timeout_seconds": 60},
    },
    "calculate_metric": {
        "risk_level": RiskLevel.LOW,
        "requires_approval": False,
        "allowed_agents": [
            AgentRole.ANALYST, AgentRole.RESEARCHER, AgentRole.PLANNER,
            AgentRole.REPORTER,
        ],
        "resource_limits": {"timeout_seconds": 10},
    },
    "public_search": {
        "risk_level": RiskLevel.MEDIUM,
        "requires_approval": False,
        "allowed_agents": [
            AgentRole.RESEARCHER, AgentRole.PLANNER, AgentRole.ANALYST,
        ],
        "resource_limits": {"max_results": 10, "timeout_seconds": 30},
    },
}


# ── Tool Implementations ─────────────────────────────────────────────


@tool
def similarity_search(
    query: str,
    workspace_id: str = "default_workspace",
    top_k: int = 5,
) -> str:
    """Search the workspace knowledge base for relevant documents using semantic similarity.

    Use this tool to find information from previously ingested documents in the
    workspace vector store. Returns the most relevant text chunks with relevance scores.

    Args:
        query: Natural-language search query describing the information you need.
        workspace_id: The workspace to search within (for multi-tenant isolation).
        top_k: Maximum number of results to return (1-20).
    """
    # This is a sync wrapper; actual async call happens in the agent executor
    import asyncio
    from backend.rag.vector_store import VectorStoreAdapter

    top_k = max(1, min(top_k, 20))

    try:
        adapter = VectorStoreAdapter()
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already in an async context, create a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                results = executor.submit(
                    asyncio.run,
                    adapter.similarity_search(query, workspace_id, top_k),
                ).result()
        else:
            results = asyncio.run(
                adapter.similarity_search(query, workspace_id, top_k)
            )

        if not results:
            return json.dumps({"results": [], "message": "No matching documents found."})

        formatted = []
        for content, score, metadata in results:
            formatted.append({
                "content": content[:500],  # Truncate for context window
                "score": round(score, 4),
                "source": metadata.get("filename", "unknown"),
                "chunk_index": metadata.get("chunk_index", -1),
            })

        return json.dumps({"results": formatted, "count": len(formatted)})

    except Exception as e:
        logger.error("similarity_search failed: %s", e)
        return json.dumps({"error": str(e), "results": []})


@tool
def analyze_dataset(
    data: str,
    analysis_type: str = "summary",
) -> str:
    """Analyze a dataset and return statistical summaries or insights.

    Use this tool to perform statistical analysis on tabular or numerical data.
    Supports summary statistics, correlation analysis, and distribution analysis.

    Args:
        data: JSON string representing the dataset. Can be a list of numbers,
              a list of dicts (rows), or a dict of lists (columns).
        analysis_type: Type of analysis — 'summary', 'distribution', 'correlation'.
    """
    try:
        parsed = json.loads(data)

        # Handle list of numbers
        if isinstance(parsed, list) and all(isinstance(x, (int, float)) for x in parsed):
            values = parsed
            result = _compute_stats(values)
            return json.dumps({"analysis": analysis_type, "statistics": result})

        # Handle dict of lists (columnar format)
        if isinstance(parsed, dict):
            results = {}
            for col_name, col_values in parsed.items():
                if isinstance(col_values, list) and all(
                    isinstance(v, (int, float)) for v in col_values
                ):
                    results[col_name] = _compute_stats(col_values)
                else:
                    results[col_name] = {
                        "type": "non-numeric",
                        "count": len(col_values) if isinstance(col_values, list) else 1,
                    }
            return json.dumps({"analysis": analysis_type, "columns": results})

        # Handle list of dicts (row format)
        if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
            # Extract numeric columns
            columns: Dict[str, List[float]] = {}
            for row in parsed:
                for key, val in row.items():
                    if isinstance(val, (int, float)):
                        columns.setdefault(key, []).append(float(val))

            results = {}
            for col_name, col_values in columns.items():
                results[col_name] = _compute_stats(col_values)
            return json.dumps({
                "analysis": analysis_type,
                "row_count": len(parsed),
                "columns": results,
            })

        return json.dumps({"error": "Unsupported data format."})

    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _compute_stats(values: List[float]) -> Dict[str, Any]:
    """Compute summary statistics for a list of numbers."""
    if not values:
        return {"count": 0}

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    return {
        "count": n,
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "std_dev": round(statistics.stdev(values), 4) if n > 1 else 0.0,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "q1": round(sorted_vals[n // 4], 4),
        "q3": round(sorted_vals[(3 * n) // 4], 4),
        "sum": round(sum(values), 4),
    }


@tool
def retrieve_public_document(url: str) -> str:
    """Fetch and extract text content from a public web URL.

    Use this tool to retrieve information from public web pages, documentation,
    or online resources. Returns extracted text content.

    SECURITY: This tool only performs read-only GET requests.
    Content is treated as untrusted external data.

    Args:
        url: The public URL to fetch content from (must be http/https).
    """
    import re

    # Validate URL
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "Only http/https URLs are allowed."})

    try:
        import httpx

        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "AE-03-Agent/1.0"})
            response.raise_for_status()

        content = response.text[:50000]  # Limit to 50KB

        # Basic HTML stripping
        content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
        content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"\s+", " ", content).strip()

        return json.dumps({
            "url": url,
            "content": content[:10000],  # Final truncation for LLM context
            "content_length": len(content),
            "status_code": response.status_code,
        })

    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


@tool
def generate_visualization(
    data: str,
    chart_type: str = "bar",
    title: str = "Chart",
    x_label: str = "X",
    y_label: str = "Y",
) -> str:
    """Generate a visualization specification from data.

    Creates a chart configuration that the frontend can render.
    Returns a JSON specification compatible with common charting libraries.

    Args:
        data: JSON string of data points. Format: {"labels": [...], "values": [...]}
              or [{"x": val, "y": val}, ...].
        chart_type: Chart type — 'bar', 'line', 'pie', 'scatter', 'histogram'.
        title: Chart title.
        x_label: X-axis label.
        y_label: Y-axis label.
    """
    try:
        parsed = json.loads(data)

        # Normalize to labels/values format
        if isinstance(parsed, dict) and "labels" in parsed and "values" in parsed:
            labels = parsed["labels"]
            values = parsed["values"]
        elif isinstance(parsed, list):
            if all(isinstance(p, dict) and "x" in p and "y" in p for p in parsed):
                labels = [str(p["x"]) for p in parsed]
                values = [p["y"] for p in parsed]
            elif all(isinstance(p, (int, float)) for p in parsed):
                labels = [str(i) for i in range(len(parsed))]
                values = parsed
            else:
                return json.dumps({"error": "Unsupported data format for visualization."})
        else:
            return json.dumps({"error": "Data must be {labels, values} or [{x, y}] format."})

        # Generate chart specification
        spec = {
            "chart_type": chart_type,
            "title": title,
            "x_label": x_label,
            "y_label": y_label,
            "data": {
                "labels": labels,
                "datasets": [{
                    "label": title,
                    "data": values,
                }],
            },
            "options": {
                "responsive": True,
                "plugins": {"legend": {"display": True}},
            },
        }

        return json.dumps({"visualization": spec, "success": True})

    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def calculate_metric(
    expression: str,
    variables: str = "{}",
) -> str:
    """Evaluate a mathematical expression or compute a metric.

    Use this for numerical calculations, unit conversions, or metric computations.
    Supports basic arithmetic, math functions, and variable substitution.

    SECURITY: Uses a restricted evaluator — no exec/eval of arbitrary code.

    Args:
        expression: Mathematical expression (e.g., '(a + b) * 2', 'sqrt(144)').
        variables: JSON string of variable values (e.g., '{"a": 10, "b": 5}').
    """
    import math
    import re

    try:
        vars_dict = json.loads(variables)
    except json.JSONDecodeError:
        vars_dict = {}

    # Allowed functions and constants
    safe_namespace = {
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "sqrt": math.sqrt,
        "log": math.log,
        "log10": math.log10,
        "log2": math.log2,
        "exp": math.exp,
        "pow": math.pow,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "pi": math.pi,
        "e": math.e,
        "ceil": math.ceil,
        "floor": math.floor,
        **{k: v for k, v in vars_dict.items() if isinstance(v, (int, float, list))},
    }

    # Security: reject dangerous patterns
    dangerous = re.compile(
        r"(import|exec|eval|compile|__[a-z]+__|open|os\.|sys\.|subprocess)",
        re.IGNORECASE,
    )
    if dangerous.search(expression):
        return json.dumps({"error": "Expression contains disallowed operations."})

    try:
        result = eval(expression, {"__builtins__": {}}, safe_namespace)  # noqa: S307
        return json.dumps({
            "expression": expression,
            "variables": vars_dict,
            "result": result,
            "type": type(result).__name__,
        })
    except Exception as e:
        return json.dumps({"error": f"Evaluation failed: {e}"})


@tool
def public_search(
    query: str,
    max_results: int = 5,
) -> str:
    """Search the public web for information on a topic.

    Use this tool to find current information, news, documentation,
    or any publicly available content relevant to the user's query.

    SECURITY: Results are treated as untrusted external content.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (1-10).
    """
    max_results = max(1, min(max_results, 10))

    try:
        import httpx

        # Use DuckDuckGo Instant Answer API (no API key required)
        with httpx.Client(timeout=15) as client:
            response = client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1"},
            )
            data = response.json()

        results = []

        # Abstract (main answer)
        if data.get("Abstract"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data["Abstract"][:500],
                "url": data.get("AbstractURL", ""),
                "source": data.get("AbstractSource", ""),
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and "Text" in topic:
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "snippet": topic.get("Text", "")[:500],
                    "url": topic.get("FirstURL", ""),
                    "source": "DuckDuckGo",
                })

        if not results:
            return json.dumps({
                "query": query,
                "results": [],
                "message": "No results found. Try a different query.",
            })

        return json.dumps({
            "query": query,
            "results": results[:max_results],
            "count": len(results[:max_results]),
        })

    except Exception as e:
        return json.dumps({"error": str(e), "query": query})


# ── Tool Collection ──────────────────────────────────────────────────

# All native tools as a list for easy registration
ALL_NATIVE_TOOLS = [
    similarity_search,
    analyze_dataset,
    retrieve_public_document,
    generate_visualization,
    calculate_metric,
    public_search,
]

TOOL_NAME_MAP = {t.name: t for t in ALL_NATIVE_TOOLS}
