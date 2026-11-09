"""Unit tests for graph projection worker."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from engramory.core.models import GraphEntity, GraphRelation
from engramory.workers.graph_projection import (
    project_entities,
    project_relations,
    reproject_all,
)

pytestmark = pytest.mark.asyncio


async def test_project_entities_invokes_graph_upsert_node() -> None:
    mock_repo = AsyncMock()
    mock_repo.list_graph_entities.return_value = [
        GraphEntity(
            id="ADR-01",
            entity_type="ADR",
            name="Postgres as Spine",
            tenant_id="tenant-test",
            domain_id="core",
            properties={"status": "Accepted"},
        ),
    ]

    mock_graph = AsyncMock()

    count = await project_entities(mock_repo, mock_graph, tenant_id="tenant-test")
    assert count == 1
    mock_graph.upsert_node.assert_awaited_once_with(
        id="ADR-01",
        labels=["ADR", "SDD"],
        props={
            "name": "Postgres as Spine",
            "domain_id": "core",
            "tenant_id": "tenant-test",
            "status": "Accepted",
        },
    )


async def test_project_relations_invokes_graph_upsert_edge() -> None:
    mock_repo = AsyncMock()
    mock_repo.list_graph_relations.return_value = [
        GraphRelation(
            src_id="ADR-03",
            dst_id="ADR-02",
            rel_type="DEPENDS_ON",
            tenant_id="tenant-test",
            properties={"weight": 1.0},
        ),
    ]

    mock_graph = AsyncMock()

    count = await project_relations(mock_repo, mock_graph, tenant_id="tenant-test")
    assert count == 1
    mock_graph.upsert_edge.assert_awaited_once_with(
        src="ADR-03",
        dst="ADR-02",
        rel="DEPENDS_ON",
        props={
            "tenant_id": "tenant-test",
            "weight": 1.0,
        },
    )


async def test_reproject_all_rebuilds_nodes_and_edges() -> None:
    mock_repo = AsyncMock()
    mock_repo.list_graph_entities.return_value = [
        GraphEntity(id="E1", entity_type="Concept", name="Entity 1", tenant_id="t1"),
        GraphEntity(id="E2", entity_type="Concept", name="Entity 2", tenant_id="t1"),
    ]
    mock_repo.list_graph_relations.return_value = [
        GraphRelation(src_id="E1", dst_id="E2", rel_type="RELATES_TO", tenant_id="t1"),
    ]

    mock_graph = AsyncMock()

    nodes, edges = await reproject_all(mock_repo, mock_graph, tenant_id="t1")
    assert nodes == 2
    assert edges == 1
    mock_graph.init_schema.assert_awaited_once()
    assert mock_graph.upsert_node.await_count == 2
    assert mock_graph.upsert_edge.await_count == 1
