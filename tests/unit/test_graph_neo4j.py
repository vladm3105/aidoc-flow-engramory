"""Unit tests for Neo4jGraphAdapter."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from engramory.adapters.dev.graph_neo4j import Neo4jGraphAdapter, _sanitize_ident
from engramory.ports import GraphPort


def test_neo4j_adapter_satisfies_graph_port_interface() -> None:
    adapter = Neo4jGraphAdapter()
    for method_name in ("upsert_node", "upsert_edge", "neighbors"):
        assert hasattr(adapter, method_name), f"Missing method {method_name}"
        port_sig = inspect.signature(getattr(GraphPort, method_name))
        adapter_sig = inspect.signature(getattr(adapter, method_name))
        port_params = [p for p in port_sig.parameters.keys() if p != "self"]
        adapter_params = [p for p in adapter_sig.parameters.keys() if p != "self"]
        assert port_params == adapter_params


def test_sanitize_ident_allows_valid_identifiers() -> None:
    assert _sanitize_ident("Entity", "label") == "Entity"
    assert _sanitize_ident("ADR_01", "label") == "ADR_01"
    assert _sanitize_ident("DEPENDS_ON", "rel") == "DEPENDS_ON"


def test_sanitize_ident_rejects_cypher_injection_chars() -> None:
    with pytest.raises(ValueError, match="invalid label identifier"):
        _sanitize_ident("Node; DROP DATABASE neo4j;", "label")

    with pytest.raises(ValueError, match="invalid relationship type identifier"):
        _sanitize_ident("RELATION]-()-[", "relationship type")

    with pytest.raises(ValueError, match="invalid label identifier"):
        _sanitize_ident("Label With Space", "label")


@pytest.mark.asyncio
async def test_upsert_node_executes_parameterized_query() -> None:
    mock_session = AsyncMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__.return_value = mock_session

    adapter = Neo4jGraphAdapter(driver=mock_driver)
    await adapter.upsert_node(
        id="ADR-01",
        labels=["ADR", "Architecture"],
        props={"title": "Postgres Spine", "status": "Accepted"},
    )

    mock_session.run.assert_awaited_once()
    call_args = mock_session.run.await_args
    cypher_query = call_args[0][0]
    parameters = call_args[0][1]

    assert "MERGE (n:Entity {id: $id})" in cypher_query
    assert ":`ADR`:`Architecture`" in cypher_query
    assert parameters["id"] == "ADR-01"
    assert parameters["props"]["title"] == "Postgres Spine"


@pytest.mark.asyncio
async def test_upsert_edge_executes_parameterized_query() -> None:
    mock_session = AsyncMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__.return_value = mock_session

    adapter = Neo4jGraphAdapter(driver=mock_driver)
    await adapter.upsert_edge(
        src="ADR-04",
        dst="ADR-02",
        rel="DEPENDS_ON",
        props={"weight": 1.0},
    )

    mock_session.run.assert_awaited_once()
    call_args = mock_session.run.await_args
    cypher_query = call_args[0][0]
    parameters = call_args[0][1]

    assert "MERGE (a:Entity {id: $src})" in cypher_query
    assert "MERGE (b:Entity {id: $dst})" in cypher_query
    assert "-[r:`DEPENDS_ON`]->" in cypher_query
    assert parameters["src"] == "ADR-04"
    assert parameters["dst"] == "ADR-02"
    assert parameters["props"]["weight"] == 1.0


@pytest.mark.asyncio
async def test_neighbors_parses_records() -> None:
    mock_result = AsyncMock()
    mock_result.data.return_value = [
        {
            "id": "ADR-02",
            "labels": ["Entity", "ADR"],
            "properties": {"title": "Ports and Adapters"},
        },
    ]

    mock_session = AsyncMock()
    mock_session.run.return_value = mock_result
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__.return_value = mock_session

    adapter = Neo4jGraphAdapter(driver=mock_driver)
    neighbors = await adapter.neighbors(id="ADR-04", rel="DEPENDS_ON", depth=2)

    assert len(neighbors) == 1
    assert neighbors[0]["id"] == "ADR-02"
    assert neighbors[0]["labels"] == ["ADR"]
    assert neighbors[0]["properties"]["title"] == "Ports and Adapters"
