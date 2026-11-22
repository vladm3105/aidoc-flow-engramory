"""Memory core — orchestrates retrieval, write, and distillation via MemoryPort."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields
from typing import Any

from engramory.access.poisoning_guard import inspect_memory_write
from engramory.core.models import Episode, ValidationError
from engramory.core.repository import Repository
from engramory.ports import LLMPort, VectorPort


def _thaw_value(value: Any) -> Any:
    """Convert frozen MappingProxyType / nested lists into plain mutable structures."""
    if isinstance(value, Mapping):
        return {k: _thaw_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_thaw_value(v) for v in value]
    return value


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Serialize a frozen model dataclass without failing on MappingProxyType fields."""
    return {f.name: _thaw_value(getattr(obj, f.name)) for f in fields(obj)}


class MemoryCore:
    """Hexagonal L0 MemoryPort orchestration over PostgreSQL Repository + optional LLM/Vector."""

    def __init__(
        self,
        repo: Repository,
        *,
        llm: LLMPort | None = None,
        vector: VectorPort | None = None,
        default_tenant_id: str = "default",
    ) -> None:
        self._repo = repo
        self._llm = llm
        self._vector = vector
        self._default_tenant_id = default_tenant_id

    async def add_episode(
        self,
        *,
        agent_id: str,
        content: str,
        project_id: str | None = None,
        domain_id: str | None = None,
        tenant_id: str | None = None,
        kind: str = "note",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Append a screened L1 Episode and return its UUID."""
        tid = (tenant_id or self._default_tenant_id).strip()
        clean_content = inspect_memory_write(
            content,
            tenant_id=tid,
            writer_agent_id=agent_id,
            target="episode",
        )
        ep = Episode(
            agent_id=agent_id,
            content_raw=clean_content,
            project_id=project_id,
            domain_id=domain_id,
            tenant_id=tid,
            kind=kind,
            metadata=dict(metadata or {}),
        )
        return await self._repo.add_episode(ep)

    async def search(
        self,
        *,
        query: str,
        agent_id: str | None = None,
        project_id: str | None = None,
        domain_id: str | None = None,
        tenant_id: str | None = None,
        scope: str = "agent",
        k: int = 8,
        include_advisory: bool = False,
        token_budget: int | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Retrieve ranked memories visible within the requested scope ladder."""
        tid = (tenant_id or self._default_tenant_id).strip()
        allowed_scopes: tuple[str, ...]
        if scope == "agent":
            allowed_scopes = ("agent",)
        elif scope == "project":
            allowed_scopes = ("agent", "project")
        elif scope == "domain":
            allowed_scopes = ("agent", "project", "domain")
        elif scope == "space":
            allowed_scopes = ("agent", "project", "domain", "space")
        else:
            raise ValidationError(f"Unsupported memory search scope: {scope!r}")

        query_vec: Sequence[float] | None = None
        if self._llm is not None and query.strip():
            try:
                vecs = await self._llm.embed(texts=[query])
                if vecs:
                    query_vec = vecs[0]
            except Exception:  # noqa: S110, BLE001  # reason: lexical search remains functional when embedder is unavailable
                query_vec = None

        rows = await self._repo.get_memories(
            tenant_id=tid,
            agent_id=agent_id,
            project_id=project_id,
            domain_id=domain_id,
            k=k,
            query=query,
            query_vec=query_vec,
            scopes=allowed_scopes,
        )
        out: list[dict[str, Any]] = []
        used_tokens = 0
        for mem in rows:
            if not include_advisory and mem.status != "active":
                continue
            text = mem.summary or mem.content_raw
            toks = max(1, len(text.split()))
            if token_budget is not None and (used_tokens + toks) > token_budget:
                continue
            used_tokens += toks
            rec = _dataclass_to_dict(mem)
            rec["memory_id"] = mem.id
            rec["retrieval_id"] = None
            out.append(rec)
        return out

    async def reflect(
        self,
        *,
        agent_id: str,
        project_id: str | None = None,
        tenant_id: str | None = None,
    ) -> int:
        """Distill unreflected episodes for `agent_id` into L2 memories."""
        del project_id
        from engramory.workers.distillation import reflect as run_reflect

        tid = (tenant_id or self._default_tenant_id).strip()
        return await run_reflect(
            self._repo,
            tenant_id=tid,
            agent_id=agent_id,
            llm=self._llm,
            vector=self._vector,
        )

    async def consolidate(
        self,
        *,
        agent_id: str,
        tenant_id: str | None = None,
        project_id: str | None = None,
        domain_id: str | None = None,
        scope: str = "agent",
    ) -> Mapping[str, Any]:
        """Consolidate duplicate and stale memories for `agent_id`."""
        tid = (tenant_id or self._default_tenant_id).strip()
        return await self._repo.consolidate_memories(
            tenant_id=tid,
            agent_id=agent_id,
            project_id=project_id,
            domain_id=domain_id,
            scope=scope,
        )

    async def feedback(
        self,
        *,
        retrieval_id: str,
        outcome: str,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Record retrieval quality outcome for an existing retrieval."""
        tid = (tenant_id or self._default_tenant_id).strip()
        await self._repo.record_feedback(
            retrieval_id,
            outcome,
            tenant_id=tid,
            caller_agent_id=agent_id,
        )

    async def forget(
        self,
        *,
        memory_id: str,
        reason: str,
        superseded_by: str | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Soft-retire a memory preserving provenance."""
        del superseded_by
        tid = (tenant_id or self._default_tenant_id).strip()
        await self._repo.retire_memory(
            memory_id,
            tenant_id=tid,
            caller_agent_id=agent_id,
        )
        if reason:
            await self._repo.record_audit(
                tenant_id=tid,
                agent_id=agent_id or "memory-core",
                project_id=None,
                action="memory_forget",
                allowed=True,
                reason=f"{memory_id}: {reason}",
            )

    async def get_profile(
        self,
        *,
        agent_id: str,
        tenant_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Return the agent's standing identity profile."""
        tid = (tenant_id or self._default_tenant_id).strip()
        profile = await self._repo.get_agent_profile(agent_id, tenant_id=tid)
        if profile is None:
            return {"agent_id": agent_id, "tenant_id": tid, "standing_preferences": {}}
        return _dataclass_to_dict(profile)
