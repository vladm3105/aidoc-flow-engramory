"""Distillation worker — reflection (episodes -> long-term) and consolidation
(merge/generalize/expire) passes. The 'sleep' that keeps memory dense and endless.

Worker-plane code: uses Repository directly by design; it is NOT a SPEC-01 agent tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from engramory.core.models import MEMORY_TYPES, AgentProfile, Episode, Memory, thaw_mapping
from engramory.core.poisoning_guard import MemoryPoisoningError, inspect_memory_write
from engramory.core.repository import Repository
from engramory.ports import LLMPort, VectorPort

logger = logging.getLogger(__name__)

# Fallback when LLMPort is not supplied or returns wrong dims
INTERIM_EMBEDDING_MODEL = "none/interim-reflect"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
EXPECTED_EMBED_DIMS = 1536
_EMBED_MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ConsolidationStats:
    """Summary counters from a tenant/agent memory consolidation pass."""

    tenant_id: str
    agent_id: str | None = None
    merged: int = 0
    expired: int = 0
    run_id: str | None = None


async def _synthesize_with_llm(
    llm: LLMPort,
    batch: Sequence[Episode],
    *,
    distiller_model: str,
) -> dict[str, dict[str, Any]]:
    """Invoke llm.chat() to distill batch summaries/types, or {} on mock/invalid output."""
    chat_fn = getattr(llm, "chat", None)
    if chat_fn is None:
        return {}
    prompt_items = [
        {"episode_id": ep.id or str(idx), "kind": ep.kind, "content": ep.content_raw[:2000]}
        for idx, ep in enumerate(batch)
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "Distill each agent experience episode into JSON array items with keys "
                "'episode_id', 'summary', and 'type' (episodic|semantic|procedural)."
            ),
        },
        {"role": "user", "content": json.dumps(prompt_items, ensure_ascii=False)},
    ]
    try:
        raw = await chat_fn(messages=messages, model=distiller_model)
    except Exception as exc:  # noqa: BLE001  # reason: graceful fallback to verbatim episode distillation when chat fails
        logger.debug("Optional llm.chat() distillation skipped (%s)", exc)
        return {}
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    items = (
        parsed
        if isinstance(parsed, list)
        else parsed.get("items")
        if isinstance(parsed, dict)
        else None
    )
    if not isinstance(items, list):
        return {}
    distilled: dict[str, dict[str, Any]] = {}
    for entry in items:
        if not isinstance(entry, Mapping):
            continue
        eid = str(entry.get("episode_id") or "").strip()
        summary = str(entry.get("summary") or "").strip()
        mtype = str(entry.get("type") or "episodic").strip().lower()
        if not eid or not summary:
            continue
        if mtype not in MEMORY_TYPES:
            mtype = "episodic"
        distilled[eid] = {"summary": summary, "type": mtype}
    return distilled


async def _embed_with_retries(
    llm: LLMPort,
    *,
    texts: Sequence[str],
    model_name: str,
    max_attempts: int = _EMBED_MAX_ATTEMPTS,
) -> Sequence[Sequence[float]]:
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await llm.embed(texts=texts, model=model_name)
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < max_attempts:
                await asyncio.sleep(0.05 * (2**attempt))
    if last_exc is not None:
        raise last_exc
    return []


async def consolidate(
    repo: Repository,
    *,
    tenant_id: str,
    agent_id: str | None = None,
    project_id: str | None = None,
    domain_id: str | None = None,
    scope: str | None = None,
    expire_below_confidence: float = 0.2,
    min_age_days: int = 30,
) -> ConsolidationStats:
    """Merge duplicate memories and expire stale low-confidence memories."""
    fn = getattr(repo, "consolidate_memories", None)
    if fn is None or not callable(fn):
        return ConsolidationStats(tenant_id=tenant_id, agent_id=agent_id)
    res = await fn(
        tenant_id=tenant_id,
        agent_id=agent_id,
        project_id=project_id,
        domain_id=domain_id,
        scope=scope,
        expire_below_confidence=expire_below_confidence,
        min_age_days=min_age_days,
    )
    if not isinstance(res, Mapping):
        return ConsolidationStats(tenant_id=tenant_id, agent_id=agent_id)
    raw_run = res.get("run_id")
    return ConsolidationStats(
        tenant_id=tenant_id,
        agent_id=agent_id,
        merged=int(res.get("merged", 0) or 0),
        expired=int(res.get("expired", 0) or 0),
        run_id=str(raw_run) if raw_run is not None else None,
    )


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
    prefs = thaw_mapping(profile.standing_preferences) if isinstance(profile, AgentProfile) else {}
    brain_cfg = (
        thaw_mapping(prefs.get("brain_config"))
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
        synth_map = (
            await _synthesize_with_llm(llm, batch, distiller_model=eff_distiller)
            if llm is not None
            else {}
        )
        batch_drafts: list[tuple[Episode, str, str]] = []
        for idx, ep in enumerate(batch):
            if ep.id is None:
                raise RuntimeError("hydrated episode missing id")
            synth = synth_map.get(ep.id) or synth_map.get(str(idx))
            distilled_summary = ep.content_raw
            distilled_type = "episodic"
            if synth is not None:
                try:
                    distilled_summary = inspect_memory_write(
                        str(synth["summary"]),
                        tenant_id=ep.tenant_id,
                        writer_agent_id=ep.agent_id,
                        target="distillation",
                    )
                    distilled_type = str(synth["type"])
                except MemoryPoisoningError:
                    distilled_summary = ep.content_raw
                    distilled_type = "episodic"
            batch_drafts.append((ep, distilled_summary, distilled_type))

        texts = [summary_text for _, summary_text, _ in batch_drafts]

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
                embeddings = await _embed_with_retries(llm, texts=texts, model_name=model_name)
            except Exception as exc:  # noqa: BLE001  # reason: degraded LLM embedding must fall back to unindexed lexical memory
                logger.warning(
                    "Distillation embedding failed for batch (will continue unindexed): %s", exc
                )
                embeddings = None

        for idx, (episode, distilled_summary, distilled_type) in enumerate(batch_drafts):
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
            if raw_scope == "project" and episode.project_id:
                target_scope = "project"
            elif raw_scope == "domain" and episode.domain_id:
                target_scope = "domain"
            elif raw_scope == "space" and bool(
                (episode.metadata or {}).get("_verified_space_scope")
            ):
                target_scope = "space"
            else:
                target_scope = "agent"
            ep_meta_variant = str((episode.metadata or {}).get("brain_variant") or "").strip()
            ep_variant = (
                (brain_variant or str(brain_cfg.get("active_brain_variant") or "")).strip()
                or ep_meta_variant
                or "default"
            )
            prov = _build_episode_provenance(
                episode,
                distiller=eff_distiller,
                brain_variant=ep_variant,
                embedding_model=emb_model,
                rag_params=eff_rag_params,
                agent_type=profile_agent_type,
                specializations=profile_specs,
            )
            memory = Memory(
                content_raw=episode.content_raw,
                summary=distilled_summary,
                type=distilled_type,
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
