"""Security: fail closed on store unavailability (TDD.01.04.d400; EARS.01.03.c900).

Needs no container — the DSN points at a closed port.
"""
from __future__ import annotations

import pytest

from engramory.access.authz import ActorContext
from engramory.access.surface import AccessSurface
from engramory.core.repository import Repository, StoreUnavailable

pytestmark = pytest.mark.asyncio

_CTX = ActorContext(
    agent_id="agent-a",
    project_id="p1",
    tenant_id="t-1",
    scopes=frozenset({"agent", "project", "domain", "space"}),
)


def _down_surface() -> AccessSurface:
    return AccessSurface(Repository("postgresql://nobody:nope@127.0.0.1:1/nothing"))


async def test_failclosed_on_store_down() -> None:
    """Retryable error, no stale/unscoped data — reads AND writes."""
    surface = _down_surface()
    with pytest.raises(StoreUnavailable):
        await surface.memory_search(_CTX, query="anything")
    with pytest.raises(StoreUnavailable):
        await surface.memory_add(_CTX, content="anything", kind="note")


async def test_authz_denial_stands_even_when_audit_store_is_down() -> None:
    """A deny must never turn into data exposure because auditing failed:
    the caller still gets an error (StoreUnavailable from the audit write),
    never results."""
    surface = _down_surface()
    with pytest.raises(StoreUnavailable):
        await surface.memory_search(_CTX, query="x", tenant_id="t-other")
