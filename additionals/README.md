# Additionals & Supplementary Utilities

This directory (`additionals/`) contains modular supplementary tools, batch scripts, data loaders, and benchmark utilities for the **AE-03 Directive V2 Multi-Agent Orchestration Platform**.

---

## 📁 Directory Structure

```
additionals/
├── README.md               # Overview and usage instructions
├── rag_demo_loader.py      # Standalone document ingestion & pgvector RAG loader
└── benchmark_runner.py    # Multi-agent latency, cost, and provider evaluation suite
```

---

## 🛠️ Included Utilities

### 1. `rag_demo_loader.py`
Ingests reference documents (PDFs, CSVs, TXT, Markdown) into the vector store (`Supabase pgvector` or `Chroma`) with workspace isolation tags.

**Usage:**
```bash
python additionals/rag_demo_loader.py --file path/to/document.pdf --workspace default
```

### 2. `benchmark_runner.py`
Runs a suite of automated benchmark goals across all configured LLM providers (`OpenRouter`, `Groq`, `Gemini`, `OpenAI`) to measure average node latency, token consumption, and model costs.

**Usage:**
```bash
python additionals/benchmark_runner.py --runs 5
```
