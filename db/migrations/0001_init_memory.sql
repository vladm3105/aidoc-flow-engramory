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

-- Raw experience: episodic source material (L1 -> feeds distillation).
CREATE TABLE IF NOT EXISTS episodes (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id     TEXT NOT NULL,
    project_id   TEXT,
    space_id     TEXT,
    ts           TIMESTAMPTZ DEFAULT now(),
    kind         TEXT,                      -- decision | error | outcome | note ...
    content_raw  TEXT NOT NULL,
    metadata     JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_episodes_scope ON episodes (agent_id, project_id, ts DESC);

-- Distilled long-term memory: the "brain" (L2).
-- type: semantic (facts) | episodic (what happened) | procedural (skills)
-- scope: agent | project | space | shared
CREATE TABLE IF NOT EXISTS memories (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id         TEXT,
    project_id       TEXT,
    space_id         TEXT,
    scope            TEXT NOT NULL DEFAULT 'agent',
    type             TEXT NOT NULL CHECK (type IN ('semantic','episodic','procedural')),
    content_raw      TEXT NOT NULL,         -- KEEP: enables re-embedding on migration
    summary          TEXT,                  -- distilled form injected into prompts
    embedding        VECTOR,                -- regenerable; model recorded below
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
-- Vector index is created after the embedding dimension is known per deployment, e.g.:
--   CREATE INDEX ON memories USING hnsw (embedding vector_cosine_ops);

-- Bookkeeping for the distillation/consolidation loop.
CREATE TABLE IF NOT EXISTS consolidation_runs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id    TEXT,
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    stats       JSONB DEFAULT '{}'
);
