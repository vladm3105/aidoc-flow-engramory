"""End-to-end verification of Virtual Employee Hierarchy, Multi-Model Brain Variants,
Specialist Taxonomy, and Portable Brain Passports (PLAN-024 / CHG-009).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from engramory.access.authz import ActorContext
from engramory.access.jwt_auth import TokenClaims, claims_to_actor_context
from engramory.access.surface import AccessSurface, AuthzError, GovernedWriteRejected
from engramory.cli import _parse_config_text, _upsert_profile_best_effort
from engramory.core.models import AgentProfile, Episode, Memory
from engramory.core.repository import Repository
from engramory.mcp.server import McpServer
from engramory.workers.distillation import reflect


class InMemoryVirtualEmployeeRepo:
    """In-memory repository double exercising the Virtual Employee SQL contracts."""

    def __init__(self) -> None:
        self.profiles: dict[tuple[str, str], AgentProfile] = {}
        self.episodes: list[Episode] = []
        self.memories: dict[str, Memory] = {}
        self.embeddings: dict[str, list[float]] = {}
        self.feedbacks: dict[str, list[str | None]] = {}
        self.audits: list[dict[str, Any]] = []
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    async def record_audit(self, **kwargs: Any) -> None:
        self.audits.append(kwargs)

    async def get_agent_profile(
        self, agent_id: str, *, tenant_id: str = "default"
    ) -> AgentProfile | None:
        return self.profiles.get((tenant_id, agent_id))

    async def upsert_agent_profile(self, profile: AgentProfile) -> None:
        self.profiles[(profile.tenant_id, profile.agent_id)] = profile

    async def list_agent_profiles(
        self,
        *,
        tenant_id: str,
        domain_id: str | None = None,
        agent_type: str | None = None,
        role: str | None = None,
        specialization: str | None = None,
        include_stats: bool = True,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (tid, aid), prof in self.profiles.items():
            if tid != tenant_id:
                continue
            prefs = dict(prof.standing_preferences or {})
            p_dom = prefs.get("department_focus")
            p_type = prefs.get("agent_type") or prefs.get("role")
            p_role = prefs.get("role") or prefs.get("agent_type")
            p_specs = [str(s) for s in prefs.get("specializations", [])]
            if domain_id is not None and p_dom != domain_id:
                continue
            if agent_type is not None and p_type != agent_type:
                continue
            if role is not None and p_role != role:
                continue
            if specialization is not None and specialization not in p_specs:
                continue
            agent_mems = [
                m
                for m in self.memories.values()
                if m.tenant_id == tenant_id
                and m.agent_id == aid
                and m.scope == "agent"
                and m.status == "active"
                and m.valid_to is None
            ]
            skill_cnt = len(agent_mems)
            mean_conf = (
                round(sum(m.confidence for m in agent_mems) / skill_cnt, 4)
                if skill_cnt > 0
                else 0.0
            )
            variants = sorted(
                {str(m.provenance.get("brain_variant") or "default") for m in agent_mems}
            )
            entry: dict[str, Any] = {
                "tenant_id": tid,
                "agent_id": aid,
                "display_name": prof.display_name or "",
                "agent_type": p_type,
                "role": p_role,
                "domain_id": p_dom,
                "specializations": p_specs,
                "brain_config": dict(prefs.get("brain_config") or {}),
                "standing_preferences": prefs,
            }
            if include_stats:
                entry["active_skill_count"] = skill_cnt
                entry["mean_confidence"] = mean_conf
                entry["brain_variants"] = variants
            rows.append(entry)
        if include_stats:
            rows.sort(
                key=lambda r: (
                    -float(r.get("mean_confidence", 0.0)),
                    -int(r.get("active_skill_count", 0)),
                    str(r["agent_id"]),
                )
            )
        else:
            rows.sort(key=lambda r: str(r["agent_id"]))
        return rows

    async def add_episode(self, episode: Episode) -> str:
        eid = episode.id or self._next_id("ep")
        stored = Episode(
            id=eid,
            agent_id=episode.agent_id,
            content_raw=episode.content_raw,
            project_id=episode.project_id,
            domain_id=episode.domain_id,
            tenant_id=episode.tenant_id,
            kind=episode.kind,
            metadata=dict(episode.metadata or {}),
        )
        self.episodes.append(stored)
        return eid

    async def get_unreflected_episodes(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        limit: int = 500,
        brain_variant: str = "default",
    ) -> list[Episode]:
        out: list[Episode] = []
        for ep in self.episodes:
            if ep.tenant_id != tenant_id or ep.agent_id != agent_id:
                continue
            already = any(
                m.tenant_id == ep.tenant_id
                and m.agent_id == ep.agent_id
                and m.provenance.get("episode_id") == ep.id
                and str(m.provenance.get("brain_variant") or "default") == brain_variant
                for m in self.memories.values()
            )
            if not already:
                out.append(ep)
            if len(out) >= limit:
                break
        return out

    async def upsert_memory(
        self,
        memory: Memory,
        *,
        embedding: list[float] | None = None,
        copy_embedding_from_id: str | None = None,
    ) -> str:
        mid = memory.id or self._next_id("mem")
        stored = Memory(
            id=mid,
            content_raw=memory.content_raw,
            summary=memory.summary,
            type=memory.type,
            embedding_model=memory.embedding_model,
            embedding_dims=memory.embedding_dims,
            provenance=dict(memory.provenance),
            confidence=memory.confidence,
            status=memory.status,
            source_trust=memory.source_trust,
            agent_id=memory.agent_id,
            project_id=memory.project_id,
            domain_id=memory.domain_id,
            tenant_id=memory.tenant_id,
            scope=memory.scope,
            valid_from=memory.valid_from,
            valid_to=memory.valid_to,
            supersedes=memory.supersedes,
        )
        self.memories[mid] = stored
        if embedding is not None:
            self.embeddings[mid] = list(embedding)
        elif copy_embedding_from_id is not None and copy_embedding_from_id in self.embeddings:
            self.embeddings[mid] = list(self.embeddings[copy_embedding_from_id])
        return mid

    async def get_memory(self, memory_id: str, *, tenant_id: str) -> Memory:
        mem = self.memories.get(memory_id)
        if mem is None or mem.tenant_id != tenant_id:
            raise KeyError(memory_id)
        return mem

    async def get_memories(
        self,
        *,
        tenant_id: str,
        agent_id: str | None = None,
        project_id: str | None = None,
        domain_id: str | None = None,
        k: int = 8,
        query: str | None = None,
        query_vec: Any = None,
        scopes: tuple[str, ...] | None = None,
        brain_variant: str | None = None,
        specialization: str | None = None,
        include_quarantined: bool = False,
        min_confidence: float | None = None,
    ) -> list[Memory]:
        _ = (query_vec,)
        levels = scopes or ("agent", "project", "domain", "space")
        out: list[Memory] = []
        allowed_statuses = {"active", "quarantined"} if include_quarantined else {"active"}
        for m in self.memories.values():
            if (
                m.tenant_id != tenant_id
                or m.status not in allowed_statuses
                or m.valid_to is not None
            ):
                continue
            if min_confidence is not None and m.confidence < min_confidence:
                continue
            if brain_variant is not None:
                m_var = str(m.provenance.get("brain_variant") or "default")
                if m_var != brain_variant:
                    continue
            if specialization is not None:
                m_specs = m.provenance.get("specializations") or []
                if specialization not in m_specs:
                    continue
            visible = (
                ("agent" in levels and m.scope == "agent" and m.agent_id == agent_id)
                or (
                    "project" in levels
                    and m.scope == "project"
                    and m.project_id is not None
                    and m.project_id == project_id
                )
                or (
                    "domain" in levels
                    and m.scope == "domain"
                    and m.domain_id is not None
                    and m.domain_id == domain_id
                )
                or ("space" in levels and m.scope == "space")
            )
            if not visible:
                continue
            if query and query.strip():
                q_low = query.strip().lower()
                if q_low not in m.summary.lower() and q_low not in m.content_raw.lower():
                    continue
            out.append(m)
        return out[:k]

    async def find_existing_promoted_memory(
        self,
        *,
        tenant_id: str,
        target_scope: str,
        source_memory_id: str,
        summary: str,
        project_id: str | None = None,
        domain_id: str | None = None,
        brain_variant: str = "default",
    ) -> Memory | None:
        for m in self.memories.values():
            if m.tenant_id != tenant_id or m.scope != target_scope:
                continue
            if m.status != "active" or m.valid_to is not None:
                continue
            m_var = str(m.provenance.get("brain_variant") or "default")
            if m_var != brain_variant:
                continue
            if target_scope == "project" and m.project_id != project_id:
                continue
            if target_scope == "domain" and m.domain_id != domain_id:
                continue
            if (
                m.provenance.get("promoted_from_memory_id") == source_memory_id
                or m.summary == summary
            ):
                return m
        return None

    async def log_retrievals(
        self,
        *,
        memory_ids: list[str],
        tenant_id: str,
        agent_id: str,
        project_id: str | None,
    ) -> list[str]:
        _ = (tenant_id, agent_id, project_id)
        return [self._next_id("ret") for _ in memory_ids]

    async def get_retrieval_feedback(self, *, tenant_id: str, memory_id: str) -> list[str | None]:
        _ = tenant_id
        return list(self.feedbacks.get(memory_id, []))

    async def get_brain_variant_metrics(
        self,
        *,
        tenant_id: str,
        variant: str,
        scope: str = "agent",
        agent_id: str | None = None,
        project_id: str | None = None,
        domain_id: str | None = None,
        specialization: str | None = None,
    ) -> dict[str, Any]:
        return await Repository.get_brain_variant_metrics(
            cast(Repository, self),
            tenant_id=tenant_id,
            variant=variant,
            scope=scope,
            agent_id=agent_id,
            project_id=project_id,
            domain_id=domain_id,
            specialization=specialization,
        )

    async def offboard_agent_space(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        reason: str,
        purge_episodes: bool = True,
        retire_memories: bool = True,
    ) -> dict[str, int]:
        from datetime import UTC, datetime

        _ = reason
        retired = 0
        purged = 0
        deleted_prof = 0
        if retire_memories:
            for mid, m in list(self.memories.items()):
                if (
                    m.tenant_id == tenant_id
                    and m.agent_id == agent_id
                    and m.scope == "agent"
                    and m.valid_to is None
                ):
                    self.memories[mid] = Memory(
                        id=m.id,
                        content_raw=m.content_raw,
                        summary=m.summary,
                        type=m.type,
                        embedding_model=m.embedding_model,
                        embedding_dims=m.embedding_dims,
                        provenance=dict(m.provenance),
                        confidence=m.confidence,
                        status="superseded",
                        source_trust=m.source_trust,
                        agent_id=m.agent_id,
                        project_id=m.project_id,
                        domain_id=m.domain_id,
                        tenant_id=m.tenant_id,
                        scope=m.scope,
                        valid_from=m.valid_from,
                        valid_to=datetime.now(UTC),
                        supersedes=m.supersedes,
                    )
                    retired += 1
        if purge_episodes:
            kept_eps: list[Episode] = []
            for ep in self.episodes:
                ep_scope = str((ep.metadata or {}).get("scope") or "agent")
                if ep.tenant_id == tenant_id and ep.agent_id == agent_id and ep_scope == "agent":
                    purged += 1
                else:
                    kept_eps.append(ep)
            self.episodes = kept_eps
        if (tenant_id, agent_id) in self.profiles:
            del self.profiles[(tenant_id, agent_id)]
            deleted_prof = 1
        return {
            "retired_memories": retired,
            "purged_episodes": purged,
            "deleted_profiles": deleted_prof,
        }


def test_cli_init_profile_preservation_and_config_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    cfg = _parse_config_text(
        '[actor]\nagent_id = "agent-support-01"\nproject_id = "proj-checkout"\n'
        'tenant_id = "tenant-acme"\ndomain_id = "billing"\n'
        'scopes = ["agent", "project", "domain"]\n'
        '[store]\ndsn = "postgresql://127.0.0.1:5432/engramory"\n',
        Path(".engramory/config.toml"),
    )
    assert cfg.ctx.domain_id == "billing"

    fake_repo = InMemoryVirtualEmployeeRepo()
    asyncio.run(
        fake_repo.upsert_agent_profile(
            AgentProfile(
                agent_id="agent-support-01",
                display_name="Senior Billing Specialist",
                standing_preferences={"agent_type": "agent-support", "specializations": ["stripe"]},
                tenant_id="tenant-acme",
            )
        )
    )
    monkeypatch.setattr("engramory.cli.Repository", lambda dsn: fake_repo)
    state = _upsert_profile_best_effort(
        "agent-support-01",
        "postgresql://127.0.0.1:5432/engramory",
        "tenant-acme",
        domain_id="billing",
    )
    assert state == "created"
    preserved = asyncio.run(
        fake_repo.get_agent_profile("agent-support-01", tenant_id="tenant-acme")
    )
    assert preserved is not None
    assert preserved.display_name == "Senior Billing Specialist"
    assert preserved.standing_preferences["specializations"] == ["stripe"]
    assert preserved.standing_preferences["department_focus"] == "billing"


@pytest.mark.asyncio
async def test_domain_id_propagation_across_jwt_and_mcp() -> None:
    # 1. JWT claims -> ActorContext preserves domain_id and grants 'domain' scope
    claims = TokenClaims(
        sub="agent-support-01",
        iss="https://auth.example.com",
        aud="engramory",
        exp=9999999999,
        raw_claims={
            "tenant_id": "tenant-acme",
            "department_focus": "billing",
            "groups": ["domain:billing"],
            "projects": ["proj-checkout"],
        },
    )
    ctx = claims_to_actor_context(claims)
    assert ctx.domain_id == "billing"
    assert "domain" in ctx.scopes

    # 2. McpServer._resolve_call_ctx preserves base.domain_id on project override
    fake_repo = InMemoryVirtualEmployeeRepo()
    surface = AccessSurface(cast(Repository, fake_repo))
    mcp = McpServer(surface, ctx, auth_enabled=False)
    resolved = mcp._resolve_call_ctx({"project_id": "proj-invoicing"})
    assert resolved.project_id == "proj-invoicing"
    assert resolved.domain_id == "billing"

    # 3. Cross-department domain_id spoofing is blocked unless caller holds 'space' scope
    with pytest.raises(AuthzError, match="cross-department domain_id override"):
        await surface.memory_search(ctx, "stripe webhook", domain_id="hr-payroll")


@pytest.mark.asyncio
async def test_multi_model_distillation_specialist_roster_and_brain_compare() -> None:
    repo = InMemoryVirtualEmployeeRepo()
    mock_llm = AsyncMock()
    mock_llm.embed.side_effect = lambda texts, model=None: [[0.12] * 1536 for _ in texts]
    mock_vec = AsyncMock()
    surface = AccessSurface(cast(Repository, repo), llm=mock_llm)

    ctx_sup1 = ActorContext(
        agent_id="agent-support-01",
        project_id="proj-checkout",
        tenant_id="tenant-acme",
        domain_id="billing",
        scopes=frozenset({"agent", "project", "domain"}),
    )
    ctx_sup2 = ActorContext(
        agent_id="agent-support-02",
        project_id="proj-checkout",
        tenant_id="tenant-acme",
        domain_id="billing",
        scopes=frozenset({"agent", "project", "domain"}),
    )
    ctx_dev1 = ActorContext(
        agent_id="agent-dev-01",
        project_id="proj-checkout",
        tenant_id="tenant-acme",
        domain_id="engineering",
        scopes=frozenset({"agent", "project", "domain"}),
    )

    # Configure Virtual Employee profiles with archetype, specializations, and brain_config
    await surface.agent_profile_upsert(
        ctx_sup1,
        display_name="Support Specialist #1 (Stripe)",
        agent_type="agent-support",
        role="support",
        domain_id="billing",
        specializations=["stripe", "chargebacks"],
        brain_config={
            "active_brain_variant": "claude-sonnet-4-6",
            "distiller_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
            "rag_params": {"rrf_k": 60, "top_k": 8},
        },
    )
    await surface.agent_profile_upsert(
        ctx_sup2,
        display_name="Support Specialist #2 (OIDC)",
        agent_type="agent-support",
        role="support",
        domain_id="billing",
        specializations=["oidc", "sso"],
    )
    await surface.agent_profile_upsert(
        ctx_dev1,
        display_name="Dev Specialist #1 (Postgres)",
        agent_type="agent-dev",
        role="dev",
        domain_id="engineering",
        specializations=["pgvector"],
    )

    # Record episodes for agent-support-01
    await surface.memory_add(
        ctx_sup1,
        content="Stripe webhook signature mismatch: verify raw request body and 300s clock skew.",
        specializations=["stripe", "webhooks"],
    )

    # Distill Copy 1 using instance default brain_config ('claude-sonnet-4-6')
    c1 = await reflect(
        cast(Repository, repo),
        tenant_id="tenant-acme",
        agent_id="agent-support-01",
        llm=mock_llm,
        vector=mock_vec,
    )
    assert c1 == 1
    # Re-running reflect for the same variant is a no-op
    assert (
        await reflect(
            cast(Repository, repo),
            tenant_id="tenant-acme",
            agent_id="agent-support-01",
            llm=mock_llm,
            vector=mock_vec,
        )
        == 0
    )

    # Distill Copy 2 of the EXACT same episode under a second model variant ('gemini-2.5-pro')
    c2 = await reflect(
        cast(Repository, repo),
        tenant_id="tenant-acme",
        agent_id="agent-support-01",
        llm=mock_llm,
        vector=mock_vec,
        brain_variant="gemini-2.5-pro",
        distiller_model="gemini-2.5-pro",
        rag_params={"rrf_k": 40, "top_k": 5},
    )
    assert c2 == 1

    # Record feedback so claude-sonnet-4-6 has higher useful_rate
    for mid, m in repo.memories.items():
        if m.provenance.get("brain_variant") == "claude-sonnet-4-6":
            repo.feedbacks[mid] = ["useful", "useful"]
        else:
            repo.feedbacks[mid] = ["useful", "not_useful"]

    # Compare the two brain variants side-by-side
    cmp_res = await surface.agent_brain_compare(
        ctx_sup1,
        variant_a="claude-sonnet-4-6",
        variant_b="gemini-2.5-pro",
        query="Stripe webhook",
        specialization="stripe",
    )
    assert cmp_res["winner"] == "claude-sonnet-4-6"
    assert cmp_res["variant_a"]["skill_count"] == 1
    assert cmp_res["variant_b"]["skill_count"] == 1
    assert cmp_res["variant_a"]["useful_rate"] == 1.0
    assert cmp_res["variant_b"]["useful_rate"] == 0.5
    assert cmp_res["query_comparison"]["overlap_ratio"] == 1.0

    # Experience-ranked roster discovery filters by agent_type and specialization
    support_roster = await surface.agent_roster_list(
        ctx_sup1,
        domain_id="billing",
        agent_type="agent-support",
    )
    assert [a["agent_id"] for a in support_roster] == ["agent-support-01", "agent-support-02"]
    assert support_roster[0]["active_skill_count"] == 2
    assert set(support_roster[0]["brain_variants"]) == {"claude-sonnet-4-6", "gemini-2.5-pro"}

    stripe_specialists = await surface.agent_roster_list(
        ctx_sup1,
        agent_type="agent-support",
        specialization="stripe",
    )
    assert [a["agent_id"] for a in stripe_specialists] == ["agent-support-01"]


@pytest.mark.asyncio
async def test_virtual_employee_brain_passport_promotion_archetype_guard_and_offboard() -> None:
    repo = InMemoryVirtualEmployeeRepo()
    mock_llm = AsyncMock()
    mock_llm.embed.side_effect = lambda texts, model=None: [[0.25] * 1536 for _ in texts]
    surface = AccessSurface(cast(Repository, repo), llm=mock_llm)

    ctx_sup1 = ActorContext(
        agent_id="agent-support-01",
        project_id="acme-payments",
        tenant_id="tenant-acme",
        domain_id="billing",
        scopes=frozenset({"agent", "project", "domain"}),
    )
    ctx_sup2 = ActorContext(
        agent_id="agent-support-02",
        project_id="acme-payments",
        tenant_id="tenant-acme",
        domain_id="billing",
        scopes=frozenset({"agent", "project", "domain"}),
    )
    ctx_dev1 = ActorContext(
        agent_id="agent-dev-01",
        project_id="acme-payments",
        tenant_id="tenant-acme",
        domain_id="engineering",
        scopes=frozenset({"agent", "project", "domain"}),
    )

    await surface.agent_profile_upsert(
        ctx_sup1,
        display_name="Acme Billing Specialist #1",
        agent_type="agent-support",
        domain_id="billing",
        specializations=["stripe", "chargebacks"],
        standing_preferences={
            "escalation_contacts": ["alice@acme-corp.com", "https://hooks.acme-corp.internal/pager"]
        },
    )

    # Add private episode + shared project episode on Tenant A
    await surface.memory_add(
        ctx_sup1,
        content=(
            "On tenant-acme project acme-payments, when Stripe chargeback "
            "550e8400-e29b-41d4-a716-446655440000 arrives from 10.24.1.99 for "
            "bob@acme-corp.com, submit delivery receipt within 7 days."
        ),
        scope="agent",
        specializations=["stripe", "chargebacks"],
    )
    await surface.memory_add(
        ctx_sup1,
        content="Shared checkout release freeze window is 02:00 UTC.",
        scope="project",
    )
    await reflect(
        cast(Repository, repo),
        tenant_id="tenant-acme",
        agent_id="agent-support-01",
        llm=mock_llm,
        vector=AsyncMock(),
        brain_variant="claude-sonnet-4-6",
        distiller_model="claude-sonnet-4-6",
    )

    agent_mems = [
        m for m in repo.memories.values() if m.scope == "agent" and m.agent_id == "agent-support-01"
    ]
    assert len(agent_mems) == 1
    private_mem_id = str(agent_mems[0].id)

    # 1. Peer agent-support-02 CANNOT promote agent-support-01's private scope='agent' memory ([C2])
    with pytest.raises(AuthzError, match="out_of_scope"):
        await surface.memory_promote(
            ctx_sup2,
            memory_id=private_mem_id,
            target_scope="domain",
            evidence_ref="CHG-009-PROMOTE",
        )

    # 2. Owner agent-support-01 promotes skill to department scope ('domain')
    # and retry is idempotent ([M4], [M-MA-02])
    prom1 = await surface.memory_promote(
        ctx_sup1,
        memory_id=private_mem_id,
        target_scope="domain",
        evidence_ref="CHG-009-PROMOTE",
    )
    assert prom1["idempotent_replay"] is False
    prom2 = await surface.memory_promote(
        ctx_sup1,
        memory_id=private_mem_id,
        target_scope="domain",
        evidence_ref="CHG-009-PROMOTE",
    )
    assert prom2["idempotent_replay"] is True
    assert prom2["promoted_memory_id"] == prom1["promoted_memory_id"]

    # Peer agent-support-02 in 'billing' domain can now retrieve the promoted skill
    dom_hits = await surface.memory_search(
        ctx_sup2, "delivery receipt", scope="domain", specialization="chargebacks"
    )
    assert len(dom_hits) == 1

    # 3. Export sanitized Brain Passport from Tenant A
    passport = await surface.agent_brain_export(
        ctx_sup1,
        domain_id="billing",
        brain_variant="claude-sonnet-4-6",
        specialization="stripe",
        redact_terms=["acme", "acme-payments"],
    )
    assert passport["schema_version"] == "engramory.brain_passport.v1"
    assert passport["agent_type"] == "agent-support"
    assert passport["specializations"] == ["stripe", "chargebacks"]
    assert passport["passport_sha256"]
    # Recursive sanitization scrubbed nested list in standing_preferences and 'acme-payments'
    contacts = passport["standing_preferences"]["escalation_contacts"]
    assert all("acme" not in c.lower() and "alice@" not in c for c in contacts)
    assert "acme-payments" not in passport["skills"][0]["content_raw"]

    # 4. Tampered or stripped passport_sha256 is rejected ([C4])
    tampered = dict(passport)
    tampered["passport_sha256"] = "0" * 64
    with pytest.raises(GovernedWriteRejected, match="SHA-256 integrity check failed"):
        await surface.agent_brain_import(
            ctx_sup2,
            passport=tampered,
            evidence_ref="CHG-009-IMPORT",
        )

    # 5. Cross-archetype import (support passport -> dev instance) blocked by default ([C-MA-01])
    await surface.agent_profile_upsert(
        ctx_dev1,
        display_name="Dev Instance #1",
        agent_type="agent-dev",
        domain_id="engineering",
        specializations=["pgvector"],
    )
    with pytest.raises(GovernedWriteRejected, match="agent_type_mismatch"):
        await surface.agent_brain_import(
            ctx_dev1,
            passport=passport,
            evidence_ref="CHG-009-IMPORT",
        )

    # 6. Sibling instance clone (agent-support-01 -> agent-support-02) preserves display_name
    # and union-merges specializations ([C-MA-01])
    await surface.agent_profile_upsert(
        ctx_sup2,
        display_name="Billing Specialist #2",
        agent_type="agent-support",
        domain_id="billing",
        specializations=["oidc"],
    )
    clone_res = await surface.agent_brain_import(
        ctx_sup2,
        passport=passport,
        evidence_ref="CHG-009-CLONE",
    )
    assert clone_res["skills_imported"] == 1
    sup2_prof = await repo.get_agent_profile("agent-support-02", tenant_id="tenant-acme")
    assert sup2_prof is not None
    assert sup2_prof.display_name == "Billing Specialist #2"
    assert sup2_prof.standing_preferences["specializations"] == ["oidc", "stripe", "chargebacks"]

    # 7. Offboard agent-support-01 from Tenant A: retires private memories & private episodes
    # while preserving shared project episodes ([M1])
    offboard_res = await surface.agent_space_offboard(
        ctx_sup1,
        evidence_ref="CHG-009-OFFBOARD",
        reason="tenant_transfer",
    )
    assert offboard_res["retired_memories"] >= 1
    assert offboard_res["purged_episodes"] == 1
    assert offboard_res["deleted_profiles"] == 1
    # Shared project episode remains intact
    assert len(repo.episodes) == 1
    assert repo.episodes[0].metadata.get("scope") == "project"

    # 8. Import Brain Passport into Tenant B ('tenant-globex') with vector re-embedding ([C1], [M4])
    ctx_globex = ActorContext(
        agent_id="agent-support-01",
        project_id="globex-checkout",
        tenant_id="tenant-globex",
        domain_id="billing",
        scopes=frozenset({"agent", "project", "domain"}),
    )
    imp1 = await surface.agent_brain_import(
        ctx_globex,
        passport=passport,
        evidence_ref="CHG-009-GLOBEX-ONBOARD",
    )
    assert imp1["skills_imported"] == 1
    new_mem_id = imp1["memory_ids"][0]
    assert len(repo.embeddings[new_mem_id]) == 1536
    new_mem = await repo.get_memory(new_mem_id, tenant_id="tenant-globex")
    assert new_mem.provenance["brain_variant"] == "claude-sonnet-4-6"
    assert new_mem.provenance["brain_metadata"]["distiller_model"] == "claude-sonnet-4-6"

    # Re-importing the same passport is idempotent
    imp2 = await surface.agent_brain_import(
        ctx_globex,
        passport=passport,
        evidence_ref="CHG-009-GLOBEX-ONBOARD",
    )
    assert imp2["memory_ids"] == imp1["memory_ids"]

    # Cross-department target_domain_id spoofing on agent_brain_import is rejected
    with pytest.raises(AuthzError, match="cross-department domain_id override"):
        await surface.agent_brain_import(
            ctx_globex,
            passport=passport,
            evidence_ref="CHG-009-SPOOF",
            target_domain_id="hr-payroll",
        )


@pytest.mark.asyncio
async def test_preprod_security_and_quarantined_metrics_guards() -> None:
    from engramory.access.poisoning_guard import sanitize_portable_value

    repo = InMemoryVirtualEmployeeRepo()
    surface = AccessSurface(cast(Repository, repo))
    ctx = ActorContext(
        agent_id="agent-support-01",
        project_id="acme-payments",
        tenant_id="tenant-acme",
        domain_id="billing",
        scopes=frozenset({"agent", "project", "domain"}),
    )

    # 1. Cross-project memory_ingest_event & memory_learning_health rejected without 'space' scope
    with pytest.raises(AuthzError, match="out_of_scope"):
        await surface.memory_ingest_event(
            ctx,
            {
                "kind": "execution.completed",
                "occurred_at": "2026-11-13T12:00:00Z",
                "payload_type": "ExecutionCompleted.v1",
                "payload": {"status": "ok"},
                "project": "other-project",
                "producer": "agent-support-01",
                "tenant_id": "tenant-acme",
                "summary": "Attempted cross-project event write.",
            },
            scope="project",
        )
    with pytest.raises(AuthzError, match="out_of_scope"):
        await surface.memory_learning_health(
            ctx,
            project_id="other-project",
            scope="project",
        )

    # 2. Dict keys in sanitize_portable_value are scrubbed alongside values
    scrubbed_dict, redactions = sanitize_portable_value(
        {"ops@acme-corp.com": "https://internal.acme-corp.com/runbook"},
        redact_terms=["acme-corp"],
    )
    assert "[REDACTED_EMAIL]" in scrubbed_dict
    assert redactions >= 2

    # 3. Quarantined memories are still counted in harmful_count / useful_rate metrics
    q_mem = Memory(
        content_raw="Quarantined skill",
        summary="Quarantined skill",
        type="episodic",
        embedding_model="interim-1536",
        provenance={"brain_variant": "gemini-2.5-pro"},
        confidence=0.3,
        status="quarantined",
        agent_id="agent-support-01",
        project_id="acme-payments",
        domain_id="billing",
        tenant_id="tenant-acme",
        scope="agent",
    )
    qid = await repo.upsert_memory(q_mem)
    repo.feedbacks[qid] = ["harmful"]
    metrics = await repo.get_brain_variant_metrics(
        tenant_id="tenant-acme",
        variant="gemini-2.5-pro",
        scope="agent",
        agent_id="agent-support-01",
    )
    assert metrics["skill_count"] == 0
    assert metrics["harmful_count"] == 1
    assert metrics["total_retrievals"] == 1
    assert metrics["useful_rate"] == 0.0
