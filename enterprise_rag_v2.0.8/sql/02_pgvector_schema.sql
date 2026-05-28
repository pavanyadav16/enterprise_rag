-- =============================================================================
-- Enterprise RAG Chatbot — PostgreSQL / PGVector Schema
-- =============================================================================
-- Run this script against your PostgreSQL instance BEFORE starting the app.
-- The application also auto-creates the table on first startup via
-- vector_store.init_vector_store(), so this script is useful for:
--   - Pre-provisioning in CI/CD pipelines
--   - Manual verification / inspection
--   - Creating the database and user from scratch
--
-- Tested on: PostgreSQL 15+ with pgvector 0.5+
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Step 1: Create the database and user (run as postgres superuser)
-- ---------------------------------------------------------------------------
-- Adjust the password to match conf/app.properties → pgvector.password
-- ---------------------------------------------------------------------------

-- CREATE USER rag_user WITH PASSWORD 'YourPGPassword!';
-- CREATE DATABASE rag_vectors OWNER rag_user;

-- Connect to the target database before running the rest:
-- \c rag_vectors


-- ---------------------------------------------------------------------------
-- Step 2: Enable the pgvector extension
-- ---------------------------------------------------------------------------
-- Requires superuser or the pg_extension_owner role.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS vector;


-- ---------------------------------------------------------------------------
-- Step 3: Main chunk storage table
-- ---------------------------------------------------------------------------
-- embedding dimension must match embedding.dimension in app.properties
-- (default 384 for all-MiniLM-L6-v2).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS document_chunks (
    id              BIGSERIAL       PRIMARY KEY,

    -- Foreign key to SQL Server rag_sources.source_id (not enforced cross-DB;
    -- application code keeps them consistent).
    source_id       INTEGER         NOT NULL,

    -- Position of this chunk within its source document (0-based).
    chunk_index     INTEGER         NOT NULL,

    -- The actual text content used for retrieval and LLM context.
    content         TEXT            NOT NULL,

    -- The sentence-transformer embedding vector.
    -- Dimension must match the loaded model output (384 for MiniLM-L6-v2).
    embedding       vector(384),

    -- Arbitrary JSON metadata: source_name, source_type, page numbers, etc.
    metadata        JSONB,

    -- is_active = FALSE means this chunk has been superseded by a re-index.
    -- Old chunks are kept for audit; retrieval filters on is_active = TRUE.
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,

    -- Timestamp when this chunk was written (UTC).
    indexed_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  document_chunks IS 'RAG document chunks with pgvector embeddings';
COMMENT ON COLUMN document_chunks.source_id    IS 'FK to SQL Server rag_sources.source_id';
COMMENT ON COLUMN document_chunks.chunk_index  IS '0-based position within the source document';
COMMENT ON COLUMN document_chunks.is_active    IS 'FALSE = superseded by re-index; excluded from retrieval';
COMMENT ON COLUMN document_chunks.embedding    IS 'Sentence-transformer vector (dim=384 for MiniLM-L6-v2)';


-- ---------------------------------------------------------------------------
-- Step 4: Indexes
-- ---------------------------------------------------------------------------

-- Fast lookup by source (used when deactivating old chunks before re-index)
CREATE INDEX IF NOT EXISTS idx_chunks_source_id
    ON document_chunks(source_id);

-- Partial B-tree index on is_active for the retrieval WHERE clause
CREATE INDEX IF NOT EXISTS idx_chunks_active
    ON document_chunks(is_active)
    WHERE is_active = TRUE;

-- Composite index: source_id + is_active together (common query pattern)
CREATE INDEX IF NOT EXISTS idx_chunks_source_active
    ON document_chunks(source_id, is_active);

-- ---------------------------------------------------------------------------
-- IVFFlat Approximate Nearest-Neighbour index (cosine distance)
-- ---------------------------------------------------------------------------
-- IVFFlat requires at least `lists * 39` rows to be present before it can
-- build optimally, but the CREATE succeeds on empty tables.
--
-- Tuning guidance:
--   lists = sqrt(num_rows) is a common rule of thumb.
--   100 is a safe default for up to ~10 000 chunks.
--   Increase to 200–500 for larger corpora.
--
-- After inserting a large initial batch, run:
--   REINDEX INDEX idx_chunks_embedding;
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Alternative: HNSW index (pgvector >= 0.5, generally better recall)
-- Comment out the ivfflat index above and use this instead for best accuracy:
--
-- CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
--     ON document_chunks
--     USING hnsw (embedding vector_cosine_ops)
--     WITH (m = 16, ef_construction = 64);


-- ---------------------------------------------------------------------------
-- Step 5: Grant privileges to the app user
-- ---------------------------------------------------------------------------

-- GRANT SELECT, INSERT, UPDATE, DELETE ON document_chunks TO rag_user;
-- GRANT USAGE, SELECT ON SEQUENCE document_chunks_id_seq TO rag_user;


-- ---------------------------------------------------------------------------
-- Step 6: Verify
-- ---------------------------------------------------------------------------

SELECT
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) AS total_size
FROM pg_tables
WHERE tablename = 'document_chunks';

SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'document_chunks';

-- ---------------------------------------------------------------------------
-- Useful maintenance queries
-- ---------------------------------------------------------------------------

-- Count active vs inactive chunks per source:
-- SELECT source_id,
--        COUNT(*) FILTER (WHERE is_active = TRUE)  AS active,
--        COUNT(*) FILTER (WHERE is_active = FALSE) AS inactive
-- FROM document_chunks
-- GROUP BY source_id
-- ORDER BY source_id;

-- Hard-delete all inactive (superseded) chunks to reclaim space:
-- DELETE FROM document_chunks WHERE is_active = FALSE;

-- Rebuild the ANN index after a large bulk load:
-- REINDEX INDEX idx_chunks_embedding;

-- Check index usage:
-- SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read
-- FROM pg_stat_user_indexes
-- WHERE tablename = 'document_chunks';
