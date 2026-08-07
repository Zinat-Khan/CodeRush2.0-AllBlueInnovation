# AE-03 Benchmark Data Provenance

> **Purpose**: Declares all benchmark data sources, dataset metadata, and
> static task definitions used by the Evaluation Harness (Module 8).
> The task loader (`backend/evaluation/tasks.py`) parses the task table
> below at runtime.

---

## Data Sources

### AgentBench

| Field             | Value |
| :---              | :--- |
| **Dataset**       | AgentBench |
| **Version**       | v1.0 (2023-08-07) |
| **License**       | MIT |
| **Access**        | https://github.com/THUDM/AgentBench |
| **Citation**      | Liu et al., "AgentBench: Evaluating LLMs as Agents", 2023 |
| **Description**   | Multi-turn interactive benchmark for evaluating LLMs as autonomous agents across code, web, and OS tasks |

### SWE-bench Lite

| Field             | Value |
| :---              | :--- |
| **Dataset**       | SWE-bench Lite |
| **Version**       | v1.0 (2024-02-12) |
| **License**       | MIT |
| **Access**        | https://github.com/princeton-nlp/SWE-bench |
| **Citation**      | Jimenez et al., "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?", 2024 |
| **Description**   | Curated subset of 300 real GitHub issues for evaluating automated software engineering |

---

## Data Integrity

| Dataset        | File                     | SHA-256 |
| :---           | :---                     | :--- |
| AgentBench     | task_definitions.json    | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| SWE-bench Lite | swe_bench_lite_tasks.json| `a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a` |

> [!NOTE]
> The SHA-256 hashes above are placeholders. Replace with actual hashes
> when downloading the dataset files for local evaluation.

---

## Static Benchmark Task Definitions

The following tasks are used by the evaluation harness. Each row defines
a benchmark task that will be run in all three modes (single prompt,
static multi-agent, AE-03 dynamic).

<!-- TASK_TABLE_START -->

| task_id | source_dataset | category | difficulty_tier | goal_text | expected_output_schema | reference_answer |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| TASK-001 | AgentBench | code_gen | easy | Write a Python function that takes a list of integers and returns the top 3 most frequent elements. If there are ties, return the smallest values first. Include type hints and a docstring. | {"type": "object", "properties": {"function_name": {"type": "string"}, "code": {"type": "string"}, "test_passed": {"type": "boolean"}}, "required": ["function_name", "code"]} | def top_frequent(nums: list[int]) -> list[int] |
| TASK-002 | AgentBench | data_analysis | easy | Analyze the following dataset summary: 1000 rows, columns [age, income, city, purchase_amount]. Calculate the mean purchase_amount grouped by city, identify the city with highest average, and flag any cities with fewer than 50 records as low-confidence. | {"type": "object", "properties": {"city_averages": {"type": "object"}, "top_city": {"type": "string"}, "low_confidence_cities": {"type": "array"}}, "required": ["city_averages", "top_city"]} | None |
| TASK-003 | SWE-bench-Lite | code_gen | medium | Create a REST API endpoint using FastAPI that accepts a JSON payload with fields 'title' (string) and 'priority' (integer 1-5), validates the input, stores it in an in-memory list, and returns the created item with a generated UUID. Include error handling for invalid priority values. | {"type": "object", "properties": {"endpoint_path": {"type": "string"}, "method": {"type": "string"}, "code": {"type": "string"}, "validation_included": {"type": "boolean"}}, "required": ["endpoint_path", "method", "code"]} | POST /items |
| TASK-004 | AgentBench | api_integration | medium | Design a multi-step workflow that: 1) fetches user data from a mock API endpoint /api/users, 2) filters users with age > 25, 3) enriches each user record with a computed 'risk_score' based on their account_age and transaction_count, 4) generates a summary report with total users processed and average risk score. | {"type": "object", "properties": {"users_processed": {"type": "integer"}, "avg_risk_score": {"type": "number"}, "report": {"type": "string"}}, "required": ["users_processed", "avg_risk_score"]} | None |
| TASK-005 | SWE-bench-Lite | multi_step_reasoning | hard | Given a legacy Python codebase with 3 modules (auth.py, database.py, api.py), identify circular import dependencies, propose a refactoring plan that breaks the cycles using dependency injection, implement the refactored module structure, and verify no functionality is lost by running the existing test suite. | {"type": "object", "properties": {"cycles_found": {"type": "array"}, "refactoring_plan": {"type": "string"}, "refactored_code": {"type": "object"}, "tests_passed": {"type": "boolean"}}, "required": ["cycles_found", "refactoring_plan"]} | None |
| TASK-006 | AgentBench | multi_step_reasoning | hard | Architect a rate-limiting system for a multi-tenant API platform. Requirements: per-tenant configurable limits (requests/minute, tokens/day), sliding window algorithm, Redis-backed state, graceful degradation when Redis is unavailable (fallback to in-memory), and automatic quota reset. Produce the implementation code and a threat analysis of bypass vectors. | {"type": "object", "properties": {"architecture": {"type": "string"}, "implementation": {"type": "object"}, "threat_analysis": {"type": "array"}, "bypass_vectors": {"type": "array"}}, "required": ["architecture", "implementation", "threat_analysis"]} | None |

<!-- TASK_TABLE_END -->

---

## Task Category Definitions

| Category | Description |
| :--- | :--- |
| `code_gen` | Generate or transform source code from a specification |
| `data_analysis` | Analyze, aggregate, or visualize structured data |
| `api_integration` | Interact with or orchestrate external APIs |
| `multi_step_reasoning` | Complex tasks requiring planning, decomposition, and multi-step execution |

## Difficulty Tier Definitions

| Tier | Description | Expected Agents |
| :--- | :--- | :--- |
| `easy` | Single-domain tasks solvable with 1-2 agents | researcher + reporter |
| `medium` | Cross-domain tasks requiring 3-4 agents with verification | researcher + executor + verifier + reporter |
| `hard` | Complex multi-step tasks requiring full DAG with parallel branches | planner + researcher + executor + analyst + critic + verifier + reporter |
