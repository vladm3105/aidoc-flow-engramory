"""MemoryPort — the experiential memory interface (L1/L2/L3).

Adapters: LangMem / Mem0 / Cipher over Postgres (dev & cloud), with Postgres always
canonical. A managed memory service may back this as an accelerator only.

Scope ladder (see GLOSSARY + ADR-07): agent -> project -> domain -> space, all within a
tenant. `tenant_id` is the hard isolation boundary; `space` scope = tenant-wide sharing.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class MemoryPort(Protocol):
    async def add_episode(self, *, agent_id: str, content: str,
                          project_id: str | None = None, domain_id: str | None = None,
                          tenant_id: str | None = None, kind: str = "note",
                          metadata: Mapping[str, Any] | None = None) -> str:
        """Append a raw episode (L1 source material). Returns the episode id."""
        ...

    async def search(self, *, query: str, agent_id: str | None = None,
                     project_id: str | None = None, domain_id: str | None = None,
                     tenant_id: str | None = None, scope: str = "agent",
                     k: int = 8) -> Sequence[Mapping[str, Any]]:
        """Retrieve top-k distilled memories relevant to `query`, visible at `scope`
        (agent | project | domain | space)."""
        ...

    async def distill(self, *, agent_id: str, project_id: str | None = None) -> int:
        """Reflection pass: promote recent episodes into long-term memories.
        Returns the number of memories written."""
        ...

    async def consolidate(self, *, agent_id: str) -> Mapping[str, Any]:
        """Consolidation pass: merge/generalize/expire to keep memory dense.
        Returns run statistics."""
        ...
