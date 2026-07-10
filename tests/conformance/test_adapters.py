"""Port × dev-adapter conformance (TDD-06.04.b100, partial).

Covers the VectorPort dev adapter (pgvector over the canonical Postgres) and
the fail-closed contract (BDD.01.03.b800). ``test_accelerator_cache_only``
(BDD.01.03.d010) stays pending until a managed-accelerator adapter exists —
a stub-only test would assert nothing real.

Reuses the shared ephemeral-Postgres fixture from tests/conftest.py.
"""
from __future__ import annotations

import pytest

from engramory.adapters.dev.vector_pg import PgVectorAdapter
from engramory.adapters.factory import dev_adapters
from engramory.core.models import Memory
from engramory.core.repository import Repository, StoreUnavailable

pytestmark = pytest.mark.asyncio



def _memory(summary: str, **overrides: object) -> Memory:
    kwargs: dict = dict(
        agent_id="agent-v",
        tenant_id="t-vec",
        content_raw=f"lesson: {summary}",
        summary=summary,
        type="semantic",
        embedding_model="test-embed",
        provenance={"episodes": ["e1"]},
    )
    kwargs.update(overrides)
    return Memory(**kwargs)


async def _seed(repo: Repository, vec: PgVectorAdapter) -> tuple[str, str]:
    """Two memories with orthogonal-ish embeddings; returns (near_id, far_id)."""
    near = await repo.upsert_memory(_memory("pin image tags"))
    far = await repo.upsert_memory(_memory("write tests first"))
    await vec.upsert(id=near, embedding=[1.0, 0.0, 0.0])
    await vec.upsert(id=far, embedding=[0.0, 1.0, 0.0])
    return near, far


async def test_vector_upsert_query_orders_by_distance(pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    vec = PgVectorAdapter(pg_dsn)
    near, far = await _seed(repo, vec)
    hits = await vec.query(embedding=[0.9, 0.1, 0.0], k=2, filter={"tenant_id": "t-vec"})
    assert [h[0] for h in hits] == [near, far]
    assert hits[0][1] < hits[1][1]  # closest first, distance ascending


async def test_vector_upsert_replaces_and_delete_is_idempotent(pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    vec = PgVectorAdapter(pg_dsn)
    mid = await repo.upsert_memory(_memory("replace me", tenant_id="t-vec2"))
    await vec.upsert(id=mid, embedding=[1.0, 0.0])
    await vec.upsert(id=mid, embedding=[0.0, 1.0])  # replace
    hits = await vec.query(embedding=[0.0, 1.0], k=1, filter={"tenant_id": "t-vec2"})
    assert hits[0][0] == mid and hits[0][1] == pytest.approx(0.0, abs=1e-6)
    await vec.delete(id=mid)
    await vec.delete(id=mid)  # idempotent
    assert await vec.query(embedding=[0.0, 1.0], k=1, filter={"tenant_id": "t-vec2"}) == []


async def test_vector_filter_rejects_unknown_column(pg_dsn: str) -> None:
    """The filter allowlist is a security boundary — unknown keys raise, never interpolate."""
    vec = PgVectorAdapter(pg_dsn)
    with pytest.raises(ValueError):
        await vec.query(embedding=[1.0], k=1, filter={"1=1; DROP TABLE memories; --": "x"})


async def test_adapter_failclosed() -> None:
    """BDD.01.03.b800 — adapter unavailable fails closed with a retryable error."""
    vec = PgVectorAdapter("postgresql://nobody:nope@127.0.0.1:1/nothing")
    with pytest.raises(StoreUnavailable):
        await vec.query(embedding=[1.0], k=1)


async def test_factory_wires_dev_profile(pg_dsn: str) -> None:
    adapters = dev_adapters(pg_dsn)
    assert isinstance(adapters.vector, PgVectorAdapter)
    with pytest.raises(NotImplementedError):
        from engramory.adapters.factory import adapters_for_profile

        adapters_for_profile("gcp", dsn=pg_dsn)
