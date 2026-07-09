"""Static checks on the SQL migrations (no database required)."""
from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def test_migrations_exist_and_ordered() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert [f.name for f in files] == [
        "0001_init_memory.sql",
        "0002_add_domain_scope.sql",
        "0003_reconcile_contracts.sql",
    ]


def test_scope_columns_use_tenant_id_not_space_id() -> None:
    """Scope-model invariant (ADR-07): the isolation column is tenant_id."""
    init_sql = (MIGRATIONS / "0001_init_memory.sql").read_text()
    assert "tenant_id" in init_sql
    # 'space_id' must not appear as a column definition (it was renamed to tenant_id).
    assert "space_id" not in init_sql


def test_domain_scope_is_additive() -> None:
    domain_sql = (MIGRATIONS / "0002_add_domain_scope.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS domain_id" in domain_sql


def test_migration_0003_reconciles_contracts() -> None:
    """0003 closes the SPEC-vs-schema gaps found in the 2026-07-09 review."""
    sql = (MIGRATIONS / "0003_reconcile_contracts.sql").read_text()
    for token in (
        "content_hash",        # SPEC-02 Episode idempotency key
        "status",              # SPEC-04 quarantine (EARS.01.03.b800)
        "source_trust",        # ranking trust signal
        "ts_lex",              # SPEC-03 lexical leg
        "memory_retrievals",   # feedback loop
        "audit_records",       # SPEC-01 AuditRecord
        "kb_sections",         # SPEC-02 KBSection / ADR-08 boundary rule 1
    ):
        assert token in sql, f"0003 missing '{token}'"


def test_migration_0003_enforces_tenant_wall() -> None:
    """ADR-07: tenant_id is the hard boundary — schema-enforced from 0003 on."""
    sql = (MIGRATIONS / "0003_reconcile_contracts.sql").read_text()
    assert sql.count("ALTER COLUMN tenant_id SET NOT NULL") == 2  # episodes + memories
    assert "idx_memories_tenant_live" in sql
