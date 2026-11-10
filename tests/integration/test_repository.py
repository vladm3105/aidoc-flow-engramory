"""Repository integration tests against a real Postgres (SPEC-02; TDD-02.04.b100).

Covers the relational half of the store: idempotent episode persistence
(BDD.01.03.c900 at the DB level — the a200 unit test covers only key
derivation), soft-supersede (BDD.01.03.a700 lifecycle), the ADR-07 scope
visibility ladder, and the SPEC-03 active-only default filter.
"""

from __future__ import annotations

import pytest

from engramory.core.models import Episode, KBSection, Memory
from engramory.core.repository import Repository, StoreUnavailable

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repo(pg_dsn: str) -> Repository:
    return Repository(pg_dsn)


def _memory(**overrides: object) -> Memory:
    kwargs: dict = dict(
        agent_id="agent-a",
        content_raw="always pin image tags",
        summary="pin image tags",
        type="procedural",
        embedding_model="nomic-embed-text",
        provenance={"episodes": ["e1"]},
    )
    kwargs.update(overrides)
    return Memory(**kwargs)


async def test_add_episode_is_idempotent(repo: Repository) -> None:
    """Same content + scope stores exactly once and returns the same id (c900)."""
    e = Episode(agent_id="agent-a", project_id="p1", content_raw="dedupe me", tenant_id="t-it")
    first = await repo.add_episode(e)
    second = await repo.add_episode(e)
    assert first == second
    assert await repo.count_episodes(tenant_id="t-it") == 1
    other_scope = Episode(
        agent_id="agent-a", project_id="p2", content_raw="dedupe me", tenant_id="t-it"
    )
    assert await repo.add_episode(other_scope) != first
    assert await repo.count_episodes(tenant_id="t-it") == 2


async def test_supersede_end_dates_predecessor(repo: Repository) -> None:
    """TDD.02.04.b100 — M1.valid_to set + status superseded, content_raw retained."""
    m1_id = await repo.upsert_memory(_memory(tenant_id="t-sup"))
    m2_id = await repo.upsert_memory(
        _memory(
            tenant_id="t-sup",
            content_raw="always pin image tags BY DIGEST",
            summary="pin by digest",
            supersedes=m1_id,
        )
    )
    m1 = await repo.get_memory(m1_id, tenant_id="t-sup")
    m2 = await repo.get_memory(m2_id, tenant_id="t-sup")
    assert m1.valid_to is not None and m1.status == "superseded"
    assert m1.content_raw == "always pin image tags"  # retained, never deleted
    assert m2.supersedes == m1_id
    live = await repo.get_memories(tenant_id="t-sup", agent_id="agent-a", k=10)
    assert [m.id for m in live] == [m2_id]


async def test_scope_visibility_ladder(repo: Repository) -> None:
    """ADR-07: own agent rows + shared space rows visible; other agents' private rows not."""
    await repo.upsert_memory(_memory(tenant_id="t-vis", agent_id="agent-a"))
    await repo.upsert_memory(_memory(tenant_id="t-vis", agent_id="agent-b", summary="b private"))
    await repo.upsert_memory(
        _memory(tenant_id="t-vis", agent_id=None, scope="space", summary="team lesson")
    )
    seen = await repo.get_memories(tenant_id="t-vis", agent_id="agent-a", k=10)
    summaries = {m.summary for m in seen}
    assert summaries == {"pin image tags", "team lesson"}


async def test_default_filter_excludes_non_active(repo: Repository) -> None:
    """SPEC-03 default filter: advisory/quarantined never in default retrieval."""
    await repo.upsert_memory(_memory(tenant_id="t-act"))
    await repo.upsert_memory(
        _memory(tenant_id="t-act", summary="dubious", status="advisory", confidence=0.2)
    )
    seen = await repo.get_memories(tenant_id="t-act", agent_id="agent-a", k=10)
    assert {m.summary for m in seen} == {"pin image tags"}


async def test_store_unavailable_wraps_connectivity() -> None:
    """EARS.01.03.c900 — an unreachable store surfaces as StoreUnavailable.

    Needs no container: nothing listens on port 1.
    """
    repo = Repository("postgresql://nobody:nope@127.0.0.1:1/nothing")
    with pytest.raises(StoreUnavailable):
        await repo.count_episodes(tenant_id="t")


