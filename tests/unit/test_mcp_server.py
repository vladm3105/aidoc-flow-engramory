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
        mock_ctx, task_id="IPLAN-03", depth=5, scope="project", layers=None, max_tokens=2000
    )


@pytest.mark.asyncio
async def test_mcp_call_sdd_context_slice_markdown(
    mock_surface: AccessSurface, mock_ctx: ActorContext
) -> None:
    server = McpServer(mock_surface, mock_ctx)
    req = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "sdd_context_slice",
            "arguments": {
                "task_id": "IPLAN-03",
                "format": "markdown",
                "layers": ["SPEC", "ADR"],
                "tenant_id": "custom-tenant",
                "project_id": "custom-proj",
            },
        },
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 10
    content = resp["result"]["content"]
    assert len(content) == 1
    assert "## Context Slice for IPLAN-03" in content[0]["text"]
    assert "SPEC-03" in content[0]["text"]


@pytest.mark.asyncio
async def test_mcp_call_sdd_ingest(mock_surface: AccessSurface, mock_ctx: ActorContext) -> None:
    mock_ingest = AsyncMock(return_value=(15, 20))
    server = McpServer(mock_surface, mock_ctx, ingest_fn=mock_ingest)
    req = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "sdd_ingest",
            "arguments": {
                "sdd_root": "sdd",
                "project_id": "my-project",
                "reconcile": True,
            },
        },
    }
    resp = await server.handle_request(req)
    assert resp is not None
    assert resp["id"] == 11
    assert "Ingested 15 entities and 20 relations" in resp["result"]["content"][0]["text"]
    from pathlib import Path

    mock_ingest.assert_awaited_once_with(
        sdd_root=str(Path("sdd").resolve()),
        tenant_id="test-tenant",
        project_id="my-project",
        reconcile=True,
    )


def test_mcp_http_server_endpoints(mock_surface: AccessSurface, mock_ctx: ActorContext) -> None:
    import http.client
    import threading

    server = McpServer(mock_surface, mock_ctx)
    httpd = server.create_http_server(host="127.0.0.1", port=0)
    port = httpd.server_address[1]

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        # 1. Test GET / (health/status)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["service"] == "engramory-mcp"
        conn.close()

        # 2. Test GET /sse endpoint announcement
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/sse")
        resp = conn.getresponse()
        assert resp.status == 200
        line1 = resp.readline().decode("utf-8")
        line2 = resp.readline().decode("utf-8")
        assert "event: endpoint" in line1
        assert "data: /messages" in line2
        conn.close()

        # 3. Test POST /messages JSON-RPC tools/list
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        rpc_req = {"jsonrpc": "2.0", "id": 99, "method": "tools/list"}
        post_data = json.dumps(rpc_req)
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-ID": "header-tenant",
            "X-Project-ID": "header-project",
        }
        conn.request("POST", "/messages", body=post_data, headers=headers)
        resp = conn.getresponse()
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["id"] == 99
        tools = res["result"]["tools"]
        assert any(tool["name"] == "sdd_context_slice" for tool in tools)
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


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


def test_mcp_http_server_authenticated_endpoints(mock_surface: AccessSurface) -> None:
    import http.client
    import threading

    from engramory.access.jwt_auth import AuthenticationError, JWKSValidator, TokenClaims

    mock_validator = MagicMock(spec=JWKSValidator)

    def fake_validate(token: str) -> TokenClaims:
        clean = token.replace("Bearer ", "").strip()
        if clean == "valid-token":
            return TokenClaims(
                sub="agent-auth-sub",
                iss="https://auth-stage.aiops-flow.io/application/o/engramory/",
                aud="engramory",
                exp=9999999999,
                raw_claims={
                    "jti": "jti-clean-1",
                    "agent_id": "agent-verified",
                    "namespaces": ["aidoc"],
                    "projects": ["aidoc-flow-iplanic"],
                    "roles": ["developer"],
                },
            )
        elif clean == "revoked-token":
            return TokenClaims(
                sub="agent-revoked-sub",
                iss="https://auth-stage.aiops-flow.io/application/o/engramory/",
                aud="engramory",
                exp=9999999999,
                raw_claims={
                    "jti": "jti-revoked-99",
                    "agent_id": "agent-revoked",
                },
            )
        raise AuthenticationError("Invalid or expired token")

    mock_validator.validate_token.side_effect = fake_validate

    default_ctx = ActorContext(
        agent_id="default-agent",
        tenant_id="default",
        project_id="core",
        scopes=frozenset({"agent"}),
    )

    server = McpServer(
        mock_surface,
        default_ctx,
        token_validator=mock_validator,
        auth_enabled=True,
        revoked_identifiers={"jti-revoked-99"},
    )
    httpd = server.create_http_server(host="127.0.0.1", port=0)
    port = httpd.server_address[1]

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        # 1. Missing Authorization header on POST /messages -> 401
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        rpc_req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        conn.request(
            "POST",
            "/messages",
            body=json.dumps(rpc_req),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 401
        err_res = json.loads(resp.read().decode("utf-8"))
        assert err_res["error"]["code"] == -32001
        conn.close()

        # 2. Invalid Bearer token on POST /messages -> 401
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Content-Type": "application/json", "Authorization": "Bearer bad-token"}
        conn.request("POST", "/messages", body=json.dumps(rpc_req), headers=headers)
        resp = conn.getresponse()
        assert resp.status == 401
        conn.close()

        # 3. Revoked token on POST /messages -> 401
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Content-Type": "application/json", "Authorization": "Bearer revoked-token"}
        conn.request("POST", "/messages", body=json.dumps(rpc_req), headers=headers)
        resp = conn.getresponse()
        assert resp.status == 401
        assert "revoked by platform kill-switch" in resp.read().decode("utf-8")
        conn.close()

        # 4. Valid Bearer token on POST /messages -> 200 OK
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer valid-token",
            "X-Tenant-ID": "spoofed-tenant",  # spoofing attempt
        }
        conn.request("POST", "/messages", body=json.dumps(rpc_req), headers=headers)
        resp = conn.getresponse()
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["id"] == 1
        tools = data["result"]["tools"]
        assert any(t["name"] == "memory_search" for t in tools)
        conn.close()

        # 5. GET /sse without token -> 401
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/sse")
        resp = conn.getresponse()
        assert resp.status == 401
        conn.close()

        # 6. GET /sse with query param token -> 200
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/sse?token=valid-token")
        resp = conn.getresponse()
        assert resp.status == 200
        line1 = resp.readline().decode("utf-8")
        assert "event: endpoint" in line1
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
