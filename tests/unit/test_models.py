"""Unit tests for the canonical data contracts (SPEC-02; TDD-02.04.a100/a200).

Model-level invariants only — no database. The repository integration tests
(tests/integration/test_repository.py) arrive with IPLAN-02 file order 6.
"""
from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, datetime

import pytest

from engramory.core.models import Episode, Memory, ValidationError, content_hash


def _memory(**overrides: object) -> Memory:
    """Valid baseline Memory; override single fields to probe invariants."""
    kwargs: dict = dict(
        agent_id="agent-a",
        content_raw="postgres is the canonical spine",
        summary="canonical spine",
        type="semantic",
        embedding_model="nomic-embed-text",
        provenance={"episodes": ["e1"], "project": "p1"},
    )
    kwargs.update(overrides)
    return Memory(**kwargs)


def test_memory_invariants() -> None:
    """TDD.02.04.a100 — Memory requires provenance + embedding_model (+ content)."""
    with pytest.raises(ValidationError):
        _memory(provenance={})
    with pytest.raises(ValidationError):
        _memory(embedding_model="")
    with pytest.raises(ValidationError):
        _memory(content_raw="")  # edge case: missing content_raw
    m = _memory()
    assert m.status == "active"  # migration-0003 default
    assert m.source_trust == "agent"
    assert m.tenant_id == "default"


def test_memory_domain_values_enforced() -> None:
    """type/status/source_trust/scope must stay within the schema CHECK domains."""
    with pytest.raises(ValidationError):
        _memory(type="wrong")
    with pytest.raises(ValidationError):
        _memory(status="bogus")
    with pytest.raises(ValidationError):
        _memory(source_trust="alien")
    with pytest.raises(ValidationError):
        _memory(scope="galaxy")
    with pytest.raises(ValidationError):
        _memory(confidence=1.5)


def test_idempotent_episode() -> None:
    """TDD.02.04.a200 — same content + scope stores once; different scope stores twice."""
    store: dict[tuple[str, str, str, str], Episode] = {}
    for _ in range(3):
        e = Episode(agent_id="a1", project_id="p1", content_raw="same content")
        store[e.idempotency_key()] = e
    assert len(store) == 1
    other_scope = Episode(agent_id="a1", project_id="p2", content_raw="same content")
    store[other_scope.idempotency_key()] = other_scope
    assert len(store) == 2


def test_episode_hash_matches_migration_backfill() -> None:
    """content_hash must equal migration 0003's md5(content_raw) backfill expression."""
    assert content_hash("x") == hashlib.md5(b"x", usedforsecurity=False).hexdigest()
    e = Episode(agent_id="a1", content_raw="x")
    assert e.content_hash == content_hash("x")


def test_episode_hash_cannot_go_stale() -> None:
    """content_hash is derived (init=False): replace() re-derives, no stale hash."""
    e = dataclasses.replace(Episode(agent_id="a1", content_raw="old"), content_raw="new")
    assert e.content_hash == content_hash("new")
    with pytest.raises(TypeError):
        Episode(agent_id="a1", content_raw="x", content_hash="deadbeef")  # type: ignore[call-arg]


def test_episode_requires_agent_and_content() -> None:
    with pytest.raises(ValidationError):
        Episode(agent_id="", content_raw="x")
    with pytest.raises(ValidationError):
        Episode(agent_id="a1", content_raw="")
    with pytest.raises(ValidationError):
        Episode(agent_id="a1", content_raw="x", kind="")


def test_mapping_fields_are_isolated_and_readonly() -> None:
    """Validated invariants cannot be revoked by mutating the caller's dict."""
    src: dict = {"episodes": ["e1"]}
    m = _memory(provenance=src)
    src.clear()
    assert m.provenance  # still non-empty despite caller mutation
    with pytest.raises(TypeError):
        m.provenance["x"] = 1  # type: ignore[index]


def test_memory_persistence_fields() -> None:
    """id/valid_from are None pre-persist; valid_to is a datetime; dims positive."""
    m = _memory()
    assert m.id is None and m.valid_from is None and m.valid_to is None
    ts = datetime(2026, 7, 9, tzinfo=UTC)
    ended = _memory(valid_to=ts, valid_from=ts, id="m-1", embedding_dims=768)
    assert ended.valid_to == ts
    with pytest.raises(ValidationError):
        _memory(embedding_dims=0)


def test_memory_agent_id_conditional_on_scope() -> None:
    """agent_id nullable in schema, but required for agent-scoped memories."""
    with pytest.raises(ValidationError):
        _memory(agent_id=None)  # scope defaults to 'agent'
    shared = _memory(agent_id=None, scope="space")
    assert shared.agent_id is None
