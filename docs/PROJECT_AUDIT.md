# AE-03 Project Audit Document (Directive V2 Compliance)

> **Directive Version**: V2 Master Architecture + Security + Implementation + Validation Directive  
> **Audit Date**: 2026-08-07  
> **Auditor**: Lead Architect & Agentic AI Engineer  

---

## 1. Executive Summary

This audit evaluates the current state of the repository against **Master Directive V2**, which mandates a critical architectural pivot:

- **Obsolete Architecture**: Custom Kahn DAG execution engine (`backend/engine/executor.py`), custom topological sorting (`backend/compiler/validator.py`), custom async graph execution, custom state management, and n8n webhook/workflow dependencies (`backend/integrations/n8n_client.py`, `n8n_workflows/`).
- **Required Architecture**: **LangGraph + LangChain + Native @tool Functions**, with `langchain-google-genai` as primary provider, LangGraph `StateGraph` as the sole execution engine, LangGraph checkpointing/interrupts, deterministic `PolicyEngine`, native RAG pipeline (`/backend/rag/`), and evaluation suite.

---

## 2. Component Inventory & Migration Analysis

| CURRENT COMPONENT | CURRENT RESPONSIBILITY | DEPENDENCIES | KEEP / MODIFY / REMOVE | REPLACEMENT | MIGRATION RISK |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `backend/engine/executor.py` | Custom Kahn DAG topological execution engine | `backend/schemas/contracts.py`, `backend/engine/state_manager.py` | **REMOVE** | LangGraph `StateGraph` execution engine | High — core engine replacement |
| `backend/engine/state_manager.py` | Custom `ExecutionState` and shared memory dict | Python standard library | **MODIFY** | LangGraph `AgentState` TypedDict / Pydantic state | Medium — state schema migration |
| `backend/engine/recovery.py` | Custom compensation router and retry policies | `backend/schemas/contracts.py` | **MODIFY** | LangGraph native retry policy & node exception handling | Medium — recovery logic migration |
| `backend/compiler/graph_compiler.py` | Compiles prompt into custom `ExecutionGraph` dict | `backend/providers/router.py`, `backend/compiler/validator.py` | **MODIFY** | Task-to-Graph Compiler (`/backend/graph/task_compiler.py`) producing LangGraph `StateGraph` | High — compiler output structure change |
| `backend/compiler/validator.py` | Custom Kahn algorithm cycle detection & graph validation | `backend/schemas/contracts.py` | **REMOVE** | LangGraph native DAG validation + task compiler pre-validation | Low — validation moved to compiler |
| `backend/compiler/prompt_templates.py` | System prompt string templates | None | **MODIFY** | LangChain prompt templates & structured output parsers | Low — string formatting change |
| `backend/integrations/n8n_client.py` | HTTP POST client calling n8n webhooks | `httpx`, `backend/config.py` | **REMOVE** | Native LangChain `@tool` functions | High — external dependency elimination |
| `backend/agents/worker_data.py` | Researcher agent making n8n webhook calls | `backend/integrations/n8n_client.py` | **MODIFY** | LangChain Native `@tool` Research Agent using `public_search` tool | Medium — agent implementation change |
| `backend/agents/worker_code.py` | Code executor agent making n8n webhook calls | `backend/integrations/n8n_client.py` | **MODIFY** | LangChain Native `@tool` Tool/Execution Agent using native tools | Medium — agent implementation change |
| `backend/agents/worker_api.py` | API worker agent making n8n webhook calls | `backend/integrations/n8n_client.py` | **MODIFY** | LangChain Native `@tool` API Agent using native HTTP tool | Medium — agent implementation change |
| `n8n_workflows/*` | n8n JSON workflow definitions | n8n Cloud | **REMOVE** | Native LangChain `@tool` functions inside Python backend | Low — files exist only for obsolete n8n architecture |
| `backend/providers/base.py` | Abstract custom LLM provider base class | `pydantic` | **MODIFY** | LangChain `BaseChatModel` abstraction layer | Medium — provider interface shift |
| `backend/providers/gemini_provider.py` | Direct Google GenAI API client | `google-generativeai` | **MODIFY** | `langchain-google-genai` (`ChatGoogleGenerativeAI`) | Medium — library migration |
| `backend/providers/openai_provider.py` | Direct OpenAI API client | `openai` | **MODIFY** | `langchain-openai` (`ChatOpenAI`) | Low — provider wrapper change |
| `backend/providers/ollama_provider.py` | Direct Ollama HTTP client | `httpx` | **MODIFY** | `langchain-community` (`ChatOllama`) | Low — provider wrapper change |
| `backend/providers/router.py` | Custom LLM provider router with fallback | All provider classes | **MODIFY** | LangChain Multi-Provider Model Router (`/backend/models/model_router.py`) | Medium — routing logic migration |
| `backend/safety/policy_engine.py` | Custom policy evaluation logic | `backend/schemas/contracts.py` | **MODIFY** | Deterministic PolicyEngine (`/backend/safety/policy_engine.py`) with deny-by-default security | Medium — governance engine upgrade |
| `backend/safety/permissions.py` | Custom permission models | `pydantic` | **MODIFY** | Agent Capability Matrix (`/backend/safety/agent_config.py`) | Low — schema update |
| `backend/safety/interceptor.py` | Custom execution middleware | `backend/schemas/contracts.py` | **MODIFY** | PolicyEngine tool interception & prompt-injection scanner | Medium — middleware upgrade |
| `backend/safety/approval_gate.py` | Custom HITL approval gate | `asyncio.Event` | **MODIFY** | LangGraph native `interrupt()` and resume mechanism | High — HITL mechanism shift |
| `backend/observability/tracker.py` | Token and cost tracking utility | Python time/dict | **KEEP / MODIFY** | Adapt to capture LangChain call metrics and LangGraph event traces | Low — metric collection enhancement |
| `backend/observability/tracer.py` | Trace logging & run store | Python dict | **KEEP / MODIFY** | Adapt to log LangGraph event stream | Low — event schema alignment |
| `backend/observability/replay.py` | Custom run replay engine | `backend/engine/executor.py` | **MODIFY** | LangGraph Replay Engine using stored LangGraph thread checkpoints | Medium — replay backend migration |
| `backend/evaluation/benchmark.py` | 3-mode evaluation runner | `backend/engine/executor.py` | **MODIFY** | LangGraph Evaluation Harness with baseline comparison | Medium — evaluation harness migration |
| `backend/evaluation/tasks.py` | Benchmark task loader | Markdown parsing | **KEEP** | Standard task loader from `evaluation/DATA_PROVENANCE.md` | None — zero risk |
| `backend/evaluation/reporter.py` | Comparative report generator | Pydantic models | **KEEP / MODIFY** | Adapt for LangGraph evaluation metrics | Low — report format alignment |
| `backend/api/routes.py` | FastAPI REST endpoints | `backend/engine/executor.py` | **MODIFY** | Adapt to execute and inspect LangGraph StateGraphs | Medium — API handler migration |
| `backend/api/sse.py` | SSE event streaming endpoints | `backend/observability/tracer.py` | **MODIFY** | Stream LangGraph event stream via SSE | Low — event stream mapping |
| `backend/main.py` | FastAPI application factory | `fastapi`, `uvicorn` | **KEEP / MODIFY** | Register new LangGraph, RAG, and API routes | Low — route registration update |
| `backend/config.py` | Pydantic BaseSettings config | `pydantic-settings` | **MODIFY** | Add `PRIMARY_PROVIDER`, `PRIMARY_MODEL`, RAG config, run budgets | Low — settings extension |
| `frontend/*` | Next.js + React Flow frontend | Next.js, React Flow | **KEEP / MODIFY** | Update UI state mapping to consume LangGraph event stream and HITL interrupts | Medium — frontend API binding update |
| `evaluation/DATA_PROVENANCE.md` | Benchmark task dataset documentation | Markdown | **KEEP** | DATA_PROVENANCE specification | None — zero risk |

