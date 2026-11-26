-- 0013 — Wave P3 Indexes, Trigger Hygiene, Nullable embedding_model & Blob FK Integrity
-- (PLAN-026 / CHG-013: M1).
-- Idempotent across re-runs.
-- Migrator role note: FORCE ROW LEVEL SECURITY is enabled on canonical tables (0011/0012);
-- running this migration on non-empty tables requires a SUPERUSER or BYPASSRLS role.

BEGIN;
SET LOCAL lock_timeout = '5s';

-- 1. Missing hot-path indexes for domain/variant reads, KB versions, retrievals, audits, and blobs.
CREATE INDEX IF NOT EXISTS idx_memories_tenant_domain_live
    ON memories (tenant_id, domain_id, valid_from DESC)
    WHERE valid_to IS NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_memories_tenant_variant_live
    ON memories (tenant_id, (COALESCE(provenance->>'brain_variant', 'default')))
    WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_kb_sections_tenant_doc_ver
    ON kb_sections (tenant_id, doc_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_memory_retrievals_tenant_mem
    ON memory_retrievals (tenant_id, memory_id);

CREATE INDEX IF NOT EXISTS idx_audit_records_tenant_ts
    ON audit_records (tenant_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_kb_blobs_status_updated
    ON kb_blobs (tenant_id, status, updated_at);

-- 2. Dedicated updated_at trigger function on kb_blobs.
CREATE OR REPLACE FUNCTION set_kb_blobs_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_kb_blobs_updated_at ON kb_blobs;
CREATE TRIGGER trg_kb_blobs_updated_at
    BEFORE UPDATE ON kb_blobs
    FOR EACH ROW EXECUTE FUNCTION set_kb_blobs_updated_at();

-- 3. Nullable embedding_model hygiene for unembedded rows.
ALTER TABLE memories ALTER COLUMN embedding_model DROP NOT NULL;
UPDATE memories
SET embedding_model = NULL
WHERE embedding IS NULL
  AND embedding_model IN ('none', 'none/interim-reflect', '');

-- 4. Referential integrity between kb_sections.blob_sha256 and kb_blobs(tenant_id, sha256).
-- Clean up any dangling blob references first so constraint validation succeeds.
UPDATE kb_sections s
SET blob_sha256 = NULL,
    blob_uri = NULL
WHERE s.blob_sha256 IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM kb_blobs b
      WHERE b.tenant_id = s.tenant_id
        AND b.sha256 = s.blob_sha256
  );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.kb_sections'::regclass
          AND conname = 'fk_kb_sections_blob'
    ) THEN
        ALTER TABLE public.kb_sections
            ADD CONSTRAINT fk_kb_sections_blob
            FOREIGN KEY (tenant_id, blob_sha256)
            REFERENCES public.kb_blobs (tenant_id, sha256)
            ON DELETE SET NULL (blob_sha256);
    END IF;
END $$;

COMMIT;
