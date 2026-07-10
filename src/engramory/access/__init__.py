"""Access surface: authorization + the tool layer agents call (SPEC-01).

Transport-agnostic core; the MCP gateway (engramory.mcp) binds these to MCP
tools. Every call is authorized (default deny), scoped, and audited.
"""
from __future__ import annotations

from engramory.access.authz import ActorContext, AuthzDecision, TargetScope, authorize
from engramory.access.surface import AccessSurface, AuthzError, GovernedWriteRejected, MemoryHit

__all__ = [
    "AccessSurface",
    "ActorContext",
    "AuthzDecision",
    "AuthzError",
    "GovernedWriteRejected",
    "MemoryHit",
    "TargetScope",
    "authorize",
]
