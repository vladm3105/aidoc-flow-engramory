-- Down migration for 0013 — Wave P3 Indexes, Trigger Hygiene, Nullable embedding_model & Blob FK
-- Reverses 0013_wave_p3_indexes_and_hygiene.sql safely inside a bounded lock_timeout transaction.
-- Requires SUPERUSER or BYPASSRLS role when non-empty FORCE RLS tables are updated.

BEGIN;
SET LOCAL lock_timeout = '5s';

-- 1. Drop fk_kb_sections_blob foreign key constraint.
ALTER TABLE public.kb_sections
    DROP CONSTRAINT IF EXISTS fk_kb_sections_blob;

-- 2. Restore trg_kb_blobs_updated_at to set_updated_at() and remove set_kb_blobs_updated_at().
DROP TRIGGER IF EXISTS trg_kb_blobs_updated_at ON kb_blobs;
CREATE TRIGGER trg_kb_blobs_updated_at
    BEFORE UPDATE ON kb_blobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP FUNCTION IF EXISTS set_kb_blobs_updated_at();

-- 3. Backfill unembedded NULL embedding_model values before restoring NOT NULL constraint.
UPDATE memories
SET embedding_model = COALESCE(embedding_model, 'none')
WHERE embedding_model IS NULL;
ALTER TABLE memories ALTER COLUMN embedding_model SET NOT NULL;

-- 4. Drop Wave P3 indexes.
DROP INDEX IF EXISTS idx_kb_blobs_status_updated;
DROP INDEX IF EXISTS idx_audit_records_tenant_ts;
DROP INDEX IF EXISTS idx_memory_retrievals_tenant_mem;
DROP INDEX IF EXISTS idx_kb_sections_tenant_doc_ver;
DROP INDEX IF EXISTS idx_memories_tenant_variant_live;
DROP INDEX IF EXISTS idx_memories_tenant_domain_live;

COMMIT;
