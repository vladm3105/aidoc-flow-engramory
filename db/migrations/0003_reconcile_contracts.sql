-- 0003 — Contract reconciliation (pre-first-run). Closes the spec-vs-schema
-- gaps found in the 2026-07-09 review. Idempotent; additive except the
-- NOT NULL tightenings, which are safe pre-first-run (tables are empty; the
-- guarded UPDATEs make re-runs and non-empty dev DBs safe too).
-- Contracts backed: SPEC-01 (AuditRecord), SPEC-02 (Episode.content_hash,
-- KBSection, Memory.status), SPEC-03 (lexical leg, active-only default),
-- SPEC-04 (quarantine), ADR-07 (tenant wall), ADR-08 (kb_sections boundary).

-- 1. Episode idempotency key (SPEC-02 Episode.content_hash; BDD.01.03.c900).
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS content_hash TEXT;
UPDATE episodes SET content_hash = md5(content_raw) WHERE content_hash IS NULL;
ALTER TABLE episodes ALTER COLUMN content_hash SET NOT NULL;

-- 2. Tenant wall becomes schema-enforced (ADR-07). 'default' = the
--    single-tenant Phase-0 tenant.
ALTER TABLE episodes ALTER COLUMN tenant_id SET DEFAULT 'default';
UPDATE episodes SET tenant_id = 'default' WHERE tenant_id IS NULL;
ALTER TABLE episodes ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE memories ALTER COLUMN tenant_id SET DEFAULT 'default';
UPDATE memories SET tenant_id = 'default' WHERE tenant_id IS NULL;
ALTER TABLE memories ALTER COLUMN tenant_id SET NOT NULL;

-- Idempotency is per write scope; coalesce() because project_id is nullable
-- and Postgres unique indexes treat NULLs as distinct.
CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_idempotency
    ON episodes (tenant_id, agent_id, coalesce(project_id, ''), content_hash);

-- 3. Memory lifecycle status (SPEC-04 quarantine -> EARS.01.03.b800).
--    Default retrieval serves ONLY status = 'active'.
ALTER TABLE memories ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE memories DROP CONSTRAINT IF EXISTS chk_memories_status;
ALTER TABLE memories ADD CONSTRAINT chk_memories_status
    CHECK (status IN ('active', 'advisory', 'quarantined', 'superseded'));

-- 4. Feedback-loop usage signals + authorship trust for ranking.
ALTER TABLE memories ADD COLUMN IF NOT EXISTS retrieval_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_retrieved_at TIMESTAMPTZ;
ALTER TABLE memories ADD COLUMN IF NOT EXISTS source_trust TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE memories DROP CONSTRAINT IF EXISTS chk_memories_source_trust;
ALTER TABLE memories ADD CONSTRAINT chk_memories_source_trust
    CHECK (source_trust IN ('human', 'tool', 'agent'));

-- 5. Lexical leg for hybrid retrieval (SPEC-03 rank fusion).
ALTER TABLE memories ADD COLUMN IF NOT EXISTS ts_lex tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(summary, '') || ' ' || content_raw)
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_memories_lex ON memories USING gin (ts_lex);

-- 6. Tenant-leading indexes. The partial index IS the default read path:
--    live (valid_to IS NULL), active-only.
CREATE INDEX IF NOT EXISTS idx_memories_tenant_live
    ON memories (tenant_id, scope, type)
    WHERE valid_to IS NULL AND status = 'active';
CREATE INDEX IF NOT EXISTS idx_episodes_tenant ON episodes (tenant_id, ts DESC);

-- 7. Retrieval log: every hit handed to an agent is recorded so outcomes
--    can drive the confidence-update rule (SPEC-04; MemoryPort.feedback).
CREATE TABLE IF NOT EXISTS memory_retrievals (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    memory_id    UUID NOT NULL REFERENCES memories(id),
    tenant_id    TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    project_id   TEXT,
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    feedback     TEXT CHECK (feedback IN ('useful', 'not_useful', 'harmful')),
    feedback_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_retrievals_memory
    ON memory_retrievals (memory_id, retrieved_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrievals_tenant
    ON memory_retrievals (tenant_id, agent_id, retrieved_at DESC);

-- 8. Audit sink (SPEC-01 AuditRecord; EARS.01.03.b050).
CREATE TABLE IF NOT EXISTS audit_records (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id  TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    project_id TEXT,
    action     TEXT NOT NULL,
    allowed    BOOLEAN NOT NULL,
    reason     TEXT NOT NULL,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_records (tenant_id, ts DESC);

-- 9. Knowledge-core canonical table (SPEC-02 KBSection; ADR-08 boundary
--    rule 1: memory tables never mix with kb_sections). Versioned +
--    permanent (CORES.md lifecycle): new versions append; nothing deleted.
CREATE TABLE IF NOT EXISTS kb_sections (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    project_id      TEXT,
    domain_id       TEXT,
    scope           TEXT NOT NULL DEFAULT 'project',
    doc_id          TEXT NOT NULL,
    citation        TEXT NOT NULL,
    text            TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    embedding       VECTOR,
    embedding_model TEXT,
    embedding_dims  INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS trg_kb_sections_updated_at ON kb_sections;
CREATE TRIGGER trg_kb_sections_updated_at
    BEFORE UPDATE ON kb_sections
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_sections_version
    ON kb_sections (tenant_id, doc_id, citation, version);
CREATE INDEX IF NOT EXISTS idx_kb_sections_scope
    ON kb_sections (tenant_id, project_id, doc_id);
