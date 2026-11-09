"""Regression tests for PLAN-017 audit gaps, bugs, and isolation hardening."""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engramory.access.authz import ActorContext
from engramory.access.jwt_auth import (
    AuthenticationError,
    JWKSValidator,
    TokenClaims,
    check_token_revocation,
)
from engramory.access.surface import AccessSurface
from engramory.adapters.dev.llm_litellm import LiteLLMAdapter
from engramory.cli import _close_adapters
from engramory.core.context_tools import (
    AidocDependencyGraph,
    ArtifactNode,
    ContextSlicer,
    StackPolicyValidator,
    compact_node_content,
)
from engramory.core.extraction.enrichment import SemanticChunker
from engramory.core.git_sync import GitSyncEngine
from engramory.core.kb_graph import ingest_sdd_knowledge_graph
from engramory.core.knowledge import KnowledgeCore
from engramory.core.models import Episode, KBSection, ValidationError
from engramory.core.parsers.base import Section
from engramory.mcp.server import TOOLS_SCHEMA, McpServer
from engramory.workers.distillation import reflect

# ── Phase 1 & 2: Empty SDD Purge, Sub-Chunk Citations, Header Preservation, Git Refs ─


@pytest.mark.asyncio
async def test_ingest_sdd_empty_dir_still_reconciles_stale_entities(tmp_path: Path) -> None:
    sdd_dir = tmp_path / "sdd"
    sdd_dir.mkdir()

    mock_repo = AsyncMock()
    mock_repo.purge_stale_graph_entities.return_value = ["OLD-01"]
    mock_graph = AsyncMock()

    n_ent, n_rel = await ingest_sdd_knowledge_graph(
        mock_repo,
        mock_graph,
        sdd_root=sdd_dir,
        tenant_id="t-empty",
        project_id="proj-empty",
        reconcile=True,
    )
    assert (n_ent, n_rel) == (0, 0)
    mock_repo.purge_stale_graph_entities.assert_awaited_once_with(
        tenant_id="t-empty",
        project_id="proj-empty",
        active_ids=[],
    )
    mock_graph.purge_stale_nodes.assert_awaited_once_with(
        tenant_id="t-empty",
        active_ids=[],
        project_id="proj-empty",
    )


def test_semantic_chunker_preserves_doc_context_header_across_splits() -> None:
    chunker = SemanticChunker(min_words=5, max_words=20)
    header = "[Doc: Architecture Guide > Section: Storage]\n"
    long_body = " ".join(f"word{i}" for i in range(45))
    sec = Section(
        section_id="sec_1",
        name="Storage",
        order=1,
        level=2,
        content=f"{header}{long_body}",
        citation="#storage",
        char_start=0,
        char_end=len(header) + len(long_body),
    )
    refined = chunker.refine_sections([sec])
    assert len(refined) >= 3
    for idx, part in enumerate(refined, 1):
        assert part.content.startswith(header), f"Part {idx} lost contextual header: {part.content}"


@pytest.mark.asyncio
async def test_knowledge_core_disambiguates_subchunk_and_binary_citations(tmp_path: Path) -> None:
    doc_file = tmp_path / "guide.pdf"
    doc_file.write_bytes(b"\xff\xfe\x00\x00binary-pdf-bytes")

    mock_router = MagicMock()
    mock_router.extract_document.return_value = MagicMock(
        sections=[
            Section(
                section_id="sec_1",
                name="Intro (Part 1)",
                order=1,
                level=1,
                content="First subchunk content safe text.",
                citation="#intro",
                char_start=0,
                char_end=100,
            ),
            Section(
                section_id="sec_2",
                name="Intro (Part 2)",
                order=2,
                level=1,
                content="Second subchunk content safe text.",
                citation="#intro-p2",
                char_start=0,
                char_end=100,
            ),
        ],
    )
    captured: list[KBSection] = []

    async def fake_upsert_batch(sections: list[KBSection]) -> dict[Any, str]:
        captured.extend(sections)
        return {
            (s.tenant_id, s.doc_id, s.citation, s.version): f"id-{i}"
            for i, s in enumerate(sections)
        }

    mock_repo = AsyncMock()
    mock_repo.upsert_kb_sections_batch.side_effect = fake_upsert_batch

    core = KnowledgeCore(mock_repo, router=mock_router, chunker=None)
    await core.ingest_documents_batch(
        [doc_file],
        tenant_id="t1",
        project_id="p1",
        evidence_ref="CHG-017",
        embed=False,
    )
    assert len(captured) == 2
    assert captured[0].citation != captured[1].citation


