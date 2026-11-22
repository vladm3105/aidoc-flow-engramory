-- Down migration for 0011_wave_p0_isolation_and_rls.sql (PLAN-026 / CHG-010).
-- Kept outside db/migrations/ so forward runners do not execute rollback DDL.

DO $$
DECLARE
    tbl text;
    tenant_tables constant text[] := ARRAY[
        'agent_profiles',
        'episodes',
        'memories',
        'consolidation_runs',
        'memory_retrievals',
        'audit_records',
        'kb_sections',
        'graph_entities',
        'graph_relations',
        'kb_documents',
        'kb_runbook_steps',
        'kb_blobs',
        'kb_blob_chunks',
        'kb_blob_chunks_large'
    ];
BEGIN
    FOREACH tbl IN ARRAY tenant_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'p_' || tbl || '_worker_sweep', tbl);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'p_' || tbl || '_tenant_isolation', tbl);
        EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', tbl);
    END LOOP;
END $$;

DROP VIEW IF EXISTS v_ambient_memory_active;
CREATE VIEW v_ambient_memory_active AS
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

-- Restore (tenant_id, step_uid) PK on kb_runbook_steps after removing duplicates that
-- cannot coexist under the pre-0011 constraint.
DO $$
DECLARE
    pk_name text;
BEGIN
    SELECT c.conname INTO pk_name
    FROM pg_constraint c
    WHERE c.conrelid = 'public.kb_runbook_steps'::regclass
      AND c.contype = 'p';
    IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.kb_runbook_steps DROP CONSTRAINT %I', pk_name);
    END IF;

    DELETE FROM public.kb_runbook_steps a
    USING public.kb_runbook_steps b
    WHERE a.ctid < b.ctid
      AND a.tenant_id = b.tenant_id
      AND a.step_uid = b.step_uid;

    ALTER TABLE public.kb_runbook_steps ADD PRIMARY KEY (tenant_id, step_uid);
END $$;

DROP INDEX IF EXISTS idx_kb_documents_tenant_agent;
ALTER TABLE kb_documents DROP COLUMN IF EXISTS agent_id;

DROP INDEX IF EXISTS idx_kb_sections_tenant_agent;
ALTER TABLE kb_sections DROP COLUMN IF EXISTS agent_id;

ALTER TABLE memory_retrievals DROP COLUMN IF EXISTS applied_delta;

DROP INDEX IF EXISTS idx_consolidation_runs_tenant_started;
ALTER TABLE consolidation_runs DROP COLUMN IF EXISTS tenant_id;
