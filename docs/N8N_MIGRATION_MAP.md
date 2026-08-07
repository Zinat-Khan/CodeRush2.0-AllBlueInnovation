# AE-03 n8n Migration Map Document (Directive V2 Compliance)

> **Directive Version**: V2 Master Architecture + Security + Implementation + Validation Directive  
> **Migration Rule**: Section 3 — REMOVE OBSOLETE N8N EXECUTION  
> **Status**: Audit & Mapping Complete — Ready for Removal in Module 1  

---

## 1. Overview

Master Directive V2 strictly mandates that the final AE-03 system **MUST NOT** depend on n8n for:
- Workflow execution
- Task scheduling
- Agent orchestration
- Webhooks
- Credentials
- State management
- Execution control

This document maps all existing n8n components to their new native **LangGraph + LangChain + Native @tool** replacements, assessing impact across backend routes, frontend components, environment variables, database/storage records, tests, and active features.

---

## 2. Component Migration Mapping Matrix

| OLD COMPONENT | CURRENT RESPONSIBILITY | NEW LANGGRAPH / TOOL / SERVICE COMPONENT | MIGRATION STATUS | IMPACT / CONSUMERS |
| :--- | :--- | :--- | :--- | :--- |
| `backend/integrations/n8n_client.py` | HTTP POST client (`N8nClient`) forwarding agent tool requests to n8n webhook URLs | Native LangChain `@tool` functions executed directly within Python backend process | **SCHEDULED FOR REMOVAL (Module 1)** | Consumed by `worker_data.py`, `worker_code.py`, `worker_api.py`. Backend routes will use LangGraph tool execution nodes. |
| `n8n_workflows/worker_data_workflow.json` | n8n workflow for data research and web search | Native LangChain `@tool` `public_search` and `public_document_retrieval` in `/backend/tools/search_tools.py` | **SCHEDULED FOR REMOVAL (Module 1)** | Replaced by native Python searching tools using DuckDuckGo / Tavily / Serper or native HTTP requests. |
| `n8n_workflows/worker_code_workflow.json` | n8n workflow for code execution and data analysis | Native LangChain `@tool` `analyze_dataset` and `calculate_metric` in `/backend/tools/analysis_tools.py` | **SCHEDULED FOR REMOVAL (Module 1)** | Replaced by native Python math and data analysis functions with PolicyEngine sandboxing. |
| `n8n_workflows/worker_api_workflow.json` | n8n workflow for external REST API calls | Native LangChain `@tool` HTTP requests in `/backend/tools/api_tools.py` with URL validation | **SCHEDULED FOR REMOVAL (Module 1)** | Replaced by `httpx` native tool calls guarded by PolicyEngine network security rules. |
| `n8n_workflows/README.md` | Documentation for configuring n8n cloud webhooks | `/docs/N8N_MIGRATION_MAP.md` (this file) documenting elimination of n8n | **SCHEDULED FOR REMOVAL (Module 1)** | Replaced by native architecture documentation in `/docs/ARCHITECTURE.md`. |
| `backend/agents/worker_data.py` | Agent making HTTP requests to n8n `/worker-data-hook` | LangChain `RESEARCHER` Agent invoking `@tool public_search` | **SCHEDULED FOR REFACTORING (Module 2/3)** | Rebound to LangGraph node invoking native `@tool`. |
| `backend/agents/worker_code.py` | Agent making HTTP requests to n8n `/worker-code-hook` | LangChain `EXECUTOR` / `ANALYST` Agent invoking `@tool analyze_dataset` | **SCHEDULED FOR REFACTORING (Module 2/3)** | Rebound to LangGraph node invoking native `@tool`. |
| `backend/agents/worker_api.py` | Agent making HTTP requests to n8n `/worker-api-hook` | LangChain Agent invoking native API `@tool` | **SCHEDULED FOR REFACTORING (Module 2/3)** | Rebound to LangGraph node invoking native `@tool`. |
| `N8N_WEBHOOK_BASE_URL` in `.env` / `.env.example` / `backend/config.py` | Environment variable storing n8n webhook base URL | Removed from configuration schema. Replaced by `PRIMARY_PROVIDER`, `GOOGLE_API_KEY`, etc. | **SCHEDULED FOR REMOVAL (Module 1)** | `backend/config.py` updated to drop `n8n_webhook_base_url` setting. |

---

## 3. Detailed Inspection & Risk Assessment

Before removing n8n infrastructure, Directive V2 Section 3 mandates inspecting:

1. **What the n8n integration currently does**: Forwards agent task payloads over HTTP to external n8n cloud webhooks (`/worker-data-hook`, `/worker-code-hook`, `/worker-api-hook`).
2. **Which backend routes depend on it**: `POST /api/execute` currently triggers the custom executor which dispatches workers calling `n8n_client.py`.
3. **Which frontend components depend on those routes**: Frontend (`app/page.tsx`) calls `/api/execute`. Frontend does NOT depend on n8n directly; it consumes backend REST/SSE.
4. **Which environment variables exist for it**: `N8N_WEBHOOK_BASE_URL` in `.env.example`, `.env`, and `backend/config.py`.
5. **Which database records depend on it**: No database records depend on n8n (state is transient in memory/runs store).
6. **Which tests depend on it**: Unit tests (`test_module4.py`) mock `n8n_client.py`.
7. **Which features would break if it were removed**: Execution of worker nodes would fail if removed without adding native `@tool` replacements.

---

## 4. Migration Execution Plan (Module 1 & 2)

1. **Module 1**:
   - Update `requirements.txt` to add `langchain`, `langgraph`, `langchain-google-genai`, `langchain-openai`, `langchain-community`, `chromadb`, `sentence-transformers`, `faiss-cpu`, `pydantic>=2.10.0`.
   - Update `.env.example` and `backend/config.py` to remove `N8N_WEBHOOK_BASE_URL` and add Google/OpenAI/Ollama/RAG configuration settings.
   - Deprecate `backend/integrations/n8n_client.py` and delete `n8n_workflows/` folder.
2. **Module 2**:
   - Implement LangChain Multi-Provider Model Router (`/backend/models/model_router.py`).
3. **Module 3**:
   - Implement native `@tool` functions and RAG knowledge pipeline (`/backend/rag/`, `/backend/tools/`).
4. **Module 4**:
   - Implement Task-to-Graph Compiler (`/backend/graph/task_compiler.py`) producing LangGraph `StateGraph`.

---

## 5. Verification & Audit Trail

Searching the codebase confirms the following n8n references to be migrated:
- `backend/integrations/n8n_client.py`
- `n8n_workflows/README.md`
- `n8n_workflows/worker_api_workflow.json`
- `n8n_workflows/worker_code_workflow.json`
- `n8n_workflows/worker_data_workflow.json`
- `backend/config.py` (`n8n_webhook_base_url`)
- `.env.example` (`N8N_WEBHOOK_BASE_URL`)

All references will be systematically replaced with native LangChain/LangGraph constructs without leaving dead code.
