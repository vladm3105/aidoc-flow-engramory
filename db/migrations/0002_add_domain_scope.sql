-- 0002 — Additive: domain scope level.
-- Foundation tweak for BRD-04 (Domain & Project Configuration). Non-destructive;
-- does not modify 0001. Inserts 'domain' into the ladder: agent -> project -> domain -> space.
-- Full domain config + domain-shared memory is delivered in the BRD-04 cycle.

ALTER TABLE episodes ADD COLUMN IF NOT EXISTS domain_id TEXT;   -- nullable: a domain groups projects
ALTER TABLE memories ADD COLUMN IF NOT EXISTS domain_id TEXT;   -- nullable

-- Indexes to support domain-scoped retrieval.
CREATE INDEX IF NOT EXISTS idx_episodes_domain ON episodes (domain_id, project_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories (domain_id, project_id, scope, type);

-- Note: memories.scope has no CHECK constraint, so the value set extends to
--   'agent' | 'project' | 'domain' | 'space'
-- with no schema change. A 'domain'-scoped memory is shared by all projects in its
-- domain_id; a 'space'-scoped memory is shared tenant-wide. See GLOSSARY + ADR-07.
