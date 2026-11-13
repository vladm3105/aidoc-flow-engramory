"""Distillation worker — reflection (episodes -> long-term) and consolidation
(merge/generalize/expire) passes. The 'sleep' that keeps memory dense and endless.

Worker-plane code: uses Repository directly by design; it is NOT a SPEC-01 agent tool.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from engramory.core.models import AgentProfile, Episode, Memory
from engramory.core.repository import Repository
from engramory.ports import LLMPort, VectorPort

logger = logging.getLogger(__name__)

# Fallback when LLMPort is not supplied or fails
INTERIM_EMBEDDING_MODEL = "none/interim-reflect"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
EXPECTED_EMBED_DIMS = 1536


async def reflect(
    repo: Repository,
    *,
    tenant_id: str,
    agent_id: str,
    llm: LLMPort | None = None,
    vector: VectorPort | None = None,
    batch_size: int = 32,
    brain_variant: str | None = None,
    distiller_model: str | None = None,
    rag_params: Mapping[str, Any] | None = None,
) -> int:
    """Project not-yet-projected episodes into agent-scoped Memory rows.

    When ``llm`` and ``vector`` are provided, summaries are embedded in batches
    via LiteLLM / LLMPort and the resulting projections are written to pgvector.
    Idempotent across runs per ``brain_variant``: linkage is ``provenance['episode_id']``
    + ``provenance['brain_variant']``; an episode already carrying a projection for
    ``brain_variant`` is skipped. Returns the number of memories created.
    """
    profile = (
        await repo.get_agent_profile(agent_id, tenant_id=tenant_id)
        if hasattr(repo, "get_agent_profile")
        else None
    )
    prefs = dict(profile.standing_preferences) if isinstance(profile, AgentProfile) else {}
    brain_cfg = (
        dict(prefs.get("brain_config") or {})
        if isinstance(prefs.get("brain_config"), Mapping)
        else {}
    )
    eff_variant = (
        brain_variant or str(brain_cfg.get("active_brain_variant") or "")
    ).strip() or "default"
    eff_distiller = (
        distiller_model or str(brain_cfg.get("distiller_model") or "")
    ).strip() or "reflect-v1"
    eff_rag_params: dict[str, Any] = (
        dict(rag_params)
        if rag_params is not None
        else (
            dict(brain_cfg["rag_params"])
            if isinstance(brain_cfg.get("rag_params"), Mapping)
            else {}
        )
    )
    profile_agent_type = str(prefs.get("agent_type") or prefs.get("role") or "").strip() or None
    raw_prof_specs = prefs.get("specializations")
    profile_specs = (
        [str(s).strip() for s in raw_prof_specs if str(s).strip()]
        if isinstance(raw_prof_specs, list | tuple)
        else []
    )

    episodes = await repo.get_unreflected_episodes(
        tenant_id=tenant_id, agent_id=agent_id, brain_variant=eff_variant
    )
    if not episodes:
        return 0

    created = 0
    # Process episodes in batches
    for i in range(0, len(episodes), batch_size):
        batch = episodes[i : i + batch_size]
        texts = [ep.content_raw for ep in batch]

        embeddings: Sequence[Sequence[float]] | None = None
        model_name = next(
            (
                val
                for attr in ("embed_model", "_embed_model", "_default_embed_model")
                if isinstance((val := getattr(llm, attr, None)), str) and val.strip()
            ),
            str(brain_cfg.get("embedding_model") or "").strip() or DEFAULT_EMBED_MODEL,
        )
        if llm is not None and vector is not None:
            try:
                embeddings = await llm.embed(texts=texts, model=model_name)
            except Exception as exc:
                logger.warning(
                    "Distillation embedding failed for batch (will continue unindexed): %s", exc
                )
                embeddings = None

        for idx, episode in enumerate(batch):
            if episode.id is None:
                raise RuntimeError("hydrated episode missing id")
            vec_candidate = (
                embeddings[idx] if (embeddings is not None and idx < len(embeddings)) else None
            )
            if vec_candidate and len(vec_candidate) != EXPECTED_EMBED_DIMS:
                logger.warning(
                    "Distillation embedding dim mismatch (expected %d, got %d); "
                    "skipping vector projection.",
                    EXPECTED_EMBED_DIMS,
                    len(vec_candidate),
                )
                vec_candidate = None
            has_vector = bool(vec_candidate) and len(vec_candidate or ()) == EXPECTED_EMBED_DIMS
            emb_model = model_name if has_vector else INTERIM_EMBEDDING_MODEL

            raw_scope = str((episode.metadata or {}).get("scope") or "agent")
            target_scope = (
                raw_scope if raw_scope in {"agent", "project", "domain", "space"} else "agent"
            )
            prov = _build_episode_provenance(
                episode,
                distiller=eff_distiller,
                brain_variant=eff_variant,
                embedding_model=emb_model,
                rag_params=eff_rag_params,
                agent_type=profile_agent_type,
                specializations=profile_specs,
            )
            memory = Memory(
                content_raw=episode.content_raw,
                summary=episode.content_raw,
                type="episodic",
                embedding_model=emb_model,
                embedding_dims=len(vec_candidate)
                if (has_vector and vec_candidate is not None)
                else None,
                provenance=prov,
                confidence=_resolve_episode_confidence(episode),
                agent_id=episode.agent_id,
                project_id=episode.project_id,
                domain_id=episode.domain_id,
                tenant_id=episode.tenant_id,
                scope=target_scope,
            )
            memory_id = await repo.upsert_memory(
                memory, embedding=vec_candidate if has_vector else None
            )
            if has_vector and vector is not None and vec_candidate is not None:
                await vector.upsert(
                    id=memory_id,
                    embedding=vec_candidate,
                    metadata={"tenant_id": episode.tenant_id},
                )

            created += 1

    return created


_PROVENANCE_PASSTHROUGH_KEYS = (
    "event_id",
    "event_kind",
    "producer",
    "occurred_at",
    "payload_type",
    "origin",
    "trace_id",
    "effectiveness_score",
    "calibration_delta",
    "agent_type",
    "specializations",
)


def _build_episode_provenance(
    episode: Episode,
    *,
    distiller: str,
    brain_variant: str = "default",
    embedding_model: str = INTERIM_EMBEDDING_MODEL,
    rag_params: Mapping[str, Any] | None = None,
    agent_type: str | None = None,
    specializations: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build distilled Memory.provenance preserving OriginProvenance, W3C trace_id,
    multi-model brain_metadata, agent_type, and specializations."""
    prov: dict[str, Any] = {
        "episode_id": episode.id,
        "distiller": distiller,
        "brain_variant": brain_variant,
        "brain_metadata": {
            "brain_variant": brain_variant,
            "distiller_model": distiller,
            "embedding_model": embedding_model,
            "rag_params": dict(rag_params or {}),
        },
    }
    meta = episode.metadata or {}
    for key in _PROVENANCE_PASSTHROUGH_KEYS:
        if key in meta and meta[key] is not None:
            val = meta[key]
            prov[key] = dict(val) if isinstance(val, dict) else val
    if agent_type and not prov.get("agent_type"):
        prov["agent_type"] = agent_type
    ep_specs_raw = meta.get("specializations")
    ep_specs = (
        [str(s).strip() for s in ep_specs_raw if str(s).strip()]
        if isinstance(ep_specs_raw, list | tuple)
        else []
    )
    merged_specs = list(dict.fromkeys([*(specializations or ()), *ep_specs]))
    if merged_specs:
        prov["specializations"] = merged_specs
    return prov


def _resolve_episode_confidence(episode: Episode) -> float:
    """Resolve initial Memory.confidence from episode.metadata['effectiveness_score']."""
    meta = episode.metadata or {}
    raw_score = meta.get("effectiveness_score")
    if (
        isinstance(raw_score, (int, float))
        and not isinstance(raw_score, bool)
        and 0.0 <= float(raw_score) <= 1.0
    ):
        return float(raw_score)
    return 0.6
