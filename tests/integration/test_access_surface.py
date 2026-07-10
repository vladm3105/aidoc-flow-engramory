"""Access-surface integration tests (SPEC-01; TDD-01.04.b200).

Real Postgres via the shared fixture; asserts persistence AND the audit trail
(EARS.01.03.b050 — every decision audited).
"""
from __future__ import annotations

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface, GovernedWriteRejected
from engramory.core.repository import Repository

pytestmark = pytest.mark.asyncio


def _ctx(tenant: str, **overrides: object) -> ActorContext:
    kwargs: dict = dict(
        agent_id="agent-a",
        project_id="p1",
        tenant_id=tenant,
        scopes=frozenset({"agent", "project", "space"}),
    )
    kwargs.update(overrides)
    return ActorContext(**kwargs)


@pytest.fixture
def surface(pg_dsn: str) -> AccessSurface:
    return AccessSurface(Repository(pg_dsn))


async def test_memory_add_persists_and_audits(surface: AccessSurface, pg_dsn: str) -> None:
    ctx = _ctx("t-as1")
    episode_id = await surface.memory_add(ctx, content="learned a lesson", kind="note")
    assert episode_id
    audits = await Repository(pg_dsn).get_audit_records(tenant_id="t-as1")
    assert audits and audits[0]["action"] == "memory_add" and audits[0]["allowed"] is True


async def test_governed_write_rejected(surface: AccessSurface, pg_dsn: str) -> None:
    """TDD.01.04.b200 — knowledge_ingest without evidence_ref: reject + audit deny."""
    ctx = _ctx("t-as2")
    with pytest.raises(GovernedWriteRejected):
        await surface.knowledge_ingest(
            ctx, doc_id="ADR-07", citation="§scope", text="claim", evidence_ref=None
        )
    audits = await Repository(pg_dsn).get_audit_records(tenant_id="t-as2")
    assert audits and audits[0]["allowed"] is False
    assert await Repository(pg_dsn).count_kb_sections(tenant_id="t-as2") == 0  # nothing stored


async def test_governed_write_with_evidence_lands(surface: AccessSurface, pg_dsn: str) -> None:
    ctx = _ctx("t-as3")
    section_id = await surface.knowledge_ingest(
        ctx, doc_id="ADR-07", citation="§scope", text="claim", evidence_ref="PR#24"
    )
    assert section_id
    assert await Repository(pg_dsn).count_kb_sections(tenant_id="t-as3") == 1


async def test_memory_search_logs_retrievals(surface: AccessSurface, pg_dsn: str) -> None:
    """SPEC-03: every returned hit carries a retrieval_id (feedback-loop plumbing)."""
    from engramory.core.models import Memory

    repo = Repository(pg_dsn)
    ctx = _ctx("t-as4")
    await repo.upsert_memory(
        Memory(
            agent_id="agent-a",
            tenant_id="t-as4",
            content_raw="pin tags",
            summary="pin tags",
            type="procedural",
            embedding_model="m",
            provenance={"e": ["1"]},
        )
    )
    hits = await surface.memory_search(ctx, query="tags", k=5)
    assert len(hits) == 1
    assert hits[0].retrieval_id and hits[0].summary == "pin tags"
    assert hits[0].token_count > 0
