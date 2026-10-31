-- 0004 — Ambient Gateway View Projection (LiteLLM Proxy integration).
-- Exposes an immutable read-only view contract (v_ambient_memory_active)
-- for the Ambient Brain & Gateway (platform-litellm-laya) per ADR-004.
-- Serves active, non-superseded, non-quarantined memories.

CREATE OR REPLACE VIEW v_ambient_memory_active AS
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
