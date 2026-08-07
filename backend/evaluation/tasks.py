"""
AE-03 Benchmark Task Loader — Parse Tasks from DATA_PROVENANCE.md.

Provides:
  - load_benchmark_tasks(): reads the markdown task table from
    DATA_PROVENANCE.md and returns a list of BenchmarkTask models.
  - DEFAULT_PROVENANCE_PATH: default location of the provenance file.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.schemas.artifacts import BenchmarkTask, DifficultyTier

logger = logging.getLogger(__name__)

# Default path relative to project root (c:\hack)
DEFAULT_PROVENANCE_PATH = Path(__file__).parent.parent.parent / "evaluation" / "DATA_PROVENANCE.md"


def load_benchmark_tasks(
    provenance_path: Optional[str | Path] = None,
) -> List[BenchmarkTask]:
    """
    Load benchmark task definitions from DATA_PROVENANCE.md.

    Parses the markdown table between ``<!-- TASK_TABLE_START -->`` and
    ``<!-- TASK_TABLE_END -->`` markers.  Each row becomes a
    ``BenchmarkTask`` Pydantic model.

    Args:
        provenance_path: Path to the DATA_PROVENANCE.md file.
            Defaults to ``evaluation/DATA_PROVENANCE.md`` relative to
            the project root.

    Returns:
        List of validated BenchmarkTask objects.

    Raises:
        FileNotFoundError: If the provenance file does not exist.
        ValueError: If the task table cannot be parsed.
    """
    path = Path(provenance_path) if provenance_path else DEFAULT_PROVENANCE_PATH

    if not path.exists():
        raise FileNotFoundError(
            f"DATA_PROVENANCE.md not found at: {path}"
        )

    content = path.read_text(encoding="utf-8")
    logger.info("Loading benchmark tasks from: %s", path)

    # Extract the table between markers
    table_match = re.search(
        r"<!-- TASK_TABLE_START -->\s*(.*?)\s*<!-- TASK_TABLE_END -->",
        content,
        re.DOTALL,
    )
    if not table_match:
        raise ValueError(
            "Could not find task table markers "
            "(<!-- TASK_TABLE_START --> / <!-- TASK_TABLE_END -->) "
            f"in {path}"
        )

    table_text = table_match.group(1).strip()
    tasks = _parse_markdown_table(table_text)

    logger.info("Loaded %d benchmark tasks", len(tasks))
    return tasks


def _parse_markdown_table(table_text: str) -> List[BenchmarkTask]:
    """
    Parse a markdown pipe-delimited table into BenchmarkTask objects.

    Expected columns:
        task_id | source_dataset | category | difficulty_tier |
        goal_text | expected_output_schema | reference_answer
    """
    lines = [
        line.strip()
        for line in table_text.strip().split("\n")
        if line.strip() and not line.strip().startswith("| :---")
    ]

    if len(lines) < 2:
        raise ValueError("Task table must have a header row and at least one data row.")

    # Parse header
    header = _split_row(lines[0])
    expected_columns = [
        "task_id", "source_dataset", "category", "difficulty_tier",
        "goal_text", "expected_output_schema", "reference_answer",
    ]

    # Normalise header names
    normalised_header = [col.strip().lower().replace(" ", "_") for col in header]

    # Validate required columns are present
    for col in expected_columns[:5]:  # First 5 are mandatory
        if col not in normalised_header:
            raise ValueError(
                f"Missing required column '{col}' in task table header. "
                f"Found: {normalised_header}"
            )

    tasks: List[BenchmarkTask] = []

    for line_idx, line in enumerate(lines[1:], start=2):
        cells = _split_row(line)
        if len(cells) < len(normalised_header):
            # Pad with empty strings
            cells.extend([""] * (len(normalised_header) - len(cells)))

        row = {
            normalised_header[i]: cells[i].strip()
            for i in range(len(normalised_header))
        }

        try:
            # Parse expected_output_schema as JSON
            schema_str = row.get("expected_output_schema", "{}")
            try:
                schema = json.loads(schema_str) if schema_str else {}
            except json.JSONDecodeError:
                logger.warning(
                    "Invalid JSON in expected_output_schema for %s: %s",
                    row.get("task_id", f"row-{line_idx}"),
                    schema_str[:100],
                )
                schema = {}

            # Parse reference_answer (None if empty or "None")
            ref_answer = row.get("reference_answer", "")
            if not ref_answer or ref_answer.lower() == "none":
                ref_answer = None

            # Parse difficulty tier
            tier_str = row.get("difficulty_tier", "medium").lower()
            try:
                tier = DifficultyTier(tier_str)
            except ValueError:
                logger.warning(
                    "Unknown difficulty tier '%s' for task %s, defaulting to 'medium'.",
                    tier_str, row.get("task_id"),
                )
                tier = DifficultyTier.MEDIUM

            task = BenchmarkTask(
                task_id=row["task_id"],
                source_dataset=row["source_dataset"],
                goal_text=row["goal_text"],
                expected_output_schema=schema,
                difficulty_tier=tier,
                category=row.get("category", "general"),
                reference_answer=ref_answer,
            )
            tasks.append(task)

        except Exception as e:
            logger.error(
                "Failed to parse task at row %d: %s (row data: %s)",
                line_idx, e, row,
            )
            raise ValueError(
                f"Failed to parse task at row {line_idx}: {e}"
            ) from e

    return tasks


def _split_row(row: str) -> List[str]:
    """
    Split a markdown table row by pipes, stripping outer pipes.

    Handles escaped pipes within JSON strings by using a simple
    state machine.
    """
    # Remove leading/trailing pipes and whitespace
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]

    # Split by pipe, but respect JSON braces
    cells: List[str] = []
    current = ""
    brace_depth = 0
    bracket_depth = 0

    for char in row:
        if char == "{":
            brace_depth += 1
            current += char
        elif char == "}":
            brace_depth -= 1
            current += char
        elif char == "[":
            bracket_depth += 1
            current += char
        elif char == "]":
            bracket_depth -= 1
            current += char
        elif char == "|" and brace_depth == 0 and bracket_depth == 0:
            cells.append(current.strip())
            current = ""
        else:
            current += char

    if current:
        cells.append(current.strip())

    return cells


def get_tasks_by_difficulty(
    tasks: List[BenchmarkTask],
    tier: DifficultyTier,
) -> List[BenchmarkTask]:
    """Filter tasks by difficulty tier."""
    return [t for t in tasks if t.difficulty_tier == tier]


def get_tasks_by_category(
    tasks: List[BenchmarkTask],
    category: str,
) -> List[BenchmarkTask]:
    """Filter tasks by category."""
    return [t for t in tasks if t.category == category]


def get_task_summary(tasks: List[BenchmarkTask]) -> Dict[str, Any]:
    """Return a summary of loaded tasks for reporting."""
    by_tier = {}
    by_category = {}
    by_source = {}

    for t in tasks:
        by_tier[t.difficulty_tier.value] = by_tier.get(t.difficulty_tier.value, 0) + 1
        by_category[t.category] = by_category.get(t.category, 0) + 1
        by_source[t.source_dataset] = by_source.get(t.source_dataset, 0) + 1

    return {
        "total_tasks": len(tasks),
        "by_difficulty": by_tier,
        "by_category": by_category,
        "by_source": by_source,
    }
