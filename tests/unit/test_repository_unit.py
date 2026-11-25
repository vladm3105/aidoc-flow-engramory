"""High-signal unit tests for Repository & PgVectorAdapter using an async fake pool."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolClosed, PoolTimeout

from engramory.adapters.dev.vector_pg import PgVectorAdapter
from engramory.core.models import (
    AgentProfile,
    Episode,
    GraphEntity,
    GraphRelation,
    KBDocument,
    KBRunbookStep,
    KBSection,
    Memory,
    ValidationError,
)
from engramory.core.repository import Repository, StoreUnavailable

_MEM_ROW: dict[str, Any] = {
    "id": "mem-1",
    "agent_id": "a1",
    "project_id": "p1",
    "domain_id": "core",
    "tenant_id": "t1",
    "scope": "agent",
    "type": "semantic",
    "content_raw": "raw memory text",
    "summary": "short memory summary",
    "embedding_model": "embed",
    "embedding_dims": 1536,
    "provenance": {
        "brain_variant": "default",
        "specializations": ["sec"],
        "brain_metadata": {"distiller_model": "m1", "rag_params": {"k": 5}},
    },
    "confidence": 0.85,
    "status": "active",
    "source_trust": "agent",
    "valid_from": None,
    "valid_to": None,
    "supersedes": None,
}

_KB_ROW: dict[str, Any] = {
    "id": "sec-1",
    "doc_id": "doc-1",
    "citation": "§1",
    "text": "section text",
    "tenant_id": "t1",
    "project_id": "p1",
    "domain_id": "core",
    "scope": "project",
    "agent_id": "a1",
    "version": 2,
    "modality": "text",
    "blob_sha256": None,
    "blob_uri": None,
    "media_metadata": '{"k":"v"}',
}

_EP_ROW: dict[str, Any] = {
    "id": "ep-1",
    "agent_id": "a1",
    "project_id": "p1",
    "domain_id": "core",
    "tenant_id": "t1",
    "kind": "plan.authored",
    "content_raw": "plan episode",
    "metadata": {
        "origin": {"repo": "org/r", "plan_id": "PLAN-1"},
        "payload": {"exec_ready_score": 85},
    },
}


def _sql_text(query: Any) -> str:
    if hasattr(query, "as_string"):
        try:
            return str(query.as_string(None))
        except Exception:
            return str(query)
    return str(query)


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 1) -> None:
        self._rows = rows
        self.rowcount = rowcount

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeConn:
    def __init__(
        self, responder: Callable[[str, Any], tuple[list[dict[str, Any]], int]] | None = None
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._responder = responder

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    @asynccontextmanager
    async def cursor(self) -> AsyncIterator[_FakeConn]:
        yield self

    async def executemany(self, query: Any, params_seq: Any = None) -> None:
        self.calls.append((_sql_text(query), params_seq))

    async def execute(self, query: Any, params: Any = None) -> _FakeCursor:
        sql = _sql_text(query)
        self.calls.append((sql, params))
        if "set_config" in sql:
            return _FakeCursor([{"set_config": ""}], rowcount=1)
        if self._responder is not None:
            rows, rc = self._responder(sql, params)
            return _FakeCursor(rows, rowcount=rc)
        return _default_response(sql, params)


def _default_response(sql: str, params: Any) -> _FakeCursor:
    if "SELECT 1" in sql and "FROM" not in sql:
        return _FakeCursor([{"?column?": 1}])
    if "INSERT INTO kb_sections" in sql and isinstance(params, dict) and "doc_ids" in params:
        rows = [
            {
                "id": f"sec-{i}",
                "tenant_id": params["tenants"][i],
                "doc_id": params["doc_ids"][i],
                "citation": params["citations"][i],
                "version": params["versions"][i],
            }
            for i in range(len(params["doc_ids"]))
        ]
        return _FakeCursor(rows, rowcount=len(rows))
    if "INSERT INTO episodes" in sql:
        return _FakeCursor([{"id": "ep-new"}])
    if "INSERT INTO memories" in sql:
        return _FakeCursor([{"id": "11111111-1111-1111-1111-111111111111"}])
    if "INSERT INTO memory_retrievals" in sql:
        return _FakeCursor([{"id": "ret-1"}])
    if "INSERT INTO graph_entities" in sql:
        return _FakeCursor([{"id": "ent-1"}], rowcount=1)
    if "INSERT INTO graph_relations" in sql or "INSERT INTO consolidation_runs" in sql:
        return _FakeCursor([{"id": "rel-1"}], rowcount=1)
    if "INSERT INTO kb_sections" in sql:
        return _FakeCursor([{"id": "sec-1"}])
    if "INSERT INTO kb_documents" in sql:
        return _FakeCursor([{"doc_id": "doc-1"}])
    if "COUNT(DISTINCT agent_id) AS n" in sql:
        return _FakeCursor([{"n": 2}])
    if "COUNT(*)::integer AS total" in sql:
        return _FakeCursor([{"total": 2, "useful": 1, "not_useful": 1, "harmful": 0}])
    if "SELECT count(*) AS n" in sql:
        return _FakeCursor([{"n": 4}])
    if "MAX(version)::integer AS max_ver" in sql:
        return _FakeCursor([{"doc_id": "doc-1", "max_ver": 4}])
    if "DISTINCT e.tenant_id, e.agent_id" in sql:
        return _FakeCursor([{"tenant_id": "t1", "agent_id": "a1"}])
    if "SELECT tenant_id, agent_id, project_id, domain_id, scope, valid_to" in sql:
        return _FakeCursor(
            [
                {
                    "tenant_id": "t1",
                    "agent_id": "a1",
                    "project_id": "p1",
                    "domain_id": "core",
                    "scope": "agent",
                    "valid_to": None,
                }
            ]
        )
    if "SELECT memory_id, agent_id, feedback, applied_delta" in sql:
        return _FakeCursor(
            [
                {
                    "memory_id": "mem-1",
                    "agent_id": "a1",
                    "feedback": None,
                    "applied_delta": 0.0,
                }
            ]
        )
    if "SELECT confidence, status, scope" in sql:
        return _FakeCursor([{"confidence": 0.5, "status": "active", "scope": "project"}])
    if "FROM memory_retrievals" in sql and "applied_delta" in sql:
        return _FakeCursor(
            [
                {
                    "id": "ret-1",
                    "memory_id": "mem-1",
                    "tenant_id": "t1",
                    "agent_id": "a1",
                    "project_id": "p1",
                    "feedback": None,
                    "applied_delta": 0.0,
                }
            ]
        )
    if "SELECT feedback" in sql and "FROM memory_retrievals" in sql:
        return _FakeCursor([{"feedback": "useful"}])
    if "FROM agent_profiles p" in sql:
        return _FakeCursor(
            [
                {
                    "tenant_id": "t1",
                    "agent_id": "a1",
                    "display_name": "Agent 1",
                    "standing_preferences": {
                        "agent_type": "coder",
                        "role": "coder",
                        "department_focus": "core",
                        "specializations": ["sec"],
                        "brain_config": {},
                    },
                    "active_skill_count": 2,
                    "mean_confidence": 0.9,
                    "brain_variants": ["default"],
                }
            ]
        )
    if "FROM agent_profiles" in sql:
        return _FakeCursor(
            [
                {
                    "tenant_id": "t1",
                    "agent_id": "a1",
                    "display_name": "Agent 1",
                    "standing_preferences": {"role": "coder"},
                }
            ]
        )
    if "embedding <=> %(vec)s::vector AS distance" in sql:
        return _FakeCursor([{"id": "mem-1", "distance": 0.12}])
    if "FROM episodes" in sql:
        return _FakeCursor([_EP_ROW])
    if "SELECT id FROM memories" in sql:
        return _FakeCursor([{"id": "11111111-1111-1111-1111-111111111111"}])
    if "FROM memories" in sql and "content_raw" in sql:
        return _FakeCursor([_MEM_ROW])
    if "FROM kb_runbook_steps" in sql:
        return _FakeCursor(
            [
                {
                    "step_uid": "doc-1#s1",
                    "doc_id": "doc-1",
                    "project_id": "p1",
                    "scope": "project",
                    "title": "Runbook",
                    "repo_path": "docs/r.md",
                    "trigger_alerts": ["ALT-1"],
                    "step_order": 1,
                    "step_id": "s1",
                    "heading_path": "H1",
                    "tier": "green",
                    "mode": "read-only",
                    "expect_exit": 0,
                    "timeout_sec": 5,
                    "command_body": "echo ok",
                    "prose_context": "",
                    "rollback_step": None,
                }
            ]
        )
    if "FROM kb_sections" in sql:
        return _FakeCursor([_KB_ROW])
    if "DELETE FROM kb_sections" in sql or "DELETE FROM graph_entities" in sql:
        return _FakeCursor([{"id": "old-1"}], rowcount=1)
    if "FROM walk" in sql or "FROM lineage" in sql or "FROM blast" in sql:
        return _FakeCursor(
            [
                {
                    "id": "ent-2",
                    "domain_id": "core",
                    "entity_type": "SPEC",
                    "name": "Spec 2",
                    "properties": {"project_id": "p1"},
                }
            ]
        )
    if "SELECT id FROM graph_entities" in sql:
        return _FakeCursor([{"id": "old-1"}])
    if "FROM graph_entities" in sql:
        return _FakeCursor(
            [
                {
                    "id": "ent-1",
                    "tenant_id": "t1",
                    "domain_id": "core",
                    "entity_type": "SPEC",
                    "name": "Spec 1",
                    "properties": {"project_id": "p1"},
                }
            ]
        )
    if "FROM graph_relations" in sql:
        return _FakeCursor(
            [
                {
                    "id": "rel-1",
                    "tenant_id": "t1",
                    "src_id": "ent-1",
                    "dst_id": "ent-2",
                    "rel_type": "DEPENDS_ON",
                    "properties": {},
                }
            ]
        )
    if "FROM audit_records" in sql:
        return _FakeCursor(
            [
                {
                    "agent_id": "a1",
                    "project_id": "p1",
                    "action": "memory_search",
                    "allowed": True,
                    "reason": "ok",
                    "ts": "2026-11-22T00:00:00Z",
                }
            ]
        )
    return _FakeCursor([{"id": "11111111-1111-1111-1111-111111111111"}], rowcount=1)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn_obj = conn

    @asynccontextmanager
    async def connection(self, timeout: float = 5.0) -> AsyncIterator[_FakeConn]:
        _ = timeout
        yield self._conn_obj


def _make_fake_repo(
    responder: Callable[[str, Any], tuple[list[dict[str, Any]], int]] | None = None,
) -> tuple[Repository, _FakeConn]:
    repo = Repository("postgresql://nobody:nope@127.0.0.1:1/nothing", min_size=0)
    conn = _FakeConn(responder)
    pool = _FakePool(conn)

    async def _fake_get_pool() -> _FakePool:
        return pool

    repo._get_pool = _fake_get_pool  # type: ignore[method-assign]  # reason: fake pool
    return repo, conn


@pytest.mark.asyncio
async def test_repository_episodes_memories_profiles_and_vector_unit() -> None:
    repo, conn = _make_fake_repo()

    assert await repo.ping() is True
    ep = Episode(
        agent_id="a1",
        project_id="p1",
        domain_id="core",
        tenant_id="t1",
        content_raw="raw episode text",
        metadata={"event_id": "evt-1"},
    )
    assert await repo.add_episode(ep) == "ep-new"
    assert len(await repo.get_unreflected_episodes(tenant_id="t1", agent_id="a1")) == 1
    assert await repo.get_active_agents_with_unreflected_episodes(tenant_id="t1") == [("t1", "a1")]

    mem = Memory(
        content_raw="raw",
        summary="sum",
        type="semantic",
        embedding_model="embed",
        provenance={"brain_variant": "default", "specializations": ["sec"]},
        agent_id="a1",
        project_id="p1",
        domain_id="core",
        tenant_id="t1",
        scope="agent",
    )
    assert (
        await repo.upsert_memory(mem, embedding=[0.1] * 1536)
        == "11111111-1111-1111-1111-111111111111"
    )
    assert await repo.get_memory("mem-1", tenant_id="t1") is not None
    assert (await repo.get_retrieval("ret-1", tenant_id="t1"))["agent_id"] == "a1"

    await repo.retire_memory(
        "mem-1",
        tenant_id="t1",
        caller_agent_id="a1",
        caller_project_id="p1",
        caller_domain_id="core",
        allowed_scopes=["agent", "project"],
    )
    await repo.record_feedback("ret-1", "harmful", tenant_id="t1", caller_agent_id="a1")
    await repo.record_feedback("ret-1", "useful", tenant_id="t1", caller_agent_id="a1")
    assert await repo.get_retrieval_feedback(tenant_id="t1", memory_id="mem-1") == ["useful"]
    fb_sum = await repo.get_retrieval_feedback_summary(tenant_id="t1", memory_ids=["mem-1"])
    assert fb_sum == {"total": 2, "useful": 1, "not_useful": 1, "harmful": 0}
    assert await repo.get_retrieval_feedback_summary(tenant_id="t1", memory_ids=[]) == {
        "total": 0,
        "useful": 0,
        "not_useful": 0,
        "harmful": 0,
    }

    prof = AgentProfile(
        agent_id="a1",
        display_name="Agent 1",
        standing_preferences={"role": "coder"},
        tenant_id="t1",
    )
    await repo.upsert_agent_profile(prof)
    got_prof = await repo.get_agent_profile("a1", tenant_id="t1")
    assert got_prof is not None and got_prof.display_name == "Agent 1"
    roster = await repo.list_agent_profiles(tenant_id="t1", include_stats=True)
    assert roster[0]["agent_id"] == "a1"

    mems = await repo.get_memories(
        tenant_id="t1",
        agent_id="a1",
        project_id="p1",
        domain_id="core",
        query="raw%",
        query_vec=[0.1] * 1536,
        scopes=("agent", "project"),
        brain_variant="default",
        specialization="sec",
        include_quarantined=True,
        min_confidence=0.2,
    )
    assert len(mems) == 1
    assert await repo.find_existing_promoted_memory(
        tenant_id="t1",
        target_scope="project",
        source_memory_id="mem-1",
        project_id="p1",
        summary="sum",
    )
    metrics = await repo.get_brain_variant_metrics(tenant_id="t1", variant="default")
    assert metrics["skill_count"] == 1
    offboarded = await repo.offboard_agent_space(
        tenant_id="t1", agent_id="a1", reason="reassignment"
    )
    assert set(offboarded) == {"retired_memories", "purged_episodes", "deleted_profiles"}

    health = await repo.get_learning_health_summary(tenant_id="t1", project_id="p1", agent_id="a1")
    assert set(health) == {"exec_ready_calibration", "repeat_findings"}
    assert await repo.log_consolidation_run(tenant_id="t1", agent_id="a1", stats={"ok": True})
    cons = await repo.consolidate_memories(tenant_id="t1", agent_id="a1", scope="agent")
    assert cons["run_id"] == "rel-1"
    imported = await repo.import_agent_brain(
        tenant_id="t1", profile=prof, memories=[(mem, [0.2] * 1536)]
    )
    assert imported == ["11111111-1111-1111-1111-111111111111"]

    vec = PgVectorAdapter("postgresql://nobody:nope@127.0.0.1:1/nothing")
    vec._repo = repo
    await vec.upsert(id="mem-1", embedding=[0.1] * 1536, metadata={"tenant_id": "t1"})
    hits = await vec.query(embedding=[0.1] * 1536, k=3, filter={"tenant_id": "t1"})
    assert hits == [("mem-1", 0.12)]
    await vec.delete(id="mem-1", tenant_id="t1")
    with pytest.raises(ValidationError):
        await vec.upsert(id="mem-1", embedding=[0.1], metadata={"tenant_id": "t1"})
    with pytest.raises(ValueError, match="unknown column"):
        await vec.query(embedding=[0.1] * 1536, filter={"tenant_id": "t1", "bad_col": "x"})
    assert any("set_config" in sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_repository_kb_graph_audits_and_runbooks_unit() -> None:
    repo, _ = _make_fake_repo()

    sec = KBSection(
        doc_id="doc-1",
        citation="§1",
        text="kb text",
        tenant_id="t1",
        project_id="p1",
        domain_id="core",
        scope="project",
        agent_id="a1",
        version=2,
    )
    assert await repo.upsert_kb_section(sec) == "sec-1"
    await repo.set_kb_section_embedding("sec-1", tenant_id="t1", embedding=[0.1] * 1536)
    id_map = await repo.upsert_kb_sections_batch([sec])
    assert id_map[("t1", "doc-1", "§1", 2)] == "sec-0"
    assert await repo.get_max_kb_section_versions("t1", ["doc-1"], project_id="p1") == {"doc-1": 4}
    assert (
        await repo.delete_kb_sections_for_doc("t1", "doc-1", project_id="p1", exclude_version=2)
        == 1
    )
    await repo.set_kb_sections_embedding_batch([("sec-1", [0.1] * 1536)], tenant_id="t1")
    assert await repo.get_kb_section("sec-1", tenant_id="t1") is not None
    assert len(await repo.list_kb_sections(tenant_id="t1", project_id="p1", doc_id="doc-1")) == 1
    assert (
        len(
            await repo.search_kb_sections(
                "kb%",
                tenant_id="t1",
                project_id="p1",
                query_vec=[0.1] * 1536,
                modality="text",
                hybrid_rrf=True,
            )
        )
        == 1
    )

    doc = KBDocument(
        doc_id="doc-1",
        repo_path="docs/r.md",
        title="Runbook",
        kind="runbook",
        tenant_id="t1",
        project_id="p1",
        watched_paths=("src/**",),
    )
    assert await repo.upsert_kb_document(doc) == "doc-1"
    step = KBRunbookStep(
        step_uid="doc-1#s1",
        doc_id="doc-1",
        step_order=1,
        step_id="s1",
        heading_path="H1",
        tier="green",
        mode="read-only",
        command_body="echo ok",
        tenant_id="t1",
    )
    assert await repo.upsert_kb_runbook_steps("t1", "doc-1", [step]) == 1
    assert len(await repo.slice_runbook(tenant_id="t1", project_id="p1", max_tier="green")) == 1
    assert await repo.delete_kb_document("t1", "doc-1", repo_path="docs/r.md", project_id="p1") == 1

    ent = GraphEntity(
        id="ent-1",
        entity_type="SPEC",
        name="Spec 1",
        tenant_id="t1",
        domain_id="core",
        properties={"project_id": "p1"},
    )
    rel = GraphRelation(src_id="ent-1", dst_id="ent-2", rel_type="DEPENDS_ON", tenant_id="t1")
    await repo.upsert_graph_entity(ent)
    assert await repo.upsert_graph_relation(rel) == "rel-1"
    assert await repo.bulk_upsert_graph_entities([ent]) == 1
    assert await repo.bulk_upsert_graph_relations([rel]) == 1
    assert await repo.get_graph_entity("ent-1", tenant_id="t1") is not None
    assert len(await repo.list_graph_entities(tenant_id="t1", entity_type="SPEC")) == 1
    assert len(await repo.list_graph_relations(tenant_id="t1", entity_id="ent-1")) == 1
    assert len(await repo.find_neighbors(id="ent-1", tenant_id="t1", project_id="p1")) == 1
    assert len(await repo.find_ancestors(id="ent-1", tenant_id="t1", project_id="p1")) == 1
    assert len(await repo.find_descendants(id="ent-1", tenant_id="t1", project_id="p1")) == 1
    assert await repo.purge_stale_graph_entities(
        tenant_id="t1", project_id="p1", active_ids=["ent-1"]
    ) == ["old-1"]
    await repo.delete_graph_entity("ent-1", tenant_id="t1")
    await repo.delete_graph_relation(
        src_id="ent-1", dst_id="ent-2", rel_type="DEPENDS_ON", tenant_id="t1"
    )

    await repo.record_audit(
        tenant_id="t1", agent_id="a1", project_id="p1", action="read", allowed=True, reason="ok"
    )
    assert len(await repo.get_audit_records(tenant_id="t1")) == 1
    assert await repo.log_retrievals(
        memory_ids=["mem-1"], tenant_id="t1", agent_id="a1", project_id="p1"
    ) == ["ret-1"]
    assert await repo.count_episodes(tenant_id="t1") == 4
    assert await repo.count_kb_sections(tenant_id="t1") == 4
    assert await repo.count_graph_entities(tenant_id="t1") == 4
    assert await repo.count_graph_relations(tenant_id="t1") == 4


class _StubAsyncPool:
    """Minimal AsyncConnectionPool test double supporting open/close/connection lifecycle."""

    def __init__(
        self,
        *,
        fail_exc: Exception | None = None,
        closed: bool = False,
    ) -> None:
        self.fail_exc = fail_exc
        self.closed = closed
        self.open_calls = 0
        self.close_calls = 0

    async def open(self, *, wait: bool = False) -> None:
        self.open_calls += 1

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True

    @asynccontextmanager
    async def connection(self, *, timeout: float = 5.0) -> AsyncIterator[_FakeConn]:
        if self.fail_exc is not None:
            raise self.fail_exc
        yield _FakeConn()


@pytest.mark.asyncio
async def test_repository_pool_configured_with_check_and_timeouts() -> None:
    captured_kwargs: list[dict[str, Any]] = []
    stub_pool = _StubAsyncPool()

    def _make_pool(**kwargs: Any) -> _StubAsyncPool:
        captured_kwargs.append(kwargs)
        return stub_pool

    repo = Repository("postgresql://127.0.0.1:1/nothing", min_size=1, max_size=4)
    with patch("engramory.core.repository.AsyncConnectionPool", side_effect=_make_pool) as mock_cls:
        mock_cls.check_connection = AsyncConnectionPool.check_connection
        pool = await repo._get_pool()

    assert pool is stub_pool
    assert stub_pool.open_calls == 1
    assert len(captured_kwargs) == 1
    opts = captured_kwargs[0]
    assert opts["check"] is AsyncConnectionPool.check_connection
    assert opts["kwargs"] == {"row_factory": dict_row, "connect_timeout": 5}
    assert opts["max_lifetime"] == 1800.0
    assert opts["max_idle"] == 300.0
    assert opts["reconnect_timeout"] == 30.0
    assert opts["reconnect_failed"] == repo._on_reconnect_failed


@pytest.mark.asyncio
async def test_repository_resets_wedged_pool_on_pool_timeout_and_recovers() -> None:
    wedged_pool = _StubAsyncPool(fail_exc=PoolTimeout("couldn't get a connection after 5.00 sec"))
    healthy_pool = _StubAsyncPool()
    pools = iter([wedged_pool, healthy_pool])

    repo = Repository("postgresql://127.0.0.1:1/nothing")
    with patch(
        "engramory.core.repository.AsyncConnectionPool",
        side_effect=lambda **kwargs: next(pools),
    ) as mock_cls:
        mock_cls.check_connection = AsyncConnectionPool.check_connection
        with pytest.raises(StoreUnavailable, match="couldn't get a connection"):
            async with repo._conn():
                pass

        assert wedged_pool.close_calls == 1
        assert await repo.ping() is True
        assert healthy_pool.open_calls == 1


@pytest.mark.asyncio
async def test_repository_recovers_after_pool_closed_and_reconnect_failed() -> None:
    first_pool = _StubAsyncPool()
    second_pool = _StubAsyncPool(fail_exc=PoolClosed("pool is closed"), closed=True)
    third_pool = _StubAsyncPool()
    pools = iter([first_pool, second_pool, third_pool])

    repo = Repository("postgresql://127.0.0.1:1/nothing")
    with patch(
        "engramory.core.repository.AsyncConnectionPool",
        side_effect=lambda **kwargs: next(pools),
    ) as mock_cls:
        mock_cls.check_connection = AsyncConnectionPool.check_connection

        # reconnect_failed callback evicts & closes the current pool
        assert await repo._get_pool() is first_pool
        await repo._on_reconnect_failed(first_pool)  # type: ignore[arg-type]  # reason: stub pool double
        assert first_pool.close_calls == 1

        # Next _get_pool() creates second_pool; PoolClosed during _conn() resets it
        with pytest.raises(StoreUnavailable, match="pool is closed"):
            async with repo._conn():
                pass
        assert second_pool.close_calls == 1

        # Next call allocates third_pool and succeeds
        assert await repo.ping() is True
        assert third_pool.open_calls == 1
