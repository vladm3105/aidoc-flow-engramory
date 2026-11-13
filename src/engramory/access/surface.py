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

import dataclasses
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engramory.access.authz import ActorContext, AuthzDecision, TargetScope, authorize
from engramory.access.poisoning_guard import (
    MemoryPoisoningError,
    inspect_memory_write,
    sanitize_portable_text,
    sanitize_portable_value,
)
from engramory.core.models import (
    AgentProfile,
    Episode,
    KBDocument,
    KBRunbookStep,
    KBSection,
    ValidationError,
)
from engramory.core.repository import Repository
from engramory.ports import GraphPort, LLMPort, StoragePort

logger = logging.getLogger(__name__)

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


_PASSPORT_CANONICAL_KEYS = (
    "schema_version",
    "agent_id",
    "agent_type",
    "display_name",
    "domain_id",
    "specializations",
    "brain_metadata",
    "standing_preferences",
    "skills",
)


def _compute_passport_sha256(payload: Mapping[str, Any]) -> str:
    """Compute canonical SHA-256 digest over portable Brain Passport fields."""
    canonical = {k: payload.get(k) for k in _PASSPORT_CANONICAL_KEYS if k in payload}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """SPEC-03 result shape: pass retrieval_id to memory_feedback."""

    memory_id: str
    summary: str
    score: float  # interim: stored confidence (fusion score arrives with SPEC-03)
    provenance: Mapping[str, Any]
    retrieval_id: str
    token_count: int


LAYER_ORDER: dict[str, int] = {
    "BRD": 1,
    "PRD": 2,
    "SYS": 3,
    "EARS": 3,
    "SRS": 4,
    "ADR": 5,
    "ARCH": 6,
    "DSG": 7,
    "SPEC": 8,
    "BDD": 8,
    "TDD": 9,
    "IPLAN": 10,
    "CHG": 11,
    "EVAL": 12,
}


def _extract_layer_prefix(node: Mapping[str, Any]) -> str:
    nid = str(node.get("id", "")).split(":", 1)[-1]
    if "-" in nid:
        return nid.split("-")[0].upper()
    labels = node.get("labels", [])
    for lbl in labels:
        lbl_upper = str(lbl).upper()
        if lbl_upper in LAYER_ORDER:
            return lbl_upper
    return "UNKNOWN"


