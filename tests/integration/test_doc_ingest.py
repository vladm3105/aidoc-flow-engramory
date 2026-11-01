"""Integration tests for document ingestion into kb_sections."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.core.chunking import ingest_file
from engramory.core.repository import Repository
from engramory.ports import LLMPort

pytestmark = pytest.mark.asyncio


def _vec384(val: float = 0.5) -> list[float]:
    v = [0.0] * 384
    v[0] = val
    return v


async def test_ingest_file_creates_kb_sections_and_embeddings(tmp_path: Path, pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    surface = AccessSurface(repo)
    ctx = ActorContext(
        tenant_id="t-doc-ingest",
        agent_id="agent-ingest",
        project_id="p-doc-ingest",
        scopes=("project", "agent"),
    )

    doc_file = tmp_path / "ADR-099_test.md"
    doc_file.write_text(
        """# ADR-099: Micro-segmentation

## Context
We need strict network boundaries between services.

## Decision
All inbound ingress must pass through the edge gateway.
""",
        encoding="utf-8",
    )

    mock_llm = AsyncMock(spec=LLMPort)
    mock_llm.embed.return_value = [_vec384(0.95)]

    class MockAdapters:
        llm = mock_llm

    cnt = await ingest_file(
        doc_file,
        surface=surface,
        repo=repo,
        ctx=ctx,
        scope="project",
        embed=True,
        adapters=MockAdapters(),
    )
    assert cnt == 3  # preamble, context, decision

    async with repo._conn() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT doc_id, citation, text, embedding_dims, embedding IS NOT NULL as has_emb
                FROM kb_sections
                WHERE tenant_id = 't-doc-ingest' AND doc_id = 'ADR-099_test'
                ORDER BY citation
                """
            )
        ).fetchall()
        assert len(rows) == 3
        # Decision section should have embedding populated
        dec_row = [r for r in rows if r["citation"] == "§decision"][0]
        assert "All inbound ingress" in dec_row["text"]
        assert dec_row["has_emb"] is True
        assert dec_row["embedding_dims"] == 384
