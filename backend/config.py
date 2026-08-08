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

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Central configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # ── Primary Provider Selection ────────────────────────────────────
    primary_provider: str = Field(
        default="openai",
        description="Primary LLM provider (google | openai | ollama).",
    )
    primary_model: str = Field(
        default="gemini-1.5-pro",
        description="Primary model identifier.",
    )

    # ── Google Gemini (Main 1) ────────────────────────────────────────
    google_api_key: str = Field(
        default="",
        description="Google API key for Gemini models.",
        validation_alias=AliasChoices("google_api_key", "gemini_api_key", "GOOGLE_API_KEY", "GEMINI_API_KEY"),
    )
    google_model: str = Field(
        default="gemini-2.0-flash",
        description="Default Google Gemini model identifier.",
    )


    # ── OpenAI (Main 2) ───────────────────────────────────────────────
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key for GPT-4o / GPT-4o-mini.",
    )
    openai_model: str = Field(
        default="gpt-4o",
        description="Default OpenAI model identifier.",
    )

    # ── Groq (Main 3) ─────────────────────────────────────────────────
    groq_api_key: str = Field(
        default="",
        description="Groq API key.",
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Default Groq model identifier.",
    )

    # ── OpenRouter Fallback APIs (Keys 1-7) ────────────────────────────
    openrouter_key_1: str = Field(
        default="",
        description="OpenRouter Key 1 (Claude 3.5).",
    )
    openrouter_key_2: str = Field(
        default="",
        description="OpenRouter Key 2 (ChatGPT).",
    )
    openrouter_key_3: str = Field(
        default="",
        description="OpenRouter Key 3 (Gemini).",
    )
    openrouter_key_4: str = Field(
        default="",
        description="OpenRouter Key 4 (Claude).",
    )
    openrouter_key_5: str = Field(
        default="",
        description="OpenRouter Key 5 (ChatGPT).",
    )
    openrouter_key_6: str = Field(
        default="",
        description="OpenRouter Key 6 (Gemini Embeddings).",
    )
    openrouter_key_7: str = Field(
        default="",
        description="OpenRouter Key 7 (ChatGPT).",
    )

    # ── Real-Time Web Research & Domain Data APIs ─────────────────────
    tavily_api_key: str = Field(
        default="",
        description="Tavily Search API key.",
    )
    serper_api_key: str = Field(
        default="",
        description="Serper.dev Google Search API key.",
    )
    alpha_vantage_api_key: str = Field(
        default="",
        description="Alpha Vantage Financial Data API key.",
    )


    # ── Ollama (Local Fallback) ───────────────────────────────────────
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Base URL for the local Ollama server.",
    )
    ollama_model: str = Field(
        default="llama3.2",
        description="Default Ollama model identifier.",
    )

    # ── RAG Pipeline & Database ───────────────────────────────────────
    vector_store_type: str = Field(
        default="postgres",
        description="Vector store backend (postgres | chroma | faiss).",
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

    # ── PostgreSQL / Supabase Vector Database Configuration ───────────
    postgres_host: str = Field(
        default="db.owibnpmtjhrczimayetl.supabase.co",
        description="PostgreSQL host for vector storage.",
    )
    postgres_port: int = Field(
        default=5432,
        description="PostgreSQL port.",
    )
    postgres_db: str = Field(
        default="postgres",
        description="PostgreSQL database name.",
    )
    postgres_user: str = Field(
        default="postgres",
        description="PostgreSQL username.",
    )
    postgres_password: str = Field(
        default="",
        description="PostgreSQL password / DB Secret.",
    )
    supabase_url: str = Field(
        default="https://owibnpmtjhrczimayetl.supabase.co",
        description="Supabase project URL.",
    )
    supabase_publishable_key: str = Field(
        default="sb_publishable_DWjQGxtD1LIqRc6uRJwEyQ_IVg9s5IE",
        description="Supabase Publishable Key.",
    )
    supabase_secret: str = Field(
        default="",
        description="Supabase Secret Key.",
    )


    @property
    def postgres_connection_string(self) -> str:
        """Construct PostgreSQL connection string."""
        import urllib.parse
        pwd = urllib.parse.quote_plus(self.postgres_password)
        return f"postgresql://{self.postgres_user}:{pwd}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

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
        default=1000,
        description="Maximum number of scratchpad entries per run.",
    )

    # ── Application ───────────────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Logging verbosity.")

    # ── V1 Backward-Compat Aliases ────────────────────────────────────

    @property
    def gemini_api_key(self) -> str:
        """V1 alias for google_api_key."""
        return self.google_api_key

    @property
    def ollama_host(self) -> str:
        """V1 alias for ollama_base_url."""
        return self.ollama_base_url

    @property
    def default_provider(self) -> str:
        """V1 alias for primary_provider."""
        return self.primary_provider

    @property
    def scratch_memory_ttl_seconds(self) -> int:
        """V1 alias for scratchpad_ttl_seconds."""
        return self.scratchpad_ttl_seconds

    @property
    def scratch_memory_max_entries(self) -> int:
        """V1 alias for max_scratchpad_entries."""
        return self.max_scratchpad_entries

    @property
    def default_model_ollama(self) -> str:
        """V1 alias for ollama_model."""
        return self.ollama_model

    def get_model_for_provider(self, provider: str) -> str:
        """V1 compat: Return the default model for a given provider name."""
        mapping = {
            "openai": self.openai_model,
            "gemini": self.google_model,
            "google": self.google_model,
            "groq": self.groq_model,
            "ollama": self.ollama_model,
        }
        return mapping.get(provider, self.primary_model)

    def has_provider_key(self, provider: str) -> bool:
        """V1 compat: Check whether a provider has a configured API key or is available."""
        if provider == "ollama":
            return True  # Local, always available
        key = self.get_provider_api_key(provider)
        if key:
            return True
        # Also check V1 name 'gemini'
        if provider == "gemini":
            return bool(self.google_api_key)
        return False

    # ── Derived Helpers ───────────────────────────────────────────────

    def get_provider_api_key(self, provider: str) -> str:
        """Return the API key for a given provider name."""
        mapping = {
            "google": self.google_api_key,
            "gemini": self.google_api_key,
            "openai": self.openai_api_key,
            "groq": self.groq_api_key,
        }
        return mapping.get(provider, "")

    def get_provider_model(self, provider: str) -> str:
        """Return the default model for a given provider name."""
        mapping = {
            "google": self.google_model,
            "gemini": self.google_model,
            "openai": self.openai_model,
            "groq": self.groq_model,
            "ollama": self.ollama_model,
        }
        return mapping.get(provider, self.primary_model)



@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a cached singleton AppSettings instance."""
    return AppSettings()
