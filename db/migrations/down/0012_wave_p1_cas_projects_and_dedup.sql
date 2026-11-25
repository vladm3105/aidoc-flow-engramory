-- Down migration for 0012_wave_p1_cas_projects_and_dedup.sql
-- Restores schema to post-0011 state. Stored outside db/migrations/ per PLAN-026 S6.

DROP TABLE IF EXISTS public.kb_blob_aliases;
DROP TABLE IF EXISTS public.kb_blob_projects;

DROP INDEX IF EXISTS uq_episodes_event_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_event_id
    ON episodes (tenant_id, (metadata->>'event_id'))
    WHERE metadata ? 'event_id';
