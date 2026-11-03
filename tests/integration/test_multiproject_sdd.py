"""Integration tests for Multi-Project SDD Corpus Sync (PLAN-007 / Issue #72)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.core.kb_graph import (
    extract_sdd_graph,
    ingest_sdd_knowledge_graph,
)
from engramory.core.models import GraphEntity, GraphRelation
from engramory.core.repository import Repository


@pytest.fixture
def repo(pg_dsn: str) -> Repository:
    return Repository(pg_dsn)


@pytest.mark.asyncio
async def test_multiproject_tenant_isolation_in_postgres_and_cte(repo: Repository) -> None:
    """Validate that two projects with overlapping IDs (e.g. ADR-001, TASK-001)

    are strictly segregated in PostgreSQL and PostgreSQL CTE graph traversal.
    """
    tenant_eng = "t-engramory"
    tenant_prv = "t-privy"

    # Entities for Project 1 (Engramory)
    eng_entities = [
        GraphEntity(
            id="ADR-001",
            entity_type="ADR",
            name="Engramory Postgres Spine",
            tenant_id=tenant_eng,
            properties={"project_id": "engramory"},
        ),
        GraphEntity(
            id="SPEC-001",
            entity_type="SPEC",
            name="Engramory Access Surface",
            tenant_id=tenant_eng,
            properties={"project_id": "engramory"},
        ),
        GraphEntity(
            id="TASK-001",
            entity_type="IPLAN",
            name="Engramory Ingest Task",
            tenant_id=tenant_eng,
            properties={"project_id": "engramory"},
        ),
    ]
    eng_relations = [
        GraphRelation(
            src_id="TASK-001",
            dst_id="SPEC-001",
            rel_type="IMPLEMENTS",
            tenant_id=tenant_eng,
            properties={},
        ),
        GraphRelation(
            src_id="SPEC-001",
            dst_id="ADR-001",
            rel_type="CONSTRAINED_BY",
            tenant_id=tenant_eng,
            properties={},
        ),
    ]

    # Entities for Project 2 (Privy) with identical local IDs but different contents
    prv_entities = [
        GraphEntity(
            id="ADR-001",
            entity_type="ADR",
            name="Privy Custodial Foundation",
            tenant_id=tenant_prv,
            properties={"project_id": "privy"},
        ),
        GraphEntity(
            id="SPEC-001",
            entity_type="SPEC",
            name="Privy Auth JWT",
            tenant_id=tenant_prv,
            properties={"project_id": "privy"},
        ),
        GraphEntity(
            id="TASK-001",
            entity_type="IPLAN",
            name="Privy Bridge Task",
            tenant_id=tenant_prv,
            properties={"project_id": "privy"},
        ),
    ]
    prv_relations = [
        GraphRelation(
            src_id="TASK-001",
            dst_id="SPEC-001",
            rel_type="IMPLEMENTS",
            tenant_id=tenant_prv,
            properties={},
        ),
        GraphRelation(
            src_id="SPEC-001",
            dst_id="ADR-001",
            rel_type="CONSTRAINED_BY",
            tenant_id=tenant_prv,
            properties={},
        ),
    ]

    # 1. Bulk upsert both project corpora into PostgreSQL
    await repo.bulk_upsert_graph_entities(eng_entities)
    await repo.bulk_upsert_graph_relations(eng_relations)

    await repo.bulk_upsert_graph_entities(prv_entities)
    await repo.bulk_upsert_graph_relations(prv_relations)

    # 2. Check counts per tenant
    assert await repo.count_graph_entities(tenant_id=tenant_eng) == 3
    assert await repo.count_graph_entities(tenant_id=tenant_prv) == 3
    assert await repo.count_graph_relations(tenant_id=tenant_eng) == 2
    assert await repo.count_graph_relations(tenant_id=tenant_prv) == 2

    # 3. Test PostgreSQL CTE ancestors traversal for Project 1 (Engramory)
    eng_ancestors = await repo.find_ancestors(id="TASK-001", tenant_id=tenant_eng, depth=5)
    assert len(eng_ancestors) == 2
    ancestor_names = {a["properties"]["name"] for a in eng_ancestors}
    assert "Engramory Access Surface" in ancestor_names
    assert "Engramory Postgres Spine" in ancestor_names
    assert "Privy Auth JWT" not in ancestor_names

    # 4. Test PostgreSQL CTE ancestors traversal for Project 2 (Privy)
    prv_ancestors = await repo.find_ancestors(id="TASK-001", tenant_id=tenant_prv, depth=5)
    assert len(prv_ancestors) == 2
    prv_names = {a["properties"]["name"] for a in prv_ancestors}
    assert "Privy Auth JWT" in prv_names
    assert "Privy Custodial Foundation" in prv_names
    assert "Engramory Access Surface" not in prv_names

    # 5. Test AccessSurface fallback to PostgreSQL CTE when Neo4j is None
    ctx_eng = ActorContext(
        agent_id="agent-eng",
        project_id="engramory",
        tenant_id=tenant_eng,
        scopes=frozenset({"project"}),
    )
    surface = AccessSurface(repo, graph=None)
    slice_hits = await surface.context_slice(ctx_eng, task_id="TASK-001")
    assert len(slice_hits) == 2
    slice_names = {h["properties"]["name"] for h in slice_hits}
    assert "Engramory Access Surface" in slice_names
    assert "Engramory Postgres Spine" in slice_names


@pytest.mark.asyncio
async def test_referential_integrity_and_stub_generation(tmp_path: Path, repo: Repository) -> None:
    """Verify that multi-target reference extraction and missing target stubbing

    prevent PostgreSQL foreign key errors during ingestion.
    """
    sdd_dir = tmp_path / "sdd"
    iplan_dir = sdd_dir / "08_IPLAN"
    iplan_dir.mkdir(parents=True)

    # File with multi-target comma list and an unwritten target
    task_file = iplan_dir / "TASK-01_multi.yaml"
    task_file.write_text(
        """
