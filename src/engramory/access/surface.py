"""AccessSurface — the tool layer every agent call goes through (SPEC-01).

Order of operations on every call: authorize (default deny) -> audit the
decision -> execute. Fail-closed (ADR-06/EARS.01.03.c900): StoreUnavailable
propagates as a retryable error; a denial NEVER degrades into data exposure —
if even the audit write fails, the caller gets an error, not results.

TRUST MODEL (the assumption the whole wall rests on): ActorContext is an
AUTHENTICATED principal. The MCP gateway constructs it exclusively from
verified OIDC claims (Keycloak dev / Identity Platform / Entra ID); nothing
client-supplied may reach ActorContext.tenant_id or .scopes. This module
never re-verifies identity — it authorizes what the gateway already
authenticated.

Retrieval note (interim, until the LLMPort dev adapter lands): memory_search
ranks by recency — the query text scopes the *audit trail*, not the ranking.
SPEC-03 rank fusion (vector + lexical + recency) arrives with the retrieval
service; the surface contract (MemoryHit with retrieval_id + token_count)
is already final. Reads are bound to the caller's own project (ctx.project_id);
domain-level targeting arrives with BRD-04 when ActorContext gains domain
membership.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from engramory.access.authz import ActorContext, AuthzDecision, TargetScope, authorize
from engramory.core.models import AgentProfile, Episode, KBSection
from engramory.core.repository import Repository

# PRD.01.perf.context_token_budget — the default cap on injected context.
DEFAULT_TOKEN_BUDGET = 2000
# Surface-level bounds (availability): callers cannot turn one tool call into
# an unbounded amount of store work.
MAX_K = 100
MAX_CONTENT_BYTES = 65536

# Ladder order (ADR-07): a request at level L may see L and every narrower
# level the caller holds — never a level the caller was not granted.
_LADDER = ("agent", "project", "domain", "space")


def _granted_levels(requested: str, granted: frozenset[str]) -> tuple[str, ...]:
    ceiling = _LADDER.index(requested) if requested in _LADDER else 0
    return tuple(level for level in _LADDER[: ceiling + 1] if level in granted)


class AuthzError(PermissionError):
    """The call was denied; no data was returned. The denial is audited."""


class GovernedWriteRejected(PermissionError):
    """SDD-artifact write without an approved-evidence reference (EARS.01.03.a700)."""


def _token_count(text: str) -> int:
    """Budget heuristic: ~4 chars/token. Replaced by real tokenizer counts when
    the LLMPort adapter lands; the MemoryHit contract does not change."""
    return max(1, len(text) // 4)


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """SPEC-03 result shape: pass retrieval_id to memory_feedback."""

    memory_id: str
    summary: str
    score: float  # interim: stored confidence (fusion score arrives with SPEC-03)
    provenance: Mapping[str, Any]
    retrieval_id: str
    token_count: int


class AccessSurface:
    """Authorized, scoped, audited read/write interface for all agents."""

    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def _decide(
        self, ctx: ActorContext, action: str, target: TargetScope, note: str | None = None
    ) -> AuthzDecision:
        """Authorize and audit; raise AuthzError on deny (after the audit lands).

        ``note`` appends caller-supplied context (e.g. a forget reason) to the
        audited decision reason — one audit row per decision, never a second.
        """
        decision = authorize(ctx, action, target)
        reason = decision.reason if note is None else f"{decision.reason}: {note}"
        await self._repo.record_audit(
            tenant_id=ctx.tenant_id,
            agent_id=ctx.agent_id,
            project_id=ctx.project_id,
            action=action,
            allowed=decision.allowed,
            reason=reason,
        )
        if not decision.allowed:
            raise AuthzError(f"{action}: {decision.reason}")
        return decision

    async def memory_search(
        self,
        ctx: ActorContext,
        query: str,
        *,
        scope: str = "agent",
        tenant_id: str | None = None,
        k: int = 8,
        token_budget: int | None = DEFAULT_TOKEN_BUDGET,
    ) -> list[MemoryHit]:
        """Scoped retrieval of distilled memory; every hit is retrieval-logged.

        Results contain ONLY ladder levels the caller holds, up to `scope`;
        reads are bound to the caller's own project. `token_budget=None`
        explicitly disables the PRD default cap.
        """
        target = TargetScope(tenant_id=tenant_id or ctx.tenant_id, scope=scope)
        await self._decide(ctx, "memory_search", target)
        levels = _granted_levels(scope, ctx.scopes)
        memories = await self._repo.get_memories(
            tenant_id=target.tenant_id,
            agent_id=ctx.agent_id,
            project_id=ctx.project_id,
            domain_id=None,  # domain membership lands on ActorContext with BRD-04
            k=max(1, min(k, MAX_K)),
            scopes=levels,
        )
        kept: list[tuple[str, str, float, Mapping[str, Any], int]] = []
        budget = token_budget
        for memory in memories:
            tokens = _token_count(memory.summary)
            if budget is not None:
                if tokens > budget:
                    break
                budget -= tokens
            assert memory.id is not None  # hydrated rows always carry id
            kept.append(
                (memory.id, memory.summary, memory.confidence, memory.provenance, tokens)
            )
        retrieval_ids = await self._repo.log_retrievals(
            memory_ids=[m[0] for m in kept],
            tenant_id=target.tenant_id,
            agent_id=ctx.agent_id,
            project_id=ctx.project_id,
        )
        return [
            MemoryHit(
                memory_id=memory_id,
                summary=summary,
                score=score,
                provenance=provenance,
                retrieval_id=retrieval_id,
                token_count=tokens,
            )
            for (memory_id, summary, score, provenance, tokens), retrieval_id in zip(
                kept, retrieval_ids, strict=True
            )
        ]

    async def memory_add(
        self,
        ctx: ActorContext,
        *,
        content: str,
        kind: str = "note",
        domain_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Append an episode in the caller's scope (idempotent by content hash)."""
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ValueError(f"content exceeds {MAX_CONTENT_BYTES} bytes")
        await self._decide(
            ctx, "memory_add", TargetScope(tenant_id=ctx.tenant_id, scope="agent")
        )
        episode = Episode(
            agent_id=ctx.agent_id,
            content_raw=content,
            project_id=ctx.project_id,
            domain_id=domain_id,
            tenant_id=ctx.tenant_id,
            kind=kind,
            metadata=metadata or {},
        )
        return await self._repo.add_episode(episode)

    async def memory_feedback(
        self, ctx: ActorContext, retrieval_id: str, outcome: str
    ) -> None:
        """Record a retrieval outcome (useful | not_useful | harmful) — the
        signal for the SPEC-04 confidence-update rule. Bound to the caller's
        tenant; a foreign retrieval_id raises KeyError like a missing one."""
        await self._decide(
            ctx, "memory_feedback", TargetScope(tenant_id=ctx.tenant_id, scope="agent")
        )
        await self._repo.record_feedback(retrieval_id, outcome, tenant_id=ctx.tenant_id)

    async def memory_forget(
        self, ctx: ActorContext, memory_id: str, *, reason: str
    ) -> None:
        """Retire a memory (soft: end-date + supersede status; audited with the
        caller's reason). A foreign memory_id raises KeyError like a missing
        one — existence never crosses the tenant wall."""
        await self._decide(
            ctx,
            "memory_forget",
            TargetScope(tenant_id=ctx.tenant_id, scope="agent"),
            note=reason,
        )
        await self._repo.retire_memory(memory_id, tenant_id=ctx.tenant_id)

    async def agent_profile_get(self, ctx: ActorContext) -> AgentProfile:
        """Load the caller's own L3 identity profile — never another agent's.
        A fresh store yields a default profile (no error)."""
        await self._decide(
            ctx, "agent_profile_get", TargetScope(tenant_id=ctx.tenant_id, scope="agent")
        )
        profile = await self._repo.get_agent_profile(ctx.agent_id)
        return profile if profile is not None else AgentProfile(agent_id=ctx.agent_id)

    async def knowledge_ingest(
        self,
        ctx: ActorContext,
        *,
        doc_id: str,
        citation: str,
        text: str,
        evidence_ref: str | None,
        scope: str = "project",
        version: int = 1,
    ) -> str:
        """Governed write: authorize first, then require an approved-evidence
        reference (ADR-06). Both gates audit their decision."""
        await self._decide(
            ctx, "knowledge_ingest", TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        )
        if not evidence_ref:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="knowledge_ingest",
                allowed=False,
                reason="missing_evidence_ref",
            )
            raise GovernedWriteRejected("knowledge_ingest requires evidence_ref")
        section = KBSection(
            doc_id=doc_id,
            citation=citation,
            text=text,
            project_id=ctx.project_id,
            tenant_id=ctx.tenant_id,
            scope=scope,
            version=version,
        )
        return await self._repo.upsert_kb_section(section)
