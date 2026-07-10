"""Unit tests for authorize() — default deny (SPEC-01; TDD-01.04.a100)."""
from __future__ import annotations

from engramory.access.authz import ActorContext, TargetScope, authorize


def _ctx(**overrides: object) -> ActorContext:
    kwargs: dict = dict(
        agent_id="agent-a",
        project_id="p1",
        tenant_id="t-1",
        scopes=frozenset({"agent", "project"}),
    )
    kwargs.update(overrides)
    return ActorContext(**kwargs)


def test_default_deny_out_of_scope() -> None:
    """TDD.01.04.a100 — cross-tenant target denies with reason 'out_of_scope'."""
    decision = authorize(_ctx(), "memory_search", TargetScope(tenant_id="t-2", scope="agent"))
    assert decision.allowed is False
    assert decision.reason == "out_of_scope"


def test_default_deny_empty_scopes() -> None:
    """Edge case (TDD-01.04.a100): no granted scopes -> deny."""
    decision = authorize(
        _ctx(scopes=frozenset()), "memory_search", TargetScope(tenant_id="t-1", scope="agent")
    )
    assert decision.allowed is False


def test_default_deny_unknown_action() -> None:
    decision = authorize(_ctx(), "drop_all_tables", TargetScope(tenant_id="t-1", scope="agent"))
    assert decision.allowed is False
    assert decision.reason == "unknown_action"


def test_default_deny_ungranted_scope_level() -> None:
    decision = authorize(_ctx(), "memory_search", TargetScope(tenant_id="t-1", scope="space"))
    assert decision.allowed is False
    assert decision.reason == "scope_not_granted"


def test_allow_within_tenant_and_granted_scope() -> None:
    decision = authorize(_ctx(), "memory_search", TargetScope(tenant_id="t-1", scope="agent"))
    assert decision.allowed is True
    assert decision.reason  # every decision carries an auditable reason


def test_every_decision_has_a_reason_for_audit() -> None:
    """EARS.01.03.b050 — allow AND deny decisions must be auditable."""
    for action in ("memory_search", "bogus"):
        d = authorize(_ctx(), action, TargetScope(tenant_id="t-1", scope="agent"))
        assert isinstance(d.reason, str) and d.reason
