"""SPEC-01 tool-set completion tests (PLAN-002 Phase 1; TDD-01.04).

Real Postgres via the shared fixture. Covers the repository capabilities
(record_feedback, tenant-scoped get_memory, retire_memory, agent profiles)
and the three remaining SPEC-01 surface tools (memory_feedback,
memory_forget, agent_profile_get) — every decision audited.
"""
from __future__ import annotations

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.core.models import AgentProfile, Episode, Memory
from engramory.core.repository import Repository

pytestmark = pytest.mark.asyncio


def _ctx(tenant: str, **overrides: object) -> ActorContext:
    kwargs: dict = dict(
        agent_id="agent-a",
        project_id="p1",
        tenant_id=tenant,
        scopes=frozenset({"agent", "project", "space"}),
    )
    kwargs.update(overrides)
    return ActorContext(**kwargs)


def _memory(tenant: str, **overrides: object) -> Memory:
    kwargs: dict = dict(
        agent_id="agent-a",
        content_raw="always pin image tags",
        summary="pin image tags",
        type="procedural",
        embedding_model="m",
        provenance={"episodes": ["e1"]},
        tenant_id=tenant,
    )
    kwargs.update(overrides)
    return Memory(**kwargs)


@pytest.fixture
def repo(pg_dsn: str) -> Repository:
    return Repository(pg_dsn)


@pytest.fixture
def surface(repo: Repository) -> AccessSurface:
    return AccessSurface(repo)


# ── Repository: feedback ────────────────────────────────────────────────────

async def test_record_feedback_sets_outcome(repo: Repository) -> None:
    mem_id = await repo.upsert_memory(_memory("t-fb1"))
    [rid] = await repo.log_retrievals(
        memory_ids=[mem_id], tenant_id="t-fb1", agent_id="agent-a", project_id="p1"
    )
    await repo.record_feedback(rid, "useful", tenant_id="t-fb1")
    assert await repo.get_retrieval_feedback(
        tenant_id="t-fb1", memory_id=mem_id
    ) == ["useful"]


async def test_record_feedback_rejects_unknown_outcome(repo: Repository) -> None:
    with pytest.raises(ValueError):
        await repo.record_feedback("00000000-0000-0000-0000-000000000000",
                                   "amazing", tenant_id="t-fb2")


async def test_record_feedback_is_tenant_guarded(repo: Repository) -> None:
    """A foreign tenant must not be able to stamp feedback on my retrieval."""
    mem_id = await repo.upsert_memory(_memory("t-fb3"))
    [rid] = await repo.log_retrievals(
        memory_ids=[mem_id], tenant_id="t-fb3", agent_id="agent-a", project_id="p1"
    )
    with pytest.raises(KeyError):
        await repo.record_feedback(rid, "harmful", tenant_id="t-other")
    assert await repo.get_retrieval_feedback(
        tenant_id="t-fb3", memory_id=mem_id
    ) == [None]  # untouched


# ── Repository: tenant-scoped get_memory + retire ───────────────────────────

async def test_get_memory_is_tenant_scoped(repo: Repository) -> None:
    mem_id = await repo.upsert_memory(_memory("t-gm1"))
    fetched = await repo.get_memory(mem_id, tenant_id="t-gm1")
    assert fetched.id == mem_id
    with pytest.raises(KeyError):
        await repo.get_memory(mem_id, tenant_id="t-other")


async def test_retire_memory_end_dates_and_hides(repo: Repository) -> None:
    mem_id = await repo.upsert_memory(_memory("t-rt1"))
    await repo.retire_memory(mem_id, tenant_id="t-rt1")
    retired = await repo.get_memory(mem_id, tenant_id="t-rt1")
    assert retired.valid_to is not None and retired.status == "superseded"
    assert retired.content_raw  # provenance/content retained, never deleted
    live = await repo.get_memories(tenant_id="t-rt1", agent_id="agent-a", k=10)
    assert live == []


async def test_retire_memory_twice_raises_and_preserves_first_end_date(
    repo: Repository,
) -> None:
    """Bitemporal integrity: a second forget must not rewrite valid_to."""
    mem_id = await repo.upsert_memory(_memory("t-rt3"))
    await repo.retire_memory(mem_id, tenant_id="t-rt3")
    first = await repo.get_memory(mem_id, tenant_id="t-rt3")
    with pytest.raises(KeyError):
        await repo.retire_memory(mem_id, tenant_id="t-rt3")
    again = await repo.get_memory(mem_id, tenant_id="t-rt3")
    assert again.valid_to == first.valid_to


