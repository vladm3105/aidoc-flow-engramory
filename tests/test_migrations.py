"""Static checks on the SQL migrations (no database required)."""
from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def test_migrations_exist_and_ordered() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert [f.name for f in files] == [
        "0001_init_memory.sql",
        "0002_add_domain_scope.sql",
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
