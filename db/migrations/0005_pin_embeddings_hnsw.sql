-- 0005 — Pin embedding dimensions to 384 (bge-small-en-v1.5) and add HNSW indices.
-- Note: Must drop dependent view v_ambient_memory_active prior to altering column type,
-- and restore it immediately after.
-- Idempotency: Guarded by DO block so redeploying against already-upgraded 1536-dim tables
-- does not downcast or wipe vectors.

DO $$
DECLARE
    v_dim integer;
BEGIN
    SELECT atttypmod INTO v_dim
    FROM pg_attribute
    WHERE attrelid = 'memories'::regclass AND attname = 'embedding';

    -- Only downcast to 384 if the column is not already sized to a larger dimension (e.g. 1536)
    IF v_dim IS NULL OR v_dim <= 384 THEN
        DROP VIEW IF EXISTS v_ambient_memory_active;

        -- 1. Sanitize memories with mismatched dimensions before altering column type
        UPDATE memories
        SET embedding = NULL, embedding_dims = NULL
        WHERE embedding IS NOT NULL AND vector_dims(embedding) != 384;

        ALTER TABLE memories ALTER COLUMN embedding TYPE vector(384);

        CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw
            ON memories USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
    END IF;

    SELECT atttypmod INTO v_dim
    FROM pg_attribute
    WHERE attrelid = 'kb_sections'::regclass AND attname = 'embedding';

    -- 2. Pin and index kb_sections
    IF v_dim IS NULL OR v_dim <= 384 THEN
        UPDATE kb_sections
        SET embedding = NULL, embedding_dims = NULL
        WHERE embedding IS NOT NULL AND vector_dims(embedding) != 384;

        ALTER TABLE kb_sections ALTER COLUMN embedding TYPE vector(384);

        CREATE INDEX IF NOT EXISTS idx_kb_sections_embedding_hnsw
            ON kb_sections USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
    END IF;
END $$;

-- 3. Restore v_ambient_memory_active view projection
CREATE OR REPLACE VIEW v_ambient_memory_active AS
SELECT
    m.id,
    m.tenant_id,
    m.project_id,
    m.scope,
    m.type AS memory_type,
    COALESCE(m.summary, m.content_raw) AS content,
    m.content_raw,
    m.embedding,
    m.embedding_model,
    m.embedding_dims,
    m.source_trust,
    m.confidence,
    m.created_at
FROM memories m
WHERE m.status = 'active'
  AND m.valid_to IS NULL;
