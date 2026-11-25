-- 0011 — Wave P0 Tenant & Scope Isolation, Composite Runbook Step PK,
-- Reversible Feedback Delta, security_invoker View & FORCE ROW LEVEL SECURITY
-- (PLAN-026 / CHG-010: C2, C4, C5, H9).
-- Idempotent across re-runs.

-- 1. Schema additions for C2 & C5
ALTER TABLE consolidation_runs
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_consolidation_runs_tenant_started
    ON consolidation_runs (tenant_id, started_at DESC);

ALTER TABLE memory_retrievals
    ADD COLUMN IF NOT EXISTS applied_delta DOUBLE PRECISION NOT NULL DEFAULT 0.0;

ALTER TABLE kb_sections
    ADD COLUMN IF NOT EXISTS agent_id TEXT NOT NULL DEFAULT 'system';
CREATE INDEX IF NOT EXISTS idx_kb_sections_tenant_agent
    ON kb_sections (tenant_id, agent_id);

ALTER TABLE kb_documents
    ADD COLUMN IF NOT EXISTS agent_id TEXT NOT NULL DEFAULT 'system';
CREATE INDEX IF NOT EXISTS idx_kb_documents_tenant_agent
    ON kb_documents (tenant_id, agent_id);

-- 2. C4: Migrate kb_runbook_steps PK from (tenant_id, step_uid) to (tenant_id, doc_id, step_uid)
DO $$
DECLARE
    pk_name text;
    pk_cols text[];
BEGIN
    SELECT c.conname,
           array_agg(a.attname::text ORDER BY u.ord)
    INTO pk_name, pk_cols
    FROM pg_constraint c
    CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = u.attnum
    WHERE c.conrelid = 'public.kb_runbook_steps'::regclass
      AND c.contype = 'p'
    GROUP BY c.conname;

    IF pk_cols IS DISTINCT FROM ARRAY['tenant_id', 'doc_id', 'step_uid'] THEN
        IF pk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.kb_runbook_steps DROP CONSTRAINT %I', pk_name);
        END IF;

        UPDATE public.kb_runbook_steps
        SET step_uid = doc_id || '#' || step_id
        WHERE step_uid IS DISTINCT FROM (doc_id || '#' || step_id)
          AND POSITION('#' IN step_id) = 0;

        ALTER TABLE public.kb_runbook_steps
            ADD PRIMARY KEY (tenant_id, doc_id, step_uid);
    END IF;
END $$;

-- 3. C5: Upgrade v_ambient_memory_active with security_invoker = true + agent_id/domain_id/provenance
DROP VIEW IF EXISTS v_ambient_memory_active;
CREATE VIEW v_ambient_memory_active WITH (security_invoker = true) AS
SELECT
    m.id,
    m.tenant_id,
    m.agent_id,
    m.project_id,
    m.domain_id,
    m.scope,
    m.type AS memory_type,
    COALESCE(m.summary, m.content_raw) AS content,
    m.content_raw,
    m.embedding,
    m.embedding_model,
    m.embedding_dims,
    m.source_trust,
    m.confidence,
    m.provenance,
    m.created_at
FROM memories m
WHERE m.status = 'active'
  AND m.valid_to IS NULL;

-- 4. Provision non-owner NOLOGIN roles (when CREATEROLE is available) + grants
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_app') THEN
        BEGIN
            CREATE ROLE engramory_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
        EXCEPTION
            WHEN insufficient_privilege OR duplicate_object THEN NULL;
        END;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_worker') THEN
        BEGIN
            CREATE ROLE engramory_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
        EXCEPTION
            WHEN insufficient_privilege OR duplicate_object THEN NULL;
        END;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_app') THEN
        GRANT USAGE ON SCHEMA public TO engramory_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO engramory_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO engramory_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO engramory_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO engramory_app;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_worker') THEN
        GRANT USAGE ON SCHEMA public TO engramory_worker;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO engramory_worker;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO engramory_worker;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO engramory_worker;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO engramory_worker;
    END IF;
END $$;

-- 5. Enable & FORCE ROW LEVEL SECURITY across all 14 canonical tenant tables
DO $$
DECLARE
    tbl text;
    has_worker boolean;
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
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_worker')
    INTO has_worker;

    FOREACH tbl IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'p_' || tbl || '_tenant_isolation', tbl);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR ALL TO PUBLIC '
            'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), ''''))',
            'p_' || tbl || '_tenant_isolation',
            tbl
        );

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'p_' || tbl || '_worker_sweep', tbl);
        IF has_worker THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL TO engramory_worker '
                'USING (current_setting(''app.worker_sweep'', true) = ''on'' '
                '       OR tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')) '
                'WITH CHECK (current_setting(''app.worker_sweep'', true) = ''on'' '
                '            OR tenant_id = NULLIF(current_setting(''app.tenant_id'', true), ''''))',
                'p_' || tbl || '_worker_sweep',
                tbl
            );
        END IF;
    END LOOP;
END $$;
