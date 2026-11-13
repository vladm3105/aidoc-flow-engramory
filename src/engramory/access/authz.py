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
        "memory_ingest_event",
        "memory_learning_health",
        "agent_profile_get",
        "agent_brain_export",
        "agent_brain_import",
        "knowledge_ingest",
        "knowledge_ingest_media",
        "knowledge_search",
        "blob_put",
        "blob_get",
        "blob_head",
        "graph_neighbors",
        "context_slice",
        "blast_radius",
        "docs_ingest_runbook",
        "docs_slice_runbook",
        "docs_validate",
        "docs_check_drift",
        "docs_export_llms",
        "docs_smoke_run",
        "docs_tools_inventory",
        "docs_build_portal",
        "sdd_ingest",
        "sdd_validate_graph",
        "sdd_check_invariants",
        "sdd_lint_policy",
    }
)


@dataclass(frozen=True, slots=True)
class ActorContext:
    """The caller's identity and grants (SPEC-01 data model)."""

    agent_id: str
    project_id: str
    tenant_id: str
    scopes: frozenset[str]  # granted visibility levels per ADR-07
    domain_id: str | None = None  # optional department/domain focus for virtual employees


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
