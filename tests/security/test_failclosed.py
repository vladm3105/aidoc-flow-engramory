"""Security: fail closed on store unavailability (TDD.01.04.d400; EARS.01.03.c900).

Needs no container — the DSN points at a closed port with short pool open_timeout.
Covers ALL 30 public async methods on AccessSurface plus a reflection invariant so
any newly added AccessSurface method must also be tested for fail-closed behavior.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.core.models import KBDocument, KBSection
from engramory.core.repository import Repository, StoreUnavailable

_CTX = ActorContext(
    agent_id="agent-a",
    project_id="p1",
    tenant_id="t-1",
    scopes=frozenset({"agent", "project", "domain", "space"}),
)


def _down_surface() -> tuple[Repository, AccessSurface]:
    repo = Repository(
        "postgresql://nobody:nope@127.0.0.1:1/nothing",
        min_size=0,
        open_timeout=0.1,
    )
    return repo, AccessSurface(repo)


_SURFACE_CALLERS: dict[str, Callable[[AccessSurface, Path], Awaitable[object]]] = {
    "audit_denial": lambda s, _p: s.audit_denial(_CTX, "memory_search", "deny-reason"),
    "memory_search": lambda s, _p: s.memory_search(_CTX, query="anything"),
    "graph_neighbors": lambda s, _p: s.graph_neighbors(_CTX, id="ADR-01"),
    "context_slice": lambda s, _p: s.context_slice(_CTX, task_id="IPLAN-01"),
    "blast_radius": lambda s, _p: s.blast_radius(_CTX, doc_id="ADR-01"),
    "memory_add": lambda s, _p: s.memory_add(_CTX, content="anything", kind="note"),
    "memory_feedback": lambda s, _p: s.memory_feedback(_CTX, "ret-1", "useful"),
    "memory_forget": lambda s, _p: s.memory_forget(_CTX, "mem-1", reason="obsolete"),
    "agent_profile_get": lambda s, _p: s.agent_profile_get(_CTX),
    "agent_profile_upsert": lambda s, _p: s.agent_profile_upsert(_CTX, display_name="Agent A"),
    "agent_roster_list": lambda s, _p: s.agent_roster_list(_CTX),
    "memory_promote": lambda s, _p: s.memory_promote(
        _CTX, memory_id="mem-1", target_scope="project", evidence_ref="CHG-013"
    ),
    "agent_brain_export": lambda s, _p: s.agent_brain_export(_CTX),
    "agent_brain_import": lambda s, _p: s.agent_brain_import(
        _CTX, passport={"schema_version": "engramory.brain_passport.v1"}, evidence_ref="CHG-013"
    ),
    "agent_brain_compare": lambda s, _p: s.agent_brain_compare(_CTX, variant_a="a", variant_b="b"),
    "agent_space_offboard": lambda s, _p: s.agent_space_offboard(_CTX, evidence_ref="CHG-013"),
    "memory_ingest_event": lambda s, _p: s.memory_ingest_event(
        _CTX,
        {
            "project": "p1",
            "producer": "agent-a",
            "kind": "execution.completed",
            "occurred_at": "2026-11-22T00:00:00Z",
            "payload_type": "task.result",
            "payload": {"status": "completed"},
        },
    ),
    "memory_learning_health": lambda s, _p: s.memory_learning_health(_CTX),
    "knowledge_ingest": lambda s, _p: s.knowledge_ingest(
        _CTX, doc_id="d1", citation="s1", text="hello", evidence_ref="CHG-013"
    ),
    "knowledge_ingest_batch": lambda s, _p: s.knowledge_ingest_batch(
        _CTX,
        sections=[
            KBSection(doc_id="d1", citation="s1", text="hello", tenant_id="t-1", project_id="p1")
        ],
        evidence_ref="CHG-013",
    ),
    "knowledge_search": lambda s, _p: s.knowledge_search(_CTX, query="hello"),
    "blob_put": lambda s, _p: s.blob_put(_CTX, key="k1", data=b"hello"),
    "blob_head": lambda s, _p: s.blob_head(_CTX, sha256="a" * 64),
    "blob_get": lambda s, _p: s.blob_get(_CTX, sha256="a" * 64),
    "knowledge_ingest_files": lambda s, tmp: s.knowledge_ingest_files(
        _CTX, paths=[tmp / "a.md"], evidence_ref="CHG-013", repo_root=tmp
    ),
    "knowledge_sync_git": lambda s, tmp: s.knowledge_sync_git(_CTX, repo_path=tmp),
    "sdd_ingest": lambda s, tmp: s.sdd_ingest(_CTX, sdd_root=tmp, evidence_ref="CHG-013"),
    "knowledge_ingest_media": lambda s, tmp: s.knowledge_ingest_media(
        _CTX, file_path=tmp / "img.png", evidence_ref="CHG-013", allowed_root=tmp
    ),
    "docs_ingest_runbook": lambda s, _p: s.docs_ingest_runbook(
        _CTX,
        doc=KBDocument(
            doc_id="DOC-1",
            repo_path="docs/a.md",
            title="Runbook",
            kind="runbook",
            domain_id="core",
            namespace="ns:core",
            watched_paths=("src/**",),
        ),
        steps=(),
        evidence_ref="CHG-013",
    ),
    "docs_slice_runbook": lambda s, _p: s.docs_slice_runbook(_CTX, alert_id="ALT-1"),
}


def test_failclosed_covers_every_public_async_surface_method() -> None:
    """Reflection guard: every public async AccessSurface method must be in _SURFACE_CALLERS."""
    actual = {
        name
        for name, _fn in inspect.getmembers(AccessSurface, predicate=inspect.iscoroutinefunction)
        if not name.startswith("_")
    }
    assert actual == set(_SURFACE_CALLERS), (
        f"Uncovered AccessSurface methods: {sorted(actual - set(_SURFACE_CALLERS))}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", sorted(_SURFACE_CALLERS))
async def test_failclosed_on_store_down(method_name: str, tmp_path: Path) -> None:
    """Every AccessSurface operation fails closed with StoreUnavailable when Postgres is down."""
    repo, surface = _down_surface()
    try:
        with pytest.raises(StoreUnavailable):
            await _SURFACE_CALLERS[method_name](surface, tmp_path)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_authz_denial_stands_even_when_audit_store_is_down() -> None:
    """A deny must never turn into data exposure because auditing failed:
    the caller still gets an error (StoreUnavailable from the audit write),
    never results."""
    repo, surface = _down_surface()
    try:
        with pytest.raises(StoreUnavailable):
            await surface.memory_search(_CTX, query="x", tenant_id="t-other")
    finally:
        await repo.close()