---

## 3. Mandatory Directive V2 Architectural Requirements

1. **LangGraph as Sole Orchestration Engine**: Eliminate all custom Kahn DAG execution engines (`executor.py`, `validator.py`). LangGraph `StateGraph` manages graph execution, state, transitions, conditional routing, checkpoints, interrupts, retries, and recovery.
2. **Elimination of n8n Execution**: Remove all n8n webhook calls, webhooks, clients, credentials, and workflow execution. Replace with native LangChain `@tool` functions inside the backend.
3. **Core Technology Stack**: Python 3.11+, FastAPI, Pydantic v2, LangChain, LangGraph, `langchain-google-genai` (`ChatGoogleGenerativeAI`) as primary provider, with fallback to OpenAI and Ollama through LangChain.
4. **Native LangChain `@tool` Capabilities**: Implement native tools (`similarity_search`, `analyze_dataset`, `retrieve_public_document`, `generate_visualization`, `calculate_metric`, `public_search`).
5. **Native RAG Pipeline**: Build `/backend/rag/` using `RecursiveCharacterTextSplitter` (1000/200), Chroma/FAISS vector storage, `GoogleGenerativeAIEmbeddings`/`HuggingFaceEmbeddings`, `VectorStoreAdapter`, and workspace isolation.
6. **Deterministic PolicyEngine & Deny-by-Default Security**: Implement `/backend/safety/policy_engine.py` with deny-by-default authorization, prompt-injection scanner (external content treated as untrusted data), file/network/command execution boundaries.
7. **LangGraph HITL Interrupt/Resume**: Replace fake/custom approval loops with real LangGraph `interrupt()` and resume APIs (`/api/workflow/approve/{run_id}`, `/api/workflow/reject/{run_id}`, `/api/workflow/request-changes/{run_id}`).
8. **11 Specialised Agent Logical Components**: Orchestrator, Planner, Researcher, RAG Agent, Tool/Execution Agent, Analyst, Critic, Verifier, Security/Policy Layer, Reporter, Visualization Agent.
9. **Strict 11-Module Build Sequence**: Execute exactly Modules 1 through 11 with the mandatory step-gate protocol.
