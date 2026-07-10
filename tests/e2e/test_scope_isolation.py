"""E2E scope isolation (BDD.01.03.e500; TDD.01.04.c300): cross-tenant deny + audit."""
from __future__ import annotations

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface, AuthzError
from engramory.core.models import Memory
from engramory.core.repository import Repository

pytestmark = pytest.mark.asyncio


async def test_cross_tenant_denied_e2e(pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    surface = AccessSurface(repo)
    # a memory record owned by tenant t-2
    await repo.upsert_memory(
        Memory(
            agent_id="agent-b",
            tenant_id="t-iso2",
            content_raw="t-2 secret lesson",
            summary="t-2 secret lesson",
            type="semantic",
            embedding_model="m",
            provenance={"e": ["1"]},
        )
    )
    # agent-A (tenant t-1) requests it
    ctx = ActorContext(
        agent_id="agent-a",
        project_id="p1",
        tenant_id="t-iso1",
        scopes=frozenset({"agent", "project", "domain", "space"}),
    )
    with pytest.raises(AuthzError):
        await surface.memory_search(ctx, query="secret", tenant_id="t-iso2")
    # deny AuditRecord present, and no data crossed the wall
    audits = await repo.get_audit_records(tenant_id="t-iso1")
    assert audits and audits[0]["allowed"] is False and audits[0]["reason"] == "out_of_scope"
