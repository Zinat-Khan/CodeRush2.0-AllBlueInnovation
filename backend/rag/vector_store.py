"""
AE-03 Vector Store Adapter (Directive V2).

Abstracts vector storage behind a unified interface supporting:
  - **Chroma** (default, persistent, via ``chromadb``)
  - **FAISS** (in-memory, via ``faiss-cpu``)

Embeddings via ``GoogleGenerativeAIEmbeddings`` (primary) or
``HuggingFaceEmbeddings`` (fallback).

Enforces workspace isolation — every document and query is scoped to
a ``workspace_id`` via metadata filtering.

Pipeline position: EMBEDDING → **VECTOR STORE** → SIMILARITY SEARCH
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from backend.config import AppSettings, get_settings
from backend.schemas.contracts import RAGChunk, RAGDocument

logger = logging.getLogger(__name__)


# ── Embedding Factory ────────────────────────────────────────────────


def _create_embeddings(settings: AppSettings):
    """
    Create an embedding model instance based on configuration.

    Returns a LangChain Embeddings instance.
    """
    provider = settings.embedding_provider

    if provider == "google" and settings.google_api_key:
        try:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            return GoogleGenerativeAIEmbeddings(
                model="models/embedding-001",
                google_api_key=settings.google_api_key,
            )
        except Exception as e:
            logger.warning("Google embeddings failed, falling back to HuggingFace: %s", e)

    # Fallback: HuggingFace (local, free)
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
        )
    except ImportError:
        logger.warning("HuggingFaceEmbeddings not available, using mock embeddings.")
        return _MockEmbeddings()


class _MockEmbeddings:
    """Fallback mock embeddings for development/testing when no real provider is available."""

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Generate deterministic mock embeddings from text hash."""
        return [self._hash_embed(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        """Generate a deterministic mock embedding for a query."""
        return self._hash_embed(text)

    @staticmethod
    def _hash_embed(text: str, dim: int = 384) -> List[float]:
        """Create a deterministic embedding from text hash."""
        h = hashlib.sha256(text.encode()).hexdigest()
        # Use hash bytes to generate float values between -1 and 1
        values = []
        for i in range(dim):
            byte_val = int(h[(i * 2) % len(h):(i * 2 + 2) % len(h) or len(h)], 16)
            values.append((byte_val / 255.0) * 2 - 1)
        return values


# ── Vector Store Adapter ─────────────────────────────────────────────


class VectorStoreAdapter:
    """
    Unified vector store abstraction with workspace isolation.

    Supports Chroma (persistent) and FAISS (in-memory) backends.
    All operations are scoped to a ``workspace_id`` to enforce
    multi-tenant data isolation.

    Usage::

        adapter = VectorStoreAdapter()
        doc_id = await adapter.ingest_document(
            filename="report.pdf",
            chunks=["chunk1 text", "chunk2 text"],
            workspace_id="ws-123",
        )
        results = await adapter.similarity_search(
            query="What is the conclusion?",
            workspace_id="ws-123",
            top_k=5,
        )
    """

    def __init__(self, settings: Optional[AppSettings] = None):
        self._settings = settings or get_settings()
        self._embeddings = _create_embeddings(self._settings)
        self._store = None
        self._store_type = self._settings.vector_store_type
        self._init_store()

    def _init_store(self) -> None:
        """Initialise the backing vector store."""
        if self._store_type == "chroma":
            self._init_chroma()
        elif self._store_type == "faiss":
            self._init_faiss()
        else:
            logger.warning(
                "Unknown vector_store_type '%s', defaulting to chroma.",
                self._store_type,
            )
            self._store_type = "chroma"
            self._init_chroma()

    def _init_chroma(self) -> None:
        """Initialise Chroma persistent store."""
        try:
            from langchain_community.vectorstores import Chroma

            persist_dir = self._settings.chroma_persist_dir
            os.makedirs(persist_dir, exist_ok=True)

            self._store = Chroma(
                collection_name="ae03_documents",
                embedding_function=self._embeddings,
                persist_directory=persist_dir,
            )
            logger.info("Chroma vector store initialised at %s", persist_dir)
        except ImportError:
            logger.warning("chromadb not installed. Falling back to in-memory FAISS.")
            self._store_type = "faiss"
            self._init_faiss()
        except Exception as e:
            logger.error("Chroma init failed: %s. Falling back to FAISS.", e)
            self._store_type = "faiss"
            self._init_faiss()

    def _init_faiss(self) -> None:
        """Initialise FAISS in-memory store."""
        try:
            from langchain_community.vectorstores import FAISS

            # FAISS needs at least one document to init, so we start empty
            # and lazy-create on first ingest
            self._store = None  # Will be created on first add
            self._faiss_class = FAISS
            logger.info("FAISS vector store ready (lazy init).")
        except ImportError:
            logger.error("Neither chromadb nor faiss-cpu installed. Vector store unavailable.")
            self._store = None

    # ── Ingest ────────────────────────────────────────────────────────

    async def ingest_document(
        self,
        filename: str,
        chunks: List[str],
        workspace_id: str = "default_workspace",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RAGDocument:
        """
        Ingest document chunks into the vector store.

        Args:
            filename: Original document filename.
            chunks: Pre-split text chunks.
            workspace_id: Workspace scope for isolation.
            metadata: Optional extra metadata per document.

        Returns:
            RAGDocument record with chunk count.
        """
        extra_meta = metadata or {}
        content_hash = hashlib.sha256("".join(chunks).encode()).hexdigest()

        # Build LangChain Document objects with workspace isolation metadata
        documents = []
        rag_chunks = []

        for idx, chunk_text in enumerate(chunks):
            doc_meta = {
                "workspace_id": workspace_id,
                "filename": filename,
                "chunk_index": idx,
                "content_hash": content_hash,
                **extra_meta,
            }
            documents.append(Document(page_content=chunk_text, metadata=doc_meta))

            rag_chunks.append(
                RAGChunk(
                    document_id="",  # Will be set below
                    content=chunk_text,
                    chunk_index=idx,
                    workspace_id=workspace_id,
                    metadata=doc_meta,
                )
            )

        # Add to vector store
        if self._store_type == "faiss" and self._store is None:
            # Lazy FAISS init with first batch
            self._store = self._faiss_class.from_documents(
                documents, self._embeddings
            )
        elif self._store is not None:
            self._store.add_documents(documents)
        else:
            logger.error("No vector store available for ingestion.")

        # Build RAGDocument record
        rag_doc = RAGDocument(
            filename=filename,
            workspace_id=workspace_id,
            chunk_count=len(chunks),
            total_tokens=sum(len(c.split()) for c in chunks),
            metadata={**extra_meta, "content_hash": content_hash},
        )

        # Backfill document_id into chunks
        for chunk in rag_chunks:
            chunk.document_id = rag_doc.document_id

        logger.info(
            "Ingested %d chunks from '%s' into %s (workspace=%s)",
            len(chunks),
            filename,
            self._store_type,
            workspace_id,
        )

        return rag_doc

    # ── Search ────────────────────────────────────────────────────────

    async def similarity_search(
        self,
        query: str,
        workspace_id: str = "default_workspace",
        top_k: int = 5,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """
        Perform similarity search scoped to a workspace.

        Args:
            query: Search query text.
            workspace_id: Workspace scope for isolation.
            top_k: Number of results to return.

        Returns:
            List of (content, score, metadata) tuples.
        """
        if self._store is None:
            logger.warning("Vector store not initialised. Returning empty results.")
            return []

        try:
            # Use metadata filter for workspace isolation
            filter_dict = {"workspace_id": workspace_id}

            if self._store_type == "chroma":
                results = self._store.similarity_search_with_relevance_scores(
                    query, k=top_k, filter=filter_dict
                )
            else:
                # FAISS doesn't support native metadata filtering,
                # so we fetch more and filter post-hoc
                results_raw = self._store.similarity_search_with_score(
                    query, k=top_k * 3
                )
                results = [
                    (doc, score)
                    for doc, score in results_raw
                    if doc.metadata.get("workspace_id") == workspace_id
                ][:top_k]

            return [
                (doc.page_content, float(score), doc.metadata)
                for doc, score in results
            ]

        except Exception as e:
            logger.error("Similarity search failed: %s", e)
            return []

    # ── Delete ────────────────────────────────────────────────────────

    async def delete_workspace(self, workspace_id: str) -> int:
        """
        Delete all documents for a workspace.

        Returns count of deleted documents.
        """
        if self._store is None:
            return 0

        try:
            if self._store_type == "chroma" and hasattr(self._store, "_collection"):
                # Chroma supports deletion by metadata filter
                collection = self._store._collection
                results = collection.get(where={"workspace_id": workspace_id})
                ids = results.get("ids", [])
                if ids:
                    collection.delete(ids=ids)
                    logger.info(
                        "Deleted %d chunks for workspace '%s'", len(ids), workspace_id
                    )
                    return len(ids)
            else:
                logger.warning(
                    "Delete not supported for %s store.", self._store_type
                )
        except Exception as e:
            logger.error("Delete failed: %s", e)

        return 0

    # ── Observability ─────────────────────────────────────────────────

    def get_store_info(self) -> Dict[str, Any]:
        """Return store metadata for observability."""
        info = {
            "store_type": self._store_type,
            "available": self._store is not None,
            "embedding_provider": self._settings.embedding_provider,
        }

        if self._store_type == "chroma" and self._store is not None:
            try:
                collection = self._store._collection
                info["document_count"] = collection.count()
            except Exception:
                info["document_count"] = -1

        return info
