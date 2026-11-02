"""Unit tests for Engramory MCP Gateway server (SPEC-01 / ADR-10)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.mcp.server import McpServer


@pytest.fixture
def mock_surface() -> AccessSurface:
    surface = MagicMock(spec=AccessSurface)
    surface.context_slice = AsyncMock(
        return_value=[
            {"id": "SPEC-03", "labels": ["SPEC"], "properties": {"name": "Retrieval"}},
            {"id": "ADR-03", "labels": ["ADR"], "properties": {"name": "Graph Engine"}},
        ]
    )
    surface.blast_radius = AsyncMock(
        return_value=[
            {"id": "IPLAN-03", "labels": ["IPLAN"], "properties": {"name": "Build"}},
        ]
    )
    surface.graph_neighbors = AsyncMock(
        return_value=[
            {"id": "ADR-01", "labels": ["ADR"], "properties": {}},
        ]
    )
    surface.memory_add = AsyncMock(return_value="ep-123")
    return surface


@pytest.fixture
def mock_ctx() -> ActorContext:
    return ActorContext(
        agent_id="test-agent",
        project_id="test-proj",
        tenant_id="test-tenant",
        scopes=frozenset({"agent", "project"}),
    )


@pytest.mark.asyncio
async def test_mcp_initialize(mock_surface: AccessSurface, mock_ctx: ActorContext) -> None:
    server = McpServer(mock_surface, mock_ctx)
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "engramory-mcp"


@pytest.mark.asyncio
async def test_mcp_tools_list(mock_surface: AccessSurface, mock_ctx: ActorContext) -> None:
    server = McpServer(mock_surface, mock_ctx)
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 2
    tools = resp["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert "sdd_context_slice" in tool_names
    assert "sdd_blast_radius" in tool_names
    assert "sdd_ingest" in tool_names
    assert "memory_search" in tool_names
    assert "memory_add" in tool_names


@pytest.mark.asyncio
async def test_mcp_call_sdd_context_slice(
    mock_surface: AccessSurface, mock_ctx: ActorContext
) -> None:
    server = McpServer(mock_surface, mock_ctx)
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "sdd_context_slice",
            "arguments": {"task_id": "IPLAN-03", "depth": 5},
        },
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 3
    content = resp["result"]["content"]
    assert len(content) == 1
    data = json.loads(content[0]["text"])
    assert len(data) == 2
    assert data[0]["id"] == "SPEC-03"
    mock_surface.context_slice.assert_awaited_once_with(
        mock_ctx, task_id="IPLAN-03", depth=5, scope="project"
    )


@pytest.mark.asyncio
async def test_mcp_call_sdd_blast_radius(
    mock_surface: AccessSurface, mock_ctx: ActorContext
) -> None:
    server = McpServer(mock_surface, mock_ctx)
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "sdd_blast_radius",
            "arguments": {"doc_id": "ADR-03", "depth": 4},
        },
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 4
    content = resp["result"]["content"]
    data = json.loads(content[0]["text"])
    assert len(data) == 1
    assert data[0]["id"] == "IPLAN-03"
    mock_surface.blast_radius.assert_awaited_once_with(
        mock_ctx, doc_id="ADR-03", depth=4, scope="project"
    )
