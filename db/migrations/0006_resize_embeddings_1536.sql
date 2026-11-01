-- 0006 — Resize embedding dimensions to 1536 (qwen3-embedding-8b / text-embedding-3-small) and rebuild HNSW indices.
-- Note: Must drop dependent view v_ambient_memory_active prior to altering column type,
-- and restore it immediately after.

DROP VIEW IF EXISTS v_ambient_memory_active;

-- 1. Sanitize memories with mismatched dimensions before altering column type
UPDATE memories
SET embedding = NULL, embedding_dims = NULL
WHERE embedding IS NOT NULL AND vector_dims(embedding) != 1536;

DROP INDEX IF EXISTS idx_memories_embedding_hnsw;

ALTER TABLE memories ALTER COLUMN embedding TYPE vector(1536);

CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 2. Resize and index kb_sections
UPDATE kb_sections
SET embedding = NULL, embedding_dims = NULL
WHERE embedding IS NOT NULL AND vector_dims(embedding) != 1536;

DROP INDEX IF EXISTS idx_kb_sections_embedding_hnsw;

ALTER TABLE kb_sections ALTER COLUMN embedding TYPE vector(1536);

CREATE INDEX IF NOT EXISTS idx_kb_sections_embedding_hnsw
    ON kb_sections USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

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
