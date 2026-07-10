"""E2E scoped retrieval (BDD.01.03.a100): own + shared memories, nothing else."""
from __future__ import annotations

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.core.models import Memory
from engramory.core.repository import Repository

pytestmark = pytest.mark.asyncio


def _mem(summary: str, **overrides: object) -> Memory:
    kwargs: dict = dict(
        agent_id="agent-a",
        tenant_id="t-ret",
        content_raw=f"lesson: {summary}",
        summary=summary,
        type="semantic",
        embedding_model="m",
        provenance={"e": ["1"]},
    )
    kwargs.update(overrides)
    return Memory(**kwargs)


async def test_scoped_retrieve_e2e(pg_dsn: str) -> None:
    repo = Repository(pg_dsn)
    surface = AccessSurface(repo)
    await repo.upsert_memory(_mem("own lesson"))
    await repo.upsert_memory(_mem("team lesson", agent_id=None, scope="space"))
    await repo.upsert_memory(_mem("someone else's", agent_id="agent-b"))
    ctx = ActorContext(
        agent_id="agent-a",
        project_id="p1",
        tenant_id="t-ret",
        scopes=frozenset({"agent", "project", "domain", "space"}),
    )
    hits = await surface.memory_search(ctx, query="lesson", scope="space", k=10)
    assert {h.summary for h in hits} == {"own lesson", "team lesson"}


async def test_ungranted_scope_levels_never_leak(pg_dsn: str) -> None:
    """A caller granted only 'agent' must not receive space-scoped rows, even
    in the right tenant and even when they exist (scope-containment)."""
    repo = Repository(pg_dsn)
    surface = AccessSurface(repo)
    await repo.upsert_memory(_mem("own private", tenant_id="t-cont"))
    await repo.upsert_memory(
        _mem("tenant-wide", tenant_id="t-cont", agent_id=None, scope="space")
    )
    ctx = ActorContext(
        agent_id="agent-a", project_id="p1", tenant_id="t-cont", scopes=frozenset({"agent"})
    )
    hits = await surface.memory_search(ctx, query="anything", k=10)
    assert {h.summary for h in hits} == {"own private"}


async def test_token_budget_truncates(pg_dsn: str) -> None:
    """SPEC-01/PRD.01.perf.context_token_budget — hits capped by cumulative tokens."""
    repo = Repository(pg_dsn)
    surface = AccessSurface(repo)
    for i in range(5):
        await repo.upsert_memory(_mem(f"lesson number {i} " + "x" * 100, tenant_id="t-bud"))
    ctx = ActorContext(
        agent_id="agent-a", project_id="p1", tenant_id="t-bud", scopes=frozenset({"agent"})
    )
    unbounded = await surface.memory_search(ctx, query="lesson", k=10)
    assert len(unbounded) == 5
    tight = await surface.memory_search(ctx, query="lesson", k=10, token_budget=40)
    assert 1 <= len(tight) < 5
    assert sum(h.token_count for h in tight) <= 40