class AccessSurface:
    """Authorized, scoped, audited read/write interface for all agents."""

    def __init__(
        self,
        repository: Repository,
        *,
        graph: GraphPort | None = None,
        llm: LLMPort | None = None,
        storage: StoragePort | None = None,
    ) -> None:
        self._repo = repository
        self._graph = graph
        self._llm = llm
        self._storage = storage

    async def _expand_neighbors_with_fallback(
        self,
        *,
        id: str,
        tenant_id: str,
        rel: str | None = None,
        depth: int = 1,
        project_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Query Neo4j primary graph adapter and fall back to PostgreSQL recursive CTE."""
        hits: Sequence[Mapping[str, Any]] = []
        if self._graph is not None:
            try:
                hits = await self._graph.neighbors(
                    id=id,
                    rel=rel,
                    depth=depth,
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
            except Exception as exc:
                logger.debug("Graph adapter neighbors query failed for %s: %s", id, exc)
        if not hits and hasattr(self._repo, "find_neighbors"):
            try:
                hits = await self._repo.find_neighbors(
                    id=id,
                    tenant_id=tenant_id,
                    rel=rel,
                    depth=depth,
                    project_id=project_id,
                )
            except Exception as exc:
                logger.debug("Repository find_neighbors fallback failed for %s: %s", id, exc)
        return hits

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

    async def _enforce_domain_boundary(
        self, ctx: ActorContext, action: str, requested_domain_id: str | None
    ) -> str | None:
        """Prevent a Virtual Employee from spoofing an unauthorized domain_id
        unless granted tenant-wide 'space' scope (or 'domain' scope when unbound)."""
        if requested_domain_id is not None and requested_domain_id != ctx.domain_id:
            if ctx.domain_id is not None and "space" not in ctx.scopes:
                await self._repo.record_audit(
                    tenant_id=ctx.tenant_id,
                    agent_id=ctx.agent_id,
                    project_id=ctx.project_id,
                    action=action,
                    allowed=False,
                    reason=(
                        f"out_of_scope: cross-department domain_id override ({requested_domain_id})"
                    ),
                )
                raise AuthzError(
                    "out_of_scope: cross-department domain_id override requires space scope"
                )
            if ctx.domain_id is None and "domain" not in ctx.scopes and "space" not in ctx.scopes:
                await self._repo.record_audit(
                    tenant_id=ctx.tenant_id,
                    agent_id=ctx.agent_id,
                    project_id=ctx.project_id,
                    action=action,
                    allowed=False,
                    reason=(
                        f"out_of_scope: domain_id override without domain/space scope "
                        f"({requested_domain_id})"
                    ),
                )
                raise AuthzError("out_of_scope: domain_id override requires domain or space scope")
        return requested_domain_id if requested_domain_id is not None else ctx.domain_id

    async def memory_search(
        self,
        ctx: ActorContext,
        query: str,
        *,
        scope: str = "agent",
        tenant_id: str | None = None,
        domain_id: str | None = None,
        brain_variant: str | None = None,
        specialization: str | None = None,
        k: int = 8,
        token_budget: int | None = DEFAULT_TOKEN_BUDGET,
        expand_graph: bool = False,
        include_knowledge: bool = False,
    ) -> list[MemoryHit]:
        """Scoped retrieval of distilled memory; every hit is retrieval-logged.

        Results contain ONLY ladder levels the caller holds, up to `scope`;
        reads are bound to the caller's own project. `token_budget=None`
        explicitly disables the PRD default cap. When `include_knowledge=True`,
        matching L0 canonical knowledge sections are fused into the token-budgeted
        response alongside L2 distilled memories.
        """
        target = TargetScope(tenant_id=tenant_id or ctx.tenant_id, scope=scope)
        await self._decide(ctx, "memory_search", target)
        eff_domain_id = await self._enforce_domain_boundary(ctx, "memory_search", domain_id)
        levels = _granted_levels(scope, ctx.scopes)
        query_vec: Sequence[float] | None = None
        if self._llm is not None and query and query.strip():
            try:
                vecs = await self._llm.embed(texts=[query])
                if vecs and len(vecs[0]) == 1536:
                    query_vec = vecs[0]
            except Exception:  # noqa: BLE001  # reason: embedding failure must fall back to lexical/recency retrieval
                query_vec = None
        max_hits = max(1, min(k, MAX_K))
        memories = await self._repo.get_memories(
            tenant_id=target.tenant_id,
            agent_id=ctx.agent_id,
            project_id=ctx.project_id,
            domain_id=eff_domain_id,
            k=max_hits,
            query=query,
            query_vec=query_vec,
            scopes=levels,
            brain_variant=brain_variant,
            specialization=specialization,
        )
        kept: list[tuple[str, str, float, Mapping[str, Any], int]] = []
        budget = token_budget
        for memory in memories:
            tokens = _token_count(memory.summary)
            if budget is not None:
                if tokens > budget:
                    break
                budget -= tokens
            if memory.id is None:
                raise RuntimeError("hydrated memory missing id")
            prov = dict(memory.provenance)
            if expand_graph:
                neighbors = await self._expand_neighbors_with_fallback(
                    id=memory.id,
                    tenant_id=target.tenant_id,
                    depth=1,
                    project_id=ctx.project_id,
                )
                if neighbors:
                    prov["related_entities"] = [n.get("id") for n in neighbors]
            kept.append((memory.id, memory.summary, memory.confidence, prov, tokens))
        retrieval_ids = await self._repo.log_retrievals(
            memory_ids=[m[0] for m in kept],
            tenant_id=target.tenant_id,
            agent_id=ctx.agent_id,
            project_id=ctx.project_id,
        )
        hits = [
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
        if include_knowledge and len(hits) < max_hits and (budget is None or budget > 0):
            kb_scope = "project" if scope == "agent" and "project" in ctx.scopes else scope
            if kb_scope in ("project", "domain", "space") and kb_scope in ctx.scopes:
                kb_sections = await self.knowledge_search(
                    ctx,
                    query,
                    query_vec=query_vec,
                    scope=kb_scope,
                    expand_graph=expand_graph,
                    limit=max(1, max_hits - len(hits)),
                )
                for sec in kb_sections:
                    tokens = _token_count(sec.text)
                    if budget is not None:
                        if tokens > budget:
                            continue
                        budget -= tokens
                    kb_prov: dict[str, Any] = {
                        "source": "knowledge_base",
                        "doc_id": sec.doc_id,
                        "citation": sec.citation,
                        "modality": sec.modality,
                        "blob_uri": sec.blob_uri,
                        "media_metadata": dict(sec.media_metadata),
                    }
                    hits.append(
                        MemoryHit(
                            memory_id=f"kb:{sec.id}",
                            summary=sec.text,
                            score=1.0,
                            provenance=kb_prov,
                            retrieval_id=f"kb-ret:{sec.id}",
                            token_count=tokens,
                        )
                    )
                    if len(hits) >= max_hits:
                        break
        return hits

    async def graph_neighbors(
        self,
        ctx: ActorContext,
        *,
        id: str,
        rel: str | None = None,
        depth: int = 1,
        scope: str = "project",
    ) -> Sequence[Mapping[str, Any]]:
        """Retrieve connected graph neighbors for an entity (ADR-03 / GraphPort + PG fallback)."""
        target = TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        await self._decide(ctx, "graph_neighbors", target)
        eff_project_id = ctx.project_id if scope in ("agent", "project") else None
        return await self._expand_neighbors_with_fallback(
            id=id,
            tenant_id=ctx.tenant_id,
            rel=rel,
            depth=depth,
            project_id=eff_project_id,
        )

    async def context_slice(
        self,
        ctx: ActorContext,
        *,
        task_id: str,
        depth: int = 6,
        scope: str = "project",
        layers: Sequence[str] | None = None,
        max_tokens: int = 2000,
    ) -> Sequence[Mapping[str, Any]]:
        """Retrieve upstream contract closure (ancestors) for an SDD task node."""
        target = TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        await self._decide(ctx, "context_slice", target)
        eff_project_id = ctx.project_id if scope in ("agent", "project") else None
        hits: Sequence[Mapping[str, Any]] = []
        if self._graph is not None:
            try:
                hits = await self._graph.ancestors(
                    id=task_id,
                    depth=depth,
                    tenant_id=ctx.tenant_id,
                    project_id=eff_project_id,
                )
            except Exception:
                logger.debug("Graph adapter ancestors query failed, falling back to PostgreSQL CTE")
        if not hits:
            hits = await self._repo.find_ancestors(
                id=task_id,
                tenant_id=ctx.tenant_id,
                depth=depth,
                project_id=eff_project_id,
            )

        # 1. Filter by requested layers if specified
        if layers:
            allowed_layers = {layer_name.upper() for layer_name in layers}
            hits = [h for h in hits if _extract_layer_prefix(h) in allowed_layers]

        # 2. Sort topologically from root requirements down to immediate specifications
        sorted_hits = sorted(
            hits,
            key=lambda n: (
                LAYER_ORDER.get(_extract_layer_prefix(n), 99),
                str(n.get("id", "")),
            ),
        )

        # 3. Budget tokens if max_tokens is provided
        budget = max_tokens
        kept: list[Mapping[str, Any]] = []
        for h in sorted_hits:
            props = h.get("properties", {})
            text_len = len(str(h.get("id", ""))) + len(str(props))
            approx_tokens = max(1, text_len // 4)
            if approx_tokens > budget and kept:
                break
            budget -= approx_tokens
            kept.append(h)

        return kept

    async def blast_radius(
        self,
        ctx: ActorContext,
        *,
        doc_id: str,
        depth: int = 6,
        scope: str = "project",
    ) -> Sequence[Mapping[str, Any]]:
        """Retrieve downstream dependent nodes (descendants) affected by a change."""
        target = TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        await self._decide(ctx, "blast_radius", target)
        eff_project_id = ctx.project_id if scope in ("agent", "project") else None
        if self._graph is not None:
            try:
                hits = await self._graph.descendants(
                    id=doc_id,
                    depth=depth,
                    tenant_id=ctx.tenant_id,
                    project_id=eff_project_id,
                )
                if hits:
                    return hits
            except Exception:
                logger.debug(
                    "Graph adapter descendants query failed, falling back to PostgreSQL CTE"
                )
        return await self._repo.find_descendants(
            id=doc_id,
            tenant_id=ctx.tenant_id,
            depth=depth,
            project_id=eff_project_id,
        )

    async def memory_add(
        self,
        ctx: ActorContext,
        *,
        content: str,
        kind: str = "note",
        scope: str = "agent",
        domain_id: str | None = None,
        specializations: Sequence[str] | None = None,
        brain_variant: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Append an episode in the caller's scope (idempotent by content hash)."""
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ValueError(f"content exceeds {MAX_CONTENT_BYTES} bytes")
        await self._decide(ctx, "memory_add", TargetScope(tenant_id=ctx.tenant_id, scope=scope))
        eff_domain_id = await self._enforce_domain_boundary(ctx, "memory_add", domain_id)
        merged_meta: dict[str, Any] = dict(metadata) if metadata else {}
        if scope != "agent":
            merged_meta["scope"] = scope
        if specializations is not None:
            specs_clean = list(
                dict.fromkeys(str(s).strip() for s in specializations if str(s).strip())
            )
            if specs_clean:
                merged_meta["specializations"] = specs_clean
        if brain_variant is not None and brain_variant.strip():
            merged_meta["brain_variant"] = brain_variant.strip()
        try:
            vetted_content = inspect_memory_write(
                content,
                tenant_id=ctx.tenant_id,
                writer_agent_id=ctx.agent_id,
                target="episodes",
            )
            if merged_meta:
                inspect_memory_write(
                    json.dumps(merged_meta, sort_keys=True),
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target="episodes:metadata",
                )
        except MemoryPoisoningError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_add",
                allowed=False,
                reason=f"poisoning_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc
        episode = Episode(
            agent_id=ctx.agent_id,
            content_raw=vetted_content,
            project_id=ctx.project_id,
            domain_id=eff_domain_id,
            tenant_id=ctx.tenant_id,
            kind=kind,
            metadata=merged_meta,
        )
        return await self._repo.add_episode(episode)

    async def memory_feedback(self, ctx: ActorContext, retrieval_id: str, outcome: str) -> None:
        """Record a retrieval outcome (useful | not_useful | harmful) — the
        signal for the SPEC-04 confidence-update rule. Bound to the caller's
        tenant; a foreign retrieval_id raises KeyError like a missing one."""
        await self._decide(
            ctx, "memory_feedback", TargetScope(tenant_id=ctx.tenant_id, scope="agent")
        )
        await self._repo.record_feedback(retrieval_id, outcome, tenant_id=ctx.tenant_id)

    async def memory_forget(self, ctx: ActorContext, memory_id: str, *, reason: str) -> None:
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
        profile = await self._repo.get_agent_profile(ctx.agent_id, tenant_id=ctx.tenant_id)
        return (
            profile
            if profile is not None
            else AgentProfile(agent_id=ctx.agent_id, tenant_id=ctx.tenant_id)
        )

    async def agent_profile_upsert(
        self,
        ctx: ActorContext,
        *,
        display_name: str | None = None,
        agent_type: str | None = None,
        role: str | None = None,
        domain_id: str | None = None,
        specializations: Sequence[str] | None = None,
        brain_config: Mapping[str, Any] | None = None,
        standing_preferences: Mapping[str, Any] | None = None,
    ) -> AgentProfile:
        """Governed L3 Virtual Employee profile upsert storing agent_type, role,
        department_focus (domain_id), specializations, and brain_config."""
        await self._decide(
            ctx, "agent_profile_upsert", TargetScope(tenant_id=ctx.tenant_id, scope="agent")
        )
        eff_domain = await self._enforce_domain_boundary(ctx, "agent_profile_upsert", domain_id)
        existing = await self._repo.get_agent_profile(ctx.agent_id, tenant_id=ctx.tenant_id)
        merged_prefs: dict[str, Any] = (
            dict(existing.standing_preferences) if existing is not None else {}
        )
        if standing_preferences:
            merged_prefs.update(dict(standing_preferences))
        eff_agent_type = (
            agent_type or role or merged_prefs.get("agent_type") or merged_prefs.get("role")
        )
        eff_role = role or agent_type or merged_prefs.get("role") or merged_prefs.get("agent_type")
        if eff_agent_type:
            merged_prefs["agent_type"] = str(eff_agent_type).strip()
        if eff_role:
            merged_prefs["role"] = str(eff_role).strip()
        if eff_domain:
            merged_prefs["department_focus"] = str(eff_domain).strip()
        if specializations is not None:
            merged_prefs["specializations"] = list(
                dict.fromkeys(str(s).strip() for s in specializations if str(s).strip())
            )
        if brain_config is not None:
            prev_cfg = (
                dict(merged_prefs["brain_config"])
                if isinstance(merged_prefs.get("brain_config"), Mapping)
                else {}
            )
            prev_cfg.update({k: v for k, v in brain_config.items() if v is not None})
            merged_prefs["brain_config"] = prev_cfg

        eff_display = (
            display_name
            if display_name is not None
            else (existing.display_name if existing is not None else ctx.agent_id)
        )
        try:
            vetted_display = inspect_memory_write(
                str(eff_display or ctx.agent_id),
                tenant_id=ctx.tenant_id,
                writer_agent_id=ctx.agent_id,
                target="agent_profiles:display_name",
            )
            if merged_prefs:
                inspect_memory_write(
                    json.dumps(merged_prefs, sort_keys=True),
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target="agent_profiles:standing_preferences",
                )
        except MemoryPoisoningError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="agent_profile_upsert",
                allowed=False,
                reason=f"poisoning_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc

        updated = AgentProfile(
            agent_id=ctx.agent_id,
            display_name=vetted_display,
            standing_preferences=merged_prefs,
            tenant_id=ctx.tenant_id,
        )
        await self._repo.upsert_agent_profile(updated)
        return updated

    async def agent_roster_list(
        self,
        ctx: ActorContext,
        *,
        domain_id: str | None = None,
        agent_type: str | None = None,
        role: str | None = None,
        specialization: str | None = None,
        include_stats: bool = True,
    ) -> list[dict[str, Any]]:
        """List and rank Virtual Employee instances in the caller's tenant."""
        req_scope = "project" if "project" in ctx.scopes else "agent"
        await self._decide(
            ctx, "agent_roster_list", TargetScope(tenant_id=ctx.tenant_id, scope=req_scope)
        )
        return await self._repo.list_agent_profiles(
            tenant_id=ctx.tenant_id,
            domain_id=domain_id,
            agent_type=agent_type,
            role=role,
            specialization=specialization,
            include_stats=include_stats,
        )

    async def memory_promote(
        self,
        ctx: ActorContext,
        *,
        memory_id: str,
        target_scope: str,
        evidence_ref: str | None,
        target_domain_id: str | None = None,
        target_project_id: str | None = None,
        specializations: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Governed promotion of a validated L2 memory up the ADR-07 scope ladder
        (agent -> project -> domain -> space) with strict source visibility and
        cross-instance deduplication."""
        from engramory.core.models import Memory

        if target_scope not in {"project", "domain", "space"}:
            raise ValidationError(
                f"target_scope must be one of ('project', 'domain', 'space'): {target_scope!r}"
            )
        await self._decide(
            ctx, "memory_promote", TargetScope(tenant_id=ctx.tenant_id, scope=target_scope)
        )
        if not evidence_ref:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_promote",
                allowed=False,
                reason="missing_evidence_ref",
            )
            raise GovernedWriteRejected("memory_promote requires evidence_ref")

        source = await self._repo.get_memory(memory_id, tenant_id=ctx.tenant_id)
        source_visible = (
            source.status == "active"
            and source.valid_to is None
            and source.scope in ctx.scopes
            and (
                (source.scope == "agent" and source.agent_id == ctx.agent_id)
                or (
                    source.scope == "project"
                    and source.project_id is not None
                    and source.project_id == ctx.project_id
                )
                or (
                    source.scope == "domain"
                    and source.domain_id is not None
                    and source.domain_id == ctx.domain_id
                )
                or source.scope == "space"
            )
        )
        if not source_visible:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_promote",
                allowed=False,
                reason="out_of_scope",
            )
            raise AuthzError("memory_promote: out_of_scope")

        if _LADDER.index(target_scope) < _LADDER.index(source.scope):
            raise ValidationError(
                f"Cannot demote memory from scope {source.scope!r} to {target_scope!r}"
            )

        if "space" not in ctx.scopes:
            if target_project_id is not None and target_project_id != ctx.project_id:
                await self._repo.record_audit(
                    tenant_id=ctx.tenant_id,
                    agent_id=ctx.agent_id,
                    project_id=ctx.project_id,
                    action="memory_promote",
                    allowed=False,
                    reason="out_of_scope",
                )
                raise AuthzError("memory_promote: out_of_scope")
            if (
                ctx.domain_id is not None
                and target_domain_id is not None
                and target_domain_id != ctx.domain_id
            ):
                await self._repo.record_audit(
                    tenant_id=ctx.tenant_id,
                    agent_id=ctx.agent_id,
                    project_id=ctx.project_id,
                    action="memory_promote",
                    allowed=False,
                    reason="out_of_scope",
                )
                raise AuthzError("memory_promote: out_of_scope")

        eff_project_id = (
            (target_project_id or ctx.project_id or source.project_id)
            if target_scope == "project"
            else None
        )
        eff_domain_id = (
            (target_domain_id or ctx.domain_id or source.domain_id)
            if target_scope == "domain"
            else target_domain_id
        )
        if target_scope == "project" and not eff_project_id:
            raise ValidationError("memory_promote to scope='project' requires a project_id")
        if target_scope == "domain" and not eff_domain_id:
            raise ValidationError("memory_promote to scope='domain' requires a domain_id")

        src_variant = str(source.provenance.get("brain_variant") or "default")
        existing = await self._repo.find_existing_promoted_memory(
            tenant_id=ctx.tenant_id,
            target_scope=target_scope,
            source_memory_id=memory_id,
            summary=source.summary,
            project_id=eff_project_id,
            domain_id=eff_domain_id,
            brain_variant=src_variant,
        )
        if existing is not None and existing.id is not None:
            return {
                "promoted_memory_id": existing.id,
                "source_memory_id": memory_id,
                "target_scope": target_scope,
                "domain_id": eff_domain_id,
                "project_id": eff_project_id,
                "idempotent_replay": True,
            }

        try:
            vetted_summary = inspect_memory_write(
                source.summary,
                tenant_id=ctx.tenant_id,
                writer_agent_id=ctx.agent_id,
                target=f"memory_promote:{memory_id}",
            )
            vetted_raw = inspect_memory_write(
                source.content_raw,
                tenant_id=ctx.tenant_id,
                writer_agent_id=ctx.agent_id,
                target=f"memory_promote:{memory_id}",
            )
        except MemoryPoisoningError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_promote",
                allowed=False,
                reason=f"poisoning_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc

        new_prov = dict(source.provenance)
        new_prov.update(
            {
                "promoted_from_memory_id": memory_id,
                "promoted_from_scope": source.scope,
                "promoted_by_agent_id": ctx.agent_id,
                "evidence_ref": evidence_ref,
            }
        )
        src_specs_raw = source.provenance.get("specializations")
        src_specs = (
            [str(s).strip() for s in src_specs_raw if str(s).strip()]
            if isinstance(src_specs_raw, list | tuple)
            else []
        )
        extra_specs = (
            [str(s).strip() for s in specializations if str(s).strip()]
            if specializations is not None
            else []
        )
        merged_specs = list(dict.fromkeys([*src_specs, *extra_specs]))
        if merged_specs:
            new_prov["specializations"] = merged_specs

        vec: Sequence[float] | None = None
        emb_model = source.embedding_model
        emb_dims = source.embedding_dims
        if self._llm is not None:
            try:
                vecs = await self._llm.embed(texts=[vetted_summary])
                if vecs and len(vecs[0]) == 1536:
                    vec = vecs[0]
                    emb_dims = 1536
            except Exception:  # noqa: BLE001  # reason: fallback to copying source embedding when LLM embed fails
                vec = None

        promoted_mem = Memory(
            content_raw=vetted_raw,
            summary=vetted_summary,
            type=source.type,
            embedding_model=emb_model,
            embedding_dims=emb_dims,
            provenance=new_prov,
            confidence=source.confidence,
            agent_id=source.agent_id,
            project_id=eff_project_id,
            domain_id=eff_domain_id,
            tenant_id=ctx.tenant_id,
            scope=target_scope,
            source_trust=source.source_trust,
        )
        promoted_id = await self._repo.upsert_memory(
            promoted_mem,
            embedding=vec,
            copy_embedding_from_id=memory_id if vec is None else None,
        )
        return {
            "promoted_memory_id": promoted_id,
            "source_memory_id": memory_id,
            "target_scope": target_scope,
            "domain_id": eff_domain_id,
            "project_id": eff_project_id,
            "idempotent_replay": False,
        }

    async def agent_brain_export(
        self,
        ctx: ActorContext,
        *,
        domain_id: str | None = None,
        brain_variant: str | None = None,
        specialization: str | None = None,
        min_confidence: float = 0.5,
        limit: int = MAX_K,
        redact_terms: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Export a sanitized portable Virtual Employee Brain Passport (L3 profile + L2 skills).

        Raw L1 episodes and tenant-specific L0/project data are never exported.
        All exported L2 skill summaries/content and L3 preferences are recursively
        scrubbed of PII, secrets, URLs, IPs, UUIDs, and source tenant/project identifiers,
        and sealed with a canonical SHA-256 integrity digest (passport_sha256).
        """
        await self._decide(
            ctx, "agent_brain_export", TargetScope(tenant_id=ctx.tenant_id, scope="agent")
        )
        eff_domain = domain_id if domain_id is not None else ctx.domain_id
        profile = await self.agent_profile_get(ctx)
        max_k = max(1, min(limit, MAX_K))
        memories = await self._repo.get_memories(
            tenant_id=ctx.tenant_id,
            agent_id=ctx.agent_id,
            project_id=ctx.project_id,
            domain_id=eff_domain,
            k=max_k,
            scopes=("agent",),
            brain_variant=brain_variant,
            specialization=specialization,
        )
        base_terms = [ctx.tenant_id, ctx.project_id, *redact_terms]
        skills: list[dict[str, Any]] = []
        total_redactions = 0
        for mem in memories:
            if mem.scope != "agent" or mem.confidence < min_confidence:
                continue
            if eff_domain is not None and mem.domain_id is not None and mem.domain_id != eff_domain:
                continue
            mem_terms = [*base_terms, *([mem.project_id] if mem.project_id else [])]
            clean_summary, r_sum = sanitize_portable_text(mem.summary, redact_terms=mem_terms)
            clean_raw, r_raw = sanitize_portable_text(mem.content_raw, redact_terms=mem_terms)
            raw_bm = mem.provenance.get("brain_metadata")
            skill_bm: dict[str, Any] = (
                dict(raw_bm)
                if isinstance(raw_bm, Mapping)
                else {
                    "brain_variant": str(
                        mem.provenance.get("brain_variant") or (brain_variant or "default")
                    ),
                    "distiller_model": str(mem.provenance.get("distiller") or "reflect-v1"),
                    "embedding_model": mem.embedding_model,
                    "rag_params": {},
                }
            )
            clean_bm, r_bm = sanitize_portable_value(skill_bm, redact_terms=mem_terms)
            raw_specs = mem.provenance.get("specializations")
            skill_specs = (
                [str(s).strip() for s in raw_specs if str(s).strip()]
                if isinstance(raw_specs, list | tuple)
                else []
            )
            clean_specs, r_sp = sanitize_portable_value(skill_specs, redact_terms=mem_terms)
            redactions = r_sum + r_raw + r_bm + r_sp
            total_redactions += redactions
            skills.append(
                {
                    "type": mem.type,
                    "domain_id": mem.domain_id or eff_domain,
                    "summary": clean_summary,
                    "content_raw": clean_raw,
                    "confidence": mem.confidence,
                    "brain_variant": str(
                        mem.provenance.get("brain_variant")
                        or clean_bm.get("brain_variant")
                        or (brain_variant or "default")
                    ),
                    "brain_metadata": clean_bm,
                    "specializations": clean_specs,
                    "redactions_applied": redactions,
                }
            )
        clean_display, r_disp = sanitize_portable_text(
            str(profile.display_name or ctx.agent_id), redact_terms=base_terms
        )
        total_redactions += r_disp
        clean_prefs, r_prefs = sanitize_portable_value(
            dict(profile.standing_preferences or {}), redact_terms=base_terms
        )
        total_redactions += r_prefs
        raw_bcfg = clean_prefs.get("brain_config") if isinstance(clean_prefs, Mapping) else {}
        bcfg = dict(raw_bcfg) if isinstance(raw_bcfg, Mapping) else {}
        passport_brain_metadata: dict[str, Any] = {
            "brain_variant": brain_variant or str(bcfg.get("active_brain_variant") or "default"),
            "distiller_model": str(bcfg.get("distiller_model") or "reflect-v1"),
            "extraction_model": str(bcfg.get("extraction_model") or "docling+litellm"),
            "embedding_model": str(bcfg.get("embedding_model") or "text-embedding-3-small"),
            "rag_params": dict(bcfg.get("rag_params") or {})
            if isinstance(bcfg.get("rag_params"), Mapping)
            else {},
        }
        agent_type_val = (
            clean_prefs.get("agent_type") or clean_prefs.get("role")
            if isinstance(clean_prefs, Mapping)
            else None
        )
        prof_specs_raw = (
            clean_prefs.get("specializations") if isinstance(clean_prefs, Mapping) else None
        )
        prof_specs = (
            [str(s) for s in prof_specs_raw if str(s).strip()]
            if isinstance(prof_specs_raw, list)
            else []
        )
        payload: dict[str, Any] = {
            "schema_version": "engramory.brain_passport.v1",
            "agent_id": ctx.agent_id,
            "agent_type": agent_type_val,
            "display_name": clean_display,
            "domain_id": eff_domain,
            "specializations": prof_specs,
            "brain_metadata": passport_brain_metadata,
            "standing_preferences": clean_prefs,
            "skills_exported": len(skills),
            "total_redactions": total_redactions,
            "skills": skills,
        }
        payload["passport_sha256"] = _compute_passport_sha256(payload)
        return payload

    async def agent_brain_import(
        self,
        ctx: ActorContext,
        *,
        passport: Mapping[str, Any],
        evidence_ref: str | None,
        target_domain_id: str | None = None,
        brain_variant: str | None = None,
        redact_terms: Sequence[str] = (),
        verify_integrity: bool = True,
        allow_type_override: bool = False,
    ) -> dict[str, Any]:
        """Governed import of a sanitized Virtual Employee Brain Passport into caller's tenant."""
        from engramory.core.models import Memory

        await self._decide(
            ctx, "agent_brain_import", TargetScope(tenant_id=ctx.tenant_id, scope="agent")
        )
        if not evidence_ref:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="agent_brain_import",
                allowed=False,
                reason="missing_evidence_ref",
            )
            raise GovernedWriteRejected("agent_brain_import requires evidence_ref")

        schema_ver = passport.get("schema_version")
        if schema_ver is not None and schema_ver != "engramory.brain_passport.v1":
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="agent_brain_import",
                allowed=False,
                reason=f"unsupported_schema_version: {schema_ver}",
            )
            raise GovernedWriteRejected(
                f"Unsupported Brain Passport schema_version: {schema_ver!r}"
            )

        if verify_integrity:
            supplied_sha = str(passport.get("passport_sha256") or "").strip()
            expected_sha = _compute_passport_sha256(passport)
            if not supplied_sha or supplied_sha != expected_sha:
                await self._repo.record_audit(
                    tenant_id=ctx.tenant_id,
                    agent_id=ctx.agent_id,
                    project_id=ctx.project_id,
                    action="agent_brain_import",
                    allowed=False,
                    reason="passport_integrity_mismatch",
                )
                raise GovernedWriteRejected("Brain passport SHA-256 integrity check failed")

        existing_profile = (
            await self._repo.get_agent_profile(ctx.agent_id, tenant_id=ctx.tenant_id)
            if hasattr(self._repo, "get_agent_profile")
            else None
        )
        if not isinstance(existing_profile, AgentProfile):
            existing_profile = None
        existing_prefs = (
            dict(existing_profile.standing_preferences) if existing_profile is not None else {}
        )
        target_agent_type = (
            str(existing_prefs.get("agent_type") or existing_prefs.get("role") or "").strip()
            or None
        )
        raw_pass_prefs = passport.get("standing_preferences")
        pass_prefs_dict = dict(raw_pass_prefs) if isinstance(raw_pass_prefs, Mapping) else {}
        passport_agent_type = (
            str(
                passport.get("agent_type")
                or pass_prefs_dict.get("agent_type")
                or pass_prefs_dict.get("role")
                or ""
            ).strip()
            or None
        )
        if (
            target_agent_type is not None
            and passport_agent_type is not None
            and target_agent_type != passport_agent_type
            and not allow_type_override
        ):
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="agent_brain_import",
                allowed=False,
                reason=(
                    f"agent_type_mismatch: target={target_agent_type} "
                    f"passport={passport_agent_type}"
                ),
            )
            raise GovernedWriteRejected(
                f"agent_type_mismatch: cannot import {passport_agent_type!r} brain "
                f"into {target_agent_type!r} instance without allow_type_override=True"
            )

        await self._enforce_domain_boundary(ctx, "agent_brain_import", target_domain_id)
        passport_dom = str(passport["domain_id"]).strip() if passport.get("domain_id") else None
        if target_domain_id is not None:
            eff_domain: str | None = target_domain_id
        elif ctx.domain_id is not None and "space" not in ctx.scopes:
            eff_domain = ctx.domain_id
        elif passport_dom and ("domain" in ctx.scopes or "space" in ctx.scopes):
            eff_domain = passport_dom
        else:
            eff_domain = ctx.domain_id
        clean_prefs, _ = sanitize_portable_value(pass_prefs_dict, redact_terms=redact_terms)
        prefs: dict[str, Any] = dict(existing_prefs)
        prefs.update(clean_prefs if isinstance(clean_prefs, Mapping) else {})
        if eff_domain:
            prefs["department_focus"] = eff_domain
        if target_agent_type:
            prefs["agent_type"] = target_agent_type
        elif passport_agent_type and "agent_type" not in prefs:
            prefs["agent_type"] = passport_agent_type

        existing_specs_raw = existing_prefs.get("specializations")
        existing_specs = (
            [str(s).strip() for s in existing_specs_raw if str(s).strip()]
            if isinstance(existing_specs_raw, list | tuple)
            else []
        )
        imported_specs_raw = passport.get("specializations") or pass_prefs_dict.get(
            "specializations"
        )
        imported_specs = (
            [str(s).strip() for s in imported_specs_raw if str(s).strip()]
            if isinstance(imported_specs_raw, list | tuple)
            else []
        )
        clean_imp_specs, _ = sanitize_portable_value(imported_specs, redact_terms=redact_terms)
        merged_profile_specs = list(
            dict.fromkeys(
                [*existing_specs, *(clean_imp_specs if isinstance(clean_imp_specs, list) else [])]
            )
        )
        if merged_profile_specs:
            prefs["specializations"] = merged_profile_specs

        is_sibling_clone = bool(
            passport.get("agent_id") and str(passport.get("agent_id")) != ctx.agent_id
        )
        raw_display = (
            existing_profile.display_name
            if (is_sibling_clone and existing_profile is not None and existing_profile.display_name)
            else (
                passport.get("display_name")
                or (existing_profile.display_name if existing_profile else ctx.agent_id)
            )
        )
        clean_display, _ = sanitize_portable_text(
            str(raw_display or ctx.agent_id), redact_terms=redact_terms
        )

        passport_bm_raw = passport.get("brain_metadata")
        passport_bm, _ = sanitize_portable_value(
            dict(passport_bm_raw) if isinstance(passport_bm_raw, Mapping) else {},
            redact_terms=redact_terms,
        )

        # Screen profile + all skills BEFORE any DB write ([M2])
        vetted_skills: list[dict[str, Any]] = []
        try:
            vetted_display = inspect_memory_write(
                clean_display,
                tenant_id=ctx.tenant_id,
                writer_agent_id=ctx.agent_id,
                target="brain_import:display_name",
            )
            if prefs:
                inspect_memory_write(
                    json.dumps(prefs, sort_keys=True),
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target="brain_import:standing_preferences",
                )
            if passport_bm:
                inspect_memory_write(
                    json.dumps(passport_bm, sort_keys=True),
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target="brain_import:brain_metadata",
                )
            raw_skills = passport.get("skills") or []
            for idx, item in enumerate(raw_skills):
                if not isinstance(item, Mapping):
                    continue
                raw_sum = str(item.get("summary") or "").strip()
                raw_content = str(item.get("content_raw") or raw_sum).strip()
                if not raw_sum:
                    continue
                clean_sum, _ = sanitize_portable_text(raw_sum, redact_terms=redact_terms)
                clean_content, _ = sanitize_portable_text(raw_content, redact_terms=redact_terms)
                vetted_sum = inspect_memory_write(
                    clean_sum,
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target=f"brain_import:{idx}",
                )
                vetted_content = inspect_memory_write(
                    clean_content,
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target=f"brain_import:{idx}",
                )
                item_bm_raw = item.get("brain_metadata")
                item_bm, _ = sanitize_portable_value(
                    dict(item_bm_raw)
                    if isinstance(item_bm_raw, Mapping)
                    else dict(passport_bm if isinstance(passport_bm, Mapping) else {}),
                    redact_terms=redact_terms,
                )
                if item_bm:
                    inspect_memory_write(
                        json.dumps(item_bm, sort_keys=True),
                        tenant_id=ctx.tenant_id,
                        writer_agent_id=ctx.agent_id,
                        target=f"brain_import:{idx}:brain_metadata",
                    )
                item_specs_raw = item.get("specializations")
                item_specs = (
                    [str(s).strip() for s in item_specs_raw if str(s).strip()]
                    if isinstance(item_specs_raw, list | tuple)
                    else list(merged_profile_specs)
                )
                clean_item_specs, _ = sanitize_portable_value(item_specs, redact_terms=redact_terms)
                m_type = str(item.get("type") or "procedural")
                if m_type not in {"semantic", "episodic", "procedural"}:
                    m_type = "procedural"
                conf = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
                eff_var = str(
                    brain_variant
                    or item.get("brain_variant")
                    or (item_bm.get("brain_variant") if isinstance(item_bm, Mapping) else None)
                    or (
                        passport_bm.get("brain_variant")
                        if isinstance(passport_bm, Mapping)
                        else None
                    )
                    or "default"
                )
                if isinstance(item_bm, dict):
                    item_bm["brain_variant"] = eff_var
                skill_dom = (
                    str(item.get("domain_id") or eff_domain)
                    if (item.get("domain_id") or eff_domain)
                    else None
                )
                vetted_skills.append(
                    {
                        "summary": vetted_sum,
                        "content_raw": vetted_content,
                        "type": m_type,
                        "confidence": conf,
                        "domain_id": skill_dom,
                        "brain_variant": eff_var,
                        "brain_metadata": item_bm if isinstance(item_bm, Mapping) else {},
                        "specializations": clean_item_specs
                        if isinstance(clean_item_specs, list)
                        else [],
                    }
                )
        except MemoryPoisoningError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="agent_brain_import",
                allowed=False,
                reason=f"poisoning_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc

        if hasattr(self._repo, "upsert_agent_profile"):
            await self._repo.upsert_agent_profile(
                AgentProfile(
                    agent_id=ctx.agent_id,
                    display_name=vetted_display,
                    standing_preferences=prefs,
                    tenant_id=ctx.tenant_id,
                )
            )

        existing_memories = await self._repo.get_memories(
            tenant_id=ctx.tenant_id,
            agent_id=ctx.agent_id,
            project_id=ctx.project_id,
            domain_id=eff_domain,
            k=MAX_K,
            scopes=("agent",),
        )
        existing_by_key: dict[tuple[str, str, str | None, str, str], str] = {}
        for em in existing_memories:
            if em.id is None:
                continue
            em_var = str(em.provenance.get("brain_variant") or "default")
            existing_by_key[(em_var, em.type, em.domain_id, em.summary, em.content_raw)] = em.id

        to_insert: list[dict[str, Any]] = []
        memory_ids: list[str] = []
        for sk in vetted_skills:
            dedup_key = (
                sk["brain_variant"],
                sk["type"],
                sk["domain_id"],
                sk["summary"],
                sk["content_raw"],
            )
            if dedup_key in existing_by_key:
                memory_ids.append(existing_by_key[dedup_key])
            else:
                to_insert.append(sk)

        embeddings: Sequence[Sequence[float]] | None = None
        embed_model_name = "none/portable-passport"
        if self._llm is not None and to_insert:
            try:
                embeddings = await self._llm.embed(texts=[s["summary"] for s in to_insert])
                embed_model_name = next(
                    (
                        val
                        for attr in ("embed_model", "_embed_model", "_default_embed_model")
                        if isinstance((val := getattr(self._llm, attr, None)), str) and val.strip()
                    ),
                    "text-embedding-3-small",
                )
            except Exception:  # noqa: BLE001  # reason: degraded LLM endpoint must not block canonical passport import
                embeddings = None

        for idx, sk in enumerate(to_insert):
            vec_cand = (
                embeddings[idx]
                if (
                    embeddings is not None
                    and idx < len(embeddings)
                    and len(embeddings[idx]) == 1536
                )
                else None
            )
            eff_emb_model = (
                embed_model_name
                if vec_cand is not None
                else str(sk["brain_metadata"].get("embedding_model") or "none/portable-passport")
            )
            prov: dict[str, Any] = {
                "source": "portable_brain_passport",
                "evidence_ref": evidence_ref,
                "source_agent_id": str(passport.get("agent_id") or "specialist"),
                "brain_variant": sk["brain_variant"],
                "brain_metadata": dict(sk["brain_metadata"]),
            }
            if passport_agent_type or target_agent_type:
                prov["agent_type"] = target_agent_type or passport_agent_type
            if sk["specializations"]:
                prov["specializations"] = list(sk["specializations"])
            mem = Memory(
                content_raw=sk["content_raw"],
                summary=sk["summary"],
                type=sk["type"],
                embedding_model=eff_emb_model,
                embedding_dims=1536 if vec_cand is not None else None,
                provenance=prov,
                agent_id=ctx.agent_id,
                project_id=None,
                domain_id=sk["domain_id"],
                tenant_id=ctx.tenant_id,
                scope="agent",
                confidence=sk["confidence"],
            )
            mid = await self._repo.upsert_memory(mem, embedding=vec_cand)
            memory_ids.append(mid)
            dedup_key = (
                sk["brain_variant"],
                sk["type"],
                sk["domain_id"],
                sk["summary"],
                sk["content_raw"],
            )
            existing_by_key[dedup_key] = mid

        return {
            "agent_id": ctx.agent_id,
            "agent_type": target_agent_type or passport_agent_type,
            "tenant_id": ctx.tenant_id,
            "domain_id": eff_domain,
            "specializations": merged_profile_specs,
            "skills_imported": len(memory_ids),
            "memory_ids": memory_ids,
        }

    async def agent_brain_compare(
        self,
        ctx: ActorContext,
        *,
        variant_a: str,
        variant_b: str,
        query: str | None = None,
        k: int = 5,
        scope: str = "agent",
        domain_id: str | None = None,
        specialization: str | None = None,
    ) -> dict[str, Any]:
        """Compare two multi-model brain variants (variant_a vs. variant_b) across
        skill statistics, retrieval feedback useful_rate, and optional live query hits."""
        await self._decide(
            ctx, "agent_brain_compare", TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        )
        eff_domain = await self._enforce_domain_boundary(ctx, "agent_brain_compare", domain_id)
        metrics_a = await self._repo.get_brain_variant_metrics(
            tenant_id=ctx.tenant_id,
            variant=variant_a,
            scope=scope,
            agent_id=ctx.agent_id if scope == "agent" else None,
            project_id=ctx.project_id if scope == "project" else None,
            domain_id=eff_domain,
            specialization=specialization,
        )
        metrics_b = await self._repo.get_brain_variant_metrics(
            tenant_id=ctx.tenant_id,
            variant=variant_b,
            scope=scope,
            agent_id=ctx.agent_id if scope == "agent" else None,
            project_id=ctx.project_id if scope == "project" else None,
            domain_id=eff_domain,
            specialization=specialization,
        )
        score_a = (metrics_a["useful_rate"] * 0.6) + (metrics_a["mean_confidence"] * 0.4)
        score_b = (metrics_b["useful_rate"] * 0.6) + (metrics_b["mean_confidence"] * 0.4)
        if abs(score_a - score_b) < 1e-6:
            winner = (
                variant_a if metrics_a["skill_count"] >= metrics_b["skill_count"] else variant_b
            )
        else:
            winner = variant_a if score_a > score_b else variant_b

        result: dict[str, Any] = {
            "agent_id": ctx.agent_id,
            "tenant_id": ctx.tenant_id,
            "scope": scope,
            "domain_id": eff_domain,
            "specialization": specialization,
            "variant_a": metrics_a,
            "variant_b": metrics_b,
            "winner": winner,
        }
        if query and query.strip():
            hits_a = await self.memory_search(
                ctx,
                query,
                scope=scope,
                domain_id=eff_domain,
                brain_variant=variant_a,
                specialization=specialization,
                k=k,
            )
            hits_b = await self.memory_search(
                ctx,
                query,
                scope=scope,
                domain_id=eff_domain,
                brain_variant=variant_b,
                specialization=specialization,
                k=k,
            )
            summaries_a = {h.summary for h in hits_a}
            summaries_b = {h.summary for h in hits_b}
            union_cnt = len(summaries_a | summaries_b)
            overlap_ratio = (
                round(len(summaries_a & summaries_b) / union_cnt, 4) if union_cnt > 0 else 0.0
            )
            result["query_comparison"] = {
                "query": query,
                "variant_a_hits": [
                    {"memory_id": h.memory_id, "summary": h.summary, "score": h.score}
                    for h in hits_a
                ],
                "variant_b_hits": [
                    {"memory_id": h.memory_id, "summary": h.summary, "score": h.score}
                    for h in hits_b
                ],
                "overlap_ratio": overlap_ratio,
            }
        return result

    async def agent_space_offboard(
        self,
        ctx: ActorContext,
        *,
        evidence_ref: str | None,
        reason: str = "cross_tenant_reassignment",
        purge_episodes: bool = True,
        retire_memories: bool = True,
    ) -> dict[str, Any]:
        """Governed transactional offboarding of a Virtual Employee's private
        scope='agent' space on the caller's tenant."""
        await self._decide(
            ctx,
            "agent_space_offboard",
            TargetScope(tenant_id=ctx.tenant_id, scope="agent"),
            note=reason,
        )
        if not evidence_ref:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="agent_space_offboard",
                allowed=False,
                reason="missing_evidence_ref",
            )
            raise GovernedWriteRejected("agent_space_offboard requires evidence_ref")

        counts = await self._repo.offboard_agent_space(
            tenant_id=ctx.tenant_id,
            agent_id=ctx.agent_id,
            reason=reason,
            purge_episodes=purge_episodes,
            retire_memories=retire_memories,
        )
        return {
            "agent_id": ctx.agent_id,
            "tenant_id": ctx.tenant_id,
            "reason": reason,
            "evidence_ref": evidence_ref,
            **counts,
        }

    async def memory_ingest_event(
        self,
        ctx: ActorContext,
        event_data: Mapping[str, Any] | Any,
        *,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Validate, screen, and persist a structured ExperienceEvent into L1 episodes."""
        from engramory.core.experience import ExperienceEvent

        await self._decide(
            ctx, "memory_ingest_event", TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        )
        if isinstance(event_data, ExperienceEvent):
            ev = event_data
        else:
            raw_ev = dict(event_data)
            if not raw_ev.get("tenant_id"):
                raw_ev["tenant_id"] = ctx.tenant_id
            if not raw_ev.get("project") and ctx.project_id:
                raw_ev["project"] = ctx.project_id
            if not raw_ev.get("producer") and ctx.agent_id:
                raw_ev["producer"] = ctx.agent_id
            ev = ExperienceEvent.from_dict(raw_ev)
        if ev.tenant_id != ctx.tenant_id:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_ingest_event",
                allowed=False,
                reason=f"cross_tenant_event_rejected: {ev.tenant_id}",
            )
            raise AuthzError("out_of_scope")
        if (
            ctx.project_id is not None
            and ev.project != ctx.project_id
            and "space" not in ctx.scopes
        ):
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_ingest_event",
                allowed=False,
                reason=f"cross_project_event_rejected: {ev.project}",
            )
            raise AuthzError("out_of_scope")
        if scope == "agent" and ev.producer != ctx.agent_id and "space" not in ctx.scopes:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_ingest_event",
                allowed=False,
                reason=f"cross_agent_event_rejected: {ev.producer}",
            )
            raise AuthzError("out_of_scope")
        try:
            ep = ev.to_episode()
        except MemoryPoisoningError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_ingest_event",
                allowed=False,
                reason=f"poisoning_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc
        ep_id = await self._repo.add_episode(ep)
        return {
            "episode_id": ep_id,
            "event_id": ev.event_id,
            "kind": ev.kind,
            "project": ev.project,
            "producer": ev.producer,
            "tenant_id": ev.tenant_id,
        }

    async def memory_learning_health(
        self,
        ctx: ActorContext,
        *,
        project_id: str | None = None,
        limit: int = 500,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Compute closed-loop learning health metrics over recent episodes for caller's tenant."""
        await self._decide(
            ctx, "memory_learning_health", TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        )
        if (
            project_id is not None
            and ctx.project_id is not None
            and project_id != ctx.project_id
            and "space" not in ctx.scopes
        ):
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="memory_learning_health",
                allowed=False,
                reason=f"cross_project_health_rejected: {project_id}",
            )
            raise AuthzError("out_of_scope")
        eff_proj = project_id if project_id is not None else ctx.project_id
        summary = await self._repo.get_learning_health_summary(
            tenant_id=ctx.tenant_id,
            project_id=eff_proj,
            limit=limit,
        )
        return {
            "tenant_id": ctx.tenant_id,
            "project_id": eff_proj,
            **summary,
        }

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
        modality: str = "text",
        blob_sha256: str | None = None,
        blob_uri: str | None = None,
        media_metadata: Mapping[str, Any] | None = None,
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
        try:
            vetted_text = inspect_memory_write(
                text,
                tenant_id=ctx.tenant_id,
                writer_agent_id=ctx.agent_id,
                target=doc_id,
            )
            if media_metadata:
                inspect_memory_write(
                    json.dumps(dict(media_metadata), sort_keys=True),
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target=doc_id,
                )
        except MemoryPoisoningError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="knowledge_ingest",
                allowed=False,
                reason=f"poisoning_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc
        section = KBSection(
            doc_id=doc_id,
            citation=citation,
            text=vetted_text,
            project_id=ctx.project_id,
            tenant_id=ctx.tenant_id,
            scope=scope,
            version=version,
            modality=modality,
            blob_sha256=blob_sha256,
            blob_uri=blob_uri,
            media_metadata=media_metadata or {},
        )
        try:
            return await self._repo.upsert_kb_section(section)
        except ValidationError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="knowledge_ingest",
                allowed=False,
                reason=f"cross_project_collision: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc

    async def knowledge_ingest_batch(
        self,
        ctx: ActorContext,
        *,
        sections: Sequence[KBSection],
        evidence_ref: str | None,
        scope: str = "project",
    ) -> list[str]:
        """Governed batch write: authorize first, then require an approved-evidence
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

        vetted_sections: list[KBSection] = []
        for s in sections:
            # Bound scope to authorized scope / caller's scopes
            target_scope = s.scope if s.scope in ctx.scopes else scope
            try:
                vetted_text = inspect_memory_write(
                    s.text,
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target=s.doc_id,
                )
                if s.media_metadata:
                    inspect_memory_write(
                        json.dumps(dict(s.media_metadata), sort_keys=True),
                        tenant_id=ctx.tenant_id,
                        writer_agent_id=ctx.agent_id,
                        target=s.doc_id,
                    )
            except MemoryPoisoningError as exc:
                await self._repo.record_audit(
                    tenant_id=ctx.tenant_id,
                    agent_id=ctx.agent_id,
                    project_id=ctx.project_id,
                    action="knowledge_ingest",
                    allowed=False,
                    reason=f"poisoning_rejected: {exc}",
                )
                raise GovernedWriteRejected(str(exc)) from exc

            vetted_sections.append(
                KBSection(
                    doc_id=s.doc_id,
                    citation=s.citation,
                    text=vetted_text,
                    project_id=ctx.project_id,
                    tenant_id=ctx.tenant_id,
                    domain_id=s.domain_id,
                    scope=target_scope,
                    version=s.version,
                    modality=s.modality,
                    blob_sha256=s.blob_sha256,
                    blob_uri=s.blob_uri,
                    media_metadata=s.media_metadata,
                )
            )

        try:
            res = await self._repo.upsert_kb_sections_batch(vetted_sections)
        except ValidationError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="knowledge_ingest",
                allowed=False,
                reason=f"cross_project_collision: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc
        if isinstance(res, dict):
            ids = list(res.values())
        else:
            ids = list(res)

        if self._llm is not None and vetted_sections and ids:
            try:
                embeddings = await self._llm.embed(texts=[s.text for s in vetted_sections])
                model_name = next(
                    (
                        val
                        for attr in ("embed_model", "_embed_model", "_default_embed_model")
                        if isinstance((val := getattr(self._llm, attr, None)), str) and val.strip()
                    ),
                    "text-embedding-3-small",
                )
                updates = [
                    (sec_id, vec)
                    for sec_id, vec in zip(ids, embeddings, strict=False)
                    if vec and len(vec) == 1536
                ]
                if updates and hasattr(self._repo, "set_kb_sections_embedding_batch"):
                    await self._repo.set_kb_sections_embedding_batch(
                        updates, embedding_model=model_name
                    )
            except Exception as exc:  # noqa: BLE001  # reason: offline/degraded LLM must not fail canonical section ingestion
                logger.debug("Skipping section embedding in knowledge_ingest_batch: %s", exc)

        return ids

    async def knowledge_search(
        self,
        ctx: ActorContext,
        query: str,
        *,
        domain_id: str | None = None,
        query_vec: Sequence[float] | None = None,
        scope: str | None = None,
        modality: str | None = None,
        expand_graph: bool = False,
        graph_depth: int = 1,
        limit: int = 5,
    ) -> list[KBSection]:
        """Search knowledge sections with tenant/project scoping, modality filter, and GraphRAG."""
        eff_scope = scope or "project"
        await self._decide(
            ctx, "knowledge_search", TargetScope(tenant_id=ctx.tenant_id, scope=eff_scope)
        )
        eff_domain_id = await self._enforce_domain_boundary(ctx, "knowledge_search", domain_id)
        eff_vec = query_vec
        if eff_vec is None and self._llm is not None and query and query.strip():
            try:
                vecs = await self._llm.embed(texts=[query])
                if vecs and len(vecs[0]) == 1536:
                    eff_vec = vecs[0]
            except Exception:  # noqa: BLE001  # reason: fallback to lexical search when embedding endpoint is unreachable
                eff_vec = None

        search_kwargs: dict[str, Any] = {
            "tenant_id": ctx.tenant_id,
            "project_id": ctx.project_id,
            "domain_id": eff_domain_id,
            "query_vec": eff_vec,
            "scope": scope,
            "limit": limit,
        }
        if modality is not None:
            search_kwargs["modality"] = modality
        results = await self._repo.search_kb_sections(query, **search_kwargs)
        if not expand_graph or not results:
            return results

        eff_project_id = ctx.project_id if eff_scope in ("agent", "project") else None
        safe_depth = max(1, min(graph_depth, 6))
        enriched: list[KBSection] = []
        for sec in results:
            seed_ids: list[str] = [sec.doc_id]
            if "#" in sec.citation:
                anchor = sec.citation.split("#", 1)[1]
                seed_ids.append(f"{sec.doc_id}#{anchor}")
            if sec.blob_sha256:
                seed_ids.append(f"blob:{sec.blob_sha256[:16]}")

            neighbors_by_id: dict[str, Mapping[str, Any]] = {}
            for seed_id in seed_ids:
                hits = await self._expand_neighbors_with_fallback(
                    id=seed_id,
                    tenant_id=ctx.tenant_id,
                    depth=safe_depth,
                    project_id=eff_project_id,
                )
                for hit in hits:
                    nid = str(hit.get("id", ""))
                    if nid and nid not in neighbors_by_id:
                        neighbors_by_id[nid] = dict(hit)

            if neighbors_by_id:
                new_meta = {
                    **dict(sec.media_metadata),
                    "graph_context": list(neighbors_by_id.values()),
                }
                enriched.append(dataclasses.replace(sec, media_metadata=new_meta))
            else:
                enriched.append(sec)
        return enriched

    async def blob_put(
        self,
        ctx: ActorContext,
        *,
        file_path: Path | str | None = None,
        key: str | None = None,
        data: bytes | None = None,
        content_type: str = "application/octet-stream",
        scope: str = "project",
        metadata: Mapping[str, Any] | None = None,
        allowed_root: Path | None = None,
        enforce_project_boundary: bool = False,
    ) -> dict[str, Any]:
        """Governed write of a raw binary blob into the CAS storage adapter (PLAN-023)."""
        from engramory.core.docs_tools import resolve_confined_repo_path

        await self._decide(ctx, "blob_put", TargetScope(tenant_id=ctx.tenant_id, scope=scope))
        if self._storage is None:
            raise RuntimeError("StoragePort adapter is not configured on AccessSurface")

        if metadata:
            try:
                inspect_memory_write(
                    json.dumps(dict(metadata), sort_keys=True),
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target=key or str(file_path or "blob"),
                )
            except MemoryPoisoningError as exc:
                await self._repo.record_audit(
                    tenant_id=ctx.tenant_id,
                    agent_id=ctx.agent_id,
                    project_id=ctx.project_id,
                    action="blob_put",
                    allowed=False,
                    reason=f"poisoning_rejected: {exc}",
                )
                raise GovernedWriteRejected(str(exc)) from exc

        if file_path is not None:
            raw_p = Path(file_path)
            if raw_p.is_symlink():
                raise ValidationError(f"Symlink paths are rejected for blob_put: {file_path}")
            confined_p = resolve_confined_repo_path(
                raw_p,
                allowed_root=allowed_root,
                project_id=ctx.project_id,
                enforce_project_boundary=enforce_project_boundary,
            )
            blob = await self._storage.put_file(
                file_path=confined_p,
                content_type=content_type,
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
                metadata=metadata,
            )
        elif data is not None:
            await self._storage.put(
                key=key or "",
                data=data,
                content_type=content_type,
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
                metadata=metadata,
            )
            import hashlib as _hashlib

            sha = _hashlib.sha256(data).hexdigest()
            head_res = await self._storage.head(key=sha, tenant_id=ctx.tenant_id)
            if head_res is None:
                raise RuntimeError("Blob put succeeded but head lookup returned None")
            blob = head_res
        else:
            raise ValidationError("blob_put requires either file_path or data")

        return {
            "sha256": blob.sha256,
            "tenant_id": blob.tenant_id,
            "project_id": blob.project_id,
            "media_type": blob.media_type,
            "byte_size": blob.byte_size,
            "chunk_size_bytes": blob.chunk_size_bytes,
            "chunk_count": blob.chunk_count,
            "storage_tier": blob.storage_tier,
            "status": blob.status,
            "blob_uri": f"pgblob://{blob.tenant_id}/{blob.sha256}",
        }

    async def blob_head(
        self,
        ctx: ActorContext,
        *,
        sha256: str,
        scope: str = "project",
    ) -> dict[str, Any] | None:
        """Governed metadata lookup of a CAS blob by SHA-256 within caller's tenant."""
        await self._decide(ctx, "blob_head", TargetScope(tenant_id=ctx.tenant_id, scope=scope))
        if self._storage is None:
            return None
        blob = await self._storage.head(key=sha256, tenant_id=ctx.tenant_id)
        if blob is None:
            return None
        return {
            "sha256": blob.sha256,
            "tenant_id": blob.tenant_id,
            "project_id": blob.project_id,
            "media_type": blob.media_type,
            "byte_size": blob.byte_size,
            "chunk_size_bytes": blob.chunk_size_bytes,
            "chunk_count": blob.chunk_count,
            "storage_tier": blob.storage_tier,
            "status": blob.status,
            "blob_uri": f"pgblob://{blob.tenant_id}/{blob.sha256}",
            "metadata": dict(blob.metadata),
        }

    async def blob_get(
        self,
        ctx: ActorContext,
        *,
        sha256: str,
        scope: str = "project",
    ) -> bytes:
        """Governed read of a bounded CAS blob by SHA-256 within caller's tenant."""
        await self._decide(ctx, "blob_get", TargetScope(tenant_id=ctx.tenant_id, scope=scope))
        if self._storage is None:
            raise RuntimeError("StoragePort adapter is not configured on AccessSurface")
        return await self._storage.get(key=sha256, tenant_id=ctx.tenant_id)

    async def knowledge_ingest_media(
        self,
        ctx: ActorContext,
        *,
        file_path: Path | str,
        evidence_ref: str | None,
        scope: str = "project",
        embed: bool = True,
        project_graph: bool = True,
        allowed_root: Path | None = None,
        enforce_project_boundary: bool = False,
    ) -> dict[str, Any]:
        """Governed multimodal ingestion (CAS blob + derived sections + dual-engine graph)."""
        from engramory.core.docs_tools import resolve_confined_repo_path
        from engramory.core.knowledge import KnowledgeCore

        await self._decide(
            ctx,
            "knowledge_ingest_media",
            TargetScope(tenant_id=ctx.tenant_id, scope=scope),
        )
        if not evidence_ref:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="knowledge_ingest_media",
                allowed=False,
                reason="missing_evidence_ref",
            )
            raise GovernedWriteRejected("knowledge_ingest_media requires evidence_ref")

        raw_p = Path(file_path)
        if raw_p.is_symlink():
            raise ValidationError(
                f"Symlink paths are rejected for knowledge_ingest_media: {file_path}"
            )
        confined_p = resolve_confined_repo_path(
            raw_p,
            allowed_root=allowed_root,
            project_id=ctx.project_id,
            enforce_project_boundary=enforce_project_boundary,
        )
        core = KnowledgeCore(
            self._repo,
            llm=self._llm,
            storage=self._storage,
            graph=self._graph,
        )
        try:
            return await core.ingest_media_file(
                confined_p,
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
                scope=scope,
                evidence_ref=evidence_ref,
                embed=embed,
                project_graph=project_graph,
                repo_root=allowed_root,
            )
        except MemoryPoisoningError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="knowledge_ingest_media",
                allowed=False,
                reason=f"poisoning_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc
        except ValidationError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="knowledge_ingest_media",
                allowed=False,
                reason=f"validation_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc

    async def docs_ingest_runbook(
        self,
        ctx: ActorContext,
        *,
        doc: KBDocument,
        steps: Sequence[KBRunbookStep],
        evidence_ref: str | None,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Governed write of L0 kb_documents and kb_runbook_steps (ADR-06)."""
        await self._decide(
            ctx, "docs_ingest_runbook", TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        )
        if not evidence_ref:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="docs_ingest_runbook",
                allowed=False,
                reason="missing_evidence_ref",
            )
            raise GovernedWriteRejected("docs_ingest_runbook requires evidence_ref")

        try:
            vetted_title = inspect_memory_write(
                doc.title,
                tenant_id=ctx.tenant_id,
                writer_agent_id=ctx.agent_id,
                target=doc.doc_id,
            )
        except MemoryPoisoningError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="docs_ingest_runbook",
                allowed=False,
                reason=f"poisoning_rejected: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc

        vetted_steps: list[KBRunbookStep] = []
        for st in steps:
            try:
                vetted_heading = inspect_memory_write(
                    st.heading_path,
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target=doc.doc_id,
                )
                vetted_prose = (
                    inspect_memory_write(
                        st.prose_context,
                        tenant_id=ctx.tenant_id,
                        writer_agent_id=ctx.agent_id,
                        target=doc.doc_id,
                    )
                    if st.prose_context
                    else ""
                )
                vetted_cmd = inspect_memory_write(
                    st.command_body,
                    tenant_id=ctx.tenant_id,
                    writer_agent_id=ctx.agent_id,
                    target=doc.doc_id,
                )
            except MemoryPoisoningError as exc:
                await self._repo.record_audit(
                    tenant_id=ctx.tenant_id,
                    agent_id=ctx.agent_id,
                    project_id=ctx.project_id,
                    action="docs_ingest_runbook",
                    allowed=False,
                    reason=f"poisoning_rejected: {exc}",
                )
                raise GovernedWriteRejected(str(exc)) from exc
            vetted_steps.append(
                KBRunbookStep(
                    step_uid=st.step_uid,
                    doc_id=doc.doc_id,
                    step_order=st.step_order,
                    step_id=st.step_id,
                    heading_path=vetted_heading,
                    tier=st.tier,
                    mode=st.mode,
                    command_body=vetted_cmd,
                    prose_context=vetted_prose,
                    expect_exit=st.expect_exit,
                    timeout_sec=st.timeout_sec,
                    rollback_step=st.rollback_step,
                    tenant_id=ctx.tenant_id,
                )
            )

        locked_doc = KBDocument(
            doc_id=doc.doc_id,
            repo_path=doc.repo_path,
            title=vetted_title,
            kind=doc.kind,
            namespace=doc.namespace,
            status=doc.status,
            autonomy_tier=doc.autonomy_tier,
            frontmatter=doc.frontmatter,
            watched_paths=doc.watched_paths,
            trigger_alerts=doc.trigger_alerts,
            last_verified=doc.last_verified,
            review_ttl_days=doc.review_ttl_days,
            git_commit_sha=evidence_ref,
            project_id=ctx.project_id,
            domain_id=doc.domain_id,
            tenant_id=ctx.tenant_id,
            scope=scope if scope in ctx.scopes else "project",
        )
        try:
            await self._repo.upsert_kb_document(locked_doc)
        except ValidationError as exc:
            await self._repo.record_audit(
                tenant_id=ctx.tenant_id,
                agent_id=ctx.agent_id,
                project_id=ctx.project_id,
                action="docs_ingest_runbook",
                allowed=False,
                reason=f"cross_project_collision: {exc}",
            )
            raise GovernedWriteRejected(str(exc)) from exc
        step_count = await self._repo.upsert_kb_runbook_steps(
            ctx.tenant_id, locked_doc.doc_id, vetted_steps
        )
        return {
            "status": "ok",
            "doc_id": locked_doc.doc_id,
            "step_count": step_count,
        }

    async def docs_slice_runbook(
        self,
        ctx: ActorContext,
        *,
        doc_id: str | None = None,
        alert_id: str | None = None,
        max_tier: str | None = None,
        scope: str = "project",
        domain_id: str | None = None,
        include_memories: bool = True,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        format: str = "json",
    ) -> dict[str, Any]:
        """Governed read of L0 runbook steps + optional L2 distilled memories."""
        from engramory.core.docs_tools import format_runbook_slice

        await self._decide(
            ctx, "docs_slice_runbook", TargetScope(tenant_id=ctx.tenant_id, scope=scope)
        )
        l0_steps = await self._repo.slice_runbook(
            tenant_id=ctx.tenant_id,
            project_id=ctx.project_id,
            domain_id=domain_id,
            scope=scope,
            doc_id=doc_id,
            alert_id=alert_id,
            max_tier=max_tier,
        )
        l2_memories: list[dict[str, Any]] = []
        if include_memories and (alert_id or doc_id):
            query_str = alert_id or doc_id or "runbook"
            try:
                hits = await self.memory_search(
                    ctx,
                    query_str,
                    scope=scope,
                    k=4,
                    token_budget=max(250, token_budget // 4),
                )
                l2_memories = [
                    {
                        "id": h.memory_id,
                        "content": h.summary,
                        "score": h.score,
                        "token_count": h.token_count,
                    }
                    for h in hits
                ]
            except Exception:
                l2_memories = []

        return format_runbook_slice(
            l0_steps,
            memories=l2_memories,
            token_budget=token_budget,
            output_format=format,
        )