async def test_get_memory_missing_raises(repo: Repository) -> None:
    with pytest.raises(KeyError):
        await repo.get_memory("00000000-0000-0000-0000-000000000000", tenant_id="t")


async def test_query_vec_similarity_respects_visibility(repo: Repository) -> None:
    """Similarity ranking (IPLAN-06) composes with the ADR-07 visibility ladder."""
    from engramory.adapters.dev.vector_pg import PgVectorAdapter

    vec = PgVectorAdapter(repo.dsn)
    mine = await repo.upsert_memory(_memory(tenant_id="t-qv", summary="mine"))
    other = await repo.upsert_memory(
        _memory(tenant_id="t-qv", agent_id="agent-b", summary="not visible")
    )
    v1536 = [0.0] * 1536
    v1536[0] = 1.0
    await vec.upsert(id=mine, embedding=v1536)
    await vec.upsert(id=other, embedding=v1536)  # identical vector, invisible scope
    hits = await repo.get_memories(tenant_id="t-qv", agent_id="agent-a", query_vec=v1536)
    assert [m.id for m in hits] == [mine]  # visibility beats similarity


async def test_kb_section_upsert(repo: Repository) -> None:
    """Same (tenant, doc, citation, version) updates in place — no duplicate rows."""
    s = KBSection(doc_id="ADR-07", citation="§scope", text="v1 text", tenant_id="t-kb")
    first = await repo.upsert_kb_section(s)
    second = await repo.upsert_kb_section(
        KBSection(doc_id="ADR-07", citation="§scope", text="v2 text", tenant_id="t-kb")
    )
    assert first == second
    assert await repo.count_kb_sections(tenant_id="t-kb") == 1


async def test_agent_profile_tenant_isolation_and_learning_health(repo: Repository) -> None:
    """PLAN-021 regression: agent_profiles isolates tenants sharing an agent_id,
    and get_learning_health_summary queries episodes.ts without UndefinedColumn.
    """
    from engramory.core.experience import ExperienceEvent, OriginProvenance
    from engramory.core.models import AgentProfile

    p_alpha = AgentProfile(
        agent_id="shared-agent",
        display_name="Alpha Agent",
        standing_preferences={"theme": "dark"},
        tenant_id="t-alpha",
    )
    p_beta = AgentProfile(
        agent_id="shared-agent",
        display_name="Beta Agent",
        standing_preferences={"theme": "light"},
        tenant_id="t-beta",
    )
    await repo.upsert_agent_profile(p_alpha)
    await repo.upsert_agent_profile(p_beta)

    loaded_alpha = await repo.get_agent_profile("shared-agent", tenant_id="t-alpha")
    loaded_beta = await repo.get_agent_profile("shared-agent", tenant_id="t-beta")
    assert loaded_alpha is not None and loaded_alpha.display_name == "Alpha Agent"
    assert loaded_beta is not None and loaded_beta.display_name == "Beta Agent"

    ev1 = ExperienceEvent(
        project="platform-engramory",
        producer="shared-agent",
        kind="plan.authored",
        occurred_at="2026-11-10T12:00:00Z",
        payload_type="plan.v1",
        payload={"exec_ready_score": 90},
        origin=OriginProvenance(repo="vladm3105/platform-engramory", plan_id="PLAN-021"),
        tenant_id="t-alpha",
    )
    ev2 = ExperienceEvent(
        project="platform-engramory",
        producer="shared-agent",
        kind="execution.completed",
        occurred_at="2026-11-10T12:30:00Z",
        payload_type="exec.v1",
        payload={"first_pass_success": True},
        origin=OriginProvenance(repo="vladm3105/platform-engramory", plan_id="PLAN-021"),
        effectiveness_score=0.95,
        tenant_id="t-alpha",
    )
    id1 = await repo.add_episode(ev1.to_episode())
    id1_dup = await repo.add_episode(ev1.to_episode())
    id2 = await repo.add_episode(ev2.to_episode())
    assert id1 == id1_dup
    assert id1 != id2

    summary = await repo.get_learning_health_summary(
        tenant_id="t-alpha",
        project_id="platform-engramory",
        limit=10,
    )
    assert summary["exec_ready_calibration"]["paired_plans"] == 1
    assert summary["exec_ready_calibration"]["mean_predicted_ready_ratio"] == pytest.approx(0.9)
    assert summary["exec_ready_calibration"]["actual_first_pass_rate"] == pytest.approx(1.0)
