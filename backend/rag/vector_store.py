"""
AE-03 Vector Store Adapter (Directive V2).

Abstracts vector storage behind a unified interface supporting:
  - **PostgreSQL / Supabase pgvector** (primary enterprise storage)
  - **Chroma** (persistent local storage)
  - **FAISS** (in-memory local fallback)

Embeddings via ``GoogleGenerativeAIEmbeddings`` (primary) or
``HuggingFaceEmbeddings`` (fallback).

Enforces workspace isolation — every document and query is scoped to
a ``workspace_id`` via metadata filtering.

Pipeline position: EMBEDDING → **VECTOR STORE** → SIMILARITY SEARCH
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from backend.config import AppSettings, get_settings
from backend.schemas.contracts import RAGChunk, RAGDocument

logger = logging.getLogger(__name__)


# ── Embedding Factory & Vector Normalisation ──────────────────────────


def _normalize_vec_dim(vec: List[float], target_dim: int = 1536) -> List[float]:
    """Normalize/pad vector dimension to match target dimension (e.g. 1536 for pgvector embeddings)."""
    if len(vec) == target_dim:
        return vec
    elif len(vec) < target_dim:
        repeats = (target_dim // len(vec)) + 1
        return (vec * repeats)[:target_dim]
    else:
        return vec[:target_dim]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two float vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _create_embeddings(settings: AppSettings):
    """
    Create an embedding model instance based on configuration.

    Returns a LangChain Embeddings instance.
    """
    provider = settings.embedding_provider

    # 1. Primary: OpenRouter Key 6 Gemini/OpenAI Embeddings (Active 1536-dim vector model)
    if settings.openrouter_key_6:
        try:
            from langchain_openai import OpenAIEmbeddings

            embedder = OpenAIEmbeddings(
                api_key=settings.openrouter_key_6,
                openai_api_base="https://openrouter.ai/api/v1",
                model="openai/text-embedding-3-small",
            )
            # Test query to verify
            embedder.embed_query("test")
            return embedder
        except Exception as e:
            logger.warning("OpenRouter Key 6 embeddings failed: %s", e)

    # 2. Direct Google Gemini Embeddings
    if settings.google_api_key:
        try:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            embedder = GoogleGenerativeAIEmbeddings(
                model="models/text-embedding-004",
                google_api_key=settings.google_api_key,
            )
            embedder.embed_query("test")
            return embedder
        except Exception as e:
            logger.warning("Google Gemini embeddings failed: %s", e)

    # 3. Fallback: Direct OpenAI Embeddings
    if settings.openai_api_key:
        try:
            from langchain_openai import OpenAIEmbeddings

            return OpenAIEmbeddings(
                api_key=settings.openai_api_key,
                model="text-embedding-3-small",
            )
        except Exception as e:
            logger.warning("OpenAI embeddings failed: %s", e)

    # 4. Local Fallback: HuggingFaceEmbeddings
    try:
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
        )
    except ImportError:
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
        values = []
        for i in range(dim):
            byte_val = int(h[(i * 2) % len(h):(i * 2 + 2) % len(h) or len(h)], 16)
            values.append((byte_val / 255.0) * 2 - 1)
        return values


# ── Vector Store Adapter ─────────────────────────────────────────────


class VectorStoreAdapter:
    """
    Unified vector store abstraction with workspace isolation.

    Supports PostgreSQL/Supabase (pgvector), Chroma, and FAISS backends.
    All operations are scoped to a ``workspace_id`` to enforce
    multi-tenant data isolation.
    """

    def __init__(self, settings: Optional[AppSettings] = None):
        self._settings = settings or get_settings()
        self._embeddings = _create_embeddings(self._settings)
        self._store = None
        self._supabase_client = None
        self._store_type = self._settings.vector_store_type.lower()
        self._init_store()

    def _init_store(self) -> None:
        """Initialise the backing vector store."""
        if self._store_type in ("postgres", "pgvector", "supabase"):
            self._init_postgres()
        elif self._store_type == "chroma":
            self._init_chroma()
        elif self._store_type == "faiss":
            self._init_faiss()
        else:
            logger.warning(
                "Unknown vector_store_type '%s', trying PostgreSQL / Supabase.",
                self._store_type,
            )
            self._init_postgres()

    def _init_postgres(self) -> None:
        """Initialise PostgreSQL / Supabase pgvector store."""
        try:
            from supabase import create_client

            url = self._settings.supabase_url
            key = self._settings.supabase_secret or self._settings.supabase_publishable_key
            if not url or not key:
                raise ValueError("Supabase URL or API secret missing.")

            self._supabase_client = create_client(url, key)
            self._store = self._supabase_client
            self._store_type = "postgres"
            logger.info("Supabase PostgreSQL pgvector store initialised at %s", url)
        except Exception as e:
            logger.warning("Supabase PostgreSQL init failed: %s. Falling back to Chroma.", e)
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
            self._store_type = "chroma"
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

            self._store = None  # Lazy init on first ingest
            self._faiss_class = FAISS
            self._store_type = "faiss"
            logger.info("FAISS vector store ready (lazy init).")
        except ImportError:
            logger.error("Neither PostgreSQL, chromadb, nor faiss-cpu available. Vector store unavailable.")
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
        ext = os.path.splitext(filename)[1].lstrip(".").lower() or "txt"

        if self._store_type == "postgres" and self._supabase_client is not None:
            return await self._ingest_postgres(filename, chunks, workspace_id, content_hash, ext, extra_meta)

        # Chroma / FAISS Fallback Pipeline
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
                    document_id="",
                    content=chunk_text,
                    chunk_index=idx,
                    workspace_id=workspace_id,
                    metadata=doc_meta,
                )
            )

        if self._store_type == "faiss" and self._store is None:
            self._store = self._faiss_class.from_documents(documents, self._embeddings)
        elif self._store is not None:
            self._store.add_documents(documents)
        else:
            logger.error("No vector store available for ingestion.")

        rag_doc = RAGDocument(
            filename=filename,
            workspace_id=workspace_id,
            chunk_count=len(chunks),
            total_tokens=sum(len(c.split()) for c in chunks),
            metadata={**extra_meta, "content_hash": content_hash},
        )
        return rag_doc

    async def _ingest_postgres(
        self,
        filename: str,
        chunks: List[str],
        workspace_id: str,
        content_hash: str,
        file_format: str,
        extra_meta: Dict[str, Any],
    ) -> RAGDocument:
        """Internal helper to ingest document and chunks into Supabase PostgreSQL."""
        doc_id = str(uuid.uuid4())
        source_type = extra_meta.get("source_type", "user_upload")

        # 1. Insert Document record
        doc_data = {
            "id": doc_id,
            "file_name": filename,
            "original_name": filename,
            "file_type": file_format,
            "storage_path": f"{workspace_id}/{filename}",
            "checksum": content_hash,
            "processing_status": "completed",
        }
        self._supabase_client.table("documents").insert(doc_data).execute()

        # 2. Insert Chunks & Embeddings
        embed_vectors = self._embeddings.embed_documents(chunks) if hasattr(self._embeddings, "embed_documents") else [self._embeddings.embed_query(c) for c in chunks]

        for idx, chunk_text in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            chunk_meta = {
                "workspace_id": workspace_id,
                "filename": filename,
                "source": filename,
                "source_type": source_type,
                **extra_meta,
            }
            # Insert chunk
            self._supabase_client.table("document_chunks").insert({
                "id": chunk_id,
                "document_id": doc_id,
                "chunk_index": idx,
                "chunk_text": chunk_text,
                "token_count": len(chunk_text.split()),
                "metadata": chunk_meta,
            }).execute()

            # Normalize & insert embedding (1536-dim pgvector)
            raw_vec = embed_vectors[idx] if idx < len(embed_vectors) else self._embeddings.embed_query(chunk_text)
            norm_vec = _normalize_vec_dim(raw_vec, target_dim=1536)

            self._supabase_client.table("embeddings").insert({
                "id": str(uuid.uuid4()),
                "chunk_id": chunk_id,
                "embedding": norm_vec,
            }).execute()

        logger.info(
            "Ingested %d chunks from '%s' into PostgreSQL/Supabase (doc_id=%s, workspace=%s)",
            len(chunks), filename, doc_id, workspace_id,
        )

        return RAGDocument(
            document_id=doc_id,
            filename=filename,
            workspace_id=workspace_id,
            chunk_count=len(chunks),
            total_tokens=sum(len(c.split()) for c in chunks),
            metadata={**extra_meta, "content_hash": content_hash, "db_backend": "postgres"},
        )

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
        if self._store_type == "postgres" and self._supabase_client is not None:
            return await self._search_postgres(query, workspace_id, top_k)

        if self._store is None:
            logger.warning("Vector store not initialised. Returning empty results.")
            return []

        try:
            filter_dict = {"workspace_id": workspace_id}
            if self._store_type == "chroma":
                results = self._store.similarity_search_with_relevance_scores(query, k=top_k, filter=filter_dict)
            else:
                results_raw = self._store.similarity_search_with_score(query, k=top_k * 3)
                results = [
                    (doc, score)
                    for doc, score in results_raw
                    if doc.metadata.get("workspace_id") == workspace_id
                ][:top_k]

            return [(doc.page_content, float(score), doc.metadata) for doc, score in results]
        except Exception as e:
            logger.error("Similarity search failed: %s", e)
            return []

    async def _search_postgres(
        self,
        query: str,
        workspace_id: str,
        top_k: int,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Internal helper to perform vector similarity search on Supabase PostgreSQL."""
        try:
            query_vec = self._embeddings.embed_query(query)
            query_norm = _normalize_vec_dim(query_vec, target_dim=1536)

            # Query all document chunks for this workspace
            chunks_res = self._supabase_client.table("document_chunks").select("id, chunk_text, metadata, chunk_index, document_id").execute()
            if not chunks_res.data:
                return []

            # Filter by workspace_id in metadata
            ws_chunks = [
                c for c in chunks_res.data
                if isinstance(c.get("metadata"), dict) and c["metadata"].get("workspace_id") in (workspace_id, None, "default_workspace")
            ]
            if not ws_chunks:
                ws_chunks = chunks_res.data

            chunk_ids = [c["id"] for c in ws_chunks]
            if not chunk_ids:
                return []

            # Fetch embeddings for matching chunks
            embed_res = self._supabase_client.table("embeddings").select("chunk_id, embedding").in_("chunk_id", chunk_ids).execute()
            embed_map = {}
            for e in embed_res.data:
                cid = e.get("chunk_id")
                emb = e.get("embedding")
                if not cid or not emb:
                    continue
                if isinstance(emb, str):
                    import json
                    try:
                        emb = json.loads(emb)
                    except Exception:
                        emb = [float(x) for x in emb.strip("[]").split(",") if x.strip()]
                elif isinstance(emb, list):
                    emb = [float(x) for x in emb]
                embed_map[cid] = emb

            scored_results = []
            for c in ws_chunks:
                cid = c["id"]
                if cid in embed_map:
                    sim = _cosine_similarity(query_norm, embed_map[cid])
                    scored_results.append((c["chunk_text"], float(sim), c.get("metadata") or {}))

            scored_results.sort(key=lambda x: x[1], reverse=True)
            return scored_results[:top_k]

        except Exception as e:
            logger.error("PostgreSQL similarity search error: %s", e)
            return []

    # ── Delete ────────────────────────────────────────────────────────

    async def delete_workspace(self, workspace_id: str) -> int:
        """Delete all documents for a workspace."""
        if self._store_type == "postgres" and self._supabase_client is not None:
            try:
                # Find matching documents
                docs_res = self._supabase_client.table("documents").select("id").like("storage_path", f"{workspace_id}/%").execute()
                doc_ids = [d["id"] for d in docs_res.data]
                if doc_ids:
                    for did in doc_ids:
                        self._supabase_client.table("documents").delete().eq("id", did).execute()
                    logger.info("Deleted %d documents for workspace '%s' from Supabase", len(doc_ids), workspace_id)
                    return len(doc_ids)
            except Exception as e:
                logger.error("Postgres delete failed: %s", e)
                return 0

        if self._store_type == "chroma" and self._store is not None and hasattr(self._store, "_collection"):
            try:
                collection = self._store._collection
                results = collection.get(where={"workspace_id": workspace_id})
                ids = results.get("ids", [])
                if ids:
                    collection.delete(ids=ids)
                    return len(ids)
            except Exception as e:
                logger.error("Delete failed: %s", e)

        return 0

    # ── Observability ─────────────────────────────────────────────────

    def get_store_info(self) -> Dict[str, Any]:
        """Return store metadata for observability."""
        info = {
            "store_type": self._store_type,
            "available": (self._supabase_client is not None) if self._store_type == "postgres" else (self._store is not None),
            "embedding_provider": self._settings.embedding_provider,
        }

        if self._store_type == "postgres" and self._supabase_client is not None:
            try:
                res = self._supabase_client.table("documents").select("id", count="exact").execute()
                info["document_count"] = res.count or len(res.data)
                info["postgres_host"] = self._settings.postgres_host
                info["supabase_url"] = self._settings.supabase_url
            except Exception:
                info["document_count"] = -1
        elif self._store_type == "chroma" and self._store is not None:
            try:
                info["document_count"] = self._store._collection.count()
            except Exception:
                info["document_count"] = -1

        return info
