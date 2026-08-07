# AE-03: Reproducibility Guide

> **Version**: 1.0.0 · **Last Updated**: 2026-08-07 · **Target OS**: Windows 10/11

This document provides step-by-step instructions to reproduce the AE-03 orchestrator from a clean machine, re-run benchmarks with identical inputs, and replay any previous execution.

---

## 1. Environment Specification

### Required Software

| Software | Version | Purpose |
| :--- | :--- | :--- |
| Python | 3.13+ | Backend runtime |
| Node.js | 22+ LTS | Frontend build tool |
| npm | 10+ | Frontend package manager |
| Git | 2.40+ | Version control |
| Ollama | latest | Local LLM inference |

### Python Dependencies (Pinned)

All Python dependencies are declared in `requirements.txt`:

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pydantic>=2.10.0
pydantic-settings>=2.5.0
httpx>=0.27.0
openai>=1.50.0
google-generativeai>=0.8.0
sse-starlette>=2.0.0
python-dotenv>=1.0.0
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

### Frontend Dependencies (Locked)

Frontend dependencies are locked via `frontend/package-lock.json`:

| Package | Version |
| :--- | :--- |
| next | 16.3.0 |
| react | 19.2.8 |
| react-dom | 19.2.8 |
| @xyflow/react | 12.11.2 |
| lucide-react | 1.29.0 |

---

## 2. Setup Instructions (Clean Machine → Running Demo)

### Step 1: Clone the Repository

```powershell
git clone <repository-url> c:\hack
cd c:\hack
```

### Step 2: Create Python Virtual Environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Step 3: Install Python Dependencies

```powershell
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables

```powershell
# Copy the template
Copy-Item .env.example .env

# Edit .env with your real API keys:
#   OPENAI_API_KEY=sk-...
#   GEMINI_API_KEY=AI...
#   OLLAMA_HOST=http://localhost:11434
#   N8N_WEBHOOK_BASE_URL=https://your-instance.app.n8n.cloud/webhook
```

### Step 5: Install Ollama and Pull a Model

```powershell
# Install Ollama from https://ollama.com/download
# Then pull a model:
ollama pull llama3.2
```

### Step 6: Install Frontend Dependencies

```powershell
cd frontend
npm install
cd ..
```

### Step 7: Verify Backend

```powershell
# Run the per-module test suites
python backend\tests\test_module8.py
python backend\tests\test_e2e_mvd.py
```

Expected output:
```
ALL MODULE 8 TESTS PASSED [OK]
ALL 4 MVD SCENARIOS + DATA PROVENANCE PASSED [OK]
```

### Step 8: Launch the Demo

```powershell
.\scripts\run_demo.ps1
```

This starts:
- **Backend**: `http://localhost:8000` (FastAPI + Swagger at `/api/docs`)
- **Frontend**: `http://localhost:3000` (Next.js dashboard)

### Alternative: Manual Launch

```powershell
# Terminal 1: Backend
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Frontend
cd frontend
npm run dev -- -p 3000
```

---

## 3. Seed & Determinism

### LLM Temperature Settings

| Provider | Default Temperature | Configurable Via |
| :--- | :--- | :--- |
| OpenAI | `0.0` (deterministic) | `OPENAI_DEFAULT_TEMPERATURE` in `.env` |
| Gemini | `0.0` (deterministic) | `GEMINI_DEFAULT_TEMPERATURE` in `.env` |
| Ollama | `0.0` (deterministic) | `OLLAMA_DEFAULT_TEMPERATURE` in `.env` |

Setting temperature to `0.0` maximises output reproducibility across runs. Note that even with `temperature=0.0`, LLM outputs may vary slightly between API versions.

### Random Seeds

| Component | Seed Behaviour |
| :--- | :--- |
| `uuid.uuid4()` | Non-deterministic; run IDs, graph IDs are unique per invocation |
| `asyncio.gather()` | Execution order within a parallel layer is non-deterministic |
| LLM completions | Near-deterministic at `temperature=0.0` |

**Implication:** To reproduce identical benchmark results, use the **replay engine** (see Section 5) rather than re-executing from scratch.

---

## 4. Benchmark Reproduction

### Task Source

All benchmark tasks are defined in [`evaluation/DATA_PROVENANCE.md`](../evaluation/DATA_PROVENANCE.md). This file contains:

- **6 static tasks** from AgentBench and SWE-bench Lite
- **SHA-256 integrity hashes** for source datasets
- **Parseable markdown table** between `<!-- TASK_TABLE_START -->` and `<!-- TASK_TABLE_END -->` markers

