"""
AE-03 Additionals: Automated Benchmark & Latency Suite.

Runs benchmark research goals across all configured LLM providers
to measure execution latency, token counts, and provider costs.
"""

import sys
import os
import time
import json
import asyncio
import logging

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.graph.workflow import WorkflowEngine
from backend.schemas.contracts import RunRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("benchmark_runner")

BENCHMARK_GOALS = [
    "Impact of artificial intelligence on renewable grid optimization",
    "Quantum computing algorithms for post-quantum cryptography",
    "CRISPR gene editing applications in oncology",
]


async def run_benchmark():
    """Run all benchmark goals and output summary latency report."""
    logger.info("Initializing AE-03 Multi-Agent Benchmark Suite...")
    engine = WorkflowEngine()

    results = []
    for idx, goal in enumerate(BENCHMARK_GOALS, 1):
        logger.info(f"[{idx}/{len(BENCHMARK_GOALS)}] Running benchmark goal: '{goal}'")
        start_t = time.time()

        req = RunRequest(goal=goal, workspace_id="benchmark")
        run_id = f"bench-{idx}-{int(time.time())}"

        try:
            state = await engine.execute_run(run_id=run_id, request=req)
            elapsed_s = round(time.time() - start_t, 2)
            metrics = state.get("metrics", {})

            results.append({
                "run_id": run_id,
                "goal": goal,
                "status": state.get("status", "unknown"),
                "elapsed_seconds": elapsed_s,
                "total_tokens": metrics.get("total_tokens", 0),
                "cost_usd": metrics.get("total_cost_usd", 0.0),
            })
            logger.info(f"✓ Completed in {elapsed_s}s | Tokens: {metrics.get('total_tokens', 0)}")
        except Exception as e:
            logger.error(f"✗ Benchmark run failed: {e}")

    print("\n" + "=" * 60)
    print("BENCHMARK EXECUTION SUMMARY")
    print("=" * 60)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(run_benchmark())
