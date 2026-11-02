"""Integration test for ADR knowledge graph extraction, ingestion, and hybrid traversal."""

from __future__ import annotations

import os

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.adapters.dev.graph_neo4j import Neo4jGraphAdapter
from engramory.core.kb_graph import (
    expand_graph_context,
    extract_adr_graph,
    ingest_adr_knowledge_graph,
)
from engramory.core.repository import Repository

pytestmark = pytest.mark.asyncio

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "change_me"))


async def test_extract_adr_graph_parses_yaml_files() -> None:
    entities, relations = extract_adr_graph("sdd/05_ADR")
    assert len(entities) >= 10
    assert len(relations) >= 15

    entity_ids = {e.id for e in entities}
    assert "ADR-01" in entity_ids
    assert "ADR-02" in entity_ids
    assert "ADR-03" in entity_ids


async def test_ingest_adr_knowledge_graph_and_traverse_surface(pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    graph = Neo4jGraphAdapter(uri=NEO4J_URI, auth=NEO4J_AUTH)
    tenant_id = "tenant-adr-test"

    try:
        # Ingest ADR knowledge graph into Postgres & Neo4j
        num_ent, num_rel = await ingest_adr_knowledge_graph(
            repo,
            graph,
            adr_dir="sdd/05_ADR",
            tenant_id=tenant_id,
        )
        assert num_ent >= 10
        assert num_rel >= 15

        # Verify Postgres persistence
        assert await repo.count_graph_entities(tenant_id=tenant_id) == num_ent
        assert await repo.count_graph_relations(tenant_id=tenant_id) == num_rel

        # Verify surface graph_neighbors
        surface = AccessSurface(repo, graph=graph)
        ctx = ActorContext(
            agent_id="test-agent",
            project_id="test-proj",
            tenant_id=tenant_id,
            scopes=frozenset({"agent", "project"}),
        )

        neighbors = await surface.graph_neighbors(ctx, id="ADR-03", depth=1)
        assert len(neighbors) > 0
        neighbor_ids = {n["id"] for n in neighbors}
        assert "ADR-01" in neighbor_ids or "ADR-02" in neighbor_ids

        # Test expand_graph_context
        context_nodes = await expand_graph_context(graph, ["ADR-03"], depth=2)
        assert len(context_nodes) > 0

    finally:
        await graph.close()
