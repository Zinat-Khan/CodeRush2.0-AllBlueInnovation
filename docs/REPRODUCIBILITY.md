# AE-03: Reproducibility Guide (Directive V2)

> **Version**: 2.0.0 · **Last Updated**: 2026-08-07 · **Target OS**: Windows 10/11

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
| Ollama | latest | Local LLM inference (optional) |

### Python Dependencies (Pinned)

All Python dependencies are declared in `requirements.txt`:

```
# Core
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pydantic>=2.10.0
pydantic-settings>=2.5.0
httpx>=0.27.0
python-dotenv>=1.0.0

# LLM Providers
openai>=1.50.0
google-generativeai>=0.8.0

# LangChain / LangGraph
langchain>=0.3.0
langchain-core>=0.3.0
langchain-community>=0.3.0
langchain-google-genai>=2.1.0
langchain-openai>=0.3.0
langchain-chroma>=0.2.0
langchain-huggingface>=0.1.0
langgraph>=0.4.0

# RAG
chromadb>=0.6.0

# SSE
sse-starlette>=2.0.0

# Testing
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

### Environment Variables

Create `.env` in the project root (`c:\hack\.env`):

```env
# Required — at least one provider API key
GOOGLE_API_KEY=your-google-api-key
OPENAI_API_KEY=your-openai-api-key       # Optional

# Optional — Ollama local inference
OLLAMA_BASE_URL=http://localhost:11434

# Optional — configuration
DEFAULT_PROVIDER=google
DEFAULT_MODEL=gemini-2.0-flash
MAX_BUDGET_USD=5.0
MAX_TOKENS_PER_RUN=100000
LOG_LEVEL=INFO
```

---

## 2. Setup from Clean Machine

### Step 1: Clone Repository

```powershell
git clone <repository-url> c:\hack
cd c:\hack
```

### Step 2: Install Python Dependencies

```powershell
pip install -r requirements.txt
```

### Step 3: Install Frontend Dependencies

```powershell
cd frontend
npm install
cd ..
```

### Step 4: Configure Environment

```powershell
Copy-Item .env.example .env
# Edit .env with your API keys
```

### Step 5: Verify Installation

```powershell
# Backend imports
python -c "from backend.main import create_app; print('Backend OK')"

# Frontend build
cd frontend && npx next build && cd ..

# Security tests
python -m pytest backend/tests/test_security_suite.py -v
```

---

## 3. Running the System

### Single-Command Launch

```powershell
.\scripts\run_demo.ps1
```

This starts:
- **Backend**: FastAPI on `http://localhost:8000` (21 V2 API endpoints)
- **Frontend**: Next.js on `http://localhost:3000` (React Flow dashboard)

### Manual Launch

```powershell
# Terminal 1: Backend
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Frontend
cd frontend && npm run dev -- -p 3000
```

### Health Check

```powershell
Invoke-RestMethod http://localhost:8000/api/health
# Expected: { "status": "healthy", ... }
```

---

## 4. API Endpoints (21 V2 Endpoints)

All endpoints are prefixed with `/api/v2/`:

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `POST` | `/run` | Start execution |
| `GET` | `/run/{id}/stream` | SSE event stream |
| `GET` | `/run/{id}/status` | Run status |
| `GET` | `/run/{id}/report` | Final report |
| `GET` | `/run/{id}/trace` | Full trace |
| `GET` | `/run/{id}/artifacts` | Artifacts |
| `POST` | `/run/{id}/cancel` | Cancel run |
| `POST` | `/run/{id}/approve` | HITL approval |
| `POST` | `/workflow/approve/{id}` | Bulk approve |
| `POST` | `/workflow/reject/{id}` | Bulk reject |
| `POST` | `/workflow/request-changes/{id}` | Request changes |
| `POST` | `/documents/upload` | Upload document |
| `POST` | `/rag/query` | RAG query |
| `GET` | `/runs` | List all runs |
| `GET` | `/tools` | List tools |
| `GET` | `/agents` | Agent matrix |
| `GET` | `/hitl/pending` | Pending approvals |
| `GET` | `/policy/audit` | Audit log |
| `GET` | `/observability/replay/{id}` | Replay |
| `GET` | `/observability/events/{id}` | Events |
| `GET` | `/observability/costs/{id}` | Costs |

### Example: Start a Run

```powershell
$body = @{ goal = "Research the impact of AI on healthcare"; workspace_id = "default" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/api/v2/run" -Method POST -Body $body -ContentType "application/json"
```

### Example: Fetch Report

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/v2/run/run-abc12345/report"
```

---

## 5. Model Configuration

### Provider Priority (Fallback Chain)

```
1. Google Gemini (gemini-2.0-flash)  — Primary
2. OpenAI (gpt-4o-mini)              — Fallback
3. Ollama (local model)              — Local fallback
```

### ModelRouter Behaviour

- `ModelRouter` tries the configured `DEFAULT_PROVIDER` first
- On failure, falls back to the next provider in the chain
- All calls are logged to `CostTracker` with provider, model, tokens, cost, latency

---

## 6. Running Benchmarks

### 3-Mode Evaluation

```python
import asyncio
from backend.evaluation.benchmark import BenchmarkRunner
from backend.evaluation.tasks import load_benchmark_tasks

async def run():
    tasks = load_benchmark_tasks()
    runner = BenchmarkRunner()
    results = await runner.run_all(tasks)
    comparison = runner.compare_results(results)
    print(runner.format_comparison_table(comparison))

asyncio.run(run())
```

### Benchmark Modes

| Mode | Description |
| :--- | :--- |
| `single_prompt` | One LLM call, no orchestration |
| `static_multi_agent` | Template-based DAG via `compile_from_template()` |
| `ae03_dynamic` | Full `WorkflowEngine` pipeline |

---

## 7. Running Tests

### Security Test Suite (50 tests, 18 categories)

```powershell
python -m pytest backend/tests/test_security_suite.py -v
# Expected: 50 passed in <1s
```

### Full Test Suite

```powershell
python -m pytest backend/tests/ -v --tb=short
```

---

## 8. Replaying Executions

Every run is tracked by `EventTracker`, `CostTracker`, and `AuditLog`. To replay:

### Via API

```powershell
# Get full replay record
Invoke-RestMethod http://localhost:8000/api/v2/observability/replay/run-abc12345

# Get event timeline
Invoke-RestMethod http://localhost:8000/api/v2/observability/events/run-abc12345

# Get cost breakdown
Invoke-RestMethod http://localhost:8000/api/v2/observability/costs/run-abc12345
```

### Via Python

```python
from backend.observability.tracker import EventTracker
from backend.observability.tracer import CostTracker, AuditLog
from backend.observability.replay import ReplayEngine

tracker = EventTracker()
cost_tracker = CostTracker()
audit_log = AuditLog()

engine = ReplayEngine(tracker, cost_tracker, audit_log)
record = engine.replay("run-abc12345")
print(record.to_dict())
```

---

## 9. Troubleshooting

| Issue | Solution |
| :--- | :--- |
| `ModuleNotFoundError: langchain` | Run `pip install -r requirements.txt` |
| Backend won't start | Check `.env` exists and has valid API key |
| Frontend build fails | Run `cd frontend && npm install` |
| Security tests fail | Ensure `backend/safety/` modules are intact |
| SSE stream closes immediately | Check browser supports EventSource |
| V1 routes warning on startup | Expected during V2 migration — safe to ignore |
