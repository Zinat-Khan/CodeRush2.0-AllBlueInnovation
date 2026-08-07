-- ══════════════════════════════════════════════════════════════════════
-- AE-03 PostgreSQL / pgvector Schema (Directive V2)
-- Database: Supabase @ db.owibnpmtjhrczimayetl.supabase.co
-- Run this once to initialise the schema before starting the backend.
-- ══════════════════════════════════════════════════════════════════════

-- ── Extensions ────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;          -- pgvector

-- ══════════════════════════════════════════════════════════════════════
-- TABLE: documents
-- Master registry of every file ingested into the system.
-- Covers: user uploads, agent-downloaded PDFs/web pages, inline text.
-- ══════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS documents (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id    TEXT        NOT NULL DEFAULT 'default_workspace',

    -- Source metadata
    filename        TEXT        NOT NULL,
    original_url    TEXT,                           -- set when agent downloaded the file
    source_type     TEXT        NOT NULL            -- 'user_upload' | 'agent_download' | 'inline_text'
                    CHECK (source_type IN ('user_upload', 'agent_download', 'inline_text')),
    file_format     TEXT,                           -- 'pdf' | 'txt' | 'md' | 'csv' | 'json' | 'html'
    file_size_bytes BIGINT,

    -- Content fingerprint (SHA-256 of full normalised text)
    content_hash    TEXT        UNIQUE,

    -- Chunking stats
    chunk_count     INTEGER     NOT NULL DEFAULT 0,
    total_tokens    INTEGER     NOT NULL DEFAULT 0,

    -- Status lifecycle
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'indexed', 'failed', 'deleted')),
    error_message   TEXT,

    -- Audit
    uploaded_by     TEXT        DEFAULT 'system',   -- user_id or agent_role
    run_id          TEXT,                           -- run that triggered the upload
    indexed_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ══════════════════════════════════════════════════════════════════════
-- TABLE: document_chunks
-- Individual text chunks with their pgvector embeddings.
-- This is the table queried during similarity search.
-- ══════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS document_chunks (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    workspace_id    TEXT        NOT NULL,

    -- Chunk content
    content         TEXT        NOT NULL,
    chunk_index     INTEGER     NOT NULL,           -- 0-based position in document

    -- Vector embedding (Google embedding-001 = 768 dims; MiniLM = 384)
    -- Using 768 to support both Google and HuggingFace models
    embedding       vector(768),

    -- Rich metadata for filtering & attribution
    page_number     INTEGER,
    section_title   TEXT,
    char_start      INTEGER,
    char_end        INTEGER,
    token_count     INTEGER,

    -- Source tracking (denormalised for fast filter)
    filename        TEXT        NOT NULL,
    source_type     TEXT        NOT NULL,
    original_url    TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (document_id, chunk_index)
);

-- ══════════════════════════════════════════════════════════════════════
-- TABLE: agent_downloads
-- Log of every file an agent fetched from the web.
-- Separate from uploads so we can track agent provenance independently.
-- ══════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS agent_downloads (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID        REFERENCES documents(id) ON DELETE SET NULL,
    workspace_id    TEXT        NOT NULL,
    run_id          TEXT        NOT NULL,

    -- Download details
    url             TEXT        NOT NULL,
    agent_role      TEXT        NOT NULL,           -- which agent triggered it
    http_status     INTEGER,
    content_type    TEXT,
    file_size_bytes BIGINT,

    -- Processing outcome
    indexed         BOOLEAN     NOT NULL DEFAULT FALSE,
    error_message   TEXT,

    downloaded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ══════════════════════════════════════════════════════════════════════
-- TABLE: retrieval_log
-- Audit trail of every similarity search performed.
-- Enables replay, debugging, and analytics.
-- ══════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS retrieval_log (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id    TEXT        NOT NULL,
    run_id          TEXT,
    agent_role      TEXT,

    -- Query
    query_text      TEXT        NOT NULL,
    top_k           INTEGER     NOT NULL DEFAULT 5,

    -- Results summary
    results_count   INTEGER     NOT NULL DEFAULT 0,
    top_score       FLOAT,
    avg_score       FLOAT,

    -- Source documents found (array of document ids)
    source_doc_ids  UUID[],

    queried_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    latency_ms      INTEGER
);

-- ══════════════════════════════════════════════════════════════════════
-- INDEXES
-- ══════════════════════════════════════════════════════════════════════

-- Workspace-scoped document lookups
CREATE INDEX IF NOT EXISTS idx_documents_workspace
    ON documents (workspace_id, status);

CREATE INDEX IF NOT EXISTS idx_documents_hash
    ON documents (content_hash);

CREATE INDEX IF NOT EXISTS idx_documents_run
    ON documents (run_id);

-- Fast workspace-scoped chunk fetching
CREATE INDEX IF NOT EXISTS idx_chunks_workspace
    ON document_chunks (workspace_id);

CREATE INDEX IF NOT EXISTS idx_chunks_document
    ON document_chunks (document_id);

-- pgvector HNSW index — optimised for fast ANN similarity search
-- (requires pgvector >= 0.5.0)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Agent download lookups
CREATE INDEX IF NOT EXISTS idx_downloads_workspace_run
    ON agent_downloads (workspace_id, run_id);

-- Retrieval analytics
CREATE INDEX IF NOT EXISTS idx_retrieval_workspace
    ON retrieval_log (workspace_id, queried_at DESC);

-- ══════════════════════════════════════════════════════════════════════
-- TRIGGER: auto-update updated_at on documents
-- ══════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS documents_updated_at ON documents;
CREATE TRIGGER documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ══════════════════════════════════════════════════════════════════════
-- HELPER VIEWS
-- ══════════════════════════════════════════════════════════════════════

-- Summary view: per-workspace document stats
CREATE OR REPLACE VIEW workspace_doc_stats AS
SELECT
    d.workspace_id,
    COUNT(DISTINCT d.id)                    AS total_documents,
    COUNT(DISTINCT c.id)                    AS total_chunks,
    SUM(d.total_tokens)                     AS total_tokens,
    COUNT(*) FILTER (WHERE d.status = 'indexed')   AS indexed_count,
    COUNT(*) FILTER (WHERE d.status = 'failed')    AS failed_count,
    MAX(d.indexed_at)                       AS last_indexed_at
FROM documents d
LEFT JOIN document_chunks c ON c.document_id = d.id
GROUP BY d.workspace_id;

-- View: agent download summary
CREATE OR REPLACE VIEW agent_download_stats AS
SELECT
    workspace_id,
    run_id,
    agent_role,
    COUNT(*)                                AS total_downloads,
    COUNT(*) FILTER (WHERE indexed = TRUE)  AS indexed_count,
    COUNT(*) FILTER (WHERE http_status >= 400) AS failed_count,
    MAX(downloaded_at)                      AS last_download_at
FROM agent_downloads
GROUP BY workspace_id, run_id, agent_role;
