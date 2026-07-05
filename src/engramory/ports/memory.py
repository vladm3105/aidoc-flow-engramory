"""MemoryPort — the experiential memory interface (L1/L2/L3).

Adapters: LangMem / Mem0 / Cipher over Postgres (dev & cloud), with Postgres
always canonical. A managed memory service may back this as an accelerator only.
"""
from __future__ import annotations
from typing import Protocol, Sequence, Mapping, Any


class MemoryPort(Protocol):
    def add_episode(self, *, agent_id: str, project_id: str | None,
                    content: str, kind: str = "note",
                    metadata: Mapping[str, Any] | None = None) -> str:
        """Append a raw episode (L1 source material). Returns the episode id."""
        ...

    def search(self, *, query: str, agent_id: str | None = None,
               project_id: str | None = None, scope: str = "agent",
               k: int = 8) -> Sequence[Mapping[str, Any]]:
        """Retrieve top-k distilled memories relevant to the query."""
        ...

    def distill(self, *, agent_id: str, project_id: str | None = None) -> int:
        """Reflection pass: promote recent episodes into long-term memories."""
        ...

    def consolidate(self, *, agent_id: str) -> Mapping[str, Any]:
        """Consolidation pass: merge/generalize/expire to keep memory dense."""
        ...
