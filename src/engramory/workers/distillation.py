"""Distillation worker — reflection (episodes -> long-term) and consolidation
(merge/generalize/expire) passes. The 'sleep' that keeps memory dense and endless.

Worker-plane code: uses Repository directly by design; it is NOT a SPEC-01 agent tool.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from engramory.core.models import Memory
from engramory.core.repository import Repository
from engramory.ports import LLMPort, VectorPort

logger = logging.getLogger(__name__)

# Fallback when LLMPort is not supplied or fails
INTERIM_EMBEDDING_MODEL = "none/interim-reflect"
DEFAULT_EMBED_MODEL = "bge-small-en-v1.5"


async def reflect(
    repo: Repository,
    *,
    tenant_id: str,
    agent_id: str,
    llm: LLMPort | None = None,
    vector: VectorPort | None = None,
    batch_size: int = 32,
) -> int:
    """Project not-yet-projected episodes into agent-scoped Memory rows.

    When ``llm`` and ``vector`` are provided, summaries are embedded in batches
    via LiteLLM / LLMPort and the resulting projections are written to pgvector.
    Idempotent across runs: linkage is ``provenance['episode_id']``; an episode
    already carrying a projection is skipped. Returns the number of memories created.
    """
    episodes = await repo.get_unreflected_episodes(tenant_id=tenant_id, agent_id=agent_id)
    if not episodes:
        return 0

    created = 0
    # Process episodes in batches
    for i in range(0, len(episodes), batch_size):
        batch = episodes[i : i + batch_size]
        texts = [ep.content_raw for ep in batch]

        embeddings: Sequence[Sequence[float]] | None = None
        model_name = getattr(llm, "_default_embed_model", DEFAULT_EMBED_MODEL)
        if llm is not None and vector is not None:
            try:
                embeddings = await llm.embed(texts=texts, model=model_name)
            except Exception as exc:
                logger.warning(
                    "Distillation embedding failed for batch (will continue unindexed): %s", exc
                )
                embeddings = None

        for idx, episode in enumerate(batch):
            assert episode.id is not None  # hydrated rows always carry id
            has_vector = embeddings is not None and idx < len(embeddings)
            emb_model = model_name if has_vector else INTERIM_EMBEDDING_MODEL

            memory = Memory(
                content_raw=episode.content_raw,
                summary=episode.content_raw,
                type="episodic",
                embedding_model=emb_model,
                embedding_dims=len(embeddings[idx])
                if (has_vector and embeddings is not None)
                else None,
                provenance={"episode_id": episode.id, "distiller": "reflect-v1"},
                agent_id=episode.agent_id,
                project_id=episode.project_id,
                domain_id=episode.domain_id,
                tenant_id=episode.tenant_id,
                scope="agent",
            )
            memory_id = await repo.upsert_memory(memory)
            if has_vector and vector is not None and embeddings is not None:
                await vector.upsert(id=memory_id, embedding=embeddings[idx])

            created += 1

    return created
