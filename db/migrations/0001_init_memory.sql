-- Engramory memory schema (engine-neutral, canonical in Postgres).
-- Principle: raw text + provenance are the source of truth;
-- embeddings and the graph are rebuildable projections.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Each agent's evolving identity / standing preferences (L3).
CREATE TABLE IF NOT EXISTS agent_profiles (
    agent_id             TEXT PRIMARY KEY,
    display_name         TEXT,
    standing_preferences JSONB DEFAULT '{}',
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now()
);

-- Keep updated_at current on every write, without depending on application code.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_agent_profiles_updated_at ON agent_profiles;
CREATE TRIGGER trg_agent_profiles_updated_at
    BEFORE UPDATE ON agent_profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Raw experience: episodic source material (L1 -> feeds distillation).
-- tenant_id is the hard multi-tenant isolation boundary (nothing crosses tenants).
CREATE TABLE IF NOT EXISTS episodes (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id     TEXT NOT NULL,
    project_id   TEXT,
    tenant_id    TEXT,
    ts           TIMESTAMPTZ DEFAULT now(),
    kind         TEXT,                      -- decision | error | outcome | note ...
    content_raw  TEXT NOT NULL,
    metadata     JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_episodes_scope ON episodes (agent_id, project_id, ts DESC);

-- Distilled long-term memory: the "brain" (L2).
-- type:  semantic (facts) | episodic (what happened) | procedural (skills)
-- scope: agent | project | space   (visibility ladder within a tenant; 'space' = tenant-wide.
--        Migration 0002 inserts 'domain' between 'project' and 'space'. See GLOSSARY + ADR-07.)
CREATE TABLE IF NOT EXISTS memories (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id         TEXT,
    project_id       TEXT,
    tenant_id        TEXT,
    scope            TEXT NOT NULL DEFAULT 'agent',   -- intentionally no CHECK: the scope
                                                      -- ladder is extended additively (see 0002)
    type             TEXT NOT NULL CHECK (type IN ('semantic','episodic','procedural')),
    content_raw      TEXT NOT NULL,         -- KEEP: enables re-embedding on migration
    summary          TEXT,                  -- distilled form injected into prompts
    embedding        VECTOR,                -- regenerable; dims recorded per row (see note)
    embedding_model  TEXT,
    embedding_dims   INTEGER,
    provenance       JSONB DEFAULT '{}',    -- source episode ids, project, agent, ts
    confidence       REAL DEFAULT 0.5,
    valid_from       TIMESTAMPTZ DEFAULT now(),
    valid_to         TIMESTAMPTZ,           -- NULL = currently valid (temporal validity)
    supersedes       UUID REFERENCES memories(id),
    created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories (agent_id, project_id, scope, type);
CREATE INDEX IF NOT EXISTS idx_memories_valid ON memories (valid_to);
-- Supports supersession-chain walks and FK integrity checks on delete.
CREATE INDEX IF NOT EXISTS idx_memories_supersedes ON memories (supersedes) WHERE supersedes IS NOT NULL;
-- Vector (ANN) index: pgvector requires a fixed dimension, so it is created per
-- deployment once EMBEDDING_DIMS is pinned (all rows must then share that dim), e.g.:
--   ALTER TABLE memories ALTER COLUMN embedding TYPE vector(768);
--   CREATE INDEX ON memories USING hnsw (embedding vector_cosine_ops);
-- content_raw is always retained, so a dimension/model change is a re-embed, not data loss.

-- Bookkeeping for the distillation/consolidation loop.
CREATE TABLE IF NOT EXISTS consolidation_runs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id    TEXT,
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    stats       JSONB DEFAULT '{}'
);
