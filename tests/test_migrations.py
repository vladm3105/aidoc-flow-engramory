"""Static checks on the SQL migrations (no database required)."""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def test_migrations_exist_and_ordered() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert [f.name for f in files] == [
        "0001_init_memory.sql",
        "0002_add_domain_scope.sql",
        "0003_reconcile_contracts.sql",
        "0004_ambient_gateway_view.sql",
        "0005_pin_embeddings_hnsw.sql",
        "0006_resize_embeddings_1536.sql",
        "0007_graph_entities_canonical.sql",
        "0008_runbooks_and_doc_governance.sql",
        "0009_tenant_profiles_and_experience_dedup.sql",
        "0010_multimodal_rag_and_blob_cas.sql",
    ]


def test_migration_0010_multimodal_blob_cas_invariants() -> None:
    """Migration 0010 defines kb_blobs, tiered 4 MiB chunk tables, STORAGE EXTERNAL, and ts_lex."""
    sql = (MIGRATIONS / "0010_multimodal_rag_and_blob_cas.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS kb_blobs" in sql
    assert "CREATE TABLE IF NOT EXISTS kb_blob_chunks" in sql
    assert "CREATE UNLOGGED TABLE IF NOT EXISTS kb_blob_chunks_large" in sql
    assert "SET STORAGE EXTERNAL" in sql
    assert "ts_blob_cold" in sql
    assert "ts_lex TSVECTOR GENERATED ALWAYS AS" in sql
    assert "idx_kb_sections_lex" in sql


def test_scope_columns_use_tenant_id_not_space_id() -> None:
    """Scope-model invariant (ADR-07): the isolation column is tenant_id."""
    init_sql = (MIGRATIONS / "0001_init_memory.sql").read_text()
    assert "tenant_id" in init_sql
    # 'space_id' must not appear as a column definition (it was renamed to
    # tenant_id). Word boundary so future tokens like 'namespace_id' don't trip it.
    assert not re.search(r"\bspace_id\b", init_sql)


def test_domain_scope_is_additive() -> None:
    domain_sql = (MIGRATIONS / "0002_add_domain_scope.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS domain_id" in domain_sql


def test_migration_0003_reconciles_contracts() -> None:
    """0003 closes the SPEC-vs-schema gaps found in the 2026-07-09 review."""
    sql = (MIGRATIONS / "0003_reconcile_contracts.sql").read_text()
    for token in (
        "content_hash",  # SPEC-02 Episode idempotency key
        "status",  # SPEC-04 quarantine (EARS.01.03.b800)
        "source_trust",  # ranking trust signal
        "ts_lex",  # SPEC-03 lexical leg
        "memory_retrievals",  # feedback loop
        "audit_records",  # SPEC-01 AuditRecord
        "kb_sections",  # SPEC-02 KBSection / ADR-08 boundary rule 1
    ):
        assert token in sql, f"0003 missing '{token}'"


def test_migration_0003_enforces_tenant_wall() -> None:
    """ADR-07: tenant_id is the hard boundary — schema-enforced from 0003 on."""
    sql = (MIGRATIONS / "0003_reconcile_contracts.sql").read_text()
    assert sql.count("ALTER COLUMN tenant_id SET NOT NULL") == 2  # episodes + memories
    assert "idx_memories_tenant_live" in sql


def test_migration_0004_ambient_gateway_view() -> None:
    """0004 exposes the immutable read-only view contract for platform-litellm-laya."""
    sql = (MIGRATIONS / "0004_ambient_gateway_view.sql").read_text()
    assert "v_ambient_memory_active" in sql
    assert "status = 'active'" in sql
    assert "valid_to IS NULL" in sql


def test_migration_0005_pin_embeddings_hnsw() -> None:
    """0005 pins vector(384) and creates HNSW indices safely with pre-alter sanitization."""
    sql = (MIGRATIONS / "0005_pin_embeddings_hnsw.sql").read_text()
    assert "vector(384)" in sql
    assert "idx_memories_embedding_hnsw" in sql
    assert "idx_kb_sections_embedding_hnsw" in sql
    assert "vector_dims(embedding) != 384" in sql
    assert "USING hnsw (embedding vector_cosine_ops)" in sql


def test_migration_0006_resize_embeddings_1536() -> None:
    """0006 resizes to vector(1536) for Qwen 8B / standard embeddings and rebuilds HNSW."""
    sql = (MIGRATIONS / "0006_resize_embeddings_1536.sql").read_text()
    assert "vector(1536)" in sql
    assert "idx_memories_embedding_hnsw" in sql
    assert "idx_kb_sections_embedding_hnsw" in sql
    assert "vector_dims(embedding) != 1536" in sql
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "v_ambient_memory_active" in sql


def test_migration_0007_graph_entities_canonical() -> None:
    """0007 creates canonical graph_entities and graph_relations schema (ADR-01/03/05)."""
    sql = (MIGRATIONS / "0007_graph_entities_canonical.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS graph_entities" in sql
    assert "CREATE TABLE IF NOT EXISTS graph_relations" in sql
    assert "PRIMARY KEY (tenant_id, id)" in sql
    assert "uq_graph_relations UNIQUE (tenant_id, src_id, dst_id, rel_type)" in sql
    assert "ON DELETE CASCADE" in sql


def test_migration_0008_runbooks_and_doc_governance() -> None:
    """0008 creates kb_documents and kb_runbook_steps with composite tenant_id PKs."""
    sql = (MIGRATIONS / "0008_runbooks_and_doc_governance.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS kb_documents" in sql
    assert "CREATE TABLE IF NOT EXISTS kb_runbook_steps" in sql
    assert "PRIMARY KEY (tenant_id, doc_id)" in sql
    assert "PRIMARY KEY (tenant_id, step_uid)" in sql
    assert "CONSTRAINT chk_green_readonly" in sql
    assert "DROP TRIGGER IF EXISTS trg_kb_documents_updated_at" in sql


def test_migration_0009_tenant_profiles_and_experience_dedup() -> None:
    """0009 adds tenant_id to agent_profiles and creates uq_episodes_event_id."""
    sql = (MIGRATIONS / "0009_tenant_profiles_and_experience_dedup.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'" in sql
    assert "PRIMARY KEY (tenant_id, agent_id)" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_episodes_event_id" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_episodes_tenant_project_ts" in sql
