-- 0009_tenant_profiles_and_experience_dedup.sql
-- Multi-tenant L3 agent_profiles isolation (ADR-07), ExperienceEvent event_id
-- idempotency index (ADR-10 / SPEC-02), and recent-episode learning health index (PLAN-021).

ALTER TABLE agent_profiles
    ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE t.relname = 'agent_profiles'
          AND t.relnamespace = current_schema()::regnamespace
          AND c.conname = 'agent_profiles_pkey'
          AND array_length(c.conkey, 1) = 1
    ) THEN
        ALTER TABLE agent_profiles DROP CONSTRAINT agent_profiles_pkey;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE t.relname = 'agent_profiles'
          AND t.relnamespace = current_schema()::regnamespace
          AND c.conname = 'agent_profiles_pkey'
    ) THEN
        ALTER TABLE agent_profiles
            ADD CONSTRAINT agent_profiles_pkey PRIMARY KEY (tenant_id, agent_id);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_event_id
    ON episodes (tenant_id, (metadata->>'event_id'))
    WHERE metadata ? 'event_id';

CREATE INDEX IF NOT EXISTS idx_episodes_tenant_project_ts
    ON episodes (tenant_id, project_id, ts DESC, id DESC);
