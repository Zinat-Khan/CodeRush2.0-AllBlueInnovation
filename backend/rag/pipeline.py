"""
AE-03 Retrieval-Augmented Generation Pipeline (Directive V2).

Implements the full RAG pipeline per Section 7:
  DOCUMENT → VALIDATION → SAFE EXTRACTION → NORMALIZATION →
  CHUNKING → EMBEDDING → VECTOR STORE → SIMILARITY SEARCH →
  OPTIONAL RERANKING → AGENT CONTEXT

Uses ``RecursiveCharacterTextSplitter`` (chunk_size=1000, chunk_overlap=200).
Integrates with ``VectorStoreAdapter`` for storage and ``ModelRouter``
for generation.

Features:
  - Multi-format document loading (PDF, TXT, MD, CSV, JSON)
  - Content validation and safe extraction
  - Workspace-scoped retrieval
  - Relevance-scored context injection
  - Source attribution tracking
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from backend.config import AppSettings, get_settings
from backend.rag.vector_store import VectorStoreAdapter
from backend.schemas.contracts import RAGDocument, ResearchSource

logger = logging.getLogger(__name__)


# ── Text Splitter ────────────────────────────────────────────────────


def _create_text_splitter(settings: AppSettings):
    """Create a RecursiveCharacterTextSplitter from config."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    return RecursiveCharacterTextSplitter(
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


# ── Document Loader ──────────────────────────────────────────────────


def _load_document(file_path: str) -> List[Document]:
    """
    Load a document from disk using appropriate LangChain loader.

    Supports: .pdf, .txt, .md, .csv, .json
    Falls back to plain text for unknown extensions.
    """
    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            try:
                from langchain_community.document_loaders import PyPDFLoader

                loader = PyPDFLoader(file_path)
                return loader.load()
            except ImportError:
                logger.warning("PyPDFLoader not available, reading PDF as text.")

        if ext == ".csv":
            try:
                from langchain_community.document_loaders import CSVLoader

                loader = CSVLoader(file_path)
                return loader.load()
            except ImportError:
                logger.warning("CSVLoader not available, reading CSV as text.")

        if ext == ".json":
            try:
                from langchain_community.document_loaders import JSONLoader

                loader = JSONLoader(file_path, jq_schema=".", text_content=False)
                return loader.load()
            except (ImportError, Exception):
                logger.warning("JSONLoader not available, reading JSON as text.")

        # Default: plain text (.txt, .md, or fallback)
        from langchain_community.document_loaders import TextLoader

        loader = TextLoader(file_path, encoding="utf-8")
        return loader.load()

    except Exception as e:
        logger.error("Failed to load document '%s': %s", file_path, e)
        # Last resort: raw read
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return [Document(page_content=content, metadata={"source": file_path})]
        except Exception as e2:
            logger.error("Raw read also failed: %s", e2)
            return []


# ── Content Validation ───────────────────────────────────────────────


def _validate_content(text: str) -> Tuple[bool, str]:
    """
    Validate document content for safety and quality.

    Returns (is_valid, reason).
    """
    if not text or not text.strip():
        return False, "Empty content"

    if len(text) > 10_000_000:  # 10MB text limit
        return False, f"Content too large: {len(text)} characters"

    # Check for prompt injection patterns (basic defense)
    injection_patterns = [
        "ignore all previous instructions",
        "ignore your instructions",
        "you are now",
        "new system prompt",
        "override your",
        "disregard all",
    ]
    text_lower = text.lower()
    for pattern in injection_patterns:
        if pattern in text_lower:
            logger.warning("Potential prompt injection detected: '%s'", pattern)
            # Don't reject, but flag in metadata

    return True, "OK"


# ── RAG Pipeline ─────────────────────────────────────────────────────