def test_git_sync_rejects_option_injection_refs(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    engine = GitSyncEngine()
    with pytest.raises(ValidationError, match="Invalid git reference"):
        engine.get_changed_files(tmp_path, base_sha="--upload-pack=evil", head_sha="HEAD")
    with pytest.raises(ValidationError, match="Invalid git reference"):
        engine.get_changed_files(tmp_path, base_sha="HEAD~1", head_sha="-oProxyCommand=evil")


# ── Phase 3: Memory Search LLM Wiring & MCP knowledge_search ─────────────────


@pytest.mark.asyncio
async def test_access_surface_memory_search_passes_query_and_embeds_1536() -> None:
    mock_repo = AsyncMock()
    mock_repo.get_memories.return_value = []
    mock_repo.log_retrievals.return_value = []
    mock_llm = AsyncMock()
    vec1536 = [0.25] * 1536
    mock_llm.embed.return_value = [vec1536]

    surface = AccessSurface(mock_repo, llm=mock_llm)
    ctx = ActorContext(
        agent_id="a1",
        project_id="p1",
        tenant_id="t1",
        scopes=frozenset({"agent", "project"}),
    )
    await surface.memory_search(ctx, query="postgres migration", k=5)
    mock_llm.embed.assert_awaited_once_with(texts=["postgres migration"])
    call_kwargs = mock_repo.get_memories.await_args.kwargs
    assert call_kwargs["query"] == "postgres migration"
    assert call_kwargs["query_vec"] == vec1536


@pytest.mark.asyncio
async def test_mcp_knowledge_search_tool_exposed_and_routed() -> None:
    tool_names = {t["name"] for t in TOOLS_SCHEMA}
    assert "knowledge_search" in tool_names

    mock_surface = AsyncMock(spec=AccessSurface)
    mock_surface.knowledge_search.return_value = [
        KBSection(
            id="sec-101",
            doc_id="SPEC-01",
            citation="docs/spec.md#L1-L10",
            text="Access surface contract",
            tenant_id="t1",
            project_id="p1",
            scope="project",
            version=1,
        )
    ]
    ctx = ActorContext(
        agent_id="a1",
        project_id="p1",
        tenant_id="t1",
        scopes=frozenset({"agent", "project"}),
    )
    server = McpServer(mock_surface, ctx)
    resp = await server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {
                "name": "knowledge_search",
                "arguments": {"query": "access surface", "k": 3},
            },
        }
    )
    assert resp is not None
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert len(payload) == 1
    assert payload[0]["doc_id"] == "SPEC-01"
    mock_surface.knowledge_search.assert_awaited_once_with(
        ctx,
        query="access surface",
        domain_id=None,
        scope="project",
        limit=3,
    )


# ── Phase 4: Embed Model Provenance, Reflect Dim Guard, & Context Tools ──────


@pytest.mark.asyncio
async def test_reflect_skips_non_1536_stub_vectors_and_handles_unspecced_mock_llm() -> None:
    mock_repo = AsyncMock()
    mock_repo.get_unreflected_episodes.return_value = [
        Episode(
            id="ep-1",
            content_raw="lesson learned",
            kind="note",
            agent_id="a1",
            project_id="p1",
            tenant_id="t1",
        )
    ]
    mock_repo.upsert_memory.return_value = "mem-1"
    # Un-specced AsyncMock where accessing .embed_model returns a MagicMock, not a str
    mock_llm = AsyncMock()
    mock_llm.embed.return_value = [[0.1, 0.2, 0.3]]  # 3 dims != 1536
    mock_vector = AsyncMock()

    count = await reflect(
        mock_repo,
        tenant_id="t1",
        agent_id="a1",
        llm=mock_llm,
        vector=mock_vector,
    )
    assert count == 1
    saved_mem = mock_repo.upsert_memory.await_args.args[0]
    assert isinstance(saved_mem.embedding_model, str)
    assert saved_mem.embedding_dims is None
    mock_vector.upsert.assert_not_awaited()


def test_litellm_adapter_exposes_embed_model_property() -> None:
    adapter = LiteLLMAdapter(default_embed_model="text-embedding-3-small")
    assert adapter.embed_model == "text-embedding-3-small"
    assert adapter._embed_model == "text-embedding-3-small"


