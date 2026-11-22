"""Integration test: PostgreSQL canonical graph entities projected to live Neo4j."""

from __future__ import annotations

from typing import Any

import pytest

from engramory.core.models import GraphEntity, GraphRelation
from engramory.core.repository import Repository
from engramory.workers.graph_projection import reproject_all

pytestmark = pytest.mark.asyncio


async def test_reproject_canonical_postgres_to_live_neo4j(pg_dsn: str, neo4j_adapter: Any) -> None:
    repo = Repository(pg_dsn)
    graph = neo4j_adapter
    tenant_id = "tenant-proj-test"

    try:
        # 1. Store canonical entities in PostgreSQL
        await repo.upsert_graph_entity(
            GraphEntity(
                id="ADR-01-spine",
                entity_type="ADR",
                name="Postgres as Spine",
                tenant_id=tenant_id,
                domain_id="core",
                properties={"layer": 5, "status": "Accepted"},
            )
        )
        await repo.upsert_graph_entity(
            GraphEntity(
                id="ADR-03-graph",
                entity_type="ADR",
                name="Neo4j Graph Engine",
                tenant_id=tenant_id,
                domain_id="core",
                properties={"layer": 5, "status": "Accepted"},
            )
        )

        # 2. Store canonical relationship in PostgreSQL
        await repo.upsert_graph_relation(
            GraphRelation(
                src_id="ADR-03-graph",
                dst_id="ADR-01-spine",
                rel_type="CONSTRAINED_BY",
                tenant_id=tenant_id,
                properties={"rationale": "Graph is rebuildable projection of Postgres"},
            )
        )

        # 3. Verify counts in PostgreSQL
        assert await repo.count_graph_entities(tenant_id=tenant_id) == 2
        assert await repo.count_graph_relations(tenant_id=tenant_id) == 1

        # 4. Execute re-projection from PostgreSQL into Neo4j
        nodes, edges = await reproject_all(repo, graph, tenant_id=tenant_id)
        assert nodes == 2
        assert edges == 1

        # 5. Query Neo4j live to verify graph traversal over projected data
        neighbors = await graph.neighbors(
            id="ADR-03-graph", rel="CONSTRAINED_BY", depth=1, tenant_id=tenant_id
        )
        assert len(neighbors) == 1
        assert neighbors[0]["id"] == "ADR-01-spine"
        assert "ADR" in neighbors[0]["labels"]
        assert neighbors[0]["properties"]["name"] == "Postgres as Spine"
        assert neighbors[0]["properties"]["tenant_id"] == tenant_id

    finally:
        await repo.close()