async def test_record_feedback_last_write_wins(repo: Repository) -> None:
    """Pinned contract: an agent may revise its outcome; the latest stamp wins
    (SPEC-04 reads the current value, not a history)."""
    mem_id = await repo.upsert_memory(_memory("t-fb4"))
    [rid] = await repo.log_retrievals(
        memory_ids=[mem_id], tenant_id="t-fb4", agent_id="agent-a", project_id="p1"
    )
    await repo.record_feedback(rid, "useful", tenant_id="t-fb4")
    await repo.record_feedback(rid, "harmful", tenant_id="t-fb4")
    assert await repo.get_retrieval_feedback(
        tenant_id="t-fb4", memory_id=mem_id
    ) == ["harmful"]


async def test_retire_memory_is_tenant_guarded(repo: Repository) -> None:
    mem_id = await repo.upsert_memory(_memory("t-rt2"))
    with pytest.raises(KeyError):
        await repo.retire_memory(mem_id, tenant_id="t-other")
    still = await repo.get_memory(mem_id, tenant_id="t-rt2")
    assert still.valid_to is None and still.status == "active"


# ── Repository: agent profiles ──────────────────────────────────────────────

async def test_agent_profile_roundtrip(repo: Repository) -> None:
    assert await repo.get_agent_profile("nobody-yet") is None
    await repo.upsert_agent_profile(
        AgentProfile(agent_id="agent-p1", display_name="Agent P",
                     standing_preferences={"tone": "terse"})
    )
    profile = await repo.get_agent_profile("agent-p1")
    assert profile is not None
    assert profile.display_name == "Agent P"
    assert profile.standing_preferences["tone"] == "terse"


async def test_agent_profile_upsert_updates(repo: Repository) -> None:
    await repo.upsert_agent_profile(AgentProfile(agent_id="agent-p2"))
    await repo.upsert_agent_profile(
        AgentProfile(agent_id="agent-p2", display_name="Renamed")
    )
    profile = await repo.get_agent_profile("agent-p2")
    assert profile is not None and profile.display_name == "Renamed"


# ── Surface: memory_feedback ────────────────────────────────────────────────

async def test_surface_memory_feedback_records_and_audits(
    surface: AccessSurface, repo: Repository
) -> None:
    ctx = _ctx("t-sf1")
    mem_id = await repo.upsert_memory(_memory("t-sf1"))
    hits = await surface.memory_search(ctx, query="tags", k=5)
    assert hits and hits[0].memory_id == mem_id
    await surface.memory_feedback(ctx, hits[0].retrieval_id, "useful")
    assert await repo.get_retrieval_feedback(
        tenant_id="t-sf1", memory_id=mem_id
    ) == ["useful"]
    audits = await repo.get_audit_records(tenant_id="t-sf1")
    assert audits[0]["action"] == "memory_feedback" and audits[0]["allowed"] is True


async def test_surface_memory_feedback_rejects_bad_outcome(
    surface: AccessSurface, repo: Repository
) -> None:
    ctx = _ctx("t-sf2")
    with pytest.raises(ValueError):
        await surface.memory_feedback(
            ctx, "00000000-0000-0000-0000-000000000000", "meh"
        )


# ── Surface: memory_forget ──────────────────────────────────────────────────

async def test_surface_memory_forget_retires_and_audits(
    surface: AccessSurface, repo: Repository
) -> None:
    ctx = _ctx("t-sg1")
    mem_id = await repo.upsert_memory(_memory("t-sg1"))
    await surface.memory_forget(ctx, mem_id, reason="stale advice")
    assert await surface.memory_search(ctx, query="tags", k=5) == []
    audits = await repo.get_audit_records(tenant_id="t-sg1")
    forget = [a for a in audits if a["action"] == "memory_forget"]
    assert forget and forget[0]["allowed"] is True
    assert "stale advice" in forget[0]["reason"]


