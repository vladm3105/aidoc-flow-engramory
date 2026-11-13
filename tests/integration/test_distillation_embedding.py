"""Integration tests for distillation worker with vector embedding."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from engramory.adapters.dev.vector_pg import PgVectorAdapter
from engramory.core.models import Episode
from engramory.core.repository import Repository
from engramory.ports import LLMPort
from engramory.workers.distillation import reflect

pytestmark = pytest.mark.asyncio


def _vec1536(val: float = 0.5) -> list[float]:
    v = [0.0] * 1536
    v[0] = val
    return v


async def test_reflect_populates_embedding_projection_and_ambient_view(pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    vec = PgVectorAdapter(pg_dsn)

    # 1. Add raw episode
    episode = Episode(
        agent_id="agent-embed-test",
        project_id="proj-ambient",
        tenant_id="tenant-embed-test",
        content_raw="Always run database migrations with transaction safety.",
    )
    ep_id = await repo.add_episode(episode)
    assert ep_id is not None

    # 2. Mock LLMPort returning 1536-dim vector
    mock_llm = AsyncMock(spec=LLMPort)
    mock_llm.embed.return_value = [_vec1536(0.88)]

    # 3. Run distillation reflection
    created = await reflect(
        repo,
        tenant_id="tenant-embed-test",
        agent_id="agent-embed-test",
        llm=mock_llm,
        vector=vec,
    )
    assert created == 1

    # 4. Verify memory is retrievable via PgVectorAdapter
    hits = await vec.query(
        embedding=_vec1536(0.88),
        k=1,
        filter={"tenant_id": "tenant-embed-test"},
    )
    assert len(hits) == 1
    assert hits[0][1] == pytest.approx(0.0, abs=1e-5)

    # 5. Verify memory is visible in v_ambient_memory_active with non-null embedding
    async with repo._conn() as conn:
        row = await (
            await conn.execute(
                """
                SELECT id, project_id, content, embedding_dims, embedding IS NOT NULL AS has_emb
                FROM v_ambient_memory_active
                WHERE tenant_id = 'tenant-embed-test' AND project_id = 'proj-ambient'
                """
            )
        ).fetchone()
        assert row is not None
        assert row["embedding_dims"] == 1536
        assert row["has_emb"] is True
        assert "Always run database migrations" in row["content"]
