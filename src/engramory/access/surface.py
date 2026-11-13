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
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engramory.access.authz import ActorContext, AuthzDecision, TargetScope, authorize
from engramory.access.poisoning_guard import MemoryPoisoningError, inspect_memory_write
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

    async def memory_search(
        self,
        ctx: ActorContext,
        query: str,
        *,
        scope: str = "agent",
        tenant_id: str | None = None,
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
            domain_id=None,  # domain membership lands on ActorContext with BRD-04
            k=max_hits,
            query=query,
            query_vec=query_vec,
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
        domain_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Append an episode in the caller's scope (idempotent by content hash)."""
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ValueError(f"content exceeds {MAX_CONTENT_BYTES} bytes")
        await self._decide(ctx, "memory_add", TargetScope(tenant_id=ctx.tenant_id, scope="agent"))
        try:
            vetted_content = inspect_memory_write(
                content,
                tenant_id=ctx.tenant_id,
                writer_agent_id=ctx.agent_id,
                target="episodes",
            )
            if metadata:
                inspect_memory_write(
                    json.dumps(dict(metadata), sort_keys=True),
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
            domain_id=domain_id,
            tenant_id=ctx.tenant_id,
            kind=kind,
            metadata=metadata or {},
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
            "domain_id": domain_id,
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
