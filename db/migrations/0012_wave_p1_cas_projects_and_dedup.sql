-- 0012_wave_p1_cas_projects_and_dedup.sql
-- Wave P1 (CHG-011 / C10, H4):
--   1. Create kb_blob_projects(tenant_id, project_id, sha256) with backfill from kb_blobs
--   2. Create kb_blob_aliases(tenant_id, project_id, alias_key) for put(key)/get(key) parity
--   3. Grant engramory_app/engramory_worker access + FORCE ROW LEVEL SECURITY on both tables
--   4. Rescope uq_episodes_event_id to (tenant_id, coalesce(project_id, ''), agent_id, event_id)
--      ignoring empty strings via NULLIF(..., '') IS NOT NULL

-- 1. Project ownership table for deduplicated CAS blobs (C10)
CREATE TABLE IF NOT EXISTS public.kb_blob_projects (
    tenant_id   TEXT        NOT NULL DEFAULT 'default',
    project_id  TEXT        NOT NULL,
    sha256      TEXT        NOT NULL CHECK (char_length(sha256) = 64),
    metadata    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, sha256),
    CONSTRAINT fk_kb_blob_projects_blob
        FOREIGN KEY (tenant_id, sha256)
        REFERENCES public.kb_blobs (tenant_id, sha256)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kb_blob_projects_tenant_sha
    ON public.kb_blob_projects (tenant_id, sha256);

-- 2. Key-alias lookup table for arbitrary put(key)/get(key) CAS keys (C10)
CREATE TABLE IF NOT EXISTS public.kb_blob_aliases (
    tenant_id   TEXT        NOT NULL DEFAULT 'default',
    project_id  TEXT        NOT NULL DEFAULT '',
    alias_key   TEXT        NOT NULL,
    sha256      TEXT        NOT NULL CHECK (char_length(sha256) = 64),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, alias_key),
    CONSTRAINT fk_kb_blob_aliases_blob
        FOREIGN KEY (tenant_id, sha256)
        REFERENCES public.kb_blobs (tenant_id, sha256)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kb_blob_aliases_tenant_sha
    ON public.kb_blob_aliases (tenant_id, sha256);

-- 3. Backfill existing kb_blobs project associations BEFORE enabling RLS on kb_blob_projects
INSERT INTO public.kb_blob_projects (tenant_id, project_id, sha256, metadata)
SELECT tenant_id, project_id, sha256, metadata
FROM public.kb_blobs
WHERE project_id IS NOT NULL
  AND btrim(project_id) != ''
ON CONFLICT (tenant_id, project_id, sha256) DO NOTHING;

-- 4. Grant role privileges + enable FORCE ROW LEVEL SECURITY on kb_blob_projects and kb_blob_aliases
DO $$
DECLARE
    tbl text;
    has_worker boolean;
    p1_tables constant text[] := ARRAY[
        'kb_blob_projects',
        'kb_blob_aliases'
    ];
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.kb_blob_projects TO engramory_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.kb_blob_aliases TO engramory_app;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_worker') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.kb_blob_projects TO engramory_worker;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.kb_blob_aliases TO engramory_worker;
    END IF;

    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engramory_worker')
    INTO has_worker;

    FOREACH tbl IN ARRAY p1_tables LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);

        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON public.%I',
            'p_' || tbl || '_tenant_isolation',
            tbl
        );
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR ALL TO PUBLIC '
            'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), ''''))',
            'p_' || tbl || '_tenant_isolation',
            tbl
        );

        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON public.%I',
            'p_' || tbl || '_worker_sweep',
            tbl
        );
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

-- 5. Rescope uq_episodes_event_id to (tenant_id, coalesce(project_id, ''), agent_id, event_id) (H4)
DO $$
DECLARE
    idx_def text;
BEGIN
    SELECT indexdef
    INTO idx_def
    FROM pg_indexes
    WHERE schemaname = current_schema()
      AND tablename = 'episodes'
      AND indexname = 'uq_episodes_event_id';

    IF idx_def IS NULL OR idx_def NOT ILIKE '%agent_id%' OR idx_def NOT ILIKE '%nullif%' THEN
        DROP INDEX IF EXISTS uq_episodes_event_id;
        CREATE UNIQUE INDEX uq_episodes_event_id
            ON episodes (tenant_id, coalesce(project_id, ''), agent_id, (metadata->>'event_id'))
            WHERE NULLIF(metadata->>'event_id', '') IS NOT NULL;
    END IF;
END $$;
