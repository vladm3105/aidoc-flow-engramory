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


async def test_context_slice_layers_and_topological_sort(
    surface: AccessSurface, pg_dsn: str
) -> None:
    """Validate layer filtering and root-to-leaf topological ordering in context_slice."""
    from engramory.core.models import GraphEntity, GraphRelation

    repo = Repository(pg_dsn)
    tenant = "t-slice-layers"
    ctx = _ctx(tenant)

    # Insert chain: IPLAN-01 -> SPEC-01 -> ADR-01 -> BRD-01
    await repo.upsert_graph_entity(
        GraphEntity(id="BRD-01", entity_type="BRD", name="Business Goals", tenant_id=tenant)
    )
    await repo.upsert_graph_entity(
        GraphEntity(id="ADR-01", entity_type="ADR", name="Architecture", tenant_id=tenant)
    )
    await repo.upsert_graph_entity(
        GraphEntity(id="SPEC-01", entity_type="SPEC", name="Specification", tenant_id=tenant)
    )
    await repo.upsert_graph_entity(
        GraphEntity(
            id="IPLAN-01", entity_type="IPLAN", name="Implementation Plan", tenant_id=tenant
        )
    )

    await repo.upsert_graph_relation(
        GraphRelation(src_id="IPLAN-01", dst_id="SPEC-01", rel_type="IMPLEMENTS", tenant_id=tenant)
    )
    await repo.upsert_graph_relation(
        GraphRelation(
            src_id="SPEC-01", dst_id="ADR-01", rel_type="CONSTRAINED_BY", tenant_id=tenant
        )
    )
    await repo.upsert_graph_relation(
        GraphRelation(src_id="ADR-01", dst_id="BRD-01", rel_type="SATISFIES", tenant_id=tenant)
    )

    # 1. Unfiltered slice: all 3 ancestors
    all_ancestors = await surface.context_slice(ctx, task_id="IPLAN-01", depth=5)
    assert len(all_ancestors) == 3
    # Topologically sorted: BRD-01 (layer 1) -> ADR-01 (layer 5) -> SPEC-01 (layer 6)
    assert [a["id"] for a in all_ancestors] == ["BRD-01", "ADR-01", "SPEC-01"]

    # 2. Filtered slice by layers=["SPEC", "ADR"]
    filtered = await surface.context_slice(ctx, task_id="IPLAN-01", depth=5, layers=["SPEC", "ADR"])
    assert len(filtered) == 2
    assert [a["id"] for a in filtered] == ["ADR-01", "SPEC-01"]

    # 3. Token budget restriction
    budgeted = await surface.context_slice(ctx, task_id="IPLAN-01", depth=5, max_tokens=10)
    assert len(budgeted) < 3
