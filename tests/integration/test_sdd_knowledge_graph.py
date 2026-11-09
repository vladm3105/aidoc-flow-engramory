"""Integration test for full SDD corpus graph ingestion, context slicing, and blast radius."""

from __future__ import annotations

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.adapters.dev.graph_neo4j import Neo4jGraphAdapter
from engramory.core.kb_graph import (
    get_sdd_blast_radius,
    get_sdd_context_slice,
    ingest_sdd_knowledge_graph,
)
from engramory.core.repository import Repository

pytestmark = pytest.mark.asyncio


async def test_sdd_knowledge_graph_ingestion_and_context_slicing(
    pg_dsn: str,
    neo4j_adapter: Neo4jGraphAdapter,
) -> None:
    repo = Repository(pg_dsn)
    graph = neo4j_adapter
    tenant_id = "tenant-sdd-test"
    project_id = "engramory-core"

    try:
        # 1. Ingest SDD knowledge graph (all layers)
        num_ent, num_rel = await ingest_sdd_knowledge_graph(
            repo,
            graph,
            sdd_root="sdd",
            tenant_id=tenant_id,
            project_id=project_id,
        )
        assert num_ent >= 30
        assert num_rel >= 50

        # 2. Verify PostgreSQL canonical persistence
        assert await repo.count_graph_entities(tenant_id=tenant_id) == num_ent
        assert await repo.count_graph_relations(tenant_id=tenant_id) == num_rel

        # Verify entity details in PG
        iplan_ent = await repo.get_graph_entity("IPLAN-01", tenant_id=tenant_id)
        assert iplan_ent is not None
        assert iplan_ent.entity_type == "IPLAN"

        # 3. Test AccessSurface Context Slicing (deterministic upstream ancestor closure)
        surface = AccessSurface(repo, graph=graph)
        ctx = ActorContext(
            agent_id="coding-agent-01",
            project_id=project_id,
            tenant_id=tenant_id,
            scopes=frozenset({"agent", "project"}),
        )

        slice_nodes = await surface.context_slice(ctx, task_id="IPLAN-03", depth=5)
        assert len(slice_nodes) > 0
        slice_ids = {n["id"] for n in slice_nodes}
        # IPLAN-03 implements SPEC-03, which is constrained by ADR-03 and satisfies BDD-01
        assert "SPEC-03" in slice_ids or "ADR-03" in slice_ids

        # 4. Test Helper get_sdd_context_slice
        direct_slice = await get_sdd_context_slice(graph, "IPLAN-03", depth=4, tenant_id=tenant_id)
        assert len(direct_slice) > 0

        # 5. Test AccessSurface Blast Radius (downstream impact of modifying a specification)
        blast_nodes = await surface.blast_radius(ctx, doc_id="ADR-03", depth=5)
        # ADR-03 is upstream of SPEC-03 / IPLAN-03
        blast_ids = {n["id"] for n in blast_nodes}
        assert len(blast_ids) > 0

        direct_blast = await get_sdd_blast_radius(graph, "SPEC-03", depth=3, tenant_id=tenant_id)
        assert isinstance(direct_blast, list)

        # 6. Test Lifecycle Deletion
        # Deleting a leaf entity cascades cleanly in Postgres and Neo4j
        ent_count_before = await repo.count_graph_entities(tenant_id=tenant_id)
        await repo.delete_graph_entity("IPLAN-06", tenant_id=tenant_id)
        await graph.delete_node(id="IPLAN-06", tenant_id=tenant_id)

        ent_count_after = await repo.count_graph_entities(tenant_id=tenant_id)
        assert ent_count_after == ent_count_before - 1
        assert await repo.get_graph_entity("IPLAN-06", tenant_id=tenant_id) is None

    finally:
        await graph.close()