def test_context_tools_url_safe_comment_stripping_and_frontmatter_and_best_depth(
    tmp_path: Path,
) -> None:
    # 1. URL before banned pattern on same line must still be caught
    stripped_ts = StackPolicyValidator._strip_inline_comment(
        'const url = "http://localhost:8000"; eval("bad") // trailing comment',
        is_python=False,
    )
    assert 'eval("bad")' in stripped_ts
    assert "trailing comment" not in stripped_ts

    stripped_py = StackPolicyValidator._strip_inline_comment(
        'tag = "color #ff00ff"; os.system("bad") # python comment',
        is_python=True,
    )
    assert 'os.system("bad")' in stripped_py
    assert "python comment" not in stripped_py

    # 2. Horizontal rule `---` in Markdown body must not toggle in_fm back on
    md_doc = (
        "---\n"
        "id: SPEC-99\n"
        "---\n"
        "# Heading\n"
        "Paragraph one.\n"
        "---\n"
        "Unimportant prose after horizontal rule that should be dropped in Tier 1.\n"
        "The system SHALL enforce isolation.\n"
    )
    compacted = compact_node_content(md_doc, tier=1)
    assert "The system SHALL enforce isolation." in compacted
    assert "Unimportant prose after horizontal rule" not in compacted

    # 3. Best-depth DAG re-expansion when a node is first reached via a deep path
    graph = AidocDependencyGraph()
    # TASK-1 -> [A, B]; A -> C -> D -> E; B -> D -> E
    # With max_depth=3:
    # Visiting via A first reaches D at depth 3 (so D's parent E at depth 4 is cut off).
    # Visiting via B later reaches D at depth 2 (so D's parent E is at depth 3 <= max_depth!).
    graph.add_node(ArtifactNode("TASK-1", "IPLAN", "", ["SPEC-A", "SPEC-B"]))
    graph.add_node(ArtifactNode("SPEC-A", "SPEC", "", ["SPEC-C"]))
    graph.add_node(ArtifactNode("SPEC-C", "SPEC", "", ["ADR-D"]))
    graph.add_node(ArtifactNode("SPEC-B", "SPEC", "", ["ADR-D"]))
    graph.add_node(ArtifactNode("ADR-D", "ADR", "", ["BRD-E"]))
    graph.add_node(ArtifactNode("BRD-E", "BRD", "", []))

    slicer = ContextSlicer(graph)
    closure = slicer.slice_subgraph("TASK-1", max_depth=3)
    closure_ids = {n.node_id for n in closure}
    assert "BRD-E" in closure_ids


# ── Phase 5: MCP HTTP Server Persistent Event Loop, /health, & JWT Fail-Closed ─


def test_mcp_http_server_health_and_persistent_event_loop() -> None:
    loop_ids: list[int] = []
    mock_surface = AsyncMock(spec=AccessSurface)

    async def record_loop(*args: Any, **kwargs: Any) -> list[Any]:
        loop_ids.append(id(asyncio.get_running_loop()))
        return []

    mock_surface.memory_search.side_effect = record_loop
    ctx = ActorContext(
        agent_id="a1",
        project_id="p1",
        tenant_id="t1",
        scopes=frozenset({"agent", "project"}),
    )
    server = McpServer(mock_surface, ctx)
    httpd = server.create_http_server(host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        # /health and /healthz both return 200
        for path in ("/health", "/healthz"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path)
            resp = conn.getresponse()
            assert resp.status == 200
            conn.close()

        # Invalid JSON returns 400 with -32700 Parse error
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/messages",
            body="{not-valid-json",
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 400
        err_data = json.loads(resp.read().decode("utf-8"))
        assert err_data["error"]["code"] == -32700
        conn.close()

        # Two sequential POSTs execute on the exact same background event loop
        for req_id in (1, 2):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {"name": "memory_search", "arguments": {"query": "q"}},
                }
            )
            conn.request(
                "POST", "/messages", body=body, headers={"Content-Type": "application/json"}
            )
            resp = conn.getresponse()
            assert resp.status == 200
            conn.close()

        assert len(loop_ids) == 2
        assert loop_ids[0] == loop_ids[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_jwt_key_rotation_preserves_headers_and_revocation_fails_closed() -> None:
    validator = JWKSValidator(
        "https://auth.example.com/jwks",
        issuer="https://auth.example.com/",
        audience="engramory",
        cache_jwk_set=False,
        lifespan=120,
        headers={"X-Custom": "1"},
    )
    assert validator.headers["X-Custom"] == "1"
    assert validator.cache_jwk_set is False
    assert validator.lifespan == 120

    claims = TokenClaims(
        sub="agent-1",
        iss="https://auth.example.com/",
        aud="engramory",
        exp=9999999999,
        raw_claims={"jti": "tok-1"},
    )
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(AuthenticationError, match="fail-closed"):
            check_token_revocation(
                claims,
                security_gate_url="http://127.0.0.1:9999",
                fail_closed=True,
            )


@pytest.mark.asyncio
async def test_cli_close_adapters_handles_sync_and_async_graph_close() -> None:
    sync_adapters = MagicMock()
    sync_adapters.graph.close = MagicMock(return_value=None)
    await _close_adapters(sync_adapters)
    sync_adapters.graph.close.assert_called_once()

    async_adapters = MagicMock()
    async_adapters.graph.close = AsyncMock(return_value=None)
    await _close_adapters(async_adapters)
    async_adapters.graph.close.assert_awaited_once()