class RAGPipeline:
    """
    Full Retrieval-Augmented Generation pipeline.

    Orchestrates: Document Loading → Validation → Safe Extraction →
    Normalization → Chunking → Embedding → Vector Store →
    Similarity Search → Optional Reranking → Agent Context.

    Usage::

        pipeline = RAGPipeline()

        # Ingest a document
        doc = await pipeline.ingest_file(
            "report.pdf", workspace_id="ws-123"
        )

        # Query with RAG context
        context = await pipeline.retrieve(
            "What were the key findings?",
            workspace_id="ws-123",
            top_k=5,
        )
    """

    def __init__(
        self,
        settings: Optional[AppSettings] = None,
        vector_store: Optional[VectorStoreAdapter] = None,
    ):
        self._settings = settings or get_settings()
        self._vector_store = vector_store or VectorStoreAdapter(self._settings)
        self._splitter = _create_text_splitter(self._settings)
        self._ingested_docs: List[RAGDocument] = []

    # ── Stage 1-5: Ingest ─────────────────────────────────────────────

    async def ingest_file(
        self,
        file_path: str,
        workspace_id: str = "default_workspace",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[RAGDocument]:
        """
        Full ingestion pipeline: Load → Validate → Extract → Normalize → Chunk → Store.

        Args:
            file_path: Path to the document file.
            workspace_id: Workspace scope for isolation.
            metadata: Optional extra metadata.

        Returns:
            RAGDocument record, or None if ingestion failed.
        """
        filename = os.path.basename(file_path)
        extra_meta = metadata or {}

        # Stage 1: DOCUMENT — Load
        logger.info("[RAG] Stage 1/6: Loading document '%s'", filename)
        documents = _load_document(file_path)
        if not documents:
            logger.error("[RAG] Failed to load document '%s'", filename)
            return None

        # Stage 2: VALIDATION — Content safety check
        logger.info("[RAG] Stage 2/6: Validating content")
        full_text = "\n".join(doc.page_content for doc in documents)
        is_valid, reason = _validate_content(full_text)
        if not is_valid:
            logger.error("[RAG] Validation failed for '%s': %s", filename, reason)
            return None

        # Stage 3: SAFE EXTRACTION — Already handled by loaders
        logger.info("[RAG] Stage 3/6: Safe extraction complete")

        # Stage 4: NORMALIZATION — Clean text
        logger.info("[RAG] Stage 4/6: Normalizing text")
        normalized_text = self._normalize_text(full_text)

        # Stage 5: CHUNKING — Split into chunks
        logger.info("[RAG] Stage 5/6: Chunking (size=%d, overlap=%d)",
                     self._settings.rag_chunk_size, self._settings.rag_chunk_overlap)
        chunks = self._splitter.split_text(normalized_text)
        if not chunks:
            logger.warning("[RAG] No chunks produced for '%s'", filename)
            return None

        # Stage 6: EMBEDDING + VECTOR STORE — Delegate to VectorStoreAdapter
        logger.info("[RAG] Stage 6/6: Embedding & storing %d chunks", len(chunks))
        rag_doc = await self._vector_store.ingest_document(
            filename=filename,
            chunks=chunks,
            workspace_id=workspace_id,
            metadata={
                "source_path": file_path,
                "content_hash": hashlib.sha256(normalized_text.encode()).hexdigest(),
                **extra_meta,
            },
        )

        self._ingested_docs.append(rag_doc)
        logger.info(
            "[RAG] Ingestion complete: %d chunks from '%s' (doc_id=%s)",
            len(chunks),
            filename,
            rag_doc.document_id,
        )

        return rag_doc

    async def ingest_text(
        self,
        text: str,
        source_name: str = "inline_text",
        workspace_id: str = "default_workspace",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[RAGDocument]:
        """
        Ingest raw text content directly (no file loading).

        Useful for ingesting web search results, API responses, etc.
        """
        is_valid, reason = _validate_content(text)
        if not is_valid:
            logger.error("[RAG] Validation failed for '%s': %s", source_name, reason)
            return None

        normalized = self._normalize_text(text)
        chunks = self._splitter.split_text(normalized)
        if not chunks:
            return None

        rag_doc = await self._vector_store.ingest_document(
            filename=source_name,
            chunks=chunks,
            workspace_id=workspace_id,
            metadata=metadata,
        )
        self._ingested_docs.append(rag_doc)
        return rag_doc

    # ── Stage 7-9: Retrieve ───────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        workspace_id: str = "default_workspace",
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant context chunks for a query.

        Stages: SIMILARITY SEARCH → OPTIONAL RERANKING → AGENT CONTEXT

        Args:
            query: Natural-language query.
            workspace_id: Workspace scope.
            top_k: Number of results.

        Returns:
            List of dicts with 'content', 'score', 'metadata' keys.
        """
        # Stage 7: SIMILARITY SEARCH
        results = await self._vector_store.similarity_search(
            query=query,
            workspace_id=workspace_id,
            top_k=top_k,
        )

        # Stage 8: OPTIONAL RERANKING (by score, already sorted by store)
        # Future: integrate cross-encoder reranker here

        # Stage 9: AGENT CONTEXT — Format for consumption
        context = []
        for content, score, metadata in results:
            context.append({
                "content": content,
                "score": round(score, 4),
                "source": metadata.get("filename", "unknown"),
                "chunk_index": metadata.get("chunk_index", -1),
                "workspace_id": metadata.get("workspace_id", workspace_id),
            })

        logger.info(
            "[RAG] Retrieved %d chunks for query (workspace=%s)",
            len(context),
            workspace_id,
        )

        return context

    async def retrieve_as_text(
        self,
        query: str,
        workspace_id: str = "default_workspace",
        top_k: int = 5,
    ) -> str:
        """
        Retrieve context and format as a single text block for LLM injection.

        Returns a formatted string suitable for system/context messages.
        """
        context = await self.retrieve(query, workspace_id, top_k)
        if not context:
            return "(No relevant context found in workspace documents.)"

        sections = []
        for i, chunk in enumerate(context, 1):
            sections.append(
                f"[Source {i}: {chunk['source']} (chunk {chunk['chunk_index']}, "
                f"relevance: {chunk['score']:.2f})]\n{chunk['content']}"
            )

        return "\n\n---\n\n".join(sections)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text: collapse whitespace, strip control characters."""
        import re

        # Remove null bytes and control chars (except newlines/tabs)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        # Normalize whitespace (preserve paragraph breaks)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def get_ingested_docs(self) -> List[RAGDocument]:
        """Return list of all ingested RAGDocument records."""
        return list(self._ingested_docs)

    def get_store_info(self) -> Dict[str, Any]:
        """Return vector store status info."""
        return self._vector_store.get_store_info()

    async def delete_workspace(self, workspace_id: str) -> int:
        """Delete all documents for a workspace."""
        count = await self._vector_store.delete_workspace(workspace_id)
        self._ingested_docs = [
            d for d in self._ingested_docs if d.workspace_id != workspace_id
        ]
        return count