### Running the Benchmark

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Run the 3-mode benchmark
python -m backend.evaluation.benchmark

# Or run individual components:
python -c "from backend.evaluation.tasks import load_benchmark_tasks; print(len(load_benchmark_tasks()))"
```

### Benchmark Modes

| Mode | Description | Cost Profile |
| :--- | :--- | :--- |
| `single_prompt` | Direct LLM call with full task context | Lowest cost, lowest quality |
| `static_multi_agent` | Hardcoded 4-node DAG (researcher→executor→verifier→reporter) | Medium cost, structured output |
| `ae03_dynamic` | Planner-compiled DAG via `GraphCompiler` | Highest cost, best quality |

### Verifying Task Integrity

```powershell
python backend\tests\test_module8.py
```

This validates:
- All 6 tasks load correctly
- Difficulty distribution: 2 easy, 2 medium, 2 hard
- Expected output schemas parse as valid JSON
- Filtering by category and difficulty works

### Generating a Comparison Report

```python
from backend.evaluation.reporter import BenchmarkReporter

# After running benchmarks and collecting results:
reporter = BenchmarkReporter(results, tasks)

# JSON report
json_report = reporter.to_json()

# Markdown report with marginal value analysis
md_report = reporter.to_markdown()
```

---

## 5. Run Replay Protocol

The replay engine allows you to reproduce any previous execution with optional provider hot-swap.

### Via API

```bash
# 1. Execute a goal (creates a stored run)
curl -X POST http://localhost:8000/api/execute \
  -H "Content-Type: application/json" \
  -d '{"goal": "Audit REST API security", "provider": "openai"}'

# Response: {"run_id": "run-a1b2c3d4", ...}

# 2. Replay the run with a different provider
curl -X POST http://localhost:8000/api/replay \
  -H "Content-Type: application/json" \
  -d '{"original_run_id": "run-a1b2c3d4", "override_provider": "ollama"}'

# Response: Side-by-side comparison of original vs replay metrics
```

### Via Python

```python
from backend.observability.replay import ReplayEngine
from backend.observability.tracer import RunStore

store = RunStore()
engine = ReplayEngine(run_store=store, node_handler=your_handler)

# Replay with provider override
comparison = await engine.replay(
    original_run_id="run-a1b2c3d4",
    override_provider="ollama",
)

# Print side-by-side comparison
print(comparison.summary_table())
```

### Replay Guarantees

| Aspect | Guarantee |
| :--- | :--- |
| **Graph structure** | Identical (same nodes, edges, system prompts) |
| **Execution order** | Same topological order |
| **Provider** | Can be overridden (hot-swap) |
| **LLM output** | May differ (LLM non-determinism) |
| **Cost comparison** | Accurate side-by-side delta |

### SSE Streaming of Replayed Runs

```bash
# Stream events from a stored run in real-time
curl -N http://localhost:8000/api/sse/runs/run-a1b2c3d4
```

Events stream with 50ms intervals, simulating real-time execution for replay visualisation.

---

## 6. Troubleshooting

### Common Issues

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| `ModuleNotFoundError: backend` | Not running from project root | `cd c:\hack` before running |
| `OPENAI_API_KEY not set` | Missing `.env` file | Copy `.env.example` to `.env` and fill keys |
| `Ollama connection refused` | Ollama not running | Start with `ollama serve` |
| `Port 8000 already in use` | Another process on port | `netstat -ano \| findstr :8000` to find PID |
| `npm install` fails | Node.js not installed or wrong version | Install Node.js 22+ LTS |
| `next build` TypeScript errors | Dependency mismatch | Delete `node_modules` and re-run `npm install` |

### Health Checks

```powershell
# Backend health
curl http://localhost:8000/api/health

# Frontend (should return HTML)
curl http://localhost:3000

# Ollama models
ollama list
```

---

## 7. Data Provenance Reference

All benchmark evaluation data is sourced from:

| Dataset | Version | License | Access |
| :--- | :--- | :--- | :--- |
| AgentBench | v1.0 | MIT | [github.com/THUDM/AgentBench](https://github.com/THUDM/AgentBench) |
| SWE-bench Lite | v1.0 | MIT | [github.com/princeton-nlp/SWE-bench](https://github.com/princeton-nlp/SWE-bench) |

Full provenance metadata, SHA-256 hashes, and task definitions are in [`evaluation/DATA_PROVENANCE.md`](../evaluation/DATA_PROVENANCE.md).
