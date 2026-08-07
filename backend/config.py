"""
AE-03 Environment Vault & Application Configuration (Directive V2).

Uses Pydantic BaseSettings to load environment variables from .env file.
Provides a singleton configuration instance via get_settings().

Changes from V1:
  - Removed: n8n_webhook_base_url
  - Added: PRIMARY_PROVIDER, GOOGLE_API_KEY, GOOGLE_MODEL, RAG settings,
           run budget limits, scratchpad TTL
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Central configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Primary Provider Selection ────────────────────────────────────
    primary_provider: str = Field(
        default="google",
        description="Primary LLM provider (google | openai | ollama).",
    )
    primary_model: str = Field(
        default="gemini-1.5-pro",
        description="Primary model identifier.",
    )

    # ── Google Gemini ─────────────────────────────────────────────────
    google_api_key: str = Field(
        default="",
        description="Google API key for Gemini models.",
    )
    google_model: str = Field(
        default="gemini-1.5-pro",
        description="Default Google Gemini model identifier.",
    )

    # ── OpenAI (Fallback 1) ───────────────────────────────────────────
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key for GPT-4o / GPT-4o-mini.",
    )
    openai_model: str = Field(
        default="gpt-4o",
        description="Default OpenAI model identifier.",
    )

    # ── Ollama (Fallback 2 — Local) ───────────────────────────────────
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Base URL for the local Ollama server.",
    )
    ollama_model: str = Field(
        default="llama3.2",
        description="Default Ollama model identifier.",
    )

    # ── RAG Pipeline ──────────────────────────────────────────────────
    vector_store_type: str = Field(
        default="chroma",
        description="Vector store backend (chroma | faiss).",
    )
    embedding_provider: str = Field(
        default="google",
        description="Embedding provider (google | huggingface).",
    )
    chroma_persist_dir: str = Field(
        default="./data/chroma",
        description="Directory for Chroma vector store persistence.",
    )
    rag_chunk_size: int = Field(
        default=1000,
        description="RecursiveCharacterTextSplitter chunk_size.",
    )
    rag_chunk_overlap: int = Field(
        default=200,
        description="RecursiveCharacterTextSplitter chunk_overlap.",
    )

    # ── Run Budget Limits ─────────────────────────────────────────────
    max_runtime_seconds: int = Field(
        default=300,
        description="Maximum wall-clock runtime per execution run.",
    )
    max_tokens: int = Field(
        default=100000,
        description="Maximum total tokens per execution run.",
    )
    max_cost: float = Field(
        default=5.0,
        description="Maximum USD cost per execution run.",
    )

    # ── Scratchpad / Memory ───────────────────────────────────────────
    scratchpad_ttl_seconds: int = Field(
        default=300,
        description="TTL for scratch memory entries (seconds).",
    )
    max_scratchpad_entries: int = Field(
        default=100,
        description="Maximum number of scratchpad entries per run.",
    )

    # ── Application ───────────────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Logging verbosity.")

    # ── Derived Helpers ───────────────────────────────────────────────

    def get_provider_api_key(self, provider: str) -> str:
        """Return the API key for a given provider name."""
        mapping = {
            "google": self.google_api_key,
            "openai": self.openai_api_key,
        }
        return mapping.get(provider, "")

    def get_provider_model(self, provider: str) -> str:
        """Return the default model for a given provider name."""
        mapping = {
            "google": self.google_model,
            "openai": self.openai_model,
            "ollama": self.ollama_model,
        }
        return mapping.get(provider, self.primary_model)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a cached singleton AppSettings instance."""
    return AppSettings()