async def test_surface_memory_forget_denied_leaves_memory_intact(
    surface: AccessSurface, repo: Repository
) -> None:
    """Authorize-before-execute for the destructive tool: a denied caller
    changes nothing, and the deny audit carries the caller's reason note."""
    from engramory.access.surface import AuthzError

    mem_id = await repo.upsert_memory(_memory("t-sg3"))
    denied = _ctx("t-sg3", scopes=frozenset())  # agent scope not granted
    with pytest.raises(AuthzError):
        await surface.memory_forget(denied, mem_id, reason="drive-by delete")
    still = await repo.get_memory(mem_id, tenant_id="t-sg3")
    assert still.status == "active" and still.valid_to is None
    audits = await repo.get_audit_records(tenant_id="t-sg3")
    assert audits[0]["action"] == "memory_forget" and audits[0]["allowed"] is False
    assert "drive-by delete" in audits[0]["reason"]


async def test_surface_memory_forget_cannot_cross_tenants(
    surface: AccessSurface, repo: Repository
) -> None:
    """Forgetting a foreign tenant's memory looks identical to a missing id."""
    mem_id = await repo.upsert_memory(_memory("t-sg2"))
    with pytest.raises(KeyError):
        await surface.memory_forget(_ctx("t-intruder"), mem_id, reason="attack")
    still = await repo.get_memory(mem_id, tenant_id="t-sg2")
    assert still.status == "active" and still.valid_to is None


# ── Surface: agent_profile_get ──────────────────────────────────────────────

async def test_surface_agent_profile_get_default_on_fresh_store(
    surface: AccessSurface, repo: Repository
) -> None:
    ctx = _ctx("t-pr1", agent_id="agent-fresh")
    profile = await surface.agent_profile_get(ctx)
    assert profile.agent_id == "agent-fresh"
    assert profile.display_name is None
    audits = await repo.get_audit_records(tenant_id="t-pr1")
    assert audits[0]["action"] == "agent_profile_get" and audits[0]["allowed"] is True


async def test_surface_agent_profile_get_returns_own_profile_only(
    surface: AccessSurface, repo: Repository
) -> None:
    await repo.upsert_agent_profile(
        AgentProfile(agent_id="agent-b", display_name="Someone Else")
    )
    profile = await surface.agent_profile_get(_ctx("t-pr2", agent_id="agent-a"))
    assert profile.agent_id == "agent-a"  # never another agent's row


# ── Worker: interim reflect() ───────────────────────────────────────────────

async def test_reflect_projects_episodes_to_searchable_memories(
    surface: AccessSurface, repo: Repository
) -> None:
    from engramory.workers.distillation import reflect

    ctx = _ctx("t-rf1")
    await surface.memory_add(ctx, content="prefer uv over pip in this workspace")
    created = await reflect(repo, tenant_id="t-rf1", agent_id="agent-a")
    assert created == 1
    hits = await surface.memory_search(ctx, query="uv", k=5)
    assert len(hits) == 1
    assert "uv over pip" in hits[0].summary


async def test_reflect_is_idempotent(surface: AccessSurface, repo: Repository) -> None:
    from engramory.workers.distillation import reflect

    ctx = _ctx("t-rf2")
    await surface.memory_add(ctx, content="ephemeral containers for tests")
    assert await reflect(repo, tenant_id="t-rf2", agent_id="agent-a") == 1
    assert await reflect(repo, tenant_id="t-rf2", agent_id="agent-a") == 0
    hits = await surface.memory_search(ctx, query="containers", k=10)
    assert len(hits) == 1  # projected exactly once


async def test_reflect_respects_agent_wall_within_tenant(repo: Repository) -> None:
    """Scope isolation inside one tenant: reflect(agent-a) must not project —
    or consume — agent-b's episodes."""
    from engramory.workers.distillation import reflect

    await repo.add_episode(
        Episode(agent_id="agent-a", content_raw="a's fact", tenant_id="t-rf4")
    )
    await repo.add_episode(
        Episode(agent_id="agent-b", content_raw="b's fact", tenant_id="t-rf4")
    )
    assert await reflect(repo, tenant_id="t-rf4", agent_id="agent-a") == 1
    remaining = await repo.get_unreflected_episodes(
        tenant_id="t-rf4", agent_id="agent-b"
    )
    assert [e.content_raw for e in remaining] == ["b's fact"]


async def test_reflect_respects_tenant_wall(repo: Repository) -> None:
    from engramory.workers.distillation import reflect

    await repo.add_episode(
        Episode(agent_id="agent-a", content_raw="foreign fact", tenant_id="t-rf3")
    )
    assert await reflect(repo, tenant_id="t-rf-other", agent_id="agent-a") == 0
