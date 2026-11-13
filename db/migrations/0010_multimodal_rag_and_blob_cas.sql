-- 0010 — Multimodal Knowledge RAG, Hybrid Lexical BM25 (ts_lex), and
-- PostgreSQL 18 20 GB+ Chunked Content-Addressable Blob Storage (PLAN-023 / CHG-008).
-- Normative upstream: platform-technology_stack/05-database-stack.md §6.13 (Decision D17)
-- and §6.14 (Rule D14a / D14b).

-- 1. Content-Addressable Blob Manifest (hot metadata in pg_default).
CREATE TABLE IF NOT EXISTS kb_blobs (
    tenant_id      TEXT        NOT NULL DEFAULT 'default',
    sha256         TEXT        NOT NULL CHECK (char_length(sha256) = 64),
    project_id     TEXT,
    media_type     TEXT        NOT NULL,
    byte_size      BIGINT      NOT NULL CHECK (byte_size >= 0 AND byte_size <= 26843545600),
    chunk_size     INTEGER     NOT NULL DEFAULT 4194304 CHECK (chunk_size BETWEEN 65536 AND 16777216),
    chunk_count    INTEGER     NOT NULL CHECK (chunk_count >= 0),
    storage_tier   TEXT        NOT NULL DEFAULT 'logged' CHECK (storage_tier IN ('logged', 'unlogged')),
    status         TEXT        NOT NULL DEFAULT 'uploading' CHECK (status IN ('uploading', 'ready', 'failed')),
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_kb_blobs_tenant_project_status
    ON kb_blobs (tenant_id, project_id, status, created_at DESC);

DROP TRIGGER IF EXISTS trg_kb_blobs_updated_at ON kb_blobs;
CREATE TRIGGER trg_kb_blobs_updated_at
    BEFORE UPDATE ON kb_blobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 2. Tier 1: LOGGED 4 MiB Chunk Table for Blobs <= 50 MiB (WAL-replicated, crash-safe).
CREATE TABLE IF NOT EXISTS kb_blob_chunks (
    tenant_id      TEXT        NOT NULL DEFAULT 'default',
    blob_sha256    TEXT        NOT NULL CHECK (char_length(blob_sha256) = 64),
    chunk_index    INTEGER     NOT NULL CHECK (chunk_index >= 0),
    chunk_sha256   BYTEA       NOT NULL CHECK (octet_length(chunk_sha256) = 32),
    byte_size      INTEGER     NOT NULL CHECK (byte_size > 0 AND byte_size <= 16777216),
    data           BYTEA       NOT NULL,
    PRIMARY KEY (tenant_id, blob_sha256, chunk_index)
) WITH (
    fillfactor = 100,
    toast.autovacuum_enabled = true,
    toast.autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_scale_factor = 0.01
);

ALTER TABLE kb_blob_chunks ALTER COLUMN data SET STORAGE EXTERNAL;

-- 3. Tier 2: UNLOGGED 4 MiB Chunk Table for Large Media > 50 MiB up to 25 GiB (0 B WAL).
CREATE UNLOGGED TABLE IF NOT EXISTS kb_blob_chunks_large (
    tenant_id      TEXT        NOT NULL DEFAULT 'default',
    blob_sha256    TEXT        NOT NULL CHECK (char_length(blob_sha256) = 64),
    chunk_index    INTEGER     NOT NULL CHECK (chunk_index >= 0),
    chunk_sha256   BYTEA       NOT NULL CHECK (octet_length(chunk_sha256) = 32),
    byte_size      INTEGER     NOT NULL CHECK (byte_size > 0 AND byte_size <= 16777216),
    data           BYTEA       NOT NULL,
    PRIMARY KEY (tenant_id, blob_sha256, chunk_index)
) WITH (
    fillfactor = 100,
    toast.autovacuum_enabled = true,
    toast.autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_scale_factor = 0.01
);

ALTER TABLE kb_blob_chunks_large ALTER COLUMN data SET STORAGE EXTERNAL;

-- 4. Conditional placement into cold tablespace ts_blob_cold when provisioned,
--    falling back cleanly to pg_default on unprivileged CI or managed cloud Postgres.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tablespace WHERE spcname = 'ts_blob_cold') THEN
        EXECUTE 'ALTER TABLE kb_blob_chunks SET TABLESPACE ts_blob_cold';
        EXECUTE 'ALTER INDEX kb_blob_chunks_pkey SET TABLESPACE ts_blob_cold';
        EXECUTE 'ALTER TABLE kb_blob_chunks_large SET TABLESPACE ts_blob_cold';
        EXECUTE 'ALTER INDEX kb_blob_chunks_large_pkey SET TABLESPACE ts_blob_cold';
    END IF;
END $$;

-- 5. Extend kb_sections with Multimodal Provenance and Hybrid Lexical BM25 (ts_lex).
ALTER TABLE kb_sections
    ADD COLUMN IF NOT EXISTS modality TEXT NOT NULL DEFAULT 'text',
    ADD COLUMN IF NOT EXISTS blob_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS blob_uri TEXT,
    ADD COLUMN IF NOT EXISTS media_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS ts_lex TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(citation, '') || ' ' || text)
    ) STORED;

ALTER TABLE kb_sections DROP CONSTRAINT IF EXISTS chk_kb_sections_modality;
ALTER TABLE kb_sections ADD CONSTRAINT chk_kb_sections_modality
    CHECK (modality IN ('text', 'markdown', 'pdf', 'document', 'image', 'audio', 'video'));

CREATE INDEX IF NOT EXISTS idx_kb_sections_lex
    ON kb_sections USING gin (ts_lex);

CREATE INDEX IF NOT EXISTS idx_kb_sections_modality
    ON kb_sections (tenant_id, project_id, modality);

CREATE INDEX IF NOT EXISTS idx_kb_sections_blob
    ON kb_sections (tenant_id, blob_sha256)
    WHERE blob_sha256 IS NOT NULL;
