-- Migration 0007: Canonical Graph Entities and Relations Schema (ADR-01 / ADR-03 / ADR-05)
-- PostgreSQL is the single source of truth for graph entities and relations.
-- The graph database (Neo4j) is a rebuildable projection derived from these tables.

CREATE TABLE IF NOT EXISTS graph_entities (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    domain_id TEXT NOT NULL DEFAULT 'default',
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS idx_graph_entities_tenant_type
    ON graph_entities (tenant_id, entity_type);

CREATE INDEX IF NOT EXISTS idx_graph_entities_domain
    ON graph_entities (tenant_id, domain_id);

CREATE TABLE IF NOT EXISTS graph_relations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL DEFAULT 'default',
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (tenant_id, src_id) REFERENCES graph_entities (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, dst_id) REFERENCES graph_entities (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_graph_relations UNIQUE (tenant_id, src_id, dst_id, rel_type)
);

CREATE INDEX IF NOT EXISTS idx_graph_relations_src
    ON graph_relations (tenant_id, src_id);

CREATE INDEX IF NOT EXISTS idx_graph_relations_dst
    ON graph_relations (tenant_id, dst_id);

CREATE INDEX IF NOT EXISTS idx_graph_relations_type
    ON graph_relations (tenant_id, rel_type);
