"""Distillation worker — reflection (episodes -> long-term) and consolidation
(merge/generalize/expire) passes. The 'sleep' that keeps memory dense and endless.

INTERIM state (PLAN-002 Phase 1.4 / ADR-10): ``reflect`` below is a verbatim
projection so agent-added episodes become retrievable before the adopted
MemoryPort engine (SPEC-04) lands. That engine replaces this body — the
signature and the CLI command that invokes it survive. Worker-plane code:
uses Repository directly by design; it is NOT a SPEC-01 agent tool.
"""
from __future__ import annotations

from engramory.core.models import Memory
from engramory.core.repository import Repository

# Placeholder until the LLMPort adapter embeds memories; Memory requires a
# non-empty embedding_model, and rows without an embedding are recency-ranked.
INTERIM_EMBEDDING_MODEL = "none/interim-reflect"


async def reflect(repo: Repository, *, tenant_id: str, agent_id: str) -> int:
    """Project not-yet-projected episodes into agent-scoped Memory rows.

    Idempotent across runs: linkage is ``provenance['episode_id']``; an episode
    already carrying a projection is skipped. Not safe against *concurrent*
    reflect runs for the same (tenant, agent) — acceptable for the dev tier.
    Returns the number of memories created.
    """
    episodes = await repo.get_unreflected_episodes(tenant_id=tenant_id, agent_id=agent_id)
    created = 0
    for episode in episodes:
        assert episode.id is not None  # hydrated rows always carry id
        await repo.upsert_memory(
            Memory(
                content_raw=episode.content_raw,
                summary=episode.content_raw,
                type="episodic",
                embedding_model=INTERIM_EMBEDDING_MODEL,
                provenance={"episode_id": episode.id, "distiller": "interim-reflect-v0"},
                agent_id=episode.agent_id,
                project_id=episode.project_id,
                domain_id=episode.domain_id,
                tenant_id=episode.tenant_id,
                scope="agent",
            )
        )
        created += 1
    return created
