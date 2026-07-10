"""Per-call authorization — default deny (SPEC-01; ADR-06 fail-closed).

Pure and deterministic: no I/O, no clock. The surface layer writes the
AuditRecord for every decision (allow AND deny, EARS.01.03.b050).
"""
from __future__ import annotations

from dataclasses import dataclass

from engramory.core.models import SCOPES

# The closed set of authorizable actions (SPEC-01 exports). Anything else
# denies with 'unknown_action' — new tools must be added here deliberately.
KNOWN_ACTIONS = frozenset(
    {
        "memory_search",
        "memory_add",
        "memory_feedback",
        "memory_forget",
        "agent_profile_get",
        "knowledge_ingest",
    }
)


@dataclass(frozen=True, slots=True)
class ActorContext:
    """The caller's identity and grants (SPEC-01 data model)."""

    agent_id: str
    project_id: str
    tenant_id: str
    scopes: frozenset[str]  # granted visibility levels per ADR-07


@dataclass(frozen=True, slots=True)
class TargetScope:
    """What the call wants to touch."""

    tenant_id: str
    scope: str  # agent | project | domain | space


@dataclass(frozen=True, slots=True)
class AuthzDecision:
    allowed: bool
    reason: str  # always non-empty — every decision is audited


def authorize(ctx: ActorContext, action: str, target: TargetScope) -> AuthzDecision:
    """Default deny; allow only a known action, same tenant, granted scope level."""
    if action not in KNOWN_ACTIONS:
        return AuthzDecision(allowed=False, reason="unknown_action")
    if target.tenant_id != ctx.tenant_id:
        return AuthzDecision(allowed=False, reason="out_of_scope")
    if target.scope not in SCOPES:
        return AuthzDecision(allowed=False, reason="unknown_scope")
    if target.scope not in ctx.scopes:
        return AuthzDecision(allowed=False, reason="scope_not_granted")
    return AuthzDecision(allowed=True, reason="scope_granted")