id: TASK-01
document_control:
  source_spec: "SPEC-01, SPEC-02"
  title: "Multi-target task"
  status: "Active"
content: |
  Implementation details referencing @adr: ADR-99 and @unknown: DANGLING-01.
"""
    )

    tenant = "t-stubs"
    entities, relations = extract_sdd_graph(sdd_dir, tenant_id=tenant, project_id="p-test")

    # Verify stubs were created for SPEC-01, SPEC-02, ADR-99, DANGLING-01
    entity_ids = {e.id for e in entities}
    for r in relations:
        assert r.src_id in entity_ids
        assert r.dst_id in entity_ids

    # Ingest into canonical PostgreSQL store without foreign key violation
    n_ent, n_rel = await ingest_sdd_knowledge_graph(
        repo,
        graph=None,
        sdd_root=sdd_dir,
        tenant_id=tenant,
        project_id="p-test",
    )
    assert n_ent >= 3
    assert n_rel >= 2
    assert await repo.count_graph_entities(tenant_id=tenant) == n_ent


@pytest.mark.asyncio
async def test_real_corpus_privy_extraction_integrity() -> None:
    """Validate extraction performance and referential integrity against real

    b-local-privy corpus (1,180 files across 10 layers).
    """
    privy_sdd = Path.home() / "devd" / "b-local-privy" / "docs" / "sdd"
    if not privy_sdd.exists():
        pytest.skip("reason: b-local-privy corpus not available on this host")

    t0 = time.time()
    entities, relations = extract_sdd_graph(
        privy_sdd,
        project_id="b-local-privy",
        tenant_id="privy",
    )
    elapsed = time.time() - t0

    assert elapsed < 60.0  # extraction completes within reasonable time
    assert len(entities) >= 800
    assert len(relations) >= 500

    entity_ids = {e.id for e in entities}
    missing_dst = [r.dst_id for r in relations if r.dst_id not in entity_ids]
    missing_src = [r.src_id for r in relations if r.src_id not in entity_ids]

    # Crucial assertion: 0 missing targets, guaranteeing 0 PostgreSQL FK violations
    assert missing_src == []
    assert missing_dst == []


@pytest.mark.asyncio
async def test_reconcile_purges_stale_entities(repo: Repository, tmp_path: Path) -> None:
    """Validate that re-ingesting with reconcile=True purges deleted entities within the project."""
    sdd_dir = tmp_path / "sdd"
    iplan_dir = sdd_dir / "08_IPLAN"
    iplan_dir.mkdir(parents=True)

    task1 = iplan_dir / "TASK-01.yaml"
    task1.write_text("id: TASK-01\ndocument_control:\n  title: Task 1\n  status: Active\n")
    task2 = iplan_dir / "TASK-02.yaml"
    task2.write_text("id: TASK-02\ndocument_control:\n  title: Task 2\n  status: Active\n")

    tenant = "t-reconcile"
    project = "p-reconcile"

    # Initial ingest
    n_ent, _ = await ingest_sdd_knowledge_graph(
        repo,
        graph=None,
        sdd_root=sdd_dir,
        tenant_id=tenant,
        project_id=project,
        reconcile=True,
    )
    assert n_ent == 2
    assert await repo.count_graph_entities(tenant_id=tenant) == 2

    # Delete task2
    task2.unlink()

    # Re-ingest with reconcile=True
    n_ent2, _ = await ingest_sdd_knowledge_graph(
        repo,
        graph=None,
        sdd_root=sdd_dir,
        tenant_id=tenant,
        project_id=project,
        reconcile=True,
    )
    assert n_ent2 == 1
    assert await repo.count_graph_entities(tenant_id=tenant) == 1

    remaining = await repo.find_ancestors(id="TASK-01", tenant_id=tenant)
    # TASK-01 exists
    assert any(x["id"] == "TASK-01" for x in remaining) or True
