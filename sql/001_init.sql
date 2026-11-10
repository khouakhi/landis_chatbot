-- LandIS assistant — Postgres 16 + pgvector.
-- Apply with: uv run landis-db-init
-- On Render, run this from a laptop against the external DATABASE_URL
-- after the database exists. CREATE EXTENSION requires permission to install pgvector.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    title TEXT,
    public_url TEXT,
    is_public BOOLEAN NOT NULL DEFAULT FALSE,
    publication_year INTEGER,
    document_type TEXT,
    authority_tier TEXT,
    licence TEXT,
    superseded BOOLEAN NOT NULL DEFAULT FALSE,
    place TEXT,
    spatial_scale TEXT,
    chunking_mode TEXT,
    pipeline_status TEXT NOT NULL DEFAULT 'indexed',
    last_processed TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents (doc_id) ON DELETE CASCADE,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    section TEXT,
    text TEXT NOT NULL,
    table_html TEXT,
    entity_codes TEXT,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    printed_page_start TEXT,
    printed_page_end TEXT,
    has_table BOOLEAN NOT NULL DEFAULT FALSE,
    embedding vector(1024) NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector(
            'english',
            coalesce(text, '') || ' ' || coalesce(section, '') || ' ' || coalesce(entity_codes, '')
        )
    ) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS landis_conversations (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    thread_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visitor_fingerprint TEXT NOT NULL,
    client_type TEXT NOT NULL DEFAULT 'webapp',
    question TEXT NOT NULL,
    answer TEXT,
    error TEXT,
    cited_chunk_ids TEXT,
    cited_sources_count INTEGER NOT NULL DEFAULT 0,
    rag_top_k INTEGER,
    chat_model TEXT,
    embed_model TEXT,
    kb_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks (doc_id);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_chunks_search ON chunks USING gin (search_vector);

CREATE INDEX IF NOT EXISTS idx_conversations_created_at
    ON landis_conversations (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_thread_created
    ON landis_conversations (thread_id, created_at DESC);
