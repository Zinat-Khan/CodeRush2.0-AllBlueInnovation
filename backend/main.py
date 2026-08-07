"""
AE-03 FastAPI Application — Entry Point.

Provides:
  - CORS-enabled FastAPI app serving the REST + SSE API
  - Health check endpoint at ``GET /api/health``
  - Lifespan startup/shutdown hooks for provider initialisation
  - Route mounting from ``backend.api.routes``
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import get_settings

logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager — runs on startup and shutdown.

    Startup:
      - Validates environment settings
      - Eagerly initialises the provider router
      - Logs available LLM providers

    Shutdown:
      - Logs graceful shutdown
    """
    settings = get_settings()
    logger.info(
        "AE-03 starting | provider=%s | log_level=%s",
        settings.primary_provider,
        settings.log_level,
    )

    # Eagerly init the provider router so first request is fast
    from backend.providers.router import ProviderRouter
    router = ProviderRouter()
    try:
        available = router.get_available_providers()
        logger.info("Available LLM providers: %s", available)
    except Exception as e:
        logger.warning("Provider init warning: %s", e)

    yield

    logger.info("AE-03 shutting down gracefully")


# ── App Factory ────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    application = FastAPI(
        title="AE-03 Orchestrator API",
        description=(
            "Multi-agent orchestration engine with DAG-based execution, "
            "real-time observability, and human-in-the-loop approval. "
            "Compile natural-language goals into executable agent graphs."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # ── CORS ───────────────────────────────────────────────────────────
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routes ─────────────────────────────────────────────────────────
    # V2 routes (Directive V2 — LangGraph-based)
    from backend.api.routes_v2 import router as v2_router
    from backend.api.sse import router as sse_router
    application.include_router(v2_router, prefix="/api")
    application.include_router(sse_router, prefix="/api")

    # ── Health Check ───────────────────────────────────────────────────
    @application.get("/api/health", tags=["system"])
    async def health_check():
        """Basic health check — confirms API is reachable."""
        return JSONResponse(
            content={
                "status": "healthy",
                "service": "ae03-orchestrator",
                "version": "0.1.0",
                "timestamp": time.time(),
            }
        )

    return application


# ── App Instance ───────────────────────────────────────────────────────

app = create_app()

# ── Logging Setup ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
