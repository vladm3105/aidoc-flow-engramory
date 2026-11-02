"""Integration tests for Neo4jGraphAdapter against real Neo4j container."""

from __future__ import annotations

import os

import pytest

from engramory.adapters.dev.graph_neo4j import Neo4jGraphAdapter

pytestmark = pytest.mark.asyncio

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "change_me"))


async def test_neo4j_live_crud_and_traversal(neo4j_adapter: Neo4jGraphAdapter) -> None:
    adapter = neo4j_adapter

    try:
        # 1. Bootstrap schema constraints
        await adapter.init_schema()

        # 2. Upsert nodes
        await adapter.upsert_node(
            id="test-adr-01",
            labels=["ADR", "Foundation"],
            props={"title": "Postgres as Spine", "status": "Accepted"},
        )
        await adapter.upsert_node(
            id="test-adr-02",
            labels=["ADR", "Architecture"],
            props={"title": "Ports and Adapters", "status": "Accepted"},
        )
        await adapter.upsert_node(
            id="test-adr-03",
            labels=["ADR", "Architecture"],
            props={"title": "Neo4j Graph Engine", "status": "Accepted"},
        )

        # 3. Upsert edges: (ADR-03)-[DEPENDS_ON]->(ADR-02)-[DEPENDS_ON]->(ADR-01)
        await adapter.upsert_edge(
            src="test-adr-03",
            dst="test-adr-02",
            rel="DEPENDS_ON",
            props={"reason": "Requires port definition"},
        )
        await adapter.upsert_edge(
            src="test-adr-02",
            dst="test-adr-01",
            rel="DEPENDS_ON",
            props={"reason": "Spine interfaces"},
        )

        # 4. Query 1-hop neighbors of ADR-03
        one_hop = await adapter.neighbors(id="test-adr-03", depth=1)
        assert len(one_hop) == 1
        assert one_hop[0]["id"] == "test-adr-02"
        assert "ADR" in one_hop[0]["labels"]

        # 5. Query 2-hop neighbors of ADR-03 (should reach ADR-01)
        two_hop = await adapter.neighbors(id="test-adr-03", depth=2)
        node_ids = {n["id"] for n in two_hop}
        assert "test-adr-02" in node_ids
        assert "test-adr-01" in node_ids

        # 6. Query filtered by relationship type
        filtered_hop = await adapter.neighbors(id="test-adr-03", rel="DEPENDS_ON", depth=2)
        filtered_ids = {n["id"] for n in filtered_hop}
        assert "test-adr-02" in filtered_ids
        assert "test-adr-01" in filtered_ids

        # 7. Delete edge: (test-adr-03)-[DEPENDS_ON]->(test-adr-02)
        await adapter.delete_edge(src="test-adr-03", dst="test-adr-02", rel="DEPENDS_ON")
        remaining_neighbors = await adapter.neighbors(id="test-adr-03", depth=1)
        assert len(remaining_neighbors) == 0

        # 8. Delete node: test-adr-01
        await adapter.delete_node(id="test-adr-01")
        all_remaining = await adapter.neighbors(id="test-adr-02", depth=1)
        assert "test-adr-01" not in {n["id"] for n in all_remaining}

    finally:
        await adapter.close()
