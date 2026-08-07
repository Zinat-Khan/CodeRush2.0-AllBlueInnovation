"""AE-03: Evaluation sub-package — Benchmark Runner, Task Loader & Reporter."""

from backend.evaluation.tasks import (
    DEFAULT_PROVENANCE_PATH,
    get_task_summary,
    get_tasks_by_category,
    get_tasks_by_difficulty,
    load_benchmark_tasks,
)
from backend.evaluation.benchmark import (
    BenchmarkRunner,
    ExecutionMode,
)
from backend.evaluation.reporter import (
    BenchmarkReporter,
    ModeAggregate,
)

__all__ = [
    "BenchmarkReporter",
    "BenchmarkRunner",
    "DEFAULT_PROVENANCE_PATH",
    "ExecutionMode",
    "ModeAggregate",
    "get_task_summary",
    "get_tasks_by_category",
    "get_tasks_by_difficulty",
    "load_benchmark_tasks",
]
