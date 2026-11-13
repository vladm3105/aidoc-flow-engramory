"""Integration tests for KnowledgeCore, batch ingestion, and retrieval (PLAN-010)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface, GovernedWriteRejected
from engramory.cli import main
from engramory.core.knowledge import KnowledgeCore
from engramory.core.models import KBSection
from engramory.core.repository import Repository
from engramory.ports.llm import LLMPort


def _vec1536(val: float = 0.5) -> list[float]:
    v = [0.0] * 1536
    v[0] = val
    return v


@pytest.mark.asyncio
async def test_knowledge_core_batch_ingest_and_retrieval(tmp_path: Path, pg_dsn: str) -> None:
    repo = Repository(pg_dsn)

    # 1. Create two test markdown files with realistic section lengths (> 30 words)
    f1 = tmp_path / "guide.md"
    f1.write_text(
        """# Architectural Guide

## Section One
Engramory stores canonical knowledge in PostgreSQL. Every piece of memory or knowledge
is persisted as a durable row with content, provenance, and vector embedding projections.
This design guarantees auditability and zero data loss across migrations and restarts.

## Section Two
The graph projection is rebuildable in Neo4j. Nodes and edges represent semantic entities
and relationships extracted from documents, enabling multi-hop path traversal and hybrid
context slices alongside vector similarity search.
""",
        encoding="utf-8",
    )

    f2 = tmp_path / "faq.md"
    f2.write_text(
        """# FAQ

## General
Frequently asked questions about storage and retrieval across Engramory services.
This document addresses common architecture inquiries, lifecycle management, and
governed write procedures mandated by ADR-06 and ADR-08.
""",
        encoding="utf-8",
    )

    mock_llm = AsyncMock(spec=LLMPort)
    mock_llm.embed.side_effect = lambda texts, model="embed": [_vec1536(0.8) for _ in texts]

    core = KnowledgeCore(repo, llm=mock_llm)

    tenant_id = "t-batch-k"
    project_id = "p-arch"

    # 2. Batch ingestion
    batch_res = await core.ingest_documents_batch(
        [f1, f2],
        tenant_id=tenant_id,
        project_id=project_id,
        scope="project",
    )

    assert len(batch_res) == 2
    assert str(f1) in batch_res
    assert str(f2) in batch_res
    assert len(batch_res[str(f1)]) == 3
    assert len(batch_res[str(f2)]) == 1

    # 3. List sections
    sections = await core.list_sections(tenant_id=tenant_id, project_id=project_id)
    assert len(sections) == 4
    doc_ids = {s.doc_id for s in sections}
    assert f"{project_id}:guide.md" in doc_ids
    assert f"{project_id}:faq.md" in doc_ids

    # 4. Get individual section
    sec_id = batch_res[str(f1)][1]
    sec = await core.get_section(sec_id, tenant_id=tenant_id)
    assert sec is not None
    assert sec.id == sec_id
    assert "Engramory stores canonical knowledge" in sec.text

    # Cross-tenant get yields None
    other_sec = await core.get_section(sec_id, tenant_id="other-tenant")
    assert other_sec is None

    # 5. Search sections
    search_results = await core.search_sections(
        "Neo4j",
        tenant_id=tenant_id,
        project_id=project_id,
        limit=5,
    )
    assert len(search_results) > 0


@pytest.mark.asyncio
async def test_knowledge_core_embedding_dimension_mismatch_handled(
    tmp_path: Path, pg_dsn: str
) -> None:
    repo = Repository(pg_dsn)

    f = tmp_path / "doc.md"
    f.write_text("# Title\n\nContent here.", encoding="utf-8")

    # Return 768 dims instead of expected 1536
    mock_llm = AsyncMock(spec=LLMPort)
    mock_llm.embed.return_value = [[0.1] * 768]

    core = KnowledgeCore(repo, llm=mock_llm, expected_embedding_dims=1536)

    # Ingestion should succeed without blowing up (text persisted, dimension mismatch skipped)
    res = await core.ingest_file(f, tenant_id="t-dim-test", project_id="p-dim")
    assert len(res) == 1

    sec = await core.get_section(res[0], tenant_id="t-dim-test")
    assert sec is not None
    assert "Content here." in sec.text


@pytest.mark.asyncio
async def test_access_surface_batch_ingest_and_search(pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    surface = AccessSurface(repo)
    ctx = ActorContext(
        tenant_id="t-surface-k",
        agent_id="agent-batch",
        project_id="p-surf",
        scopes=("project", "agent"),
    )

    sec1 = KBSection(
        tenant_id="t-surface-k",
        project_id="p-surf",
        doc_id="p-surf:doc1",
        citation="doc1#L1-L10",
        text="Surface batch item 1",
    )
    sec2 = KBSection(
        tenant_id="t-surface-k",
        project_id="p-surf",
        doc_id="p-surf:doc2",
        citation="doc2#L1-L10",
        text="Surface batch item 2",
    )

    # Reject when missing evidence_ref (ADR-06)
    with pytest.raises(GovernedWriteRejected):
        await surface.knowledge_ingest_batch(
            ctx,
            sections=[sec1, sec2],
            evidence_ref=None,
        )

    # Allow with evidence_ref
    ids = await surface.knowledge_ingest_batch(
        ctx,
        sections=[sec1, sec2],
        evidence_ref="sha256:abc123456",
    )
    assert len(ids) == 2

    # Search via surface
    results = await surface.knowledge_search(ctx, "Surface batch")
    assert len(results) >= 2


def test_cli_knowledge_file_ingest_and_search(
    tmp_path: Path, pg_dsn: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    init_code = main(
        [
            "init",
            "--agent-id",
            "agent-cli",
            "--project-id",
            "p-cli-batch",
            "--tenant-id",
            "t-cli-batch",
            "--dsn",
            pg_dsn,
        ]
    )
    assert init_code == 0
    _ = capsys.readouterr()

    # Create document
    doc = tmp_path / "notes.md"
    doc.write_text(
        """# Notes
## Meeting
Discussion about knowledge architecture.
""",
        encoding="utf-8",
    )

    # Run CLI ingest
    exit_code = main(["--json", "knowledge", "ingest", str(doc)])
    assert exit_code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["files"] == 1
    assert data["sections"] == 1

    # Run CLI search
    exit_code = main(["--json", "knowledge", "search", "Discussion"])
    assert exit_code == 0
    search_out = capsys.readouterr().out
    search_data = json.loads(search_out)
    assert search_data["count"] >= 1
    assert "Discussion about knowledge architecture." in search_data["results"][0]["text"]
