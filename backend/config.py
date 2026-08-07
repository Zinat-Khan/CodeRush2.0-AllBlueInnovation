"""
AE-03 Environment Vault & Application Configuration.

Uses Pydantic BaseSettings to load environment variables from .env file.
Provides a singleton configuration instance via get_settings().
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

    # ── LLM Provider Keys ──────────────────────────────────────────────
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key for GPT-4o / GPT-4o-mini.",
    )
    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API key for Gemini 1.5 Pro.",
    )
    ollama_host: str = Field(
        default="http://localhost:11434",
        description="Base URL for the local Ollama server.",
    )

    # ── n8n Integration ────────────────────────────────────────────────
    n8n_webhook_base_url: str = Field(
        default="https://uzaifah.app.n8n.cloud/webhook",
        description="Base URL for n8n webhook endpoints.",
    )

    # ── Application Defaults ───────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Logging verbosity.")
    default_provider: str = Field(
        default="openai",
        description="Default LLM provider (openai | gemini | ollama).",
    )
    default_model_openai: str = Field(
        default="gpt-4o",
        description="Default OpenAI model identifier.",
    )
    default_model_gemini: str = Field(
        default="gemini-1.5-pro",
        description="Default Gemini model identifier.",
    )
    default_model_ollama: str = Field(
        default="llama3",
        description="Default Ollama model identifier.",
    )

    # ── Engine Defaults ────────────────────────────────────────────────
    max_retries: int = Field(
        default=2,
        description="Maximum retry attempts per failed node.",
    )
    default_timeout_seconds: int = Field(
        default=120,
        description="Default per-node execution timeout in seconds.",
    )
    scratch_memory_ttl_seconds: int = Field(
        default=300,
        description="Default TTL for agent scratch memory entries (seconds).",
    )
    scratch_memory_max_entries: int = Field(
        default=1000,
        description="Maximum scratch memory entries per agent before LRU eviction.",
    )

    # ── Helpers ────────────────────────────────────────────────────────
    def get_model_for_provider(self, provider: str) -> str:
        """Return the default model identifier for the given provider name."""
        mapping = {
            "openai": self.default_model_openai,
            "gemini": self.default_model_gemini,
            "ollama": self.default_model_ollama,
        }
        return mapping.get(provider, self.default_model_openai)

    def has_provider_key(self, provider: str) -> bool:
        """Check whether the required API key is configured for a provider."""
        if provider == "openai":
            return bool(self.openai_api_key)
        if provider == "gemini":
            return bool(self.gemini_api_key)
        if provider == "ollama":
            return True  # No API key needed for local Ollama
        return False


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the singleton AppSettings instance (cached after first call)."""
    return AppSettings()
